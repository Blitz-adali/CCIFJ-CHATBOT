from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from concierge.auth.dependencies import CurrentUser, DatabaseSession
from concierge.db.relationaldb import crud
from .schemas import HistoryResponse, MessageResponse, ReconcileRequest, SessionResponse
from .session_access import authorize_session, pending_turn, session_token, session_turn, turn_status, normalize_approvals

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
    latest = await crud.get_latest_message(session, chat.session_id)
    pending = pending_turn(latest)
    response.headers["Cache-Control"] = "no-store"
    return HistoryResponse(
        session_id=session_id,
        messages=[MessageResponse.model_validate({**row.model_dump(),
            "response": next((item["response"] for item in row.tool_calls or [] if item.get("type") == "api_response"), None)
        }) for row in rows[:limit]],
        next_after_id=rows[limit - 1].message_id if len(rows) > limit else None,
        pending_approvals=normalize_approvals(pending["approvals"]) if pending else [],
        turn_status=turn_status(latest, chat.session_id),
        last_request_id=next((item["response"].get("request_id") for item in latest.tool_calls or [] if item.get("type") == "api_response"), None) if latest else None,
    )


@router.post("/{session_id}/claim", response_model=SessionResponse)
async def claim(session_id: str, session: DatabaseSession, user: CurrentUser, response: Response):
    if user is None:
        raise HTTPException(status_code=401, detail="Log in to claim this conversation.")
    chat = await authorize_session(session_id, user, session)
    async with session_turn(chat.session_id):
        await session.refresh(chat)
        await authorize_session(session_id, user, session)
        if chat.user_id is None:
            try:
                await crud.claim_chat_session(session, chat.session_id, user.user_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="Session not found.") from exc
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(session_id=session_id)


@router.post("/{session_id}/reconcile", response_model=SessionResponse)
async def reconcile(session_id: str, body: ReconcileRequest, session: DatabaseSession, user: CurrentUser, response: Response):
    chat = await authorize_session(session_id, user, session)
    # Do not wait for a running write and imply it was already checked.
    latest = await crud.get_latest_message(session, chat.session_id)
    if turn_status(latest, chat.session_id) == "processing":
        raise HTTPException(status_code=409, detail="This request is still running.")
    async with session_turn(chat.session_id):
        await session.refresh(chat)
        await authorize_session(session_id, user, session)
        latest = await crud.get_latest_message(session, chat.session_id)
        if latest:
            metadata = [item for item in latest.tool_calls or [] if item.get("type") != "api_turn_state"]
            await crud.update_message_metadata(session, latest, metadata or None)
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(session_id=session_id)
