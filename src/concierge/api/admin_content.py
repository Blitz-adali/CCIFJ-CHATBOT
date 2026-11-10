"""Admin dashboard content: events, member companies, Excel/CSV imports and knowledge files.

Every route needs a staff account. Events and members are read live by the agent's tools,
so changes apply to the next chat turn without a restart.
"""

import asyncio
import base64
import binascii
import hashlib
import logging
from typing import Annotated, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlmodel import col, select

from concierge.auth.access import StaffUser, require_staff
from concierge.auth.dependencies import DatabaseSession
from concierge.db.relationaldb import crud
from concierge.db.relationaldb.models import (
    Belongs, Event, EventLanguage, KnowledgeDocument, Member, MemberCategory, utcnow,
)
from concierge.imports import (
    COLUMNS, EVENT_CATEGORIES, ImportFileError, find_event, read_table, run_import, save_event, template_csv,
    validate_event,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_staff)])

ImportKind = Literal["events", "members"]
# Brochures and media kits are image-heavy PDFs (often 20-30 MB for a few pages of text).
KNOWLEDGE_MAX_BYTES = 40 * 1024 * 1024
KNOWLEDGE_MAX_PAGES = 300
KNOWLEDGE_MAX_CHARS = 2_000_000


class FileUpload(BaseModel):
    name: str = Field(default="upload", max_length=200)
    mime_type: str = Field(default="", max_length=100)
    # 15 MB of binary data is about 20 MB of base64 text.
    data_base64: str = Field(min_length=1, max_length=21_000_000)


def decode_upload(body: FileUpload, max_bytes: int) -> bytes:
    try:
        raw = base64.b64decode(body.data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, f"{body.name}: the file could not be decoded.") from None
    if not raw or len(raw) > max_bytes:
        raise HTTPException(413, f"{body.name}: files must be between 1 byte and {max_bytes // (1024 * 1024)} MB.")
    return raw


# ---------- Events ----------

class EventInput(BaseModel):
    title: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=20_000)
    category: str = Field(default="", max_length=50)
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    location: Optional[str] = None
    member_price: Optional[Union[float, str]] = None
    non_member_price: Optional[Union[float, str]] = None
    price: Optional[Union[float, str]] = None
    currency: Optional[str] = None
    reg_deadline: Optional[str] = None
    languages: list[str] = Field(default_factory=list)
    contact: Optional[str] = None


def _iso(value) -> Optional[str]:
    return value.isoformat(timespec="minutes") if value else None


def event_json(event: Event, languages: list[str]) -> dict:
    return {
        "event_id": event.event_id, "title": event.title, "description": event.description,
        "category": event.category, "location": event.location, "contact": event.contact,
        "start_at": _iso(event.start_at), "end_at": _iso(event.end_at), "reg_deadline": _iso(event.reg_deadline),
        "member_price": float(event.member_price) if event.member_price is not None else None,
        "non_member_price": float(event.non_member_price) if event.non_member_price is not None else None,
        "price": float(event.price) if event.price is not None else None,
        "currency": event.currency, "languages": sorted(languages),
    }


async def _languages(session, event_ids: list[int]) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    if event_ids:
        rows = await session.exec(select(EventLanguage).where(col(EventLanguage.event_id).in_(event_ids)))
        for row in rows.all():
            found.setdefault(row.event_id, []).append(row.language)
    return found


@router.get("/events")
async def list_events(
    session: DatabaseSession, response: Response,
    q: Annotated[str, Query(max_length=100)] = "",
    scope: Literal["upcoming", "past", "all"] = "upcoming",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    from concierge.agents.tools import _now
    query = select(Event)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(Event.title).like(like), func.lower(Event.location).like(like)))
    now = _now().replace(tzinfo=None)
    if scope == "upcoming":
        query = query.where(Event.start_at >= now).order_by(col(Event.start_at))
    elif scope == "past":
        query = query.where(Event.start_at < now).order_by(col(Event.start_at).desc())
    else:
        query = query.order_by(col(Event.start_at).desc())
    rows = list((await session.exec(query.offset(offset).limit(limit + 1))).all())
    visible = rows[:limit]
    languages = await _languages(session, [row.event_id for row in visible])
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [event_json(row, languages.get(row.event_id, [])) for row in visible],
        "next_offset": offset + limit if len(rows) > limit else None,
        "categories": list(EVENT_CATEGORIES),
    }


def _validated(body: EventInput) -> dict:
    values = body.model_dump()
    values["languages"] = ",".join(body.languages)
    clean, errors = validate_event(values)
    if errors:
        raise HTTPException(422, "Please fix: " + "; ".join(errors))
    return clean


@router.post("/events", status_code=201)
async def create_event(body: EventInput, session: DatabaseSession):
    clean = _validated(body)
    if await find_event(session, clean["title"], clean["start_at"]):
        raise HTTPException(409, "An event with this title already exists on that date.")
    event = await save_event(session, clean)
    await session.commit()
    await session.refresh(event)
    return event_json(event, clean["languages"])


