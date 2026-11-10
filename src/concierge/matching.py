"""Hybrid matching of member companies and upcoming events with a need written in plain language.

Both live in Chroma collections under var/knowledge (see concierge.knowledge). The indexes
follow the database on their own: before a search, a cheap signature (row count, last
update) is compared with the one seen last time, and only new or changed rows are
re-embedded. Staff edits and Excel imports therefore show up in the next search.

Ranking combines three signals, so a match does not depend on the user typing the same
words as the directory:

1. meaning: cosine similarity of multilingual embeddings (a French or Japanese need matches
   an English description, "agrifood" matches "Food & Beverage");
2. wording: an IDF-weighted overlap of stemmed terms, widened with a small bilingual
   vocabulary (distributor / distributeur / importer / wholesale...), which rewards the
   precise term the user asked for ("solar", "payroll", "HR");
3. structure: the company's primary or secondary sector, when the need names one.

The first two are fused by reciprocal rank, the third adds a bonus. A match carries its
similarity score, the terms it matched and a short "why" excerpt from the description, so the
agent and staff can explain why a company fits instead of guessing.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import math
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Literal

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.db.relationaldb.datetimes import japan_now
from concierge.db.relationaldb.models import Event, Member, MemberContact

Kind = Literal["members", "events"]
COLLECTIONS = {"members": "ccifj_members", "events": "ccifj_upcoming_events"}
# Cosine similarity (1 - distance) of the multilingual e5 model from which a company is
# counted as relevant; e5 scores are compressed, so unrelated text still scores ~0.75.
RELEVANT_SCORE = 0.82
# A company whose wording covers at least this share of the need's weighted terms is relevant
# even when its embedding score is a little under RELEVANT_SCORE.
RELEVANT_WORDING = 0.6
WORDING_FLOOR = 0.79
_MAX_CANDIDATES = 400
_STOP = {"with", "that", "this", "from", "have", "their", "they", "your", "about", "into", "which", "looking",
         "company", "companies", "japan", "japanese", "french", "france", "for", "and", "the", "who", "can",
         "need", "needs", "want", "wants", "find", "some", "would", "like", "help", "member", "members",
         "une", "des", "les", "pour", "avec", "dans", "qui", "que", "sur", "est", "nous", "notre", "cherche",
         "entreprise", "entreprises", "societe", "japon", "service", "services", "solution", "solutions",
         "based", "international", "partners", "partner", "fictional", "specialising", "offerings", "include",
         # field labels of the indexed text, and filler words of a request
         "sector", "also", "active", "location", "contacts", "format", "our", "nos", "notre", "someone", "something",
         "handle", "know", "knows", "well", "good", "best", "more", "new", "all", "any", "are", "has", "its",
         "industry", "industries", "secteur", "field", "area", "event", "events", "evenement", "evenements"}

# Words that mean the same thing for matching purposes, across English and French. Each
# group is matched as one concept; the embedding handles everything not listed here.
_CONCEPTS: tuple[tuple[str, ...], ...] = (
    ("food", "f&b", "fnb", "agrifood", "agroalimentaire", "alimentaire", "beverage", "boisson", "culinary", "gastronomy",
     "gastronomie", "restaurant", "restauration", "catering", "traiteur", "ingredient", "cuisine", "wine", "vin"),
    ("distribution", "distributor", "distributeur", "importer", "importateur", "import", "wholesale", "grossiste",
     "retail", "retailer", "trading", "merchandising", "reseller"),
    ("recruitment", "recrutement", "hiring", "talent", "staffing", "hr", "rh", "payroll", "paie"),
    ("finance", "financial", "financier", "bank", "banking", "banque", "fintech", "accounting", "comptabilite",
     "treasury", "cash", "budget"),
    ("software", "it", "ict", "logiciel", "digital", "numerique", "saas", "cloud", "data", "application", "automation",
     "platform", "tech", "technology", "technologie", "informatique"),
    ("logistics", "logistique", "transport", "transportation", "freight", "fret", "shipping", "delivery",
     "livraison", "supply", "fleet", "warehouse"),
    ("energy", "energie", "renewable", "renouvelable", "solar", "solaire", "storage", "decarbonization"),
    ("health", "healthcare", "sante", "medical", "medtech", "hospital", "care", "clinic"),
    ("tourism", "tourisme", "travel", "voyage", "hospitality", "hotel", "hotellerie", "accommodation", "trip"),
    ("consulting", "consultant", "conseil", "advisory", "advisor", "consultancy"),
    ("manufacturing", "manufacturer", "industrial", "industrie", "industry", "factory", "usine", "automotive",
     "automobile", "machining", "assembly", "equipment"),
    ("setup", "establishment", "implantation", "incorporation", "subsidiary", "filiale", "market", "entry"),
    ("marketing", "communication", "branding", "advertising", "publicite", "promotion", "campaign"),
    ("legal", "juridique", "law", "lawyer", "avocat", "compliance", "regulatory"),
    ("networking", "reseautage", "network", "reseau", "meet", "rencontre", "matchmaking"),
    ("training", "formation", "workshop", "atelier", "seminar", "seminaire", "course"),
    ("startup", "start", "innovation", "entrepreneur", "venture", "investor", "investisseur"),
    ("luxury", "luxe", "fashion", "mode", "cosmetics", "cosmetique", "beauty", "beaute"),
    ("sustainability", "durable", "environment", "environnement", "green", "responsible", "responsable", "esg"),
)
_CONCEPT_OF = {word: group[0] for group in _CONCEPTS for word in group}

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


# ---------- Text and terms ----------

def split_sectors(value: str | None) -> list[str]:
    return [part.strip() for part in re.split(r"[;,/|]", value or "") if part.strip()]


def member_text(member: Member) -> str:
    """What the company does. Contact people are left out on purpose: a finance company with
    an "HR Director" is not an HR provider, and job titles would match needs they do not serve."""
    place = ", ".join(part for part in (member.region, member.country) if part)
    secondary = ", ".join(split_sectors(member.secondary_sectors))
    parts = [member.name, f"Sector: {member.sector}" if member.sector else "",
             f"Also active in: {secondary}" if secondary else "", f"Location: {place}" if place else "",
             member.description or ""]
    return ". ".join(part.strip() for part in parts if part and part.strip())[:4000]


def event_text(event: Event) -> str:
    parts = [event.title, f"Format: {event.category}", f"Location: {event.location}" if event.location else "",
             event.description or ""]
    return ". ".join(part.strip() for part in parts if part and part.strip())[:4000]


def _plain(text: str) -> str:
    """Lowercase without accents, so 'Énergie' and 'energie' are the same word."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _stem(word: str) -> str:
    for suffix in ("isations", "izations", "isation", "ization", "ments", "ment", "ings", "ing", "ies", "ers",
                   "ives", "ive", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def terms(text: str) -> set[str]:
    """What a text talks about: its stemmed words, plus the concept each belongs to.

    Keeping both means "solar" matches a company that says "solar" more strongly than one that
    only says "energy", while "distributeur" still meets "distribution" through the concept.
    """
    found = set()
    for word in re.findall(r"[a-z0-9&]+", _plain(text)):
        if word in _STOP or (len(word) < 3 and word not in {"hr", "rh", "it", "ai"}):
            continue
        stem = _stem(word)
        found.add(stem)
        concept = _CONCEPT_OF.get(word) or _CONCEPT_OF.get(word.rstrip("s")) or _CONCEPT_OF.get(stem)
        if concept:
            found.add(concept)
    return found


def wording_scores(need: str, documents: dict[str, str]) -> dict[str, tuple[float, list[str]]]:
    """For each document, the IDF-weighted share of the need's terms it contains, and those terms.

    Rare terms count more than common ones, so "solar" outweighs "energy" when both appear in
    the need and many companies mention energy. Terms no document contains are left out of the
    share: they cannot tell documents apart, and filler words would otherwise dilute every score.
    """
    wanted = terms(need)
    if not wanted or not documents:
        return {key: (0.0, []) for key in documents}
    doc_terms = {key: terms(text) for key, text in documents.items()}
    total = len(documents)
    frequency = {term: sum(1 for found in doc_terms.values() if term in found) for term in wanted}
    idf = {term: math.log(1 + (total - count + 0.5) / (count + 0.5)) for term, count in frequency.items() if count}
    weight = sum(idf.values()) or 1.0
    return {key: (sum(idf[term] for term in wanted & found if term in idf) / weight, sorted(wanted & found))
            for key, found in doc_terms.items()}


def fuse(semantic: dict[str, float], wording: dict[str, tuple[float, list[str]]], bonus: dict[str, float] | None = None,
         k: int = 60) -> list[str]:
    """Reciprocal rank fusion of the embedding ranking and the wording ranking, plus a structural bonus."""
    by_meaning = sorted(semantic, key=lambda key: -semantic[key])
    worded = [key for key in semantic if wording.get(key, (0.0, []))[0] > 0]
    by_wording = sorted(worded, key=lambda key: (-wording[key][0], -semantic[key]))
    score = {key: 1 / (k + rank) for rank, key in enumerate(by_meaning, start=1)}
    for rank, key in enumerate(by_wording, start=1):
        score[key] += 1 / (k + rank)
    for key, extra in (bonus or {}).items():
        if key in score:
            score[key] += extra
    return sorted(score, key=lambda key: (-score[key], -semantic[key]))


_CONCEPT_HEADS = {group[0] for group in _CONCEPTS}


def cover_facets(ranked: list[str], wording: dict[str, tuple[float, list[str]]], need: str, limit: int) -> list[str]:
    """The best `limit` matches, with every activity the need names represented.

    "A food company with distribution experience" names two activities. Ranking alone can
    give every place to food companies, and the distributors that could carry the product
    are never shown. When the top results miss an activity that other companies match, the
    lowest-ranked of them give way to the best companies for that activity (two places at
    most, one when fewer than four are shown). The order stays: best overall first.
    """
    chosen = ranked[:limit]
    facets = [term for term in sorted(terms(need)) if term in _CONCEPT_HEADS]
    if len(facets) < 2 or len(ranked) <= limit or limit < 2:
        return chosen

    def covers(rid: str, facet: str) -> bool:
        return facet in wording.get(rid, (0.0, []))[1]

    room = 2 if limit >= 4 else 1
    for facet in facets:
        if room <= 0 or any(covers(rid, facet) for rid in chosen):
            continue
        for candidate in [rid for rid in ranked[limit:] if covers(rid, facet)][:room]:
            # Give up the lowest-ranked match that is not the only one for another activity.
            for position in range(len(chosen) - 1, 0, -1):
                leaving = chosen[position]
                others = [rid for rid in chosen if rid != leaving]
                if all(not covers(leaving, other) or any(covers(rid, other) for rid in others) for other in facets):
                    chosen = others + [candidate]
                    room -= 1
                    break
    return chosen


def is_relevant(score: float, wording: float) -> bool:
    return score >= RELEVANT_SCORE or (wording >= RELEVANT_WORDING and score >= WORDING_FLOOR)


# ---------- Index ----------

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
    rows = (await session.exec(select(Event).where(Event.start_at >= japan_now(), col(Event.cancelled).is_(False)))).all()
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


# ---------- Explanations ----------

# Directory descriptions often start with these generated lines; they are fields, not prose.
_FIELD_LINE = re.compile(r"^(sector|listed location|website|location|format|secondary sectors|communication language|contacts)\s*:", re.I)


def why_excerpt(need: str, description: str | None, limit: int = 220) -> str:
    """The description sentence sharing most concepts with the need (a reason, not a guess).

    Empty when the description has no prose beyond directory fields.
    """
    if not description:
        return ""
    wanted = terms(need)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", description)
                 if len(s.strip()) > 20 and not _FIELD_LINE.match(s.strip())]
    if not sentences:
        return ""
    best = max(sentences, key=lambda s: len(wanted & terms(s)))
    return best[:limit] + ("…" if len(best) > limit else "")


