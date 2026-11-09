"""Staff automation: engagement rules, running the job (with a simulated date), the outbox,
member engagement, and approving memberships (which starts onboarding)."""

import logging
from datetime import date, datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import col, select

from concierge import engagement
from concierge.auth.access import require_staff
from concierge.auth.dependencies import DatabaseSession
from concierge.db.relationaldb.datetimes import japan_now
from concierge.db.relationaldb.models import AutomationRun, Member, Outreach, OutreachStatus, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_staff)])


@router.get("/automation/rules")
async def list_rules(session: DatabaseSession):
    settings = await engagement.rule_settings(session)
    await session.commit()
    return [{"key": rule.key, "title": rule.title, "description": rule.description,
             "enabled": settings[rule.key].enabled, "requires_approval": settings[rule.key].requires_approval,
             "staff_task": rule.staff_task} for rule in engagement.RULES]


class RuleUpdate(BaseModel):
    enabled: Optional[bool] = None
    requires_approval: Optional[bool] = None


@router.patch("/automation/rules/{rule_key}")
async def update_rule(rule_key: str, body: RuleUpdate, session: DatabaseSession):
    settings = await engagement.rule_settings(session)
    if rule_key not in settings:
        raise HTTPException(404, "Unknown rule.")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(settings[rule_key], field, value)
    await session.commit()
    return {"key": rule_key, "enabled": settings[rule_key].enabled, "requires_approval": settings[rule_key].requires_approval}


class RunRequest(BaseModel):
    as_of: Optional[date] = None  # simulate a day; default today


@router.post("/automation/run")
async def run_now(body: RunRequest, session: DatabaseSession):
    # Keep the current Japan time of day, so "today" means now and "+60 days" means exactly 60 days later.
    as_of = datetime.combine(body.as_of, japan_now().time()) if body.as_of else None
    # A future date is a simulation: show what would fire, but save and send nothing.
    dry_run = body.as_of is not None and body.as_of > japan_now().date()
    result = await engagement.run_engagement(session, as_of, triggered_by="staff", dry_run=dry_run)
    names = {u.user_id: u.name for u in (await session.exec(select(User).where(
        col(User.user_id).in_({item["user_id"] for item in result["items"]})))).all()} if result["items"] else {}
    for item in result["items"]:
        item["member"] = names.get(item["user_id"])
    return result


@router.get("/automation/runs")
async def list_runs(session: DatabaseSession, response: Response):
    rows = (await session.exec(select(AutomationRun).order_by(col(AutomationRun.run_id).desc()).limit(20))).all()
    response.headers["Cache-Control"] = "no-store"
    return [{"run_id": r.run_id, "as_of": r.as_of.isoformat(timespec="minutes"), "triggered_by": r.triggered_by,
             "created": r.created_count, "duplicates": r.duplicate_count, "created_at": r.created_at.isoformat(timespec="seconds")}
            for r in rows]


@router.get("/outreach")
async def list_outreach(session: DatabaseSession, response: Response, status: Optional[OutreachStatus] = None,
                        limit: Annotated[int, Query(ge=1, le=200)] = 100):
    query = select(Outreach, User).join(User, User.user_id == Outreach.user_id)
    if status is not None:
        query = query.where(Outreach.status == status)
    rows = (await session.exec(query.order_by(col(Outreach.outreach_id).desc()).limit(limit))).all()
    response.headers["Cache-Control"] = "no-store"
    return [{"outreach_id": o.outreach_id, "member": u.name, "email": u.email, "rule": o.rule_key,
             "rule_title": engagement.RULE_BY_KEY[o.rule_key].title if o.rule_key in engagement.RULE_BY_KEY else o.rule_key,
             "channel": o.channel, "subject": o.subject, "body": o.body, "reason": o.reason, "status": o.status,
             "created_at": o.created_at.isoformat(timespec="minutes"),
             "sent_at": o.sent_at.isoformat(timespec="minutes") if o.sent_at else None,
             "read_at": o.read_at.isoformat(timespec="minutes") if o.read_at else None} for o, u in rows]


class OutreachAction(BaseModel):
    action: str = Field(pattern="^(send|skip|save)$")
    subject: Optional[str] = Field(default=None, max_length=255)
    body: Optional[str] = Field(default=None, max_length=20_000)


