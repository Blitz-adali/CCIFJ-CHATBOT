"""The predefined question catalog, read without initializing the agents or models."""

import json
import re
import unicodedata
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


def normalize_question(text: str) -> str:
    text = unicodedata.normalize('NFKC', text).casefold().replace('’', "'")
    return re.sub(r'\s+', ' ', text).strip().rstrip('?？.!。 ')


@lru_cache
def _canonical_questions():
    mapping = {normalize_question(question):question for question in _questions()}
    for entries in _translations().values():
        for canonical, entry in entries.items():
            mapping[normalize_question(entry['question'])] = canonical
    return mapping


def canonical_question(text: str) -> str | None:
    return _canonical_questions().get(normalize_question(text))
