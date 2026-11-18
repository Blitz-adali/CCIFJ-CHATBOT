"""WhatsApp and Slack as text front ends to the same chat pipeline as the website.

A sender starts as a guest. Anything that needs an account (bookings, reservations,
subscriptions) answers with a one-time link to log in on the website; see
concierge.account_linking. Actions that need approval are confirmed by replying YES or NO.

Webhooks are acknowledged immediately and answered from a background task, because both
platforms retry a webhook that is slow to respond.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from weakref import WeakValueDictionary

from fastapi import HTTPException, Response

from concierge import account_linking as linking
from concierge.api import chat as chat_api
from concierge.api.agent_backend import LOGIN_WARNING
from concierge.api.schemas import ApprovalRequest, ChatRequest
from concierge.api.session_access import pending_turn, session_token
from concierge.config import get_settings
from concierge.db.relationaldb import crud
from concierge.db.relationaldb.models import User
from concierge.db.relationaldb.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

LINK_WORDS = {"link", "link account", "login", "log in", "sign in", "lier", "connexion", "ログイン", "連携"}
UNLINK_WORDS = {"unlink", "logout", "log out", "sign out", "délier", "déconnexion", "ログアウト", "連携解除"}
YES_WORDS = {"yes", "y", "ok", "confirm", "approve", "oui", "confirmer", "はい", "確定"}
NO_WORDS = {"no", "n", "cancel", "reject", "non", "annuler", "いいえ", "キャンセル"}

TEXT = {
    "invite": {
        "en": "To continue, link your CCIFJ account. Open this link and log in on the website (valid for {minutes} minutes, one use):\n{url}\n\nNever send your password in this chat.",
        "fr": "Pour continuer, liez votre compte CCIFJ. Ouvrez ce lien et connectez-vous sur le site (valable {minutes} minutes, usage unique) :\n{url}\n\nN’envoyez jamais votre mot de passe dans cette conversation.",
        "ja": "続けるにはCCIFJアカウントの連携が必要です。次のリンクを開き、ウェブサイトでログインしてください（{minutes}分間有効・1回限り）：\n{url}\n\nこのチャットにパスワードを送信しないでください。",
    },
    "already_linked": {
        "en": "This chat is already linked to the CCIFJ account of {name}. Send \"unlink\" to disconnect it.",
        "fr": "Cette conversation est déjà liée au compte CCIFJ de {name}. Envoyez « unlink » pour la dissocier.",
        "ja": "このチャットは{name}さんのCCIFJアカウントと連携済みです。解除するには「unlink」と送信してください。",
    },
    "not_linked": {
        "en": "This chat is not linked to a CCIFJ account. Send \"link\" to connect one.",
        "fr": "Cette conversation n’est liée à aucun compte CCIFJ. Envoyez « link » pour en lier un.",
        "ja": "このチャットはCCIFJアカウントと連携されていません。連携するには「link」と送信してください。",
    },
    "unlinked": {
        "en": "Done. This chat is no longer linked to your CCIFJ account.",
        "fr": "C’est fait. Cette conversation n’est plus liée à votre compte CCIFJ.",
        "ja": "CCIFJアカウントとの連携を解除しました。",
    },
    "linked": {
        "en": "🎉 Success! This chat is now linked to the CCIFJ account of {name}. How can I help you today?",
        "fr": "🎉 C’est fait ! Cette conversation est maintenant liée au compte CCIFJ de {name}. Comment puis-je vous aider ?",
        "ja": "🎉 連携が完了しました。このチャットは{name}さんのCCIFJアカウントと連携されています。ご用件をお聞かせください。",
    },
    "review": {
        "en": "Please check this before I go ahead:",
        "fr": "Merci de vérifier avant que je poursuive :",
        "ja": "実行前に内容をご確認ください：",
    },
    "confirm": {
        "en": "Reply YES to confirm or NO to cancel. Nothing happens until you confirm.",
        "fr": "Répondez OUI pour confirmer ou NON pour annuler. Rien n’est fait sans votre confirmation.",
        "ja": "確定する場合は「はい」、取り消す場合は「いいえ」と返信してください。確定するまで実行されません。",
    },
    "unknown": {
        "en": "I could not confirm that the last request finished. Please check your bookings or references, then send your message again.",
        "fr": "Je n’ai pas pu confirmer que la dernière demande a abouti. Vérifiez vos réservations ou références, puis renvoyez votre message.",
        "ja": "前回の操作が完了したか確認できませんでした。予約や問い合わせ番号をご確認のうえ、もう一度メッセージをお送りください。",
    },
    "error": {
        "en": "Sorry, I could not answer just now. Please try again in a moment.",
        "fr": "Désolé, je n’ai pas pu répondre. Veuillez réessayer dans un instant.",
        "ja": "申し訳ありません。ただいま回答できませんでした。しばらくしてからもう一度お試しください。",
    },
    "text_only": {
        "en": "I can only read text messages here. Please type your question.",
        "fr": "Je ne peux lire que les messages texte ici. Merci d’écrire votre question.",
        "ja": "こちらではテキストメッセージのみ対応しています。ご質問を文字でお送りください。",
    },
}


@dataclass(frozen=True)
class Incoming:
    platform: str  # whatsapp | slack
    sender: str  # WhatsApp number (digits only) or Slack user ID
    text: str  # "" for a message that is not text (image, voice note, ...)
    message_id: str
    team_id: str = ""  # Slack workspace
    reply_to: str | None = None  # Slack DM channel


# ---------- Webhook authentication and parsing ----------

def valid_whatsapp_signature(body: bytes, header: str | None) -> bool:
    expected = "sha256=" + hmac.new(get_settings().whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), (header or "").encode())


def valid_slack_signature(body: bytes, timestamp: str | None, header: str | None) -> bool:
    try:
        # An old timestamp means a captured request is being replayed.
        if abs(time.time() - int(timestamp or "")) > 300:
            return False
    except ValueError:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(get_settings().slack_signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), (header or "").encode())


def whatsapp_messages(payload: dict) -> list[Incoming]:
    """User messages addressed to our number in a WhatsApp Cloud API webhook."""
    found = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if (value.get("metadata") or {}).get("phone_number_id") != get_settings().whatsapp_phone_number_id:
                continue
            for message in value.get("messages") or []:
                sender, message_id = str(message.get("from") or ""), str(message.get("id") or "")
                if not sender.isdigit() or not message_id:
                    continue
                text = (message.get("text") or {}).get("body") if message.get("type") == "text" else ""
                found.append(Incoming("whatsapp", sender, str(text or ""), message_id))
    return found


def slack_message(payload: dict) -> Incoming | None:
    """A person's direct message to the bot in a Slack Events API callback, or None."""
    event = payload.get("event") or {}
    if (
        payload.get("type") != "event_callback" or event.get("type") != "message"
        or event.get("channel_type") != "im" or event.get("bot_id")
        # Edits, deletions and joins carry a subtype; a shared file is still a user message.
        or event.get("subtype") not in (None, "file_share")
        or not event.get("user") or not event.get("channel") or not payload.get("team_id")
    ):
        return None
    return Incoming(
        "slack", str(event["user"]), str(event.get("text") or ""),
        str(payload.get("event_id") or event.get("client_msg_id") or event.get("ts")),
        team_id=str(payload["team_id"]), reply_to=str(event["channel"]),
    )


