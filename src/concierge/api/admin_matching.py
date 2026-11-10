"""Staff matching tools: find members for a need or an event, draft matchmaking
proposals, and handle members' introduction requests. Every route needs staff access.

Nothing is emailed: proposals are drafts for staff to copy, edit and send themselves.
"""

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlmodel import col, select

from concierge import matching
from concierge.auth.access import require_staff
from concierge.auth.dependencies import DatabaseSession
from concierge.db.relationaldb.models import Event, IntroRequest, IntroStatus, Member, User, utcnow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_staff)])


@router.get("/matching/members")
async def match_members(
    session: DatabaseSession, response: Response,
    need: Annotated[str, Query(min_length=3, max_length=1000)],
    sector: Annotated[str, Query(max_length=100)] = "",
    region: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=30)] = 10,
):
    matches = await matching.match_members(session, need, limit=limit, sector=sector or None, region=region or None)
    response.headers["Cache-Control"] = "no-store"
    return {"need": need, "items": [match.as_dict() for match in matches], "relevant_score": matching.RELEVANT_SCORE}


@router.get("/matching/events/{event_id}/members")
async def members_for_event(event_id: int, session: DatabaseSession, response: Response,
                            limit: Annotated[int, Query(ge=1, le=30)] = 10):
    """Who to invite: members whose profile is closest to the event."""
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found.")
    matches = await matching.match_members(session, matching.event_text(event), limit=limit)
    response.headers["Cache-Control"] = "no-store"
    return {"need": event.title, "items": [match.as_dict() for match in matches], "relevant_score": matching.RELEVANT_SCORE}


@router.post("/matching/reindex")
async def reindex(session: DatabaseSession):
    """Re-embed everything (normally the index follows the database by itself)."""
    return {kind: await matching.ensure_index(session, kind, force=True) for kind in ("members", "events")}


class ProposalRequest(BaseModel):
    need: str = Field(min_length=3, max_length=2000)
    requester: str = Field(default="", max_length=300)  # who the introduction is for
    member_ids: list[int] = Field(min_length=1, max_length=8)
    language: str = Field(default="en", pattern="^(en|fr|ja)$")


PROPOSAL_PROMPT = """You are a CCIFJ (French Chamber of Commerce and Industry in Japan) staff member writing a short
matchmaking proposal email. Write it in {language}.

Who the introduction is for: {requester}
What they are looking for: {need}

Member companies selected by staff (use ONLY these facts; do not invent activities, clients, sizes or results):
{companies}

Write: a subject line, a two-sentence opening that restates the need, then one short paragraph per company saying
why it could fit, based only on the facts above (when the facts are only a sector and a place, say so plainly and
suggest a first call to confirm the fit), and a closing that proposes to arrange introductions with each company's
agreement. Sign as "The CCIFJ team". Plain text only, no Markdown."""


def _company_facts(member: Member) -> str:
    place = ", ".join(part for part in (member.region, member.country) if part)
    facts = [f"- {member.name}", f"  Sector: {member.sector or 'unknown'}", f"  Place: {place or 'unknown'}"]
    prose = matching.why_excerpt("", member.description, limit=600)
    if prose:
        facts.append(f"  Description: {prose}")
    return "\n".join(facts)


@router.post("/matching/proposal")
async def draft_proposal(body: ProposalRequest, session: DatabaseSession):
    members = (await session.exec(select(Member).where(col(Member.member_id).in_(body.member_ids)))).all()
    if not members:
        raise HTTPException(404, "None of the selected companies exist.")
    language = {"en": "English", "fr": "French", "ja": "Japanese"}[body.language]
    prompt = PROPOSAL_PROMPT.format(language=language, requester=body.requester.strip() or "a CCIFJ contact",
                                    need=body.need.strip(), companies="\n".join(_company_facts(m) for m in members))
    try:
        from concierge.agents.model import llm
        reply = await llm.ainvoke(prompt)
    except Exception as exc:
        logger.exception("Drafting a matchmaking proposal failed")
        raise HTTPException(503, "The draft could not be written right now. Try again in a moment.") from exc
    text = reply.content if isinstance(reply.content, str) else "".join(
        part.get("text", "") for part in reply.content if isinstance(part, dict))
    return {"draft": text.strip(), "companies": [m.name for m in members]}


# ---------- Event invitations ----------

class InvitationRequest(BaseModel):
    event_id: int
    member_ids: list[int] = Field(min_length=1, max_length=8)
    # "auto" writes each email in the contact's (or company's) preferred language, English otherwise.
    language: str = Field(default="auto", pattern="^(auto|en|fr|ja)$")
    note: str = Field(default="", max_length=1000)  # optional staff note, e.g. a member discount


