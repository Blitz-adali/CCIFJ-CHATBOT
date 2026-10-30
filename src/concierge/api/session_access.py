"""Session capabilities, ownership checks, and per-process turn serialization."""

import asyncio
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary

from fastapi import HTTPException

from concierge.auth.security import create_token, read_token
from concierge.config import get_settings
from concierge.db.relationaldb import crud

_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def session_token(session_id: int) -> str:
    return create_token(session_id, "session", get_settings().session_token_seconds)


async def authorize_session(token, user, session):
    try:
        session_id = read_token(token, "session")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Session not found or expired.") from exc
    chat = await crud.get_chat_session(session, session_id)
    if chat is None or (chat.user_id is not None and (user is None or chat.user_id != user.user_id)):
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return chat


@asynccontextmanager
async def session_turn(session_id: int):
    # The existing agent's checkpointer is process-local: run one API worker.
    lock = _locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[session_id] = lock
    async with lock:
        yield


def pending_turn(message):
    if message is not None:
        for item in message.tool_calls or []:
            if item.get("type") == "pending_approval":
                return item
    return None


active_turns: set[int] = set()


def turn_status(message, session_id: int) -> str:
    if session_id in active_turns:
        return "processing"
    for item in (message.tool_calls or []) if message else []:
        if item.get("type") == "api_turn_state":
            # A processing marker without an active task survived a disconnect/restart.
            return "unknown"
    return "ready"


def normalize_approvals(items):
    return [
        {**item, "actions": item.get("actions") or item.get("action_requests") or []}
        for item in items or [] if isinstance(item, dict)
    ]
