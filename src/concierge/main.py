"""Run with: uv run uvicorn concierge.main:app --app-dir src --reload

Use one worker: the existing agent keeps interrupted approval state in memory.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from concierge.api import admin, admin_analytics, admin_automation, admin_content, admin_matching, auth, me, chat, health, sessions, staff
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


ENGAGEMENT_CHECK_SECONDS = 1800
ENGAGEMENT_HOUR_JST = 9


async def daily_engagement() -> None:
    """Run the onboarding/engagement rules once a day after 09:00 Japan time.

    Set CCIFJ_AUTOMATION_SCHEDULE=off to disable (staff can still press "Run now").
    """
    import os
    from sqlmodel import select
    from concierge import engagement
    from concierge.db.relationaldb.datetimes import japan_now
    from concierge.db.relationaldb.models import AutomationRun
    if os.getenv("CCIFJ_AUTOMATION_SCHEDULE", "on").strip().lower() in {"off", "0", "false"}:
        logger.info("Daily engagement schedule is off")
        return
    while True:
        try:
            now = japan_now()
            if now.hour >= ENGAGEMENT_HOUR_JST:
                async with AsyncSessionLocal() as session:
                    runs = (await session.exec(select(AutomationRun).where(AutomationRun.triggered_by == "schedule"))).all()
                    if not any(run.as_of.date() == now.date() for run in runs):
                        result = await engagement.run_engagement(session, now, triggered_by="schedule")
                        logger.info("Daily engagement run: %d new, %d duplicates", result["created"], result["duplicates"])
        except Exception:
            logger.exception("Daily engagement run failed")
        await asyncio.sleep(ENGAGEMENT_CHECK_SECONDS)


async def sync_linked_knowledge() -> None:
    """Re-check knowledge documents linked by URL (Calameo, web pages, PDFs) every few hours.

    CCIFJ_LINK_SYNC_HOURS sets the interval (default 6); "off" disables it ("Sync now" still works).
    """
    import os
    from concierge import linked_sources
    setting = os.getenv("CCIFJ_LINK_SYNC_HOURS", "6").strip().lower()
    if setting in {"off", "0", "false"}:
        logger.info("Linked knowledge sync is off")
        return
    await asyncio.sleep(120)  # let the embedding model load first
    while True:
        try:
            async with AsyncSessionLocal() as session:
                result = await linked_sources.sync_all(session)
            if result["checked"]:
                logger.info("Linked knowledge sync: %d checked, %d re-indexed", result["checked"], result["changed"])
        except Exception:
            logger.exception("Linked knowledge sync failed")
        await asyncio.sleep(float(setting) * 3600)


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
    engagement_task = asyncio.create_task(daily_engagement())
    link_sync_task = asyncio.create_task(sync_linked_knowledge())
    try:
        yield
    finally:
        sweep_task.cancel()
        engagement_task.cancel()
        link_sync_task.cancel()
        await engine.dispose()


app = FastAPI(title="CCIFJ Concierge API", lifespan=lifespan)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(staff.router)
app.include_router(admin.router)
app.include_router(admin_content.router)
app.include_router(admin_matching.router)
app.include_router(admin_automation.router)
app.include_router(admin_analytics.router)
app.include_router(me.router)
for router in (health.router, auth.router, sessions.router, chat.router, staff.router, admin.router,
               admin_content.router, admin_matching.router, admin_automation.router, me.router,
               admin_analytics.router):
    app.include_router(router, prefix="/api/v1")

# Only explicit frontend origins are allowed; no permissive wildcard default.
settings = get_settings()
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"],
    )
