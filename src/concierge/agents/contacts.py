"""CCIFJ contacts, kept as data so staff can change them without touching code.

``data/contacts.json`` holds one entry per topic: a title, the people to
contact (name, role, phone, email), and an optional note. The agents read it
on every lookup, so an edit (in the file, or in the staff view) is used by the
next answer with no restart.
"""

import json
import re
import threading
from pathlib import Path

CONTACTS_PATH = Path(__file__).resolve().parents[1] / "data" / "contacts.json"
TOPICS = (
    "events", "become_speaker", "become_sponsor", "bonjour_france", "membership",
    "corporate_news", "japanese_market_support", "recruitment_and_hr",
)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_lock = threading.Lock()


def load_contacts() -> dict:
    with _lock, open(CONTACTS_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def validate(contacts: dict) -> dict:
    """Return a clean copy, or raise ValueError saying what is wrong."""
    if not isinstance(contacts, dict) or set(contacts) != set(TOPICS):
        raise ValueError(f"Contacts must have exactly these topics: {', '.join(TOPICS)}.")
    clean = {}
    for topic in TOPICS:
        entry = contacts[topic]
        if not isinstance(entry, dict) or not str(entry.get("title") or "").strip():
            raise ValueError(f"{topic}: a title is required.")
        people = entry.get("people")
        if not isinstance(people, list) or not people:
            raise ValueError(f"{topic}: at least one contact is required.")
        clean_people = []
        for person in people:
            if not isinstance(person, dict):
                raise ValueError(f"{topic}: each contact needs a name and an email.")
            name, email = str(person.get("name") or "").strip(), str(person.get("email") or "").strip()
            if not name or not _EMAIL.match(email):
                raise ValueError(f"{topic}: each contact needs a name and a valid email (got {name or '?'} / {email or '?'}).")
            clean_people.append({
                "name": name[:100], "role": str(person.get("role") or "").strip()[:120],
                "phone": str(person.get("phone") or "").strip()[:40], "email": email[:255],
            })
        clean[topic] = {"title": str(entry["title"]).strip()[:200], "people": clean_people, "note": str(entry.get("note") or "").strip()[:300]}
    return clean


def save_contacts(contacts: dict) -> dict:
    clean = validate(contacts)
    with _lock:
        temporary = CONTACTS_PATH.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(CONTACTS_PATH)
    return clean


def describe(topic: str) -> str:
    """The contact details for one topic, as the agents read them."""
    entry = load_contacts().get(topic)
    if not entry:
        return ""
    lines = [f"{entry['title']}:"]
    numbered = len(entry["people"]) > 1
    for index, person in enumerate(entry["people"], start=1):
        indent = "   " if numbered else ""
        lines.append(f"{index}. {person['name']}" if numbered else f"- Name: {person['name']}")
        if person.get("role"):
            lines.append(f"{indent}- Role: {person['role']}")
        if person.get("phone"):
            lines.append(f"{indent}- Phone: {person['phone']}")
        lines.append(f"{indent}- Email: {person['email']}")
    if entry.get("note"):
        lines.append(f"- Note: {entry['note']}")
    return "\n".join(lines)