INVITATION_PROMPT = """You are a CCIFJ (French Chamber of Commerce and Industry in Japan) staff member writing a short,
warm invitation email to a member company, inviting them to a CCIFJ event. Write it in {language}.

Recipient: {recipient}
Member company (use ONLY these facts):
{company}

Event (use ONLY these facts; never invent speakers, agenda items, prices, dates or places):
{event}
{note}
Write, as plain text without Markdown:
- First line exactly: "Subject: <a short, specific subject naming the event>"
- A blank line, then the greeting: {greeting}
- One or two sentences inviting them to the event and saying why it is relevant to THEIR company, based only on the
  company facts above (if the only link is the sector, say so simply; do not exaggerate the fit).
- The practical details on separate lines: date and time, venue, price for members, registration deadline.
  Leave out any line whose information is not given above; never write that something is unknown or to be decided.
- How to register: {register}
- A friendly closing, signed "The CCIFJ team".
Keep it under 180 words. Do not promise anything that is not stated above."""

_LANGUAGE_NAMES = {"en": "English", "fr": "French", "ja": "Japanese"}
_LANGUAGE_CODES = {"english": "en", "anglais": "en", "french": "fr", "français": "fr", "francais": "fr",
                   "japanese": "ja", "japonais": "ja", "日本語": "ja"}


def _language_code(*preferences: Optional[str]) -> str:
    for preference in preferences:
        code = _LANGUAGE_CODES.get((preference or "").strip().casefold())
        if code:
            return code
    return "en"


def _amount(value, currency: Optional[str]) -> str:
    if value == 0:
        return "free"
    number = f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"
    return f"{number} {currency}" if currency else number


def _event_facts(event: Event, languages: list[str], page_url: Optional[str], deadline) -> str:
    when = event.start_at.strftime("%A %d %B %Y, %H:%M") if event.start_at else "date to be confirmed"
    if event.start_at and event.end_at:
        when += event.end_at.strftime("–%H:%M") if event.end_at.date() == event.start_at.date() \
            else event.end_at.strftime(" – %A %d %B %Y, %H:%M")
    prices = [(label, value) for label, value in (("members", event.member_price), ("non-members", event.non_member_price),
                                                  ("standard", event.price)) if value is not None]
    facts = [f"Title: {event.title}", f"Category: {event.category}", f"When (Japan time): {when}",
             f"Venue: {event.location or 'to be confirmed'}",
             "Price: " + ("; ".join(f"{label} {_amount(value, event.currency)}" for label, value in prices) if prices else "not known")]
    if deadline:
        facts.append(f"Registration deadline: {deadline.strftime('%d %B %Y')}")
    if languages:
        facts.append(f"Event language(s): {', '.join(languages)}")
    if page_url:
        facts.append(f"Event page: {page_url}")
    description = matching.why_excerpt("", event.description, limit=700)
    if description:
        facts.append(f"Description: {description}")
    return "\n".join(facts)


def _split_subject(text: str, fallback: str) -> tuple[str, str]:
    lines = text.strip().splitlines()
    if lines and lines[0].casefold().startswith(("subject:", "objet :", "objet:", "件名:", "件名：")):
        subject = lines[0].split(":", 1)[-1].split("：", 1)[-1].strip()
        return subject or fallback, "\n".join(lines[1:]).strip()
    return fallback, text.strip()


