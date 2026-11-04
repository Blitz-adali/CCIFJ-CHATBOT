"""Read-only staff inbox; explicit server-configured account IDs grant access.

Email ownership is not verified by this demo's signup flow, so an email
allowlist would let an attacker self-register as staff. Never use it here.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlmodel import col, select

from concierge.auth.dependencies import CurrentUser, DatabaseSession
from concierge.config import get_settings
from concierge.db.relationaldb.models import StaffRequest, StaffRequestRecipient, StaffRequestStatus

router = APIRouter(prefix="/staff", tags=["staff"])


async def require_staff(user: CurrentUser):
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to access the staff inbox.")
    if user.user_id not in get_settings().staff_user_ids:
        raise HTTPException(status_code=403, detail="Staff access required.")
    return user


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
                   "recipients": sorted(recipients.get(row.request_id, []))} for row in visible],
        "next_offset": offset + limit if len(rows) > limit else None,
    }
