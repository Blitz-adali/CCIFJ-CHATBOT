import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from concierge.auth.dependencies import CurrentUser, DatabaseSession
from concierge.config import get_settings
from concierge.db.relationaldb import crud
from concierge.db.relationaldb.models import MessageRole
from .agent_backend import ApprovalStateUnavailable, get_agent_backend
from .schemas import ApprovalRequest, ChatRequest, ChatResponse
from .session_access import authorize_session, pending_turn, session_token, session_turn

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


def agent_backend():
    """Lazy model initialization; expose a dependency for isolated API checks."""
    try:
        return get_agent_backend()
    except Exception as exc:
        logger.exception("Agent initialization failed")
        raise HTTPException(status_code=503, detail="The agent is unavailable; check server model configuration.") from exc


Backend = Annotated[object, Depends(agent_backend)]


async def save_reply(session, chat, token, result, thread_id):
    body = ChatResponse(session_id=token, **result)
    metadata = [{
        "type": "pending_approval", "thread_id": thread_id,
        "approvals": body.pending_approvals,
    }] if body.pending_approvals else None
    await crud.add_message(session, chat.session_id, MessageRole.assistant, body.reply, metadata)
    return body


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest, session: DatabaseSession, user: CurrentUser,
    backend: Backend, response: Response,
):
    token = body.session_id
    if token:
        conversation = await authorize_session(token, user, session)
    else:
        conversation = await crud.create_chat_session(session, user.user_id if user else None)
        token = session_token(conversation.session_id)
    async with session_turn(conversation.session_id):
        # Re-read ownership after waiting for another turn or a guest claim.
        await session.refresh(conversation)
        conversation = await authorize_session(token, user, session)
        latest = await crud.get_latest_message(session, conversation.session_id)
        if pending_turn(latest):
            raise HTTPException(status_code=409, detail="Approve or reject the pending action before sending another message.")
        if user is not None and conversation.user_id is None:
            try:
                conversation = await crud.claim_chat_session(session, conversation.session_id, user.user_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="Session not found.") from exc
        message = await crud.add_message(session, conversation.session_id, MessageRole.user, body.message)
        history = await crud.get_history(session, conversation.session_id, limit=100)
        thread_id = f"api:session:{conversation.session_id}:turn:{message.message_id}"
        try:
            async with asyncio.timeout(get_settings().agent_timeout_seconds):
                result = await backend.chat(history, conversation.user_id, thread_id)
        except Exception as exc:
            logger.exception("Agent turn failed for session %s", conversation.session_id)
            await crud.add_message(
                session, conversation.session_id, MessageRole.assistant,
                "The request could not be completed. Check any bookings before retrying.",
            )
            raise HTTPException(status_code=502, detail={
                "message": "The agent could not complete this request. Do not automatically retry writes.",
                "session_id": token,
            }) from exc
        response.headers["Cache-Control"] = "no-store"
        return await save_reply(session, conversation, token, result, thread_id)


@router.post("/sessions/{session_id}/approvals", response_model=ChatResponse)
async def approve(
    session_id: str, body: ApprovalRequest, session: DatabaseSession,
    user: CurrentUser, backend: Backend, response: Response,
):
    conversation = await authorize_session(session_id, user, session)
    async with session_turn(conversation.session_id):
        await session.refresh(conversation)
        await authorize_session(session_id, user, session)
        latest = await crud.get_latest_message(session, conversation.session_id)
        pending = pending_turn(latest)
        if not pending or not any(
            item.get("interrupt_id") == body.interrupt_id for item in pending["approvals"]
        ):
            raise HTTPException(status_code=409, detail="No matching pending approval.")
        try:
            async with asyncio.timeout(get_settings().agent_timeout_seconds):
                result = await backend.resume(pending["thread_id"], body.interrupt_id, body.decision)
        except ApprovalStateUnavailable as exc:
            raise HTTPException(status_code=409, detail="Approval state is unavailable after a restart or on another worker. No action was replayed.") from exc
        except Exception as exc:
            logger.exception("Agent approval failed for session %s", conversation.session_id)
            raise HTTPException(status_code=502, detail="Approval could not be completed. Check the booking before retrying.") from exc
        # Persist the new response before retiring the previous pending marker.
        result = await save_reply(session, conversation, session_id, result, pending["thread_id"])
        await crud.update_message_metadata(session, latest, None)
        response.headers["Cache-Control"] = "no-store"
        return result
