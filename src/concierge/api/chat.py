import asyncio
import logging
from contextlib import aclosing
from typing import Annotated

from anyio import CancelScope
from fastapi import APIRouter, Depends, HTTPException, Response, WebSocket, WebSocketDisconnect

from concierge.auth.dependencies import CurrentUser, DatabaseSession
from concierge.config import get_settings
from concierge.db.relationaldb import crud
from concierge.db.relationaldb.models import MessageRole
from .agent_backend import ApprovalStateUnavailable, get_agent_backend, response_locale, response_events
from .schemas import ApprovalRequest, ChatRequest, ChatResponse
from .session_access import authorize_session, pending_turn, session_token, session_turn, active_turns, turn_status, normalize_approvals

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


async def agent_backend(session: DatabaseSession):
    """Lazy model initialization; expose a dependency for isolated API checks."""
    # Authentication may have opened a read transaction before model loading.
    await session.commit()
    try:
        return await asyncio.to_thread(get_agent_backend)
    except Exception as exc:
        logger.exception("Agent initialization failed")
        raise HTTPException(status_code=503, detail={"message": "The agent is unavailable; check server model configuration.", "outcome_unknown": False, "safe_to_retry": True}) from exc


Backend = Annotated[object, Depends(agent_backend)]


def reply_metadata(body, thread_id):
    metadata = [{
        "type": "pending_approval", "thread_id": thread_id,
        "approvals": body.pending_approvals,
    }] if body.pending_approvals else []
    metadata.append({"type": "api_response", "response": body.model_dump(mode="json")})
    return metadata


async def save_reply(session, chat, token, result, thread_id):
    result = dict(result)
    result["pending_approvals"] = normalize_approvals(result.get("pending_approvals"))
    body = ChatResponse(session_id=token, **result)
    row = await crud.add_message(session, chat.session_id, MessageRole.assistant, body.reply, reply_metadata(body, thread_id))
    # add_message refreshes the row after its commit, opening another read transaction.
    await session.commit()
    return body, row


async def present_saved_reply(session, backend, body, row, thread_id, locale):
    """Presentation is optional; completed agent results are already durable."""
    if not hasattr(backend, "present_result"):
        return body
    locale_token = response_locale.set(locale)
    try:
        try:
            async with asyncio.timeout(30):
                result = await backend.present_result(body.model_dump(exclude={"session_id"}))
            rendered = ChatResponse(session_id=body.session_id, **result)
        except Exception:
            logger.warning("Answer presentation failed; keeping the saved agent result", exc_info=True)
            warning = {
                "en": "Language formatting is unavailable. The original answer is shown; no action was repeated.",
                "fr": "La mise en forme linguistique est indisponible. La réponse originale est affichée ; aucune action n’a été répétée.",
                "ja": "言語変換を利用できないため、元の回答を表示しています。操作は再実行されていません。",
            }[locale]
            rendered = body.model_copy(update={"warnings": [*body.warnings, warning]})
        # This acquires a connection only for the short result update. A failed
        # update cannot remove the raw reply committed before presentation.
        try:
            row.content = rendered.reply
            row.tool_calls = reply_metadata(rendered, thread_id)
            session.add(row)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Could not store presentation; the original result remains saved")
            return body
        return rendered
    finally:
        response_locale.reset(locale_token)


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
    # Release authorization reads before waiting on another turn.
    await session.commit()
    async with session_turn(conversation.session_id):
        # Re-read ownership after waiting for another turn or a guest claim.
        await session.refresh(conversation)
        conversation = await authorize_session(token, user, session)
        latest = await crud.get_latest_message(session, conversation.session_id)
        if turn_status(latest, conversation.session_id) == "unknown":
            raise HTTPException(status_code=409, detail={"message": "Check the previous request outcome before continuing.", "session_id": token, "outcome_unknown": True})
        if pending_turn(latest):
            raise HTTPException(status_code=409, detail="Approve or reject the pending action before sending another message.")
        if user is not None and conversation.user_id is None:
            try:
                conversation = await crud.claim_chat_session(session, conversation.session_id, user.user_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="Session not found.") from exc
        message = await crud.add_message(session, conversation.session_id, MessageRole.user, body.message, [{"type": "api_turn_state", "status": "processing"}])
        history = await crud.get_history(session, conversation.session_id, limit=100)
        thread_id = f"api:session:{conversation.session_id}:turn:{message.message_id}"
        # Snapshot the data needed by the agent and release its read transaction.
        user_id = conversation.user_id
        await session.commit()
        active_turns.add(conversation.session_id)
        locale_token = response_locale.set(body.locale)
        try:
            async with asyncio.timeout(get_settings().agent_timeout_seconds):
                result = await backend.chat(history, user_id, thread_id)
            result["request_id"] = body.request_id
            result, saved = await save_reply(session, conversation, token, result, thread_id)
        except asyncio.CancelledError:
            await crud.add_message(session, conversation.session_id, MessageRole.assistant,
                "The connection ended. Check the request outcome before continuing.",
                [{"type": "api_turn_state", "status": "unknown"}])
            raise
        except Exception as exc:
            logger.exception("Agent turn failed for session %s", conversation.session_id)
            await crud.add_message(
                session, conversation.session_id, MessageRole.assistant,
                "The request could not be completed. Check any bookings before retrying.",
                [{"type": "api_turn_state", "status": "unknown"}],
            )
            raise HTTPException(status_code=502, detail={
                "message": "The agent could not complete this request. Do not automatically retry writes.",
                "session_id": token, "outcome_unknown": True,
            }) from exc
        finally:
            active_turns.discard(conversation.session_id)
            response_locale.reset(locale_token)
        result = await present_saved_reply(session, backend, result, saved, thread_id, body.locale)
        response.headers["Cache-Control"] = "no-store"
        return result