# ---------- Sectors ----------

def sectors_of(members: Iterable) -> list[str]:
    """Every primary and secondary sector of these companies, most used first."""
    counts: dict[str, int] = {}
    for member in members:
        for name in [getattr(member, "sector", None), *split_sectors(getattr(member, "secondary_sectors", None))]:
            if name:
                counts[name] = counts.get(name, 0) + 1
    return sorted(counts, key=lambda name: (-counts[name], name))


async def directory_sectors(session: AsyncSession) -> list[str]:
    """Every primary and secondary sector in the directory, most used first."""
    rows = (await session.exec(select(Member.sector, Member.secondary_sectors))).all()
    return sectors_of(type("Row", (), {"sector": sector, "secondary_sectors": secondary}) for sector, secondary in rows)


def resolve_sectors(wanted: str | None, sectors: Iterable[str]) -> list[str]:
    """The directory sectors a user's wording refers to: 'F&B', 'food', 'agroalimentaire' and
    'Food and Beverage' all resolve to 'Food & Beverage'. Empty when nothing is close."""
    if not wanted or not wanted.strip():
        return []
    sectors = list(sectors)
    plain = {name: _plain(name).replace("&", "and") for name in sectors}
    query = _plain(wanted).replace("&", "and").strip()
    exact = [name for name, value in plain.items() if value == query]
    if exact:
        return exact
    if len(query) <= 3 and query not in {"it", "hr", "rh", "fnb"} and "and" not in query:
        return []  # too short to mean a sector
    found = [name for name, value in plain.items() if len(query) >= 3 and (query in value or value in query)]
    asked = terms(wanted)
    found += [name for name in sectors if name not in found and asked and asked & terms(name)]
    if not found:
        close = difflib.get_close_matches(query, list(plain.values()), n=3, cutoff=0.78)
        found = [name for name, value in plain.items() if value in close]
    return found


