"""The signed-in member's own onboarding profile and the messages CCIFJ sent them."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlmodel import col, select

from concierge import engagement
from concierge.auth.access import access_level
from concierge.auth.dependencies import CurrentUser, DatabaseSession
from concierge.db.relationaldb.datetimes import japan_now
from concierge.db.relationaldb.models import Outreach, OutreachStatus

router = APIRouter(prefix="/me", tags=["me"])


def _require(user):
    if user is None:
        raise HTTPException(401, "Sign in first.", headers={"WWW-Authenticate": "Bearer"})
    return user


@router.get("/profile")
async def get_profile(user: CurrentUser, session: DatabaseSession, response: Response):
    _require(user)
    level = (await access_level(session, user)).value
    profile = await engagement.get_profile(session, user.user_id)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {
        "access_level": level, "joined_at": profile.joined_at.isoformat(timespec="minutes") if profile.joined_at else None,
        "goals": engagement.split(profile.goals), "interests": profile.interests or "",
        "preferred_language": profile.preferred_language, "preferred_channel": profile.preferred_channel,
        "profile_completed": profile.profile_completed_at is not None,
        "goal_options": [{"key": key, "label": label} for key, (label, _) in engagement.GOALS.items()],
    }


class ProfileUpdate(BaseModel):
    goals: list[str] = Field(default_factory=list, max_length=6)
    interests: str = Field(default="", max_length=1000)
    preferred_language: str = Field(default="en", pattern="^(en|fr|ja)$")
    preferred_channel: str = Field(default="in_app", pattern="^(in_app|email|line)$")


@router.put("/profile")
async def update_profile(body: ProfileUpdate, user: CurrentUser, session: DatabaseSession):
    _require(user)
    unknown = [goal for goal in body.goals if goal not in engagement.GOALS]
    if unknown:
        raise HTTPException(422, f"Unknown goals: {', '.join(unknown)}")
    profile = await engagement.get_profile(session, user.user_id)
    profile.goals = ",".join(dict.fromkeys(body.goals)) or None
    profile.interests = body.interests.strip() or None
    profile.preferred_language, profile.preferred_channel = body.preferred_language, body.preferred_channel
    profile.profile_completed_at = profile.profile_completed_at or japan_now()
    session.add(profile)
    await session.commit()
    return {"profile_completed": True}


@router.get("/notifications")
async def notifications(user: CurrentUser, session: DatabaseSession, response: Response):
    """Messages CCIFJ sent this member (every channel is also shown in the app)."""
    _require(user)
    rows = (await session.exec(select(Outreach).where(
        Outreach.user_id == user.user_id, Outreach.status == OutreachStatus.sent, Outreach.channel != "staff_task")
        .order_by(col(Outreach.sent_at).desc()).limit(20))).all()
    response.headers["Cache-Control"] = "no-store"
    return [{"id": o.outreach_id, "subject": o.subject, "body": o.body, "sent_at": o.sent_at.isoformat(timespec="minutes"),
             "read": o.read_at is not None} for o in rows]


@router.post("/notifications/{outreach_id}/read", status_code=204)
async def mark_read(outreach_id: int, user: CurrentUser, session: DatabaseSession):
    _require(user)
    outreach: Optional[Outreach] = await session.get(Outreach, outreach_id)
    if outreach is None or outreach.user_id != user.user_id:
        raise HTTPException(404, "Message not found.")
    outreach.read_at = outreach.read_at or japan_now()
    session.add(outreach)
    await session.commit()
    return Response(status_code=204)
