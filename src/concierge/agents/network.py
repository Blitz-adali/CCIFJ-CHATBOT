"""The member network: directory matching and member engagement.

Both extra agents work on the hackathon's FICTIONAL data only:

- ``data/network/fictional_members.csv``: the 50 fictional companies and their
  contacts supplied by CCIFJ (company rows carry a description; contact rows
  carry a name, job title, and communication language).
- ``data/network/fictional_member_engagement.json``: fictional join dates,
  goals, and activity for some of those members, written for the prototype.

Everything here is plain, deterministic code so it can be tested without a
model: the agents call it through the tools in ``tools.py`` and only write the
explanations and the draft messages.
"""

from __future__ import annotations

import csv
import json
import math
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "network"
MEMBERS_PATH = DATA_PATH / "fictional_members.csv"
ENGAGEMENT_PATH = DATA_PATH / "fictional_member_engagement.json"
DIRECTORY_NAME = "CCIFJ member directory (fictional demo data)"
ENGAGEMENT_NAME = "Member engagement records (fictional demo data)"


# --- Directory ----------------------------------------------------------------

@dataclass(frozen=True)
class Contact:
    name: str
    salutation: str
    job_title: str
    email: str
    language: str


@dataclass(frozen=True)
class Company:
    name: str
    email: str
    country: str
    primary_sector: str
    secondary_sectors: str
    membership_type: str
    language: str
    description: str
    contacts: tuple[Contact, ...] = field(default_factory=tuple)

    @property
    def search_text(self) -> str:
        return f"{self.primary_sector}. {self.secondary_sectors}. {self.description}"


def _shown(row: dict) -> bool:
    """Only profiles their owner agreed to display in the directory are ever used."""
    return str(row.get("Display profile in the online directory", "")).strip().upper() == "TRUE"


@lru_cache(maxsize=1)
def load_directory() -> tuple[Company, ...]:
    with open(MEMBERS_PATH, encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if _shown(row)]
    contacts: dict[str, list[Contact]] = {}
    for row in rows:
        name = row["Full name"].strip()
        if "," not in name:
            continue
        company, person = (part.strip() for part in name.split(",", 1))
        contacts.setdefault(company, []).append(Contact(
            name=person, salutation=row["Salutation"].strip(), job_title=row["Job title"].strip(),
            email=row["Email"].strip(), language=row["Communication language"].strip(),
        ))
    companies = []
    for row in rows:
        name = row["Full name"].strip()
        if "," in name:
            continue
        companies.append(Company(
            name=name, email=row["Email"].strip(), country=row["Country"].strip(),
            primary_sector=row["Primary sector"].strip(), secondary_sectors=row["Secondary sector(s)"].strip(),
            membership_type=row["Membership type"].strip().removeprefix("Membership - "),
            language=row["Communication language"].strip(), description=row["Description"].strip(),
            contacts=tuple(contacts.get(name, ())),
        ))
    return tuple(companies)


def find_company(name: str) -> Optional[Company]:
    wanted = _normal(name)
    digits = re.findall(r"\d+", name)
    for company in load_directory():
        if _normal(company.name) == wanted:
            return company
    if digits:  # "company 7" -> "COMPANY 07"
        for company in load_directory():
            if re.findall(r"\d+", company.name) and int(re.findall(r"\d+", company.name)[0]) == int(digits[0]):
                return company
    return None


def find_contact(reference: str) -> Optional[tuple[Company, Contact]]:
    """A contact by email, or by name (optionally with the company)."""
    wanted = reference.strip().casefold()
    for company in load_directory():
        for contact in company.contacts:
            if contact.email.casefold() == wanted:
                return company, contact
    words = set(_words(reference))
    best: Optional[tuple[int, Company, Contact]] = None
    for company in load_directory():
        for contact in company.contacts:
            score = len(words & set(_words(contact.name)))
            if score and _normal(company.name) in _normal(reference):
                score += 2
            if score >= 2 and (best is None or score > best[0]):
                best = (score, company, contact)
    return (best[1], best[2]) if best else None


def sectors() -> list[str]:
    found: dict[str, None] = {}
    for company in load_directory():
        found.setdefault(company.primary_sector)
        for sector in company.secondary_sectors.split(","):
            if sector.strip():
                found.setdefault(sector.strip())
    return list(found)