def in_sectors(member, sectors: list[str]) -> bool:
    """True when the company's primary or a secondary sector is one of `sectors`."""
    wanted = {name.lower() for name in sectors}
    own = {name.lower() for name in [getattr(member, "sector", None) or "",
                                     *split_sectors(getattr(member, "secondary_sectors", None))] if name}
    return bool(wanted & own)


_in_sectors = in_sectors


# ---------- Matching ----------

async def match_members(session: AsyncSession, need: str, *, limit: int = 5, sector: str | None = None,
                        region: str | None = None, exclude: set[int] | None = None,
                        include_hidden: bool = False) -> list[Match]:
    """Member companies ranked by fit with `need`.

    `sector` is understood loosely (see resolve_sectors) and matches primary or secondary
    sectors; when it names no directory sector it is added to the need instead of returning
    nothing. Companies that asked not to appear in the directory are left out unless
    `include_hidden` (staff tools).
    """
    await ensure_index(session, "members")
    sectors = resolve_sectors(sector, await directory_sectors(session)) if sector else []
    query_text = need if (sectors or not sector) else f"{need}. Sector: {sector}"
    hits = await asyncio.to_thread(_query, "members", query_text, _MAX_CANDIDATES, None)
    pattern = None
    if region and region.strip().lower() not in {"japan", "jp"}:
        from concierge.agents.tools import _member_region_pattern
        pattern = _member_region_pattern(region)
    ids = [int(rid) for rid, *_ in hits if int(rid) not in (exclude or set())]
    members = {m.member_id: m for m in (await session.exec(select(Member).where(col(Member.member_id).in_(ids)))).all()} if ids else {}

    semantic: dict[str, float] = {}
    documents: dict[str, str] = {}
    for rid, score, document, _ in hits:
        member = members.get(int(rid))
        if member is None or (not include_hidden and member.directory_visible is False):
            continue
        if sectors and not _in_sectors(member, sectors):
            continue
        if pattern is not None and not pattern.search(member.region or ""):
            continue
        semantic[rid], documents[rid] = score, document
    if not semantic:
        return []
    wording = wording_scores(query_text, documents)
    # A sector the need itself names is a strong structural signal.
    named = set(resolve_sectors(need, await directory_sectors(session))) if not sectors else set()
    bonus = {rid: 1 / 120 for rid in semantic if named and _in_sectors(members[int(rid)], list(named))}
    order = cover_facets(fuse(semantic, wording, bonus), wording, query_text, limit)

    contacts: dict[int, list[MemberContact]] = {}
    chosen = [int(rid) for rid in order]
    for contact in (await session.exec(select(MemberContact).where(col(MemberContact.member_id).in_(chosen))
                                       .order_by(col(MemberContact.contact_id)))).all():
        if include_hidden or contact.directory_visible is not False:
            contacts.setdefault(contact.member_id, []).append(contact)
    matches = []
    for rid in order:
        member = members[int(rid)]
        place = ", ".join(part for part in (member.region, member.country) if part)
        why = why_excerpt(need, member.description) or " · ".join(
            part for part in (member.sector and f"Sector: {member.sector}", place and f"Location: {place}") if part)
        matches.append(Match(member.member_id, member.name, semantic[rid], why, {
            "sector": member.sector, "secondary_sectors": split_sectors(member.secondary_sectors),
            "region": member.region, "country": member.country,
            "description": member.description, "contact": member.contact, "language": member.language,
            "wording": round(wording[rid][0], 3), "matched_terms": wording[rid][1], "in_sector": bool(sectors),
            "contacts": [{"name": c.name, "salutation": c.salutation, "job_title": c.job_title, "email": c.email,
                          "language": c.language} for c in contacts.get(member.member_id, [])],
        }))
    return matches


