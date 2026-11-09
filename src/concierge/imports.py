"""Validate and save events and member companies, one at a time or from an Excel/CSV file.

Imports are two-step: `preview` parses and validates every row without writing anything,
then `commit` re-validates the same file and saves only the valid rows. Existing records
are updated instead of duplicated: events match on title + start date, members on name.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.db.relationaldb.models import Event, EventLanguage, Member, MemberCategory, utcnow

MAX_IMPORT_ROWS = 5000
EVENT_CATEGORIES = ("Business event", "Conference", "Networking", "Training")
EVENT_LANGUAGES = {"en": "en", "english": "en", "anglais": "en", "fr": "fr", "french": "fr", "français": "fr",
                   "francais": "fr", "ja": "ja", "jp": "ja", "japanese": "ja", "japonais": "ja", "日本語": "ja"}

# Column name -> (required, help text). The order is the template's column order.
EVENT_COLUMNS: dict[str, tuple[bool, str]] = {
    "title": (True, "Event title"),
    "description": (True, "What the event is about"),
    "category": (True, "One of: " + ", ".join(EVENT_CATEGORIES)),
    "start_at": (True, "Japan time, YYYY-MM-DD HH:MM"),
    "end_at": (False, "Japan time, YYYY-MM-DD HH:MM"),
    "location": (False, "Venue and city"),
    "member_price": (False, "Number"),
    "non_member_price": (False, "Number"),
    "price": (False, "Number, when there is a single price"),
    "currency": (False, "JPY or EUR"),
    "reg_deadline": (False, "Japan time, YYYY-MM-DD HH:MM"),
    "languages": (False, "Comma-separated: en, fr, ja"),
    "contact": (False, "Contact email or name"),
}
MEMBER_COLUMNS: dict[str, tuple[bool, str]] = {
    "name": (True, "Company name"),
    "sector": (False, "Directory sector, e.g. Food & Beverage"),
    "category": (False, "Membership category, e.g. Active, Startup"),
    "country": (False, "Country"),
    "region": (False, "City or address part, e.g. Minato-ku"),
    "continent": (False, "Continent"),
    "manpower": (False, "Number of employees"),
    "description": (False, "What the company does; used for matching"),
    "contact": (False, "Public contact (email, website)"),
    "established": (False, "YYYY-MM-DD or year"),
}
COLUMNS = {"events": EVENT_COLUMNS, "members": MEMBER_COLUMNS}


class ImportFileError(ValueError):
    """The file itself can't be read (wrong format, no header, too many rows)."""


# ---------- Reading files ----------

def _key(header: Any) -> str:
    return str(header or "").strip().lower().replace(" ", "_").replace("-", "_")


def read_table(raw: bytes, filename: str) -> list[dict[str, Any]]:
    """Rows of the first sheet (xlsx) or of a CSV file, keyed by normalised header names."""
    if raw.startswith(b"PK\x03\x04"):
        import openpyxl
        try:
            workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception as exc:
            raise ImportFileError(f"{filename}: could not open this Excel file.") from exc
        rows = list(workbook.worksheets[0].iter_rows(values_only=True))
        workbook.close()
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ImportFileError(f"{filename}: CSV files must be UTF-8. Save as 'CSV UTF-8' or use .xlsx.") from exc
        rows = list(csv.reader(io.StringIO(text)))
    rows = [row for row in rows if any(cell not in (None, "") for cell in row)]
    if not rows:
        raise ImportFileError(f"{filename}: the file is empty.")
    header = [_key(cell) for cell in rows[0]]
    if len(rows) - 1 > MAX_IMPORT_ROWS:
        raise ImportFileError(f"{filename}: at most {MAX_IMPORT_ROWS} rows per import.")
    return [{name: value for name, value in zip(header, row) if name} for row in rows[1:]]


def template_csv(kind: str) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS[kind].keys())
    if kind == "events":
        writer.writerow(["Business Networking Evening", "An evening to meet members.", "Networking",
                         "2027-03-12 18:30", "2027-03-12 21:00", "CCIFJ, Tokyo", "5000", "8000", "", "JPY",
                         "2027-03-10 18:00", "en,fr", "events@ccifj.or.jp"])
    else:
        writer.writerow(["Example Foods KK", "Food & Beverage", "Active", "Japan", "Minato-ku", "Asia", "45",
                         "Imports French cheese and wine; strong retail distribution in Japan.",
                         "info@example.com", "2012-04-01"])
    return out.getvalue()


# ---------- Field parsing ----------

def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None, second=0, microsecond=0)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = _text(value).replace("/", "-").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError("use YYYY-MM-DD HH:MM")


def _decimal(value: Any) -> Decimal:
    text = _text(value).replace(",", "").replace("¥", "").replace("€", "").strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError("must be a number") from None
    if number < 0:
        raise ValueError("cannot be negative")
    return number


def _int(value: Any) -> int:
    number = _decimal(value)
    if number != number.to_integral_value():
        raise ValueError("must be a whole number")
    return int(number)


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if text.isdigit() and len(text) == 4:
        return date(int(text), 1, 1)
    return _datetime(text).date()


def _optional(row: dict, errors: list[str], name: str, parse):
    if _text(row.get(name)) == "":
        return None
    try:
        return parse(row[name])
    except ValueError as exc:
        errors.append(f"{name}: {exc}")
        return None


def _required_text(row: dict, errors: list[str], name: str, limit: int) -> str:
    value = _text(row.get(name))
    if not value:
        errors.append(f"{name} is required")
    elif len(value) > limit:
        errors.append(f"{name} is longer than {limit} characters")
    return value


def _limited(row: dict, errors: list[str], name: str, limit: int) -> str | None:
    value = _text(row.get(name)) or None
    if value and len(value) > limit:
        errors.append(f"{name} is longer than {limit} characters")
    return value


