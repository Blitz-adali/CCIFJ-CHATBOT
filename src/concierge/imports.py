"""Validate and save events and member companies, one at a time or from an Excel/CSV file.

Imports are two-step: `preview` parses and validates every row without writing anything,
then `commit` re-validates the same file and saves only the valid rows. Existing records
are updated instead of duplicated: events match on title + start date, members on name.

Member files may use the CCIFJ directory export layout (`Full name`, `Primary sector`,
`Membership type`, `Email`, one row per company plus one row per contact person): the
columns are mapped by `MEMBER_ALIASES`, membership labels by `CATEGORY_HINTS`, and each
contact person becomes a `MemberContact` of their company (name, job title, email, language).
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

from concierge.db.relationaldb.models import Event, EventLanguage, Member, MemberCategory, MemberContact, utcnow

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
    "secondary_sectors": (False, "Other sectors, comma-separated"),
    "language": (False, "Preferred contact language, e.g. English, Japanese, French"),
    "directory_visible": (False, "TRUE or FALSE: show in the member directory (default TRUE)"),
    "job_title": (False, "Only on a contact-person row: name the row 'Company, First LAST'"),
}
COLUMNS = {"events": EVENT_COLUMNS, "members": MEMBER_COLUMNS}

# Column names other systems export (normalised like `_key`) -> our column. A value is only
# copied when our column is empty, so a file that already uses our names is unaffected.
MEMBER_ALIASES: dict[str, str] = {
    "full_name": "name", "company": "name", "company_name": "name", "organisation": "name",
    "primary_sector": "sector", "industry": "sector",
    "membership_type": "category", "membership": "category", "tier": "category",
    "email": "contact", "website": "contact",
    "employees": "manpower", "founded": "established",
    "secondary_sector(s)": "secondary_sectors", "secondary_sector": "secondary_sectors",
    "communication_language": "language",
    "display_profile_in_the_online_directory": "directory_visible", "display_profile": "directory_visible",
    "position": "job_title",
}
# Membership labels other systems use -> our category name. Checked in order against the
# lowercased label, so "Resident Active Member - Outside Kanto rate" is Non-Kanto, not Active.
CATEGORY_HINTS: tuple[tuple[str, str], ...] = (
    ("non-resident", "Non-Resident"), ("non resident", "Non-Resident"),
    ("outside kanto", "Non-Kanto"), ("non-kanto", "Non-Kanto"), ("non kanto", "Non-Kanto"), ("regional", "Non-Kanto"),
    ("benefactor", "Benefactor"), ("start-up", "Startup"), ("startup", "Startup"),
    ("individual", "Individual"), ("associate", "Associate"), ("active", "Active"),
)


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
                         "info@example.com", "2012-04-01", "Distribution", "English", "TRUE", ""])
        writer.writerow(["Example Foods KK, Aiko TANAKA", "", "", "", "", "", "", "", "aiko.tanaka@example.com", "",
                         "", "Japanese", "TRUE", "Sales Director"])
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


def apply_member_aliases(row: dict) -> dict:
    """Map another system's column names onto ours (see MEMBER_ALIASES)."""
    out = dict(row)
    for alias, target in MEMBER_ALIASES.items():
        if _text(out.get(target)) == "" and _text(out.get(alias)) != "":
            out[target] = out[alias]
    return out


def _flag(value: Any, default: bool = True) -> bool:
    text = _text(value).lower()
    if text in {"true", "1", "yes", "y", "oui", "vrai"}:
        return True
    if text in {"false", "0", "no", "n", "non", "faux"}:
        return False
    return default


def split_member_contacts(rows: list[tuple[int, dict]]) -> tuple[list[tuple[int, dict]], dict[str, list[dict]], list[int]]:
    """Separate company rows from contact-person rows.

    Directory exports list a company and then one row per contact person, named
    "COMPANY 01, Sophie DUBOIS" with a job title or salutation. Returns the company rows, the
    contact people grouped by lowercased company name (name, salutation, job title, email,
    language, visibility), and the row numbers of people whose company is not in the file.
    """
    companies = {_text(row.get("name")).lower() for _, row in rows
                 if _text(row.get("name")) and "," not in _text(row.get("name"))}
    kept: list[tuple[int, dict]] = []
    contacts: dict[str, list[dict]] = {}
    orphans: list[int] = []
    for number, row in rows:
        company, _, person = _text(row.get("name")).partition(",")
        is_person = bool(person.strip()) and bool(_text(row.get("job_title")) or _text(row.get("salutation")))
        if not is_person:
            kept.append((number, row))
            continue
        key = company.strip().lower()
        if key not in companies:
            orphans.append(number)
            continue
        contacts.setdefault(key, []).append({
            "name": person.strip()[:150],
            "salutation": _text(row.get("salutation"))[:20] or None,
            "job_title": _text(row.get("job_title"))[:150] or None,
            "email": (_text(row.get("email")) or _text(row.get("contact")))[:255] or None,
            "language": _text(row.get("language"))[:30] or None,
            "directory_visible": _flag(row.get("directory_visible")),
        })
    return kept, contacts, orphans


def match_category(label: str, categories: dict[str, int]) -> int | None:
    """Exact category name first, then the hints for labels such as 'Resident Active Member - Outside Kanto rate'."""
    key = label.lower()
    if key in categories:
        return categories[key]
    for hint, name in CATEGORY_HINTS:
        if hint in key and name.lower() in categories:
            return categories[name.lower()]
    return None


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
        "secondary_sectors": _limited(row, errors, "secondary_sectors", 255),
        "language": _limited(row, errors, "language", 30),
    }
    # An empty cell keeps what is stored (None is skipped on update); a new company defaults to visible.
    clean["directory_visible"] = _flag(row.get("directory_visible")) if _text(row.get("directory_visible")) else None
    category = _text(row.get("category"))
    clean["category_id"] = None
    if category:
        clean["category_id"] = match_category(category, categories)
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
    numbered = list(enumerate(rows, start=2))  # row 1 is the header
    orphans: list[int] = []
    people: dict[str, list[dict]] = {}
    if kind == "members":
        numbered = [(number, apply_member_aliases(row)) for number, row in numbered]
        numbered, people, orphans = split_member_contacts(numbered)
        if people:
            counts["contacts"] = sum(len(found) for found in people.values())  # people attached to their company
        for number in orphans:
            counts["error"] += 1
            results.append({"row": number, "label": "", "action": "error",
                            "errors": ["contact person whose company is not in this file"]})
    for number, row in numbered:
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
                if clean["directory_visible"] is None:
                    clean["directory_visible"] = True
                existing = Member(**clean)
                session.add(existing)
            else:
                for field, value in clean.items():
                    # An empty cell keeps the stored value; the matched name keeps its stored spelling.
                    if value is not None and field != "name":
                        setattr(existing, field, value)
                existing.updated_at = utcnow()
                session.add(existing)
            if kind == "members" and key in people:
                # The file is the source of truth for a company's contact people.
                await session.flush()
                await session.exec(delete(MemberContact).where(MemberContact.member_id == existing.member_id))
                for person in people[key]:
                    session.add(MemberContact(member_id=existing.member_id, **person))
    if commit:
        await session.commit()
    results.sort(key=lambda item: item["row"])
    return {"kind": kind, "committed": commit, "counts": counts, "rows": results}
