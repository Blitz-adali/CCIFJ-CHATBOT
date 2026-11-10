"""Staff inbox. Staff are accounts with role "staff" (set in the admin dashboard) or the
bootstrap IDs in CCIFJ_STAFF_USER_IDS; email ownership is not verified by the demo signup,
so staff access is never granted from an email address.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlmodel import col, select

from concierge.auth.access import require_staff
from concierge.auth.dependencies import DatabaseSession
from concierge.db.relationaldb.models import StaffRequest, StaffRequestRecipient, StaffRequestStatus

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
                   "recipients": sorted(recipients.get(row.request_id, []))} for row in visible],
        "next_offset": offset + limit if len(rows) > limit else None,
    }