# --- Matching -----------------------------------------------------------------

_STOP_WORDS = {
    "a", "an", "and", "the", "of", "for", "to", "in", "on", "with", "that", "who", "is", "are", "we", "i", "my", "our",
    "company", "companies", "member", "members", "find", "looking", "look", "need", "want", "search", "japan", "japanese",
    "french", "france", "fictional", "specialising", "offerings", "include", "its", "it", "serves", "based", "their",
    "international", "partners", "can", "could", "me", "us", "some", "any", "good", "well", "knows", "experience",
}
# Words people use for a need, mapped to the directory's own sector words.
_SYNONYMS = {
    "food": ["food", "beverage", "restaurants", "culinary", "catering", "ingredients"],
    "agrifood": ["food", "beverage", "ingredients", "products"],
    "agroalimentaire": ["food", "beverage", "ingredients"],
    "drink": ["beverage"], "wine": ["beverage", "food"], "restaurant": ["restaurants", "food", "catering"],
    "distribution": ["distribution", "retail", "trade", "trading", "logistics", "supply"],
    "distributor": ["distribution", "retail", "trade", "trading"], "importer": ["trading", "distribution", "trade", "logistics"],
    "retail": ["retail", "trade", "distribution", "shops"], "shop": ["retail", "shops"],
    "car": ["automotive", "vehicles"], "automobile": ["automotive", "vehicles"], "mobility": ["automotive", "transportation", "fleet"],
    "it": ["technology", "software", "cloud", "data"], "tech": ["technology", "software"], "digital": ["software", "digital", "technology"],
    "ai": ["data", "software", "analytics"], "saas": ["software", "cloud"],
    "bank": ["banking", "finance", "financial"], "fintech": ["finance", "financial", "banking", "digital"],
    "health": ["healthcare", "medical"], "medtech": ["medical", "devices", "healthcare"], "pharma": ["healthcare", "medical"],
    "energy": ["energy", "renewable", "solar"], "green": ["renewable", "energy", "responsible"], "solar": ["solar", "renewable"],
    "travel": ["tourism", "hospitality", "trips"], "hotel": ["hospitality", "accommodation", "hotels"], "tourism": ["tourism", "hospitality"],
    "shipping": ["logistics", "transportation", "freight"], "freight": ["freight", "logistics", "transportation"],
    "consulting": ["consulting", "services"], "hr": ["hr", "training", "organisational"], "recruitment": ["hr"],
    "setup": ["establishment", "support", "administrative"], "implantation": ["establishment", "support"],
    "marketing": ["promotional", "branding", "campaign", "sales"], "sales": ["sales", "prospecting", "b2b"],
    "manufacturing": ["manufacturing", "industrial", "machining", "assembly"], "factory": ["manufacturing", "industrial", "workshops"],
}


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def _words(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9][a-z0-9&\-]*", text.casefold()) if word not in _STOP_WORDS and len(word) > 1]


def _stem(word: str) -> str:
    for ending in ("ies", "es", "s", "ing"):
        if len(word) > 4 and word.endswith(ending):
            return word[: -len(ending)]
    return word


def _expanded(need: str) -> set[str]:
    terms: set[str] = set()
    for word in _words(need):
        terms.add(_stem(word))
        for synonym in _SYNONYMS.get(word, []) + _SYNONYMS.get(_stem(word), []):
            terms.add(_stem(synonym))
    return terms


def keyword_score(need: str, company: Company) -> tuple[float, list[str]]:
    """Share of the need's terms found in the record, and the record's own matching words."""
    terms = _expanded(need)
    if not terms:
        return 0.0, []
    record_words = {_stem(word): word for word in _words(company.search_text)}
    hits = [record_words[term] for term in terms if term in record_words]
    sector_words = {_stem(word) for word in _words(f"{company.primary_sector} {company.secondary_sectors}")}
    bonus = 0.25 if terms & sector_words else 0.0
    return min(1.0, len(hits) / max(3, len(terms)) * 2 + bonus), sorted(set(hits))


