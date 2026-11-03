"""Run with: uv run uvicorn concierge.main:app --app-dir src --reload

Use one worker: the existing agent keeps interrupted approval state in memory.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from concierge.api import auth, chat, health, sessions
from concierge.api.agent_backend import warm_agent
from concierge.config import get_settings
from concierge.db.relationaldb.session import create_db_and_tables, engine

logger = logging.getLogger(__name__)


def _log_warm_up_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        # The server keeps running; the first chat request retries and reports the error.
        logger.error("Agent warm-up failed", exc_info=task.exception())


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()  # Fail early on a missing or insecure signing secret.
    await create_db_and_tables()
    # Load the models in the background so /health answers while they load.
    warm_task = asyncio.create_task(asyncio.to_thread(warm_agent))
    warm_task.add_done_callback(_log_warm_up_failure)
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="CCIFJ Concierge API", lifespan=lifespan)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(chat.router)
for router in (health.router, auth.router, sessions.router, chat.router):
    app.include_router(router, prefix="/api/v1")

# Only explicit frontend origins are allowed; no permissive wildcard default.
settings = get_settings()
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
    )
