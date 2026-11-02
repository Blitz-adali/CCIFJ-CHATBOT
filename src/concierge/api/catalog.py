"""The predefined question catalog, read without initializing the agents or models."""

import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "data"


@lru_cache
def _questions() -> tuple[str, ...]:
    entries = json.loads((_DATA / "predefined_qa.json").read_text(encoding="utf-8"))
    return tuple(entry["question"] for entry in entries)


@lru_cache
def _translations() -> dict:
    return json.loads((_DATA / "predefined_qa_i18n.json").read_text(encoding="utf-8"))


def localized_question(canonical: str, locale: str) -> str:
    """A canonical English catalog question in the UI locale; English when untranslated."""
    return _translations().get(locale, {}).get(canonical, {}).get("question", canonical)


def catalog_questions(locale: str) -> list[str]:
    return [localized_question(question, locale) for question in _questions()]