@router.post("/sessions/{session_id}/approvals", response_model=ChatResponse)
async def approve(
    session_id: str, body: ApprovalRequest, session: DatabaseSession,
    user: CurrentUser, backend: Backend, response: Response,
):
    conversation = await authorize_session(session_id, user, session)
    # Release authorization reads before waiting on another turn.
    await session.commit()
    async with session_turn(conversation.session_id):
        await session.refresh(conversation)
        await authorize_session(session_id, user, session)
        latest = await crud.get_latest_message(session, conversation.session_id)
        pending = pending_turn(latest)
        if turn_status(latest, conversation.session_id) == "unknown":
            raise HTTPException(status_code=409, detail={"message": "Check the previous action outcome before continuing.", "session_id": session_id, "outcome_unknown": True})
        if not pending or not any(
            item.get("interrupt_id") == body.interrupt_id for item in pending["approvals"]
        ):
            raise HTTPException(status_code=409, detail="No matching pending approval.")
        original_metadata = list(latest.tool_calls or [])
        await crud.update_message_metadata(session, latest, original_metadata + [{"type": "api_turn_state", "status": "processing"}])
        active_turns.add(conversation.session_id)
        locale_token = response_locale.set(body.locale)
        try:
            async with asyncio.timeout(get_settings().agent_timeout_seconds):
                result = await backend.resume(pending["thread_id"], body.interrupt_id, body.decision)
            result["request_id"] = body.request_id
            result, saved = await save_reply(session, conversation, session_id, result, pending["thread_id"])
            await crud.update_message_metadata(session, latest, None)
        except ApprovalStateUnavailable as exc:
            await crud.update_message_metadata(session, latest, original_metadata)
            raise HTTPException(status_code=409, detail="Approval state is unavailable after a restart or on another worker. No action was replayed.") from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Agent approval failed for session %s", conversation.session_id)
            raise HTTPException(status_code=502, detail={"message": "Approval could not be completed. Check the booking before retrying.", "session_id": session_id, "outcome_unknown": True}) from exc
        finally:
            active_turns.discard(conversation.session_id)
            response_locale.reset(locale_token)
        result = await present_saved_reply(session, backend, result, saved, pending["thread_id"], body.locale)
        response.headers["Cache-Control"] = "no-store"
        return result


async def turn_events(body, session, user, backend):
    """One persisted turn for both live transports; never replay on failure."""
    events = asyncio.Queue()
    event_token = response_events.set(events)
    task = asyncio.create_task(chat(body, session, user, backend, Response()))
    response_events.reset(event_token)
    try:
        yield {"type": "progress", "message": "Processing your request…"}
        heartbeat = asyncio.get_running_loop().time()
        while not task.done():
            try:
                event = await asyncio.wait_for(events.get(), timeout=0.25)
                yield event
            except TimeoutError:
                if asyncio.get_running_loop().time() - heartbeat >= 10:
                    yield {"type": "progress", "message": "Still working on your request…"}
                    heartbeat = asyncio.get_running_loop().time()
        while not events.empty():
            yield events.get_nowait()
        result = await task
        yield {"type": "result", **result.model_dump(mode="json")}
    except HTTPException as exc:
        detail = exc.detail
        yield {
            "type": "error", "status": exc.status_code,
            "message": detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail),
            **(detail if isinstance(detail, dict) else {}),
        }
    except Exception:
        logger.exception("Live chat failed")
        yield {"type": "error", "status": 500, "message": "Chat failed. Check any bookings before retrying."}
    finally:
        # ASGI disconnects cancel the surrounding AnyIO scope. Finish task/SQL
        # cleanup before dependency teardown closes the shared database session.
        with CancelScope(shield=True):
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, session: DatabaseSession, user: CurrentUser, backend: Backend):
    import json
    from fastapi.responses import StreamingResponse

    async def frames():
        async for event in turn_events(body, session, user, backend):
            payload = dict(event)
            kind = payload.pop("type")
            yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(frames(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })




@router.websocket("/chat/ws")
async def chat_socket(websocket: WebSocket, session: DatabaseSession, backend: Backend):
    """Authenticate in the first frame, keeping bearer tokens out of URLs."""
    from pydantic import ValidationError
    from concierge.auth.security import read_token
    from concierge.db.relationaldb.models import User

    origin = websocket.headers.get("origin")
    allowed = get_settings().cors_origins
    if origin and origin not in allowed and origin != f"{websocket.url.scheme.replace('ws', 'http')}://{websocket.headers.get('host')}":
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        async with asyncio.timeout(15):
            credentials = await websocket.receive_json()
        token = credentials.get("access_token") if isinstance(credentials, dict) else None
        user = None
        if token is not None:
            try:
                user = await session.get(User, read_token(token, "access"))
                if user is None:
                    raise ValueError("Unknown account")
                # An authenticated idle socket must not hold a pooled connection.
                await session.commit()
            except (ValueError, TypeError):
                await websocket.send_json({"type": "error", "status": 401, "message": "Invalid or expired bearer token."})
                await websocket.close(code=1008)
                return
        await websocket.send_json({"type": "ready"})
        incoming = asyncio.Queue(maxsize=1)
        disconnected = asyncio.Event()

        async def receive_frames():
            try:
                while True:
                    payload = await websocket.receive_json()
                    incoming.put_nowait(payload)
            except WebSocketDisconnect:
                pass
            except (ValueError, asyncio.QueueFull):
                await websocket.close(code=1008)
            finally:
                disconnected.set()

        receiver = asyncio.create_task(receive_frames())
        closed = asyncio.create_task(disconnected.wait())

        async def while_connected(operation):
            task = asyncio.create_task(operation)
            try:
                await asyncio.wait({task, closed}, return_when=asyncio.FIRST_COMPLETED)
                if closed.done():
                    raise WebSocketDisconnect()
                return await task
            finally:
                with CancelScope(shield=True):
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        try:
            while True:
                payload = await while_connected(incoming.get())
                try:
                    body = ChatRequest.model_validate(payload)
                    if token is not None:
                        read_token(token, "access")
                except ValidationError:
                    await websocket.send_json({"type": "error", "status": 422, "message": "Invalid chat request."})
                    continue
                except ValueError:
                    await websocket.send_json({"type": "error", "status": 401, "message": "Your session expired. Log in again."})
                    await websocket.close(code=1008)
                    return

                async def transmit():
                    async with aclosing(turn_events(body, session, user, backend)) as events:
                        async for event in events:
                            await websocket.send_json(event)

                # Keep receiving while the agent runs so disconnects are detected
                # immediately, rather than at the next progress heartbeat.
                await while_connected(transmit())
        finally:
            with CancelScope(shield=True):
                receiver.cancel()
                closed.cancel()
                await asyncio.gather(receiver, closed, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except (TimeoutError, ValueError):
        await websocket.close(code=1008)


@router.get("/sessions/{session_id}/approvals")
async def list_approvals(session_id: str, session: DatabaseSession, user: CurrentUser, response: Response):
    conversation = await authorize_session(session_id, user, session)
    pending = pending_turn(await crud.get_latest_message(session, conversation.session_id))
    response.headers["Cache-Control"] = "no-store"
    return {"session_id": session_id, "pending_approvals": normalize_approvals(pending["approvals"]) if pending else []}


@router.get("/sessions/{session_id}/approvals/{interrupt_id}")
async def approval_details(session_id: str, interrupt_id: str, session: DatabaseSession, user: CurrentUser, response: Response):
    result = await list_approvals(session_id, session, user, response)
    for approval in result["pending_approvals"]:
        if approval.get("interrupt_id") == interrupt_id:
            return approval
    raise HTTPException(status_code=404, detail="Pending approval not found.")
