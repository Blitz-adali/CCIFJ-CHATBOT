"""Semantic matching of member companies and upcoming events.

Both live in Chroma collections under var/knowledge (see concierge.knowledge). The indexes
follow the database on their own: before a search, a cheap signature (row count, last
update) is compared with the one seen last time, and only new or changed rows are
re-embedded. Staff edits and Excel imports therefore show up in the next search.

A match carries its similarity score and a short "why" excerpt from the description, so
the agent and staff can explain why a company fits instead of guessing.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.db.relationaldb.datetimes import japan_now
from concierge.db.relationaldb.models import Event, Member

Kind = Literal["members", "events"]
COLLECTIONS = {"members": "ccifj_members", "events": "ccifj_upcoming_events"}
# Cosine similarity (1 - distance) of the multilingual e5 model from which a company is
# counted as relevant; e5 scores are compressed, so unrelated text still scores ~0.75.
RELEVANT_SCORE = 0.82
_STOP = {"with", "that", "this", "from", "have", "their", "they", "your", "about", "into", "which", "looking",
         "company", "companies", "japan", "french", "france", "for", "and", "the"}

_lock = threading.Lock()
_signatures: dict[str, tuple] = {}


@dataclass
class Match:
    id: int
    name: str
    score: float
    why: str
    fields: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "score": round(self.score, 3), "why": self.why, **self.fields}


def _collection(kind: Kind):
    from concierge.knowledge import chroma_client
    return chroma_client().get_or_create_collection(name=COLLECTIONS[kind], metadata={"hnsw:space": "cosine"})


def member_text(member: Member) -> str:
    place = ", ".join(part for part in (member.region, member.country) if part)
    parts = [member.name, f"Sector: {member.sector}" if member.sector else "", f"Location: {place}" if place else "",
             member.description or ""]
    return ". ".join(part.strip() for part in parts if part and part.strip())[:4000]


def event_text(event: Event) -> str:
    parts = [event.title, f"Format: {event.category}", f"Location: {event.location}" if event.location else "",
             event.description or ""]
    return ". ".join(part.strip() for part in parts if part and part.strip())[:4000]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def _signature(session: AsyncSession, kind: Kind) -> tuple:
    model = Member if kind == "members" else Event
    query = select(func.count(), func.max(model.updated_at), func.sum(getattr(model, "member_id" if kind == "members" else "event_id")))
    if kind == "events":
        query = query.where(Event.start_at >= japan_now().replace(minute=0, second=0, microsecond=0))
    return tuple((await session.exec(query)).one())


async def _documents(session: AsyncSession, kind: Kind) -> dict[str, tuple[str, dict]]:
    if kind == "members":
        rows = (await session.exec(select(Member))).all()
        return {str(m.member_id): (member_text(m), {"sector": m.sector or "", "region": m.region or "",
                                                    "country": m.country or ""}) for m in rows}
    rows = (await session.exec(select(Event).where(Event.start_at >= japan_now()))).all()
    return {str(e.event_id): (event_text(e), {"category": e.category or ""}) for e in rows}


def _sync(kind: Kind, documents: dict[str, tuple[str, dict]]) -> dict:
    """Embed new/changed rows and drop removed ones. Runs in a worker thread."""
    collection = _collection(kind)
    stored = collection.get(include=["metadatas"])
    known = {rid: (meta or {}).get("hash") for rid, meta in zip(stored["ids"], stored["metadatas"])}
    changed = [rid for rid, (text, _) in documents.items() if known.get(rid) != _digest(text)]
    removed = [rid for rid in known if rid not in documents]
    if removed:
        collection.delete(ids=removed)
    if changed:
        from concierge.agents.tools import _get_embed_model
        model = _get_embed_model()
        for start in range(0, len(changed), 32):
            batch = changed[start:start + 32]
            texts = [documents[rid][0] for rid in batch]
            vectors = model.encode([f"passage: {text}" for text in texts], normalize_embeddings=True, show_progress_bar=False)
            collection.upsert(ids=batch, embeddings=vectors.tolist(), documents=texts,
                              metadatas=[{**documents[rid][1], "hash": _digest(documents[rid][0])} for rid in batch])
    return {"indexed": len(documents), "embedded": len(changed), "removed": len(removed)}


async def ensure_index(session: AsyncSession, kind: Kind, force: bool = False) -> dict:
    """Bring the index in line with the database when it changed since the last check."""
    signature = await _signature(session, kind)
    if not force and _signatures.get(kind) == signature:
        return {"indexed": None, "embedded": 0, "removed": 0}
    documents = await _documents(session, kind)

    def run():
        with _lock:
            return _sync(kind, documents)
    result = await asyncio.to_thread(run)
    _signatures[kind] = signature
    return result


def forget_signatures() -> None:
    _signatures.clear()


def _query(kind: Kind, text: str, limit: int, where: dict | None) -> list[tuple[str, float, str, dict]]:
    from concierge.agents.tools import _get_embed_model
    collection = _collection(kind)
    count = collection.count()
    if not count:
        return []
    vector = _get_embed_model().encode(f"query: {text}", normalize_embeddings=True).tolist()
    result = collection.query(query_embeddings=[vector], n_results=min(limit, count), where=where or None,
                              include=["documents", "metadatas", "distances"])
    return [(rid, 1 - distance, document, meta or {}) for rid, distance, document, meta in
            zip(result["ids"][0], result["distances"][0], result["documents"][0], result["metadatas"][0])]


# Directory descriptions often start with these generated lines; they are fields, not prose.
_FIELD_LINE = re.compile(r"^(sector|listed location|website|location|format)\s*:", re.I)


def why_excerpt(need: str, description: str | None, limit: int = 220) -> str:
    """The description sentence sharing most words with the need (a reason, not a guess).

    Empty when the description has no prose beyond directory fields.
    """
    if not description:
        return ""
    words = {w for w in re.findall(r"\w{4,}", need.lower()) if w not in _STOP}
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", description)
                 if len(s.strip()) > 20 and not _FIELD_LINE.match(s.strip())]
    if not sentences:
        return ""
    best = max(sentences, key=lambda s: len(words & set(re.findall(r"\w{4,}", s.lower()))))
    return best[:limit] + ("…" if len(best) > limit else "")


async def match_members(session: AsyncSession, need: str, *, limit: int = 5, sector: str | None = None,
                        region: str | None = None, exclude: set[int] | None = None) -> list[Match]:
    await ensure_index(session, "members")
    where = {"sector": sector} if sector else None
    hits = await asyncio.to_thread(_query, "members", need, max(limit * 6, 30), where)
    pattern = None
    if region and region.strip().lower() not in {"japan", "jp"}:
        from concierge.agents.tools import _member_region_pattern
        pattern = _member_region_pattern(region)
    ids = [int(rid) for rid, *_ in hits if int(rid) not in (exclude or set())]
    members = {m.member_id: m for m in (await session.exec(select(Member).where(Member.member_id.in_(ids)))).all()} if ids else {}
    matches = []
    for rid, score, _, _ in hits:
        member = members.get(int(rid))
        if member is None or (pattern is not None and not pattern.search(member.region or "")):
            continue
        place = ", ".join(part for part in (member.region, member.country) if part)
        why = why_excerpt(need, member.description) or " · ".join(
            part for part in (member.sector and f"Sector: {member.sector}", place and f"Location: {place}") if part)
        matches.append(Match(member.member_id, member.name, score, why, {
            "sector": member.sector, "region": member.region, "country": member.country,
            "description": member.description, "contact": member.contact,
        }))
        if len(matches) >= limit:
            break
    return matches


async def count_relevant(session: AsyncSession, need: str, sector: str | None = None) -> tuple[int, dict[str, int]]:
    """How many members look relevant, and their sectors (what non-members may see)."""
    matches = await match_members(session, need, limit=60, sector=sector)
    relevant = [m for m in matches if m.score >= RELEVANT_SCORE]
    sectors: dict[str, int] = {}
    for match in relevant:
        key = match.fields.get("sector") or "Other"
        sectors[key] = sectors.get(key, 0) + 1
    return len(relevant), dict(sorted(sectors.items(), key=lambda item: -item[1]))


async def match_events(session: AsyncSession, interests: str, *, limit: int = 5) -> list[Match]:
    await ensure_index(session, "events")
    hits = await asyncio.to_thread(_query, "events", interests, limit * 3, None)
    ids = [int(rid) for rid, *_ in hits]
    events = {e.event_id: e for e in (await session.exec(select(Event).where(Event.event_id.in_(ids)))).all()} if ids else {}
    now = japan_now()
    matches = []
    for rid, score, _, _ in hits:
        event = events.get(int(rid))
        if event is None or event.start_at is None or event.start_at < now:
            continue
        matches.append(Match(event.event_id, event.title, score, why_excerpt(interests, event.description), {
            "category": event.category, "location": event.location,
            "start_at": event.start_at.isoformat(timespec="minutes"),
            "reg_deadline": event.reg_deadline.isoformat(timespec="minutes") if event.reg_deadline else None,
        }))
        if len(matches) >= limit:
            break
    return matches