_embeddings: Optional[list[list[float]]] = None
_embeddings_lock = threading.Lock()


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def semantic_scores(need: str, encode: Callable[[list[str]], list]) -> list[float]:
    """Meaning-based similarity of the need to each company (the directory is embedded once)."""
    global _embeddings
    companies = load_directory()
    with _embeddings_lock:
        if _embeddings is None:
            _embeddings = [list(map(float, vector)) for vector in encode([f"passage: {c.search_text}" for c in companies])]
    query = list(map(float, encode([f"query: {need}"])[0]))
    return [_cosine(query, vector) for vector in _embeddings]


@dataclass(frozen=True)
class Match:
    company: Company
    score: float
    matched_words: tuple[str, ...]


def match_companies(
    need: str, sector: str = "", limit: int = 5, encode: Optional[Callable[[list[str]], list]] = None,
) -> list[Match]:
    """Rank the directory for a business need. ``encode`` adds meaning-based search;
    without it (or if it fails) the ranking uses the records' own words only."""
    companies = load_directory()
    semantic = [0.0] * len(companies)
    if encode is not None:
        try:
            raw = semantic_scores(need, encode)
            low, high = min(raw), max(raw)
            semantic = [(value - low) / (high - low) if high > low else 0.0 for value in raw]
        except Exception:
            semantic = [0.0] * len(companies)
    wanted_sector = _normal(sector)
    ranked = []
    for company, meaning in zip(companies, semantic):
        if wanted_sector and wanted_sector not in _normal(f"{company.primary_sector} {company.secondary_sectors}"):
            continue
        keywords, hits = keyword_score(need, company)
        score = 0.55 * keywords + 0.45 * meaning if encode is not None else keywords
        ranked.append(Match(company, round(score, 3), tuple(hits)))
    ranked.sort(key=lambda match: (-match.score, match.company.name))
    ranked = [match for match in ranked if match.score > 0.12 or (wanted_sector and not need.strip())]
    limit = max(1, min(limit, 10))
    top = ranked[:limit]
    # A need often has two sides ("a food company that knows distribution"). Make
    # sure each sector the request clearly names is represented, not only the first.
    if limit >= 3:
        present = {match.company.primary_sector for match in top}
        for match in ranked[limit:]:
            if match.company.primary_sector in present:
                continue
            if keyword_score(need, match.company)[0] >= 0.7:
                top[-1] = match
                break
        top.sort(key=lambda match: (-match.score, match.company.name))
    # Companies the request names are always included, first.
    named = [company for company in companies if _normal(company.name) in _normal(need)]
    for company in reversed(named):
        top = [match for match in top if match.company.name != company.name]
        keywords, hits = keyword_score(need, company)
        top.insert(0, Match(company, 1.0, tuple(hits)))
    return top[: max(limit, len(named))]


def describe_match(match: Match) -> str:
    """One directory record as evidence for a match: every line is copied from the record."""
    company = match.company
    lines = [
        f"{company.name} | {company.primary_sector} / {company.secondary_sectors} | {company.country} | {company.membership_type}",
        f"  Directory description: {company.description}",
    ]
    if match.matched_words:
        lines.append(f"  Words of the request found in this record: {', '.join(match.matched_words)}")
    for contact in company.contacts:
        person = " ".join(part for part in (contact.salutation, contact.name) if part)
        lines.append(f"  Contact: {person}, {contact.job_title} <{contact.email}> (prefers {contact.language})")
    return "\n".join(lines)


# --- Engagement ---------------------------------------------------------------

@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    why: str      # the rule that put the member here, with its numbers
    action: str   # what the follow-up should do
    priority: int  # lower = more urgent


@lru_cache(maxsize=1)
def load_engagement() -> tuple[dict, ...]:
    with open(ENGAGEMENT_PATH, encoding="utf-8") as handle:
        return tuple(json.load(handle)["members"])


def _days(day: Optional[str], today: date) -> Optional[int]:
    if not day:
        return None
    return (today - datetime.strptime(day, "%Y-%m-%d").date()).days


