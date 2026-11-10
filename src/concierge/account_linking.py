"""Link a WhatsApp or Slack sender to their website account.

The chat channel only knows a phone number or a Slack user ID. To act for an account, the
sender opens a one-time link, logs in on the website as usual, and confirms. Passwords are
never typed into the chat.

The token is random, valid for a few minutes, stored only as a hash, and deleted when used.
It lives in its own table and is never accepted as a login: all it can do is attach one
chat identity to the account that is already signed in on the website.
"""

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import delete, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.config import get_settings
from concierge.db.relationaldb.models import ChannelIdentity, LinkToken, utcnow

PLATFORM_NAMES = {"whatsapp": "WhatsApp", "slack": "Slack"}


class LinkError(ValueError):
    """The link cannot be used; `code` is invalid_token or already_linked."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def account_hint(identity: ChannelIdentity) -> str:
    """Enough for the owner to recognise their chat account, without showing all of it."""
    return "•••• " + identity.platform_user_id[-4:]


async def get_or_create_identity(
    session: AsyncSession, platform: str, platform_user_id: str, team_id: str = "", reply_to: str | None = None,
) -> ChannelIdentity:
    query = select(ChannelIdentity).where(
        ChannelIdentity.platform == platform, ChannelIdentity.team_id == team_id,
        ChannelIdentity.platform_user_id == platform_user_id,
    )
    identity = (await session.exec(query)).first()
    if identity is None:
        identity = ChannelIdentity(platform=platform, platform_user_id=platform_user_id, team_id=team_id, reply_to=reply_to)
        session.add(identity)
    elif reply_to and identity.reply_to != reply_to:
        identity.reply_to = reply_to
        session.add(identity)
    await session.commit()
    return identity


async def create_link_token(session: AsyncSession, identity: ChannelIdentity) -> str:
    """Return a new token for this identity; any earlier link it was sent stops working."""
    now = utcnow()
    await session.exec(delete(LinkToken).where(
        (LinkToken.identity_id == identity.identity_id) | (LinkToken.expires_at <= now)
    ))
    token = secrets.token_urlsafe(32)
    session.add(LinkToken(
        token_hash=_hash(token), identity_id=identity.identity_id,
        expires_at=now + timedelta(seconds=get_settings().link_token_seconds),
    ))
    await session.commit()
    return token


def link_url(token: str) -> str:
    return f"{get_settings().public_url}/?link={token}"


async def _read(session: AsyncSession, token: str) -> tuple[LinkToken, ChannelIdentity]:
    row = await session.get(LinkToken, _hash(token))
    if row is None or row.expires_at <= utcnow():
        raise LinkError("invalid_token", "This link has expired or was already used. Ask the chatbot for a new one.")
    return row, await session.get(ChannelIdentity, row.identity_id)


async def preview_link(session: AsyncSession, token: str) -> dict:
    """What the link would connect, shown to the signed-in user before they confirm."""
    row, identity = await _read(session, token)
    return {
        "platform": identity.platform, "account_hint": account_hint(identity),
        "expires_in": max(0, int((row.expires_at - utcnow()).total_seconds())),
    }


async def complete_link(session: AsyncSession, token: str, user_id: int) -> ChannelIdentity:
    """Use the token once and attach its chat identity to ``user_id``."""
    row, identity = await _read(session, token)
    # The delete is the single-use check: of two simultaneous requests only one removes the row.
    used = await session.exec(delete(LinkToken).where(
        LinkToken.token_hash == row.token_hash, LinkToken.expires_at > utcnow()
    ))
    if used.rowcount != 1:
        await session.rollback()
        raise LinkError("invalid_token", "This link has expired or was already used. Ask the chatbot for a new one.")
    if identity.user_id is not None and identity.user_id != user_id:
        await session.commit()  # the token is spent either way
        raise LinkError("already_linked", "This chat account is linked to another CCIFJ account. Send \"unlink\" in the chat first.")
    # One chat account per platform (and Slack workspace) for each website account.
    await session.exec(update(ChannelIdentity).where(
        ChannelIdentity.user_id == user_id, ChannelIdentity.platform == identity.platform,
        ChannelIdentity.team_id == identity.team_id, ChannelIdentity.identity_id != identity.identity_id,
    ).values(user_id=None, session_id=None, linked_at=None))
    identity.user_id = user_id
    identity.linked_at = utcnow()
    session.add(identity)
    await session.commit()
    return identity


async def unlink(session: AsyncSession, identity: ChannelIdentity) -> None:
    """Detach the identity; its next message starts a new guest conversation."""
    await session.exec(delete(LinkToken).where(LinkToken.identity_id == identity.identity_id))
    identity.user_id = None
    identity.session_id = None
    identity.linked_at = None
    session.add(identity)
    await session.commit()


async def list_links(session: AsyncSession, user_id: int) -> list[ChannelIdentity]:
    return list((await session.exec(
        select(ChannelIdentity).where(ChannelIdentity.user_id == user_id).order_by(col(ChannelIdentity.linked_at))
    )).all())
