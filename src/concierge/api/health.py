from typing import Literal

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from .catalog import catalog_questions
from .paths import starter_paths

router = APIRouter(tags=["health"])


@router.get("/health", response_class=PlainTextResponse)
async def health() -> str:
    return "ok"


@router.get("/status")
async def status():
    return {"status": "ok", "mode": "live"}


@router.get("/suggestions")
async def suggestions(locale: Literal["en", "fr", "ja"] = "en"):
    """Starter questions and guided journeys, without initializing models."""
    return {"suggested_questions": catalog_questions(locale), "starter_paths": starter_paths(locale)}
