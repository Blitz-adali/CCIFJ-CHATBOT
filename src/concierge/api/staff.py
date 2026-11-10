"""Staff view API: read conversations, follow up requests, update contacts.

Off by default. Set ``CCIFJ_STAFF_KEY`` (12 characters or more) to turn it on;
every call must then send that value in the ``X-Staff-Key`` header. This is a
single shared key for the prototype: in production, put the staff view behind
CCIFJ's own staff login instead.
"""

import hmac
import os
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from concierge.agents import contacts as contacts_data
from concierge.agents import network
from concierge.auth.dependencies import DatabaseSession
from concierge.db.relationaldb import crud
from concierge.db.relationaldb.datetimes import japan_now

router = APIRouter(prefix="/staff", tags=["staff"])

MIN_KEY_LENGTH = 12


def staff_key() -> str:
    return os.getenv("CCIFJ_STAFF_KEY", "").strip()


def require_staff(x_staff_key: Annotated[str | None, Header()] = None) -> None:
    key = staff_key()
    if len(key) < MIN_KEY_LENGTH:
        # Not configured: the staff view does not exist.
        raise HTTPException(status_code=404, detail="Not found.")
    if not x_staff_key or not hmac.compare_digest(x_staff_key.encode(), key.encode()):
        raise HTTPException(status_code=401, detail="A valid staff key is required.")


Staff = Depends(require_staff)


def _utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat()


class StatusUpdate(BaseModel):
    status: Literal["new", "in_review", "closed"]


@router.get("/requests", dependencies=[Staff])
async def requests(session: DatabaseSession, status: Literal["new", "in_review", "closed"] | None = None):
    """Staff requests, introductions, and member follow-ups stored from the chat, newest first."""
    listed = await crud.list_staff_requests(session, status)
    return {"requests": [
        {
            "reference": request.reference, "type": request.request_type.value, "category": request.category,
            "subject": request.subject, "message": request.message,
            "sender_name": request.sender_name, "sender_email": request.sender_email,
            "recipients": recipients, "status": request.status.value,
            "created_at": _utc(request.created_at), "conversation_id": request.session_id,
        }
        for request, recipients in listed
    ]}


@router.post("/requests/{reference}/status", dependencies=[Staff])
async def set_status(reference: str, body: StatusUpdate, session: DatabaseSession):
    request = await crud.set_staff_request_status(session, reference, body.status)
    if request is None:
        raise HTTPException(status_code=404, detail="Request not found.")
    return {"reference": request.reference, "status": request.status.value}


@router.get("/conversations", dependencies=[Staff])
async def conversations(session: DatabaseSession, limit: Annotated[int, Query(ge=1, le=300)] = 100):
    """Recent conversations: signed-in users', and guests' that led to a staff request or are still open."""
    listed = await crud.list_conversations(session, limit)
    return {"conversations": [
        {**item, "started_at": _utc(item["started_at"]), "last_message_at": _utc(item["last_message_at"])}
        for item in listed
    ]}


@router.get("/conversations/{conversation_id}", dependencies=[Staff])
async def conversation(conversation_id: int, session: DatabaseSession):
    chat = await crud.get_chat_session(session, conversation_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    rows = await crud.list_session_messages(session, conversation_id, limit=500)
    return {"conversation_id": conversation_id, "messages": [
        {"role": row.role.value, "content": row.content, "created_at": _utc(row.created_at)}
        for row in rows if row.role.value in {"user", "assistant"}
    ]}


@router.get("/contacts", dependencies=[Staff])
async def get_contacts():
    """The contacts the concierge gives out, by topic."""
    return {"contacts": contacts_data.load_contacts(), "topics": list(contacts_data.TOPICS)}


@router.put("/contacts", dependencies=[Staff])
async def put_contacts(body: dict[str, Any]):
    """Replace the contacts. The next answer uses them; no restart is needed."""
    try:
        saved = contacts_data.save_contacts(body.get("contacts", body))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"contacts": saved}


@router.get("/engagement", dependencies=[Staff])
async def engagement():
    """Members who need a follow-up now, most urgent first, with the rule behind each (fictional demo data)."""
    today = japan_now().date()
    return {"as_of": today.isoformat(), "fictional": True, "members": [
        {
            "contact": record["contact"], "company": record["company"], "email": record["email"],
            "goal": record.get("goal"), "joined_on": record["joined_on"],
            "stage": stage.key, "stage_label": stage.label, "rule": stage.why, "action": stage.action,
        }
        for record, stage in network.members_to_follow_up(today)
    ]}
