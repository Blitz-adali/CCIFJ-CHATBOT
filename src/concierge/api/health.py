from typing import Literal
from concierge.version import build_signature

from fastapi import APIRouter, Response
from sqlalchemy import text
from concierge.auth.dependencies import DatabaseSession
from fastapi.responses import PlainTextResponse

from .agent_backend import agent_ready
from .catalog import catalog_questions
from .paths import starter_paths

# Fingerprint source at import, so a running old process cannot claim a newer
# build just because files on disk changed. Never include environment secrets.
_SOURCE_SIGNATURE = build_signature()

router = APIRouter(tags=["health"])


@router.get("/health", response_class=PlainTextResponse)
async def health() -> str:
    return "ok"


@router.get("/status")
async def status():
    from concierge.rag.src.diagnostics import snapshot
    # agent_ready turns true once startup warm-up has loaded the agent and models.
    return {"status": "ok", "mode": "live", "agent_ready": agent_ready(), "version": _SOURCE_SIGNATURE,
            "retrieval": snapshot(), "features": ["database_faq", "conversation_brief", "single_domain_direct", "event_budget_filters"]}


@router.get("/ready")
async def ready(session: DatabaseSession, response: Response):
    """Readiness is distinct from liveness: check SQL and model warm-up."""
    database_ready = False
    try:
        await session.execute(text("SELECT 1"))
        database_ready = True
    except Exception:
        pass
    ready = database_ready and agent_ready()
    response.status_code = 200 if ready else 503
    response.headers["Cache-Control"] = "no-store"
    return {"ready": ready, "database_ready": database_ready, "agent_ready": agent_ready()}


@router.get("/suggestions")
async def suggestions(locale: Literal["en", "fr", "ja"] = "en"):
    """Starter questions and guided journeys, without initializing models."""
    return {"suggested_questions": catalog_questions(locale), "starter_paths": starter_paths(locale)}