async def count_relevant(session: AsyncSession, need: str, sector: str | None = None) -> tuple[int, dict[str, int]]:
    """How many members look relevant, and their sectors (what non-members may see)."""
    matches = await match_members(session, need, limit=60, sector=sector)
    # A company in the sector the user asked for is relevant by definition.
    relevant = [m for m in matches if m.fields.get("in_sector") or is_relevant(m.score, m.fields.get("wording", 0.0))]
    sectors: dict[str, int] = {}
    for match in relevant:
        key = match.fields.get("sector") or "Other"
        sectors[key] = sectors.get(key, 0) + 1
    return len(relevant), dict(sorted(sectors.items(), key=lambda item: -item[1]))


async def match_events(session: AsyncSession, interests: str, *, limit: int = 5) -> list[Match]:
    """Upcoming events ranked by fit with `interests` (meaning and wording, see the module docstring)."""
    await ensure_index(session, "events")
    hits = await asyncio.to_thread(_query, "events", interests, _MAX_CANDIDATES, None)
    ids = [int(rid) for rid, *_ in hits]
    events = {e.event_id: e for e in (await session.exec(select(Event).where(col(Event.event_id).in_(ids)))).all()} if ids else {}
    now = japan_now()
    semantic: dict[str, float] = {}
    documents: dict[str, str] = {}
    for rid, score, document, _ in hits:
        event = events.get(int(rid))
        if event is None or event.start_at is None or event.start_at < now:
            continue
        semantic[rid], documents[rid] = score, document
    wording = wording_scores(interests, documents)
    matches = []
    for rid in fuse(semantic, wording)[:limit]:
        event = events[int(rid)]
        matches.append(Match(event.event_id, event.title, semantic[rid], why_excerpt(interests, event.description), {
            "category": event.category, "location": event.location,
            "start_at": event.start_at.isoformat(timespec="minutes"),
            "reg_deadline": event.reg_deadline.isoformat(timespec="minutes") if event.reg_deadline else None,
            "wording": round(wording[rid][0], 3), "matched_terms": wording[rid][1],
        }))
    return matches