# ---------- Sending ----------

def _post(url: str, token: str, payload: dict) -> dict:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8",
    })
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read() or b"{}")


def _parts(text: str, limit: int) -> list[str]:
    """Split a long reply at line breaks where possible."""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return parts + [text] if text else parts


async def send(platform: str, to: str, text: str) -> bool:
    """Deliver a text message; a delivery failure is logged, never raised."""
    settings = get_settings()
    try:
        for part in _parts(text, 4000 if platform == "whatsapp" else 3500):
            if platform == "whatsapp":
                version = os.getenv("WHATSAPP_API_VERSION", "v23.0").strip()
                await asyncio.to_thread(
                    _post, f"https://graph.facebook.com/{version}/{settings.whatsapp_phone_number_id}/messages",
                    settings.whatsapp_access_token,
                    {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": part, "preview_url": False}},
                )
            else:
                result = await asyncio.to_thread(
                    _post, "https://slack.com/api/chat.postMessage", settings.slack_bot_token,
                    {"channel": to, "text": part, "unfurl_links": False},
                )
                if not result.get("ok"):
                    raise RuntimeError(f"Slack rejected the message: {result.get('error')}")
        return True
    except Exception:
        logger.exception("Could not deliver a %s message", platform)
        return False


def _address(identity) -> str:
    return identity.reply_to or identity.platform_user_id


async def notify_linked(identity, name: str, locale: str = "en") -> None:
    """Tell the chat that linking worked (the last step of the linking flow)."""
    await send(identity.platform, _address(identity), TEXT["linked"][locale].format(name=name))


# ---------- Text rendering ----------

def detect_locale(text: str) -> str:
    """Language for the fixed channel messages; the agent itself answers in the user's language."""
    if re.search(r"[぀-ヿ一-鿿]", text):
        return "ja"
    if re.search(r"[éèêàùçœ]|\b(bonjour|merci|oui|non|je|une|les|pour|lier)\b", text, re.I):
        return "fr"
    return "en"


def channel_text(markdown: str, platform: str) -> str:
    """The agent writes Markdown for the website; WhatsApp and Slack use *bold* and bare links."""
    text = markdown
    if platform == "slack":
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        link = lambda m: f"<{m[2]}|{m[1]}>"
    else:
        link = lambda m: m[2] if m[1] == m[2] else f"{m[1]}: {m[2]}"
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", link, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", text, flags=re.M)
    return text.strip()


