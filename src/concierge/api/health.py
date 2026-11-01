import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

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
    data = Path(__file__).resolve().parents[1] / "data"
    entries = json.loads((data / "predefined_qa.json").read_text(encoding="utf-8"))
    questions = [entry["question"] for entry in entries]
    if locale != "en":
        localized = json.loads((data / "predefined_qa_i18n.json").read_text(encoding="utf-8")).get(locale, {})
        questions = [localized.get(question, {}).get("question", question) for question in questions]
    return {"suggested_questions": questions, "starter_paths": starter_paths(locale)}