# ---------- Events for a signed-in user ----------

def ready() -> bool:
    """True once the embedding model is loaded (the API warms it up at startup). Callers on the
    request path use this to fall back to plain filters instead of loading a model mid-request."""
    from concierge.agents import tools
    return tools._embed_model is not None


async def user_interest_text(session: AsyncSession, user_id: int) -> str:
    """What we know a user cares about: their company, their onboarding answers, what they booked."""
    from concierge.db.relationaldb.models import Belongs, Booking, BookingStatus, MemberProfile
    parts: list[str] = []
    member = (await session.exec(select(Member).join(Belongs, col(Belongs.member_id) == col(Member.member_id))
                                 .where(Belongs.user_id == user_id))).first()
    if member is not None:
        sectors = ", ".join(filter(None, [member.sector, *split_sectors(member.secondary_sectors)]))
        parts += [f"Sector: {sectors}" if sectors else "", (member.description or "")[:400]]
    profile = await session.get(MemberProfile, user_id)
    if profile is not None:
        parts += [f"Interests: {profile.interests}" if profile.interests else "",
                  f"Goals: {profile.goals.replace('_', ' ')}" if profile.goals else ""]
    booked = (await session.exec(
        select(Event.title).join(Booking, col(Booking.event_id) == col(Event.event_id))
        .where(Booking.user_id == user_id, Booking.status == BookingStatus.confirmed)
        .order_by(col(Event.start_at).desc()).limit(5))).all()
    if booked:
        parts.append("Attended: " + "; ".join(booked))
    return ". ".join(part.strip() for part in parts if part and part.strip())


async def events_for_user(session: AsyncSession, user_id: int, exclude: set[int], limit: int = 2) -> list[Event] | None:
    """Upcoming events that fit the user's company, stated interests and past bookings, registration
    still open. None when there is nothing to go on or the index is not ready (caller falls back)."""
    if not ready():
        return None
    text = await user_interest_text(session, user_id)
    if not text:
        return None
    now = japan_now()
    matches = await match_events(session, text, limit=limit + len(exclude) + 6)
    ids = [m.id for m in matches if m.id not in exclude and is_relevant(m.score + 0.03, m.fields.get("wording", 0.0))]
    if not ids:
        return None
    events = {e.event_id: e for e in (await session.exec(select(Event).where(col(Event.event_id).in_(ids)))).all()}
    open_events = [events[i] for i in ids if i in events and (events[i].reg_deadline is None or events[i].reg_deadline > now)]
    return open_events[:limit] or None