@router.put("/events/{event_id}")
async def update_event(event_id: int, body: EventInput, session: DatabaseSession):
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found.")
    clean = _validated(body)
    duplicate = await find_event(session, clean["title"], clean["start_at"])
    if duplicate is not None and duplicate.event_id != event_id:
        raise HTTPException(409, "Another event with this title already exists on that date.")
    await save_event(session, clean, event)
    await session.commit()
    await session.refresh(event)
    return event_json(event, clean["languages"])


# ---------- Member companies ----------

@router.get("/members")
async def list_members(
    session: DatabaseSession, response: Response,
    q: Annotated[str, Query(max_length=100)] = "",
    sector: Annotated[str, Query(max_length=100)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    query = select(Member, MemberCategory.category_name).join(
        MemberCategory, MemberCategory.category_id == Member.category_id, isouter=True)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(Member.name).like(like), func.lower(Member.description).like(like),
                                func.lower(Member.region).like(like)))
    if sector.strip():
        # A company is listed under its primary sector and any secondary one.
        wanted = sector.strip()
        query = query.where(or_(Member.sector == wanted, func.lower(Member.secondary_sectors).like(f"%{wanted.lower()}%")))
    rows = list((await session.exec(query.order_by(col(Member.name)).offset(offset).limit(limit + 1))).all())
    visible = rows[:limit]
    accounts: dict[int, int] = {}
    if visible:
        counts = await session.exec(
            select(Belongs.member_id, func.count()).where(col(Belongs.member_id).in_([m.member_id for m, _ in visible]))
            .group_by(Belongs.member_id))
        accounts = dict(counts.all())
    sectors = (await session.exec(select(Member.sector).where(Member.sector != None).distinct()  # noqa: E711
                                  .order_by(col(Member.sector)))).all()
    people = await crud.member_contacts(session, [member.member_id for member, _ in visible], include_hidden=True)
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [{
            "member_id": member.member_id, "name": member.name, "sector": member.sector, "category": category,
            "country": member.country, "region": member.region, "manpower": member.manpower,
            "description": member.description, "contact": member.contact,
            "secondary_sectors": member.secondary_sectors, "language": member.language,
            "directory_visible": member.directory_visible is not False,
            "contacts": [{"name": c.name, "salutation": c.salutation, "job_title": c.job_title, "email": c.email,
                          "language": c.language} for c in people.get(member.member_id, [])],
            "accounts": accounts.get(member.member_id, 0),
        } for member, category in visible],
        "next_offset": offset + limit if len(rows) > limit else None,
        "sectors": list(sectors),
    }


# ---------- Excel / CSV import ----------