def engagement_stage(record: dict, today: date) -> Stage:
    """Where a member is in their first two years, from time since joining and activity.

    The rules are fixed and read top to bottom; the first one that applies wins.
    """
    joined = _days(record.get("joined_on"), today) or 0
    attended = len(record.get("events_attended") or [])
    used = len(record.get("services_used") or [])
    idle = _days(record.get("last_activity_on"), today)
    logged_in = bool(record.get("member_area_login"))

    if joined <= 14:
        return Stage("welcome", "Welcome", f"joined {joined} days ago (14 days or less)",
                     "Welcome them, ask about their objectives, and explain the member area and the directory.", 2)
    if joined > 60 and idle is not None and idle >= 90:
        return Stage("inactive", "Inactive: re-engage", f"joined {joined} days ago and no activity for {idle} days (90 or more)",
                     "Re-engage: acknowledge the silence, offer one concrete, low-effort step that fits their goal, and offer a call with the membership team.", 1)
    if joined <= 60 and attended == 0:
        return Stage("first_event", "No event yet", f"joined {joined} days ago and has attended no event",
                     "Recommend ONE upcoming event that fits their goal and explain why it fits.", 1)
    if joined <= 60 and not logged_in:
        return Stage("member_area", "Member area not used", f"joined {joined} days ago and has never signed in to the member area",
                     "Explain how to sign in to the member area and use the directory, and why it helps their goal.", 2)
    if joined <= 180 and used == 0:
        return Stage("discover_services", "No service used yet", f"joined {joined} days ago, attended {attended} event(s), used no CCIFJ service",
                     "Recommend ONE CCIFJ service that fits their goal and explain why it fits.", 3)
    if 330 <= joined <= 395:
        return Stage("first_anniversary", "First-year check-in", f"joined {joined} days ago (about one year)",
                     "Review their first year against their goal and propose a plan for the second one.", 2)
    return Stage("active", "Active", f"joined {joined} days ago, {attended} event(s), {used} service(s), last activity {idle if idle is not None else '?'} days ago",
                 "Deepen the relationship: a committee, speaking, sponsoring, or an introduction to another member.", 5)


def find_engagement(reference: str) -> Optional[dict]:
    wanted = reference.strip().casefold()
    for record in load_engagement():
        if wanted in {record["email"].casefold(), record["contact"].casefold()}:
            return record
    words = set(_words(reference))
    company = find_company(reference)
    for record in load_engagement():
        if len(words & set(_words(record["contact"]))) >= 2:
            return record
    if company:
        for record in load_engagement():
            if record["company"] == company.name:
                return record
    return None


def describe_engagement(record: dict, today: date) -> str:
    stage = engagement_stage(record, today)
    company = find_company(record["company"])
    contact = find_contact(record["email"])
    lines = [
        f"{record['contact']} | {record['company']} | <{record['email']}>",
        f"  Job title: {contact[1].job_title if contact else 'unknown'} | Preferred language: {contact[1].language if contact else 'unknown'}",
        f"  Company: {company.primary_sector} / {company.secondary_sectors} | {company.membership_type}" if company else "  Company: not in the directory",
        f"  Directory description: {company.description}" if company else "",
        f"  Joined: {record['joined_on']} ({_days(record['joined_on'], today)} days ago)",
        f"  Goal stated when joining: {record.get('goal') or 'not stated'}",
        f"  Interests: {', '.join(record.get('interests') or []) or 'none recorded'}",
        f"  Events attended: {', '.join(record.get('events_attended') or []) or 'none'}",
        f"  Services used: {', '.join(record.get('services_used') or []) or 'none'}",
        f"  Member area: {'has signed in' if record.get('member_area_login') else 'never signed in'}",
        f"  Last activity: {record.get('last_activity_on') or 'none'}",
        f"  STAGE: {stage.label}. Rule: {stage.why}.",
        f"  What the follow-up should do: {stage.action}",
    ]
    return "\n".join(line for line in lines if line)


def members_to_follow_up(today: date, stage_key: str = "") -> list[tuple[dict, Stage]]:
    """Members who need a follow-up now, most urgent first. Active members are left alone."""
    due = [(record, engagement_stage(record, today)) for record in load_engagement()]
    due = [(record, stage) for record, stage in due if stage.key != "active"]
    if stage_key:
        due = [(record, stage) for record, stage in due if stage.key == stage_key]
    return sorted(due, key=lambda item: (item[1].priority, item[0]["joined_on"]))