def validate_event(row: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    clean: dict[str, Any] = {
        "title": _required_text(row, errors, "title", 255),
        "description": _required_text(row, errors, "description", 20_000),
        "location": _limited(row, errors, "location", 255),
        "contact": _limited(row, errors, "contact", 255),
    }
    category = _text(row.get("category"))
    match = next((c for c in EVENT_CATEGORIES if c.lower() == category.lower()), None)
    if not match:
        errors.append(f"category must be one of: {', '.join(EVENT_CATEGORIES)}")
    clean["category"] = match
    if _text(row.get("start_at")) == "":
        errors.append("start_at is required")
    for name in ("start_at", "end_at", "reg_deadline"):
        clean[name] = _optional(row, errors, name, _datetime)
    for name in ("member_price", "non_member_price", "price"):
        clean[name] = _optional(row, errors, name, _decimal)
    currency = _text(row.get("currency")).upper() or None
    if currency and (len(currency) != 3 or not currency.isalpha()):
        errors.append("currency must be a 3-letter code such as JPY")
    if currency is None and any(clean[n] is not None for n in ("member_price", "non_member_price", "price")):
        currency = "JPY"
    clean["currency"] = currency
    if clean["start_at"] and clean["end_at"] and clean["end_at"] < clean["start_at"]:
        errors.append("end_at is before start_at")
    if clean["start_at"] and clean["reg_deadline"] and clean["reg_deadline"] > clean["start_at"]:
        errors.append("reg_deadline is after start_at")
    languages = []
    for part in _text(row.get("languages")).replace(";", ",").split(","):
        if part.strip():
            code = EVENT_LANGUAGES.get(part.strip().lower())
            if code is None:
                errors.append(f"languages: unknown language '{part.strip()}' (use en, fr, ja)")
            elif code not in languages:
                languages.append(code)
    clean["languages"] = languages
    return clean, errors


def validate_member(row: dict, categories: dict[str, int]) -> tuple[dict, list[str]]:
    errors: list[str] = []
    clean: dict[str, Any] = {
        "name": _required_text(row, errors, "name", 255),
        "sector": _limited(row, errors, "sector", 100),
        "country": _limited(row, errors, "country", 100),
        "region": _limited(row, errors, "region", 255),
        "continent": _limited(row, errors, "continent", 50),
        "description": _limited(row, errors, "description", 20_000),
        "contact": _limited(row, errors, "contact", 255),
        "manpower": _optional(row, errors, "manpower", _int),
        "established_at": _optional(row, errors, "established", _date),
    }
    category = _text(row.get("category"))
    clean["category_id"] = None
    if category:
        clean["category_id"] = categories.get(category.lower())
        if clean["category_id"] is None:
            errors.append(f"category '{category}' is not a membership category ({', '.join(sorted(categories))})")
    return clean, errors


# ---------- Saving ----------

async def _member_categories(session: AsyncSession) -> dict[str, int]:
    rows = (await session.exec(select(MemberCategory.category_name, MemberCategory.category_id))).all()
    return {name.lower(): category_id for name, category_id in rows}


async def find_event(session: AsyncSession, title: str, start_at: datetime) -> Event | None:
    rows = (await session.exec(select(Event).where(func.lower(Event.title) == title.lower()))).all()
    return next((row for row in rows if row.start_at and row.start_at.date() == start_at.date()), None)


async def save_event(session: AsyncSession, clean: dict, event: Event | None = None, keep_empty: bool = False) -> Event:
    """Create or update an event and its languages. The caller commits.

    With `keep_empty` (imports), an empty value keeps what is stored instead of clearing it.
    """
    fields = {key: value for key, value in clean.items()
              if key != "languages" and not (keep_empty and event is not None and value is None)}
    if event is None:
        event = Event(**fields)
        session.add(event)
        await session.flush()
    else:
        for key, value in fields.items():
            setattr(event, key, value)
        event.updated_at = utcnow()
        session.add(event)
        if keep_empty and not clean["languages"]:
            return event
        await session.exec(delete(EventLanguage).where(EventLanguage.event_id == event.event_id))
    for language in clean["languages"]:
        session.add(EventLanguage(event_id=event.event_id, language=language))
    return event


async def run_import(session: AsyncSession, kind: str, rows: list[dict], commit: bool) -> dict:
    """Validate every row; when `commit` is true, also save the valid ones."""
    categories = await _member_categories(session) if kind == "members" else {}
    results, seen = [], set()
    counts = {"create": 0, "update": 0, "error": 0}
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        clean, errors = validate_event(row) if kind == "events" else validate_member(row, categories)
        existing = None
        if not errors:
            if kind == "events":
                key = (clean["title"].lower(), clean["start_at"].date())
                existing = await find_event(session, clean["title"], clean["start_at"])
            else:
                key = clean["name"].lower()
                existing = (await session.exec(select(Member).where(func.lower(Member.name) == key))).first()
            if key in seen:
                errors.append("duplicate of an earlier row in this file")
            seen.add(key)
        action = "error" if errors else ("update" if existing else "create")
        counts[action] += 1
        label = clean.get("title") or clean.get("name") or ""
        results.append({"row": number, "label": label, "action": action, "errors": errors})
        if commit and not errors:
            if kind == "events":
                await save_event(session, clean, existing, keep_empty=True)
            elif existing is None:
                session.add(Member(**clean))
            else:
                for field, value in clean.items():
                    # An empty cell keeps the stored value; the matched name keeps its stored spelling.
                    if value is not None and field != "name":
                        setattr(existing, field, value)
                existing.updated_at = utcnow()
                session.add(existing)
    if commit:
        await session.commit()
    return {"kind": kind, "committed": commit, "counts": counts, "rows": results}
