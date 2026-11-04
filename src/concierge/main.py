"""Run with: uv run uvicorn concierge.main:app --app-dir src --reload

Use one worker: the existing agent keeps interrupted approval state in memory.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from concierge.api import auth, chat, health, sessions, staff
from concierge.api.agent_backend import warm_agent
from concierge.config import get_settings
from concierge.db.relationaldb import crud
from concierge.db.relationaldb.models import utcnow
from concierge.db.relationaldb.session import AsyncSessionLocal, create_db_and_tables, engine

logger = logging.getLogger(__name__)


def _log_warm_up_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        # The server keeps running; the first chat request retries and reports the error.
        logger.error("Agent warm-up failed", exc_info=task.exception())


GUEST_SWEEP_INTERVAL_SECONDS = 3600


async def sweep_expired_guest_sessions() -> None:
    """Backstop for guest chats whose page never sent its close beacon (crash, lost network).

    A guest session is reachable only through its token, so once the token has expired
    nobody can open it again and it is safe to delete.
    """
    while True:
        try:
            cutoff = utcnow() - timedelta(seconds=get_settings().session_token_seconds)
            async with AsyncSessionLocal() as session:
                deleted = await crud.delete_guest_sessions_created_before(session, cutoff)
            if deleted:
                logger.info("Deleted %d expired guest sessions", deleted)
        except Exception:
            logger.exception("Guest session cleanup failed")
        await asyncio.sleep(GUEST_SWEEP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from concierge.rag.src.diagnostics import enable_persistence
    enable_persistence()
    get_settings()  # Fail early on a missing or insecure signing secret.
    await create_db_and_tables()
    # Load the models in the background so /health answers while they load.
    warm_task = asyncio.create_task(asyncio.to_thread(warm_agent))
    warm_task.add_done_callback(_log_warm_up_failure)
    sweep_task = asyncio.create_task(sweep_expired_guest_sessions())
    try:
        yield
    finally:
        sweep_task.cancel()
        await engine.dispose()


app = FastAPI(title="CCIFJ Concierge API", lifespan=lifespan)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(staff.router)
for router in (health.router, auth.router, sessions.router, chat.router, staff.router):
    app.include_router(router, prefix="/api/v1")

# Only explicit frontend origins are allowed; no permissive wildcard default.
settings = get_settings()
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
    )
