from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_class=PlainTextResponse)
async def health() -> str:
    return "ok"


@router.get("/status")
async def status():
    return {"status": "ok", "mode": "live"}


@router.get("/suggestions")
async def suggestions():
    # Same catalog used by agents, without importing or initializing models.
    import json
    from pathlib import Path

    catalog = Path(__file__).resolve().parents[1] / "data" / "predefined_qa.json"
    entries = json.loads(catalog.read_text(encoding="utf-8"))
    return {"suggested_questions": [entry["question"] for entry in entries]}
