from typing import Annotated

from fastapi import APIRouter, Query, Response

from concierge.auth.dependencies import CurrentUser, DatabaseSession
from concierge.db.relationaldb import crud
from .schemas import HistoryResponse, MessageResponse, SessionResponse
from .session_access import authorize_session, pending_turn, session_token

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(session: DatabaseSession, user: CurrentUser, response: Response):
    chat = await crud.create_chat_session(session, user.user_id if user else None)
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(session_id=session_token(chat.session_id))


@router.get("/{session_id}/messages", response_model=HistoryResponse)
async def messages(
    session_id: str, session: DatabaseSession, user: CurrentUser, response: Response,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
):
    chat = await authorize_session(session_id, user, session)
    rows = list(await crud.list_session_messages(
        session, chat.session_id, after_id=after_id, limit=limit + 1
    ))
    pending = pending_turn(await crud.get_latest_message(session, chat.session_id))
    response.headers["Cache-Control"] = "no-store"
    return HistoryResponse(
        session_id=session_id,
        messages=[MessageResponse.model_validate(row) for row in rows[:limit]],
        next_after_id=rows[limit - 1].message_id if len(rows) > limit else None,
        pending_approvals=pending["approvals"] if pending else [],
    )
