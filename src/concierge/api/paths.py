"""Guided conversation paths: allow-listed event and space journeys.

Steps are defined in ``data/conversation_paths.json``. Most steps run a fixed
read-only lookup without the language model; ``spaces.submit`` hands a
structured booking request to the agent, which still requires approval.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from concierge.db.relationaldb import crud

logger = logging.getLogger(__name__)

_PATHS_FILE = Path(__file__).resolve().parents[1] / "data" / "conversation_paths.json"


@lru_cache
def _config() -> dict:
    return json.loads(_PATHS_FILE.read_text(encoding="utf-8"))


def event_paths() -> dict:
    return _config()["events"]


def space_paths() -> dict:
    return _config()["spaces"]


def all_paths() -> dict:
    return {**event_paths(), **space_paths()}


def starter_paths(locale: str) -> list[dict]:
    return [
        {"id": step_id, "label": step["labels"][locale]}
        for step_id, step in all_paths().items()
        if step.get("starter")
    ]


def path_actions_for(user_text: str, reply: str, suggested: list[str], locale: str) -> list[dict]:
    """Offer guided journeys related to an agent answer."""
    related = " ".join([user_text, reply, *suggested]).casefold()
    actions = []
    if any(term in related for term in ("room", "space", "meeting", "salle", "会場", "部屋")):
        actions.append({"id": "spaces.start", "label": space_paths()["spaces.start"]["labels"][locale]})
    if any(term in related for term in ("event", "ticket", "événement", "billet", "イベント", "チケット")):
        actions.append({"id": "events.upcoming", "label": event_paths()["events.upcoming"]["labels"][locale]})
    return actions


def _guided(reply: str, intent: str, **extra) -> dict:
    return {"mode": "guided_path", "reply": reply, "intent": {"code": intent}, **extra}


async def run_path_step(body, user, session) -> dict | None:
    """Run a selected path step. Returns None when the agent must handle it."""
    step = body.path_step_id
    if not step:
        return None
    definitions = all_paths()
    definition = definitions.get(step)
    if definition is None:
        raise HTTPException(status_code=400, detail="Unknown conversation path step.")
    locale = body.locale
    labels = {key: item["labels"][locale] for key, item in definitions.items()}
    actions: list[dict] = []

    if definition["tool"] == "list_available_events":
        from concierge.agents.tools import list_available_events, search_vectordb

        output = await list_available_events.ainvoke({})
        if not isinstance(output, str):
            output = str(output)
        reply = _format_event_list_markdown(output, locale)
        for line in output.splitlines():
            match = re.match(r"\[ID\s+([^\]]+)\]\s+(.+?)\s+\|", line)
            if match:
                next_step = definition["next_step"]
                actions.append({"id": next_step, "event_id": match.group(1), "label": f"{labels[next_step]}: {match.group(2)}"})
        if output.strip() == "No matching events were found.":
            try:
                knowledge = await search_vectordb.ainvoke({"query": "CCIFJ upcoming events event calendar conferences seminars networking events"})
                if isinstance(knowledge, str) and knowledge.strip():
                    note = {
                        "en": "The live events database has no matching records. The related information below comes from CCIFJ website/knowledge-base content and may not reflect the current calendar.",
                        "fr": "La base de données des événements en direct ne contient aucun résultat correspondant. Les informations ci-dessous proviennent du site ou de la base de connaissances de la CCIFJ et peuvent ne pas refléter le calendrier actuel.",
                        "ja": "イベントのライブデータベースに一致する情報はありません。以下はCCIFJのウェブサイトまたはナレッジベースからの関連情報で、最新の予定とは異なる場合があります。",
                    }[locale]
                    reply = f"## Related event information\n\n{note}\n\n{knowledge.strip()}"
            except Exception:
                logger.exception("Vector knowledge fallback failed for the guided events path")
    elif definition["tool"] == "get_event_details" and body.path_event_id:
        from concierge.agents.tools import get_event_details

        output = await get_event_details.ainvoke({"event_id": body.path_event_id})
        reply = _format_event_details_markdown(str(output))
        next_step = definition["next_step"]
        actions = [{"id": next_step, "event_id": body.path_event_id, "label": labels[next_step]}]
    elif step == "events.booking_info":
        reply = {
            "en": "## Booking this event\n\n1. Sign in to your CCIFJ account.\n2. Ask to book this event and tell me how many people will attend.\n3. Provide each attendee’s name and email address.\n4. Review the booking card and approve it to save the reservation.\n\nNo payment is taken through this reservation step.",
            "fr": "## Réserver cet événement\n\n1. Connectez-vous à votre compte CCIFJ.\n2. Demandez la réservation de cet événement et indiquez le nombre de participants.\n3. Fournissez le nom et l’adresse e-mail de chaque participant.\n4. Vérifiez la carte de réservation et confirmez-la pour enregistrer la réservation.\n\nAucun paiement n’est effectué à cette étape.",
            "ja": "## イベントを予約する\n\n1. CCIFJアカウントにログインします。\n2. このイベントの予約を依頼し、参加人数を伝えます。\n3. 各参加者の氏名とメールアドレスを入力します。\n4. 予約カードを確認して承認すると、予約が保存されます。\n\nこの予約手順で支払いは発生しません。",
        }[locale]
    elif step == "spaces.start":
        reply = {
            "en": "## Check space availability\n\nEnter the requested start and end in this format:\n\n`YYYY-MM-DD HH:MM to YYYY-MM-DD HH:MM`\n\nFor example: `2026-11-12 09:00 to 2026-11-12 12:00`.",
            "fr": "## Vérifier la disponibilité d’un espace\n\nSaisissez les dates de début et de fin au format suivant :\n\n`YYYY-MM-DD HH:MM to YYYY-MM-DD HH:MM`\n\nExemple : `2026-11-12 09:00 to 2026-11-12 12:00`.",
            "ja": "## 会場の空き状況を確認\n\n開始日時と終了日時を次の形式で入力してください。\n\n`YYYY-MM-DD HH:MM to YYYY-MM-DD HH:MM`\n\n例：`2026-11-12 09:00 to 2026-11-12 12:00`",
        }[locale]
        return _guided(reply, "space_booking", active_path_step="spaces.availability")
    elif step == "spaces.availability":
        interval = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+(?:to|until|-)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", body.message, re.IGNORECASE)
        if not interval:
            return _guided("Please enter both dates and times in this format: `YYYY-MM-DD HH:MM to YYYY-MM-DD HH:MM`.",
                           "space_booking", active_path_step="spaces.availability")
        try:
            start_at, end_at = (datetime.fromisoformat(value) for value in interval.groups())
        except ValueError:
            start_at = end_at = None
        if start_at is None or end_at is None or end_at <= start_at:
            return _guided("That date range is invalid. Please provide an end time after the start time.",
                           "space_booking", active_path_step="spaces.availability")
        spaces = await crud.list_spaces(session)
        slot_text = f"{start_at:%Y-%m-%d %H:%M} to {end_at:%Y-%m-%d %H:%M}"
        for space in spaces:
            conflicts = await crud.list_space_reservations(session, int(space.space_id), start_at, end_at)
            if not conflicts:
                actions.append({"id": "spaces.booking_details", "label": f"{labels['spaces.booking_details']}: {space.name}",
                    "data": {"space_id": int(space.space_id), "space_name": space.name,
                             "start_date": start_at.strftime("%Y-%m-%d %H:%M"), "end_date": end_at.strftime("%Y-%m-%d %H:%M")}})
        copy = {
            "en": ("## Space availability", "**Requested time:**", "These spaces are available for the requested time. Choose one to continue:", "No spaces are currently listed for reservation.", "No spaces are available for that time. Try another date or time."),
            "fr": ("## Disponibilité des espaces", "**Horaire demandé :**", "Ces espaces sont disponibles. Choisissez-en un pour continuer :", "Aucun espace n’est actuellement répertorié pour la réservation.", "Aucun espace n’est disponible à cet horaire. Essayez une autre date ou heure."),
            "ja": ("## 会場の空き状況", "**ご希望の日時：**", "次の会場が利用できます。選択して続行してください。", "現在予約可能な会場は登録されていません。", "この時間帯に利用できる会場はありません。別の日時をお試しください。"),
        }[locale]
        reply = f"{copy[0]}\n\n{copy[1]} {slot_text}\n\n"
        if actions:
            reply += copy[2] + "\n\n" + "\n".join(f"- {action['label']}" for action in actions)
        elif not spaces:
            reply += copy[3]
        else:
            reply += copy[4]
    elif step == "spaces.booking_details":
        context = _validated_space_context(body.path_context, "The selected space or time is missing. Please check availability again.")
        try:
            start_at = datetime.fromisoformat(context["start_date"])
            end_at = datetime.fromisoformat(context["end_date"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="The selected date range is invalid. Please check availability again.") from exc
        selected = await crud.get_space(session, context["space_id"])
        available = (
            selected is not None and selected.name == context["space_name"] and end_at > start_at
            and await crud.is_space_available(session, context["space_id"], start_at, end_at)
        )
        if not available:
            raise HTTPException(status_code=409, detail="That space is no longer available for the selected time. Please check availability again.")
        name, start, end = context["space_name"], context["start_date"], context["end_date"]
        reply = {
            "en": f"## Prepare your booking\n\n**Space:** {name}\n**Time:** {start} to {end}\n\nTell me the meeting or event title, expected number of attendees, and whether it is public or private. I’ll prepare a booking for your review.",
            "fr": f"## Préparer votre réservation\n\n**Espace :** {name}\n**Horaire :** {start} à {end}\n\nIndiquez le titre de la réunion ou de l’événement, le nombre de participants et s’il est public ou privé. Je préparerai la réservation pour vérification.",
            "ja": f"## 予約内容を準備\n\n**会場：** {name}\n**日時：** {start} ～ {end}\n\n会議・イベント名、参加予定人数、公開・非公開を入力してください。確認用の予約内容を作成します。",
        }[locale]
        return _guided(reply, "space_booking", active_path_step="spaces.submit", path_context=context)
    elif step == "spaces.submit" and user is None:
        reply = {
            "en": "Please sign in before creating a space reservation. After signing in, start a new conversation and choose the space booking path again.",
            "fr": "Connectez-vous avant de créer une réservation d’espace. Après connexion, démarrez une nouvelle conversation et choisissez à nouveau le parcours de réservation.",
            "ja": "会場を予約するにはログインしてください。ログイン後、新しい会話を開始して予約手順を選び直してください。",
        }[locale]
        return _guided(reply, "space_booking")
    elif step == "spaces.submit":
        return None
    else:
        raise HTTPException(status_code=400, detail="This conversation path step needs an event ID.")
    return _guided(reply, "space_booking" if step.startswith("spaces.") else "events", path_actions=actions)


def _validated_space_context(context: dict, error: str) -> dict:
    space_id = context.get("space_id")
    values = [context.get(key) for key in ("space_name", "start_date", "end_date")]
    if not isinstance(space_id, int) or isinstance(space_id, bool) or not all(isinstance(value, str) for value in values):
        raise HTTPException(status_code=400, detail=error)
    return {"space_id": space_id, "space_name": values[0], "start_date": values[1], "end_date": values[2]}


def space_booking_agent_message(body) -> str:
    """Turn the guided space-booking details into an explicit agent request."""
    context = _validated_space_context(body.path_context, "Booking details are missing. Please restart the space booking path.")
    title = body.message.strip()[:255]
    attendees_match = re.search(r"\b(\d{1,4})\s+(?:people|persons|attendees|participants)\b", title, re.IGNORECASE)
    attendees = int(attendees_match.group(1)) if attendees_match else None
    public = bool(re.search(r"\b(public|open to the public|公開)\b", title, re.IGNORECASE))
    return (
        "The user selected the guided CCIFJ space booking path and supplied the following booking details. "
        "Use the book_space tool with these exact values, then follow its normal human approval process. "
        "Do not state that the reservation is saved before approval.\n"
        f"space_name={context['space_name']!r}; start_date={context['start_date']!r}; end_date={context['end_date']!r}; "
        f"title={title!r}; expected_attendees={attendees!r}; is_public={public!r}.\n"
        "If the supplied title does not identify the purpose, ask a brief clarification instead of booking."
    )


def _format_event_list_markdown(text: str, locale: str) -> str:
    headings = {"en": "## Upcoming CCIFJ events", "fr": "## Événements CCIFJ à venir", "ja": "## 今後のCCIFJイベント"}
    no_events = {"en": "No upcoming events matched.", "fr": "Aucun événement à venir ne correspond à ces critères.", "ja": "条件に一致する今後のイベントはありません。"}
    items = []
    for line in text.splitlines():
        match = re.match(r"\[ID\s+([^\]]+)\]\s+(.+?)\s+\|\s+(.+?)\s+\|\s+(.+?)\s+\|\s+Category:\s*(.+?)\s+\|\s+Price:\s*(.+)$", line)
        if match:
            event_id, title, date, location, category, price = match.groups()
            items.append(f"### {title}\n\n- **Date:** {date}\n- **Location:** {location}\n- **Category:** {category}\n- **Price:** {price}\n- **Event ID:** {event_id}")
        elif line.startswith("Showing the first "):
            items.append(f"_{line}_")
    return headings[locale] + "\n\n" + ("\n\n".join(items) if items else no_events[locale])


def _format_event_details_markdown(text: str) -> str:
    if text.startswith("No event was found") or text.startswith("Invalid event"):
        return f"## Event details\n\n{text}"
    lines = text.splitlines()
    title = lines[0].strip() if lines else "Event details"
    fields = []
    description = ""
    speakers: list[str] = []
    in_speakers = False
    for line in lines[1:]:
        if line == "Speakers:":
            in_speakers = True
        elif in_speakers and line.startswith("- "):
            speakers.append(line[2:].strip())
        elif ": " in line:
            key, value = line.split(": ", 1)
            if key == "Description":
                description = value
            else:
                fields.append(f"- **{key}:** {value}")
    reply = f"## {title}\n\n" + "\n".join(fields)
    if description:
        reply += f"\n\n### About this event\n\n{description}"
    if speakers:
        reply += "\n\n### Speakers\n\n" + "\n".join(f"- {speaker}" for speaker in speakers)
    return reply