def _detail_lines(values: dict, indent: str = ""):
    for key, value in values.items():
        if value in (None, "", [], {}) or key.endswith("_id"):
            continue
        label = key.replace("_", " ").capitalize()
        if isinstance(value, dict):
            yield f"{indent}{label}:"
            yield from _detail_lines(value, indent + "  ")
        elif isinstance(value, list):
            yield f"{indent}{label}:"
            for item in value:
                shown = ", ".join(str(v) for v in item.values() if v not in (None, "")) if isinstance(item, dict) else str(item)
                yield f"{indent}  - {shown}"
        else:
            yield f"{indent}{label}: {value}"


def approval_text(approvals: list[dict], locale: str) -> str:
    """The website shows a review card; here the same details are listed as text."""
    blocks = [TEXT["review"][locale]]
    for approval in approvals:
        for action in approval.get("actions") or approval.get("action_requests") or []:
            if isinstance(action, dict):
                title = str(action.get("name") or "action").replace("_", " ").capitalize()
                blocks.append("\n".join([f"*{title}*", *_detail_lines(action.get("args") or {})]))
    blocks.append(TEXT["confirm"][locale])
    return "\n\n".join(blocks)


async def invitation(session, identity, locale: str) -> str:
    token = await linking.create_link_token(session, identity)
    return TEXT["invite"][locale].format(
        url=linking.link_url(token), minutes=max(1, get_settings().link_token_seconds // 60),
    )


# ---------- Conversation ----------

async def _clear_unknown_outcome(session, session_id: int) -> None:
    """Same effect as POST /sessions/{id}/reconcile, once the user has been told to check."""
    latest = await crud.get_latest_message(session, session_id)
    if latest is not None:
        metadata = [item for item in latest.tool_calls or [] if item.get("type") != "api_turn_state"]
        await crud.update_message_metadata(session, latest, metadata or None)


def _backend():
    # The agent is loaded on first use, exactly as for a website request.
    return chat_api.LazyBackend()


async def converse(session, identity, user, incoming: Incoming, locale: str) -> str:
    """Run one turn through the website's chat endpoint and render the answer as text."""
    conversation = await crud.get_chat_session(session, identity.session_id) if identity.session_id else None
    if conversation is not None and conversation.user_id not in (None, identity.user_id):
        conversation = None
    if conversation is None:
        conversation = await crud.create_chat_session(session, identity.user_id)
        identity.session_id = conversation.session_id
        session.add(identity)
        await session.commit()
    session_id = conversation.session_id
    token = session_token(session_id)
    backend = _backend()
    pending = pending_turn(await crud.get_latest_message(session, session_id))
    await session.commit()
    try:
        if pending:
            word = incoming.text.strip().casefold().strip(" .!。！")
            if word not in YES_WORDS | NO_WORDS:
                return approval_text(pending["approvals"], locale)
            result = await chat_api.approve(token, ApprovalRequest(
                request_id=incoming.message_id[:100], locale=locale,
                interrupt_id=pending["approvals"][0]["interrupt_id"],
                decision="approve" if word in YES_WORDS else "reject",
            ), session, user, backend, Response())
        else:
            result = await chat_api.chat(ChatRequest(
                request_id=incoming.message_id[:100], locale=locale, session_id=token, message=incoming.text[:16000],
            ), session, user, backend, Response())
    except HTTPException as exc:
        await session.rollback()
        if isinstance(exc.detail, dict) and exc.detail.get("outcome_unknown"):
            await _clear_unknown_outcome(session, session_id)
            return TEXT["unknown"][locale]
        logger.warning("Channel turn was rejected: %s %s", exc.status_code, exc.detail)
        return TEXT["error"][locale]
    if result.pending_approvals:
        return approval_text(result.pending_approvals, locale)
    text = channel_text(result.reply, identity.platform)
    needs_login = user is None and (
        any(warning in LOGIN_WARNING.values() for warning in result.warnings)
        or any(action.get("id") == "auth.login" for action in result.next_actions)
    )
    if needs_login:
        text += "\n\n" + await invitation(session, identity, locale)
    return text


async def respond(session, incoming: Incoming) -> tuple[str, str]:
    """Return (address, reply text) for one incoming message."""
    identity = await linking.get_or_create_identity(
        session, incoming.platform, incoming.sender, incoming.team_id, incoming.reply_to,
    )
    address = _address(identity)
    locale = detect_locale(incoming.text)
    if not incoming.text.strip():
        return address, TEXT["text_only"][locale]
    user = await session.get(User, identity.user_id) if identity.user_id else None
    word = incoming.text.strip().casefold().strip(" .!。！")
    if word in LINK_WORDS:
        if user is not None:
            return address, TEXT["already_linked"][locale].format(name=user.name)
        return address, await invitation(session, identity, locale)
    if word in UNLINK_WORDS:
        if user is None:
            return address, TEXT["not_linked"][locale]
        await linking.unlink(session, identity)
        return address, TEXT["unlinked"][locale]
    return address, await converse(session, identity, user, incoming, locale)


_seen: OrderedDict[str, None] = OrderedDict()
_locks: WeakValueDictionary[tuple, asyncio.Lock] = WeakValueDictionary()


def _first_delivery(incoming: Incoming) -> bool:
    """Both platforms redeliver a webhook they think was missed; answer each message once."""
    key = f"{incoming.platform}:{incoming.message_id}"
    if key in _seen:
        return False
    _seen[key] = None
    if len(_seen) > 5000:
        _seen.popitem(last=False)
    return True


async def handle(incoming: Incoming) -> None:
    """Answer one incoming message. Runs after the webhook has been acknowledged."""
    if not _first_delivery(incoming):
        return
    key = (incoming.platform, incoming.team_id, incoming.sender)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    # One turn at a time per sender, in the order the messages arrived.
    async with lock:
        address = incoming.reply_to or incoming.sender
        try:
            async with AsyncSessionLocal() as session:
                address, reply = await respond(session, incoming)
        except Exception:
            logger.exception("Could not answer a %s message", incoming.platform)
            reply = TEXT["error"][detect_locale(incoming.text)]
        await send(incoming.platform, address, reply)
