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