@router.get("/import/{kind}/template", response_class=PlainTextResponse)
async def import_template(kind: ImportKind):
    return PlainTextResponse(template_csv(kind), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="ccifj-{kind}-template.csv"'})


@router.get("/import/{kind}/columns")
async def import_columns(kind: ImportKind):
    return [{"name": name, "required": required, "help": help_text}
            for name, (required, help_text) in COLUMNS[kind].items()]


@router.post("/import/{kind}")
async def import_file(kind: ImportKind, body: FileUpload, session: DatabaseSession, commit: bool = False):
    """commit=false validates and reports what would change; commit=true saves the valid rows."""
    raw = decode_upload(body, 10 * 1024 * 1024)
    try:
        rows = await asyncio.to_thread(read_table, raw, body.name)
    except ImportFileError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await run_import(session, kind, rows, commit)


# ---------- Knowledge files ----------

class KnowledgeUpload(FileUpload):
    # Base64 is 4/3 of the binary size; a little headroom so decode_upload gives the clear "too large" message.
    data_base64: str = Field(min_length=1, max_length=KNOWLEDGE_MAX_BYTES * 4 // 3 + 1024)
    title: str = Field(default="", max_length=255)
    source_url: Optional[str] = Field(default=None, max_length=500)


class KnowledgeLink(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    title: str = Field(default="", max_length=255)
    download_url: Optional[str] = Field(default=None, max_length=500)


def knowledge_json(doc: KnowledgeDocument) -> dict:
    return {
        "doc_id": doc.doc_id, "title": doc.title, "filename": doc.filename, "mime_type": doc.mime_type,
        "source_url": doc.source_url, "char_count": doc.char_count, "chunk_count": doc.chunk_count,
        "created_at": doc.created_at.isoformat() + "Z",
        "linked": doc.sync_url is not None, "download_url": doc.download_url,
        "checked_at": _utc(doc.checked_at), "changed_at": _utc(doc.changed_at), "sync_note": doc.sync_error,
    }


def _utc(value) -> Optional[str]:
    return value.isoformat() + "Z" if value else None


@router.get("/knowledge")
async def list_knowledge(session: DatabaseSession, response: Response):
    rows = (await session.exec(select(KnowledgeDocument).order_by(col(KnowledgeDocument.created_at).desc()))).all()
    response.headers["Cache-Control"] = "no-store"
    return [knowledge_json(row) for row in rows]


@router.post("/knowledge", status_code=201)
async def upload_knowledge(body: KnowledgeUpload, session: DatabaseSession, staff: StaffUser):
    from concierge import knowledge
    from concierge.api.attachments import extract_attachment

    source_url = (body.source_url or "").strip() or None
    if source_url and not source_url.startswith("https://"):
        raise HTTPException(422, "The public link must start with https://")
    raw = decode_upload(body, KNOWLEDGE_MAX_BYTES)
    digest = hashlib.sha256(raw).hexdigest()
    if (await session.exec(select(KnowledgeDocument).where(KnowledgeDocument.sha256 == digest))).first():
        raise HTTPException(409, f"{body.name}: this file is already in the knowledge base.")
    extracted = await asyncio.to_thread(
        extract_attachment, body.model_dump(include={"name", "mime_type", "data_base64"}),
        max_bytes=KNOWLEDGE_MAX_BYTES, max_pages=KNOWLEDGE_MAX_PAGES, max_chars=KNOWLEDGE_MAX_CHARS)
    title = body.title.strip() or body.name.rsplit(".", 1)[0]
    doc = KnowledgeDocument(title=title, filename=body.name, mime_type=extracted["mime_type"],
                            source_url=source_url, sha256=digest, char_count=len(extracted["text"]),
                            uploaded_by=staff.user_id, created_at=utcnow())
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    try:
        doc.chunk_count = await asyncio.to_thread(
            knowledge.index_document, doc.doc_id, title, knowledge.document_url(doc.doc_id, source_url), extracted["text"],
            body.name)
    except Exception as exc:
        logger.exception("Indexing knowledge document %s failed", doc.doc_id)
        await asyncio.to_thread(knowledge.remove_document, doc.doc_id)
        await session.delete(doc)
        await session.commit()
        raise HTTPException(503, "The file was read but could not be indexed. Try again later.") from exc
    session.add(doc)
    await session.commit()
    return knowledge_json(doc)


@router.post("/knowledge/link", status_code=201)
async def link_knowledge(body: KnowledgeLink, session: DatabaseSession, staff: StaffUser):
    """Add a document by URL (Calameo, web page or file link); it is re-checked on a schedule."""
    from concierge import linked_sources

    url = body.url.strip()
    download_url = (body.download_url or "").strip() or None
    digest = linked_sources.link_digest(url)
    if (await session.exec(select(KnowledgeDocument).where(KnowledgeDocument.sha256 == digest))).first():
        raise HTTPException(409, "This link is already in the knowledge base. Use Sync now to refresh it.")
    try:
        fetched = await asyncio.to_thread(linked_sources.fetch_source, url, download_url,
                                          max_pages=KNOWLEDGE_MAX_PAGES, max_chars=KNOWLEDGE_MAX_CHARS)
    except linked_sources.LinkError as exc:
        raise HTTPException(422, str(exc)) from None
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Reading linked source %s failed: %s", url, exc)
        raise HTTPException(502, f"The link could not be read: {linked_sources.error_text(exc)}") from None
    now = utcnow()
    doc = KnowledgeDocument(title=body.title.strip() or fetched.title, filename=fetched.filename,
                            mime_type=fetched.mime_type, source_url=url, sha256=digest,
                            char_count=len(fetched.text), uploaded_by=staff.user_id, created_at=now,
                            sync_url=url, download_url=download_url, version=fetched.version,
                            checked_at=now, changed_at=now, sync_error=fetched.note or None)
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    from concierge import knowledge
    try:
        doc.chunk_count = await asyncio.to_thread(
            knowledge.index_document, doc.doc_id, doc.title, url, fetched.text, fetched.filename)
    except Exception as exc:
        logger.exception("Indexing linked document %s failed", doc.doc_id)
        await asyncio.to_thread(knowledge.remove_document, doc.doc_id)
        await session.delete(doc)
        await session.commit()
        raise HTTPException(503, "The link was read but could not be indexed. Try again later.") from exc
    session.add(doc)
    await session.commit()
    return knowledge_json(doc)


@router.post("/knowledge/{doc_id}/sync")
async def sync_knowledge(doc_id: int, session: DatabaseSession):
    """Fetch a linked document now (staff just updated it on Calameo or the website)."""
    from concierge import linked_sources
    doc = await session.get(KnowledgeDocument, doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found.")
    if not doc.sync_url:
        raise HTTPException(400, "This document was uploaded as a file; upload the new version instead.")
    changed = await linked_sources.sync_document(session, doc)
    return {**knowledge_json(doc), "changed": changed}


@router.delete("/knowledge/{doc_id}", status_code=204)
async def delete_knowledge(doc_id: int, session: DatabaseSession):
    from concierge import knowledge
    doc = await session.get(KnowledgeDocument, doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found.")
    await asyncio.to_thread(knowledge.remove_document, doc_id)
    await session.delete(doc)
    await session.commit()
    return Response(status_code=204)