@router.patch("/outreach/{outreach_id}")
async def act_on_outreach(outreach_id: int, body: OutreachAction, session: DatabaseSession):
    outreach = await session.get(Outreach, outreach_id)
    if outreach is None:
        raise HTTPException(404, "Message not found.")
    if outreach.status != OutreachStatus.pending:
        raise HTTPException(409, "Only pending messages can be changed.")
    if body.subject is not None:
        outreach.subject = body.subject.strip() or outreach.subject
    if body.body is not None:
        outreach.body = body.body.strip() or outreach.body
    if body.action == "send":
        await engagement.deliver(session, outreach)
    elif body.action == "skip":
        outreach.status = OutreachStatus.skipped
    session.add(outreach)
    await session.commit()
    return {"outreach_id": outreach_id, "status": outreach.status}


class RewriteRequest(BaseModel):
    language: str = Field(default="en", pattern="^(en|fr|ja)$")


@router.post("/outreach/{outreach_id}/rewrite")
async def rewrite_outreach(outreach_id: int, body: RewriteRequest, session: DatabaseSession):
    """Let the model polish or translate a pending draft; the facts must stay the same."""
    outreach = await session.get(Outreach, outreach_id)
    if outreach is None or outreach.status != OutreachStatus.pending:
        raise HTTPException(409, "Only pending messages can be rewritten.")
    language = {"en": "English", "fr": "French", "ja": "Japanese"}[body.language]
    prompt = (f"Rewrite this message from the CCIFJ (French Chamber of Commerce and Industry in Japan) to a member in "
              f"{language}. Keep it warm, short and professional. Keep every fact, name, date and event exactly; add no new "
              f"facts. Return the subject on the first line, then a blank line, then the body. Plain text.\n\n"
              f"Subject: {outreach.subject}\n\n{outreach.body}")
    try:
        from concierge.agents.model import llm
        reply = await llm.ainvoke(prompt)
    except Exception as exc:
        logger.exception("Rewriting outreach %s failed", outreach_id)
        raise HTTPException(503, "The message could not be rewritten right now.") from exc
    text = (reply.content if isinstance(reply.content, str) else "").strip()
    subject, _, rest = text.partition("\n")
    return {"subject": subject.removeprefix("Subject:").strip()[:255] or outreach.subject, "body": rest.strip() or outreach.body}


@router.get("/engagement")
async def member_engagement(session: DatabaseSession, response: Response):
    as_of = japan_now()
    states = await engagement.member_states(session, as_of)
    await session.commit()  # profiles created on first sight
    sent = dict((await session.exec(select(Outreach.user_id, func.count()).where(
        Outreach.status == OutreachStatus.sent).group_by(Outreach.user_id))).all())
    response.headers["Cache-Control"] = "no-store"
    rows = [{
        "user_id": s.user.user_id, "name": s.user.name, "email": s.user.email,
        "company": s.company.name if s.company else None,
        "joined_at": s.profile.joined_at.isoformat(timespec="minutes") if s.profile.joined_at else None,
        "profile_completed": s.profile.profile_completed_at is not None,
        "goals": engagement.split(s.profile.goals), "channel": s.profile.preferred_channel,
        "last_activity": s.last_activity.isoformat(timespec="minutes"), "last_activity_kind": s.last_activity_kind,
        "days_inactive": s.days_inactive(as_of), "score": engagement.engagement_score(s, as_of),
        "stage": engagement.stage(s, as_of), "messages_sent": sent.get(s.user.user_id, 0),
    } for s in states]
    return sorted(rows, key=lambda row: (row["stage"] != "at risk", row["score"]))


class ApproveMembership(BaseModel):
    email: str = Field(min_length=3, max_length=255)


@router.post("/members/{member_id}/accounts")
async def approve_membership(member_id: int, body: ApproveMembership, session: DatabaseSession):
    """Link an existing account to this member company and start its onboarding (welcome sent now)."""
    member = await session.get(Member, member_id)
    if member is None:
        raise HTTPException(404, "Member company not found.")
    user = (await session.exec(select(User).where(func.lower(User.email) == body.email.strip().lower()))).first()
    if user is None:
        raise HTTPException(404, "No account uses this email. Ask the person to create an account first.")
    result = await engagement.approve_membership(session, user, member)
    return {"user_id": user.user_id, "member": member.name, "welcome": result["items"]}
