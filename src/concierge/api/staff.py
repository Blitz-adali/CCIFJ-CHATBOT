"""Staff inbox. Staff are accounts with role "staff" (set in the admin dashboard) or the
bootstrap IDs in CCIFJ_STAFF_USER_IDS; email ownership is not verified by the demo signup,
so staff access is never granted from an email address.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import col, select

from concierge.auth.access import require_staff
from concierge.auth.dependencies import DatabaseSession
from concierge.db.relationaldb.models import (
    ChatSession, Message, MessageRole, StaffRequest, StaffRequestRecipient, StaffRequestStatus, User,
)

router = APIRouter(prefix="/staff", tags=["staff"])


class StatusUpdate(BaseModel):
    status: StaffRequestStatus


@router.patch("/requests/{reference}", dependencies=[Depends(require_staff)])
async def update_request_status(reference: str, body: StatusUpdate, session: DatabaseSession):
    request = (await session.exec(select(StaffRequest).where(StaffRequest.reference == reference))).first()
    if request is None:
        raise HTTPException(status_code=404, detail="Request not found.")
    request.status = body.status
    session.add(request)
    await session.commit()
    return {"reference": reference, "status": request.status}


@router.get("/requests", dependencies=[Depends(require_staff)])
async def requests(
    session: DatabaseSession, response: Response,
    status: StaffRequestStatus | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    query = select(StaffRequest)
    if status is not None:
        query = query.where(StaffRequest.status == status)
    rows = list((await session.exec(query.order_by(col(StaffRequest.created_at).desc(), col(StaffRequest.request_id).desc()).offset(offset).limit(limit + 1))).all())
    visible = rows[:limit]
    recipients = {}
    if visible:
        for recipient in (await session.exec(select(StaffRequestRecipient).where(col(StaffRequestRecipient.request_id).in_([row.request_id for row in visible])))).all():
            recipients.setdefault(recipient.request_id, []).append(recipient.email)
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [{"reference": row.reference, "request_type": row.request_type,
                   "category": row.category, "subject": row.subject, "message": row.message,
                   "sender_name": row.sender_name, "sender_email": row.sender_email,
                   "status": row.status, "created_at": row.created_at.isoformat() + "Z",
                   "recipients": sorted(recipients.get(row.request_id, [])),
                   # The chat this request came from; None for requests stored before chats were kept.
                   "conversation_id": row.session_id} for row in visible],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


# ---------- Conversations ----------
# Read-only. Staff see what the visitor and the agent wrote, never the agent's internal
# tool messages. Signed-in conversations are kept; a guest conversation is kept only when
# it produced a request the visitor approved (see crud._delete_guest_sessions).

VISIBLE_ROLES = (MessageRole.user, MessageRole.assistant)
PREVIEW_CHARS = 160


def _utc(value) -> str:
    return value.isoformat() + "Z"


def _sources(message: Message) -> list[dict]:
    """The pages and documents the agent cited in this answer."""
    for item in message.tool_calls or []:
        if item.get("type") == "api_response":
            return [{"title": source.get("title") or source.get("url") or "", "url": source.get("url") or ""}
                    for source in (item.get("response") or {}).get("sources") or [] if isinstance(source, dict)]
    return []


async def _requests_by_session(session, session_ids: list[int]) -> dict[int, list[dict]]:
    linked: dict[int, list[dict]] = {}
    if session_ids:
        rows = (await session.exec(select(StaffRequest).where(col(StaffRequest.session_id).in_(session_ids))
                                   .order_by(col(StaffRequest.request_id)))).all()
        for row in rows:
            linked.setdefault(row.session_id, []).append(
                {"reference": row.reference, "subject": row.subject, "status": row.status})
    return linked


@router.get("/conversations", dependencies=[Depends(require_staff)])
async def conversations(
    session: DatabaseSession, response: Response,
    with_request: bool = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    """Saved conversations, most recent activity first. `with_request` keeps those that led to a request."""
    stats = (select(Message.session_id, func.count().label("messages"), func.max(Message.created_at).label("last_at"),
                    func.min(Message.message_id).label("first_id"))
             .where(col(Message.role).in_(VISIBLE_ROLES)).group_by(Message.session_id).subquery())
    query = select(ChatSession, stats.c.messages, stats.c.last_at, stats.c.first_id).join(
        stats, stats.c.session_id == ChatSession.session_id)
    if with_request:
        query = query.where(col(ChatSession.session_id).in_(
            select(StaffRequest.session_id).where(col(StaffRequest.session_id).is_not(None))))
    rows = list((await session.exec(
        query.order_by(stats.c.last_at.desc(), col(ChatSession.session_id).desc()).offset(offset).limit(limit + 1))).all())
    visible = rows[:limit]
    ids = [chat.session_id for chat, *_ in visible]
    users = {user.user_id: user for user in (await session.exec(
        select(User).where(col(User.user_id).in_([chat.user_id for chat, *_ in visible if chat.user_id])))).all()}
    first = {message.message_id: message.content for message in (await session.exec(
        select(Message).where(col(Message.message_id).in_([first_id for *_, first_id in visible])))).all()}
    linked = await _requests_by_session(session, ids)
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [{
            "conversation_id": chat.session_id,
            "visitor": _visitor(users.get(chat.user_id)),
            "started_at": _utc(chat.created_at), "last_at": _utc(last_at), "message_count": count,
            "preview": " ".join((first.get(first_id) or "").split())[:PREVIEW_CHARS],
            "requests": linked.get(chat.session_id, []),
        } for chat, count, last_at, first_id in visible],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


def _visitor(user: User | None) -> dict:
    return {"name": user.name, "email": user.email, "guest": False} if user else {"name": "", "email": "", "guest": True}


@router.get("/conversations/{conversation_id}", dependencies=[Depends(require_staff)])
async def conversation(conversation_id: int, session: DatabaseSession, response: Response):
    """One conversation as the visitor saw it: their messages, the agent's answers and cited sources."""
    chat = await session.get(ChatSession, conversation_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Conversation not found. Guest chats without a request are not kept.")
    messages = (await session.exec(
        select(Message).where(Message.session_id == conversation_id, col(Message.role).in_(VISIBLE_ROLES))
        .order_by(col(Message.message_id)))).all()
    user = await session.get(User, chat.user_id) if chat.user_id else None
    response.headers["Cache-Control"] = "no-store"
    return {
        "conversation_id": chat.session_id, "visitor": _visitor(user), "started_at": _utc(chat.created_at),
        "requests": (await _requests_by_session(session, [chat.session_id])).get(chat.session_id, []),
        "messages": [{"role": message.role, "content": message.content, "created_at": _utc(message.created_at),
                      "sources": _sources(message) if message.role == MessageRole.assistant else []}
                     for message in messages],
    }