@router.post("/matching/invitations")
async def draft_invitations(body: InvitationRequest, session: DatabaseSession):
    """One invitation email per selected member company, addressed to its contact person."""
    import asyncio
    from concierge.db.relationaldb.models import EventLanguage, MemberContact
    from concierge.source_context import effective_deadline, matching_event

    event = await session.get(Event, body.event_id)
    if event is None:
        raise HTTPException(404, "Event not found.")
    members = (await session.exec(select(Member).where(col(Member.member_id).in_(body.member_ids)))).all()
    if not members:
        raise HTTPException(404, "None of the selected companies exist.")
    contacts: dict[int, list[MemberContact]] = {}
    for contact in (await session.exec(select(MemberContact).where(col(MemberContact.member_id).in_(body.member_ids))
                                       .order_by(col(MemberContact.contact_id)))).all():
        contacts.setdefault(contact.member_id, []).append(contact)
    languages = list((await session.exec(select(EventLanguage.language)
                                         .where(EventLanguage.event_id == event.event_id))).all())
    from concierge.db.relationaldb.datetimes import japan_now

    record = matching_event(event) or {}
    page_url = record.get("url")
    deadline = effective_deadline(event)
    now = japan_now()
    warnings = []
    if event.start_at and event.start_at < now:
        warnings.append("This event has already started or taken place.")
    elif deadline and deadline < now:
        warnings.append(f"Registration closed on {deadline.strftime('%d %B %Y')}. "
                        "Check with the organiser before inviting anyone.")
        deadline = None  # never print a past deadline in an invitation
    event_facts = _event_facts(event, languages, page_url, deadline)
    register = (f"invite them to register on the event page ({page_url}) or simply reply to this email."
                if page_url else "invite them to simply reply to this email to register or ask questions.")
    register += (" Also mention that, once signed in, they can book directly with the CCIFJ online assistant "
                 "on the CCIFJ website.")
    note = f"\nStaff note to include naturally: {body.note.strip()}\n" if body.note.strip() else ""

    from concierge.agents.model import llm

    async def draft(member: Member) -> dict:
        people = contacts.get(member.member_id, [])
        person = next((c for c in people if c.email), people[0] if people else None)
        code = body.language if body.language != "auto" else _language_code(
            person.language if person else None, member.language)
        if person:
            name = " ".join(part for part in (person.salutation, person.name) if part)
            recipient = f"{name}{', ' + person.job_title if person.job_title else ''}, at {member.name}"
            greeting = f'address {name} by name, appropriate for {_LANGUAGE_NAMES[code]} business email'
        else:
            recipient = f"the team at {member.name} (no named contact)"
            greeting = f'address "the {member.name} team", appropriate for {_LANGUAGE_NAMES[code]} business email'
        prompt = INVITATION_PROMPT.format(language=_LANGUAGE_NAMES[code], recipient=recipient,
                                          company=_company_facts(member), event=event_facts, note=note,
                                          greeting=greeting, register=register)
        reply = await llm.ainvoke(prompt)
        text = reply.content if isinstance(reply.content, str) else "".join(
            part.get("text", "") for part in reply.content if isinstance(part, dict))
        subject, message = _split_subject(text, f"Invitation: {event.title}")
        return {"member_id": member.member_id, "company": member.name, "language": code,
                "to_name": person.name if person else None, "to_email": person.email if person else None,
                "subject": subject, "body": message}

    try:
        items = await asyncio.gather(*(draft(member) for member in members))
    except Exception as exc:
        logger.exception("Drafting event invitations failed")
        raise HTTPException(503, "The invitations could not be written right now. Try again in a moment.") from exc
    return {"event": event.title, "items": list(items), "warnings": warnings}


# ---------- Introduction requests ----------

class IntroUpdate(BaseModel):
    status: Optional[IntroStatus] = None
    staff_note: Optional[str] = Field(default=None, max_length=4000)


@router.get("/intro-requests")
async def list_intro_requests(session: DatabaseSession, response: Response, status: Optional[IntroStatus] = None,
                              offset: Annotated[int, Query(ge=0)] = 0,
                              limit: Annotated[int, Query(ge=1, le=100)] = 50):
    query = select(IntroRequest)
    if status is not None:
        query = query.where(IntroRequest.status == status)
    rows = list((await session.exec(query.order_by(col(IntroRequest.created_at).desc())
                                    .offset(offset).limit(limit + 1))).all())
    visible = rows[:limit]
    member_ids = {r.target_member_id for r in visible} | {r.requester_member_id for r in visible if r.requester_member_id}
    members = {m.member_id: m for m in (await session.exec(select(Member).where(col(Member.member_id).in_(member_ids)))).all()} if member_ids else {}
    users = {u.user_id: u for u in (await session.exec(select(User).where(
        col(User.user_id).in_({r.requester_user_id for r in visible})))).all()} if visible else {}

    def company(member_id):
        member = members.get(member_id)
        return None if member is None else {"member_id": member.member_id, "name": member.name, "sector": member.sector,
                                            "place": ", ".join(p for p in (member.region, member.country) if p)}
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [{
            "intro_id": r.intro_id, "reference": r.reference, "status": r.status, "message": r.message,
            "staff_note": r.staff_note, "created_at": r.created_at.isoformat() + "Z",
            "requester": {"name": users[r.requester_user_id].name, "email": users[r.requester_user_id].email}
            if r.requester_user_id in users else None,
            "requester_company": company(r.requester_member_id), "target": company(r.target_member_id),
        } for r in visible],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


@router.patch("/intro-requests/{intro_id}")
async def update_intro_request(intro_id: int, body: IntroUpdate, session: DatabaseSession):
    intro = await session.get(IntroRequest, intro_id)
    if intro is None:
        raise HTTPException(404, "Introduction request not found.")
    if body.status is not None:
        intro.status = body.status
    if body.staff_note is not None:
        intro.staff_note = body.staff_note.strip() or None
    intro.updated_at = utcnow()
    session.add(intro)
    await session.commit()
    return {"intro_id": intro_id, "status": intro.status, "staff_note": intro.staff_note}
