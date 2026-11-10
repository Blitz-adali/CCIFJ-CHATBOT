"""Member onboarding and engagement: rules, last activity, message drafts and delivery.

`run_engagement(as_of=...)` looks at every member account and fires the rules below. Rules
are plain code so staff can read and switch them; the matching engine supplies the events,
services and member companies that make each message personal.

Proof that it works is built in:
- Every message is an `Outreach` row with the facts that triggered it (`reason`).
- `dedupe_key` is unique, so running the job twice never sends anything twice.
- `as_of` lets staff simulate a later date (no one waits 60 days in a demo).

Delivery: in-app messages appear in the member's chat; email and LINE go to the mock
outbox (the same rows; production would hand them to GetResponse / LINE); a staff task
becomes a staff request in the existing inbox. Rules that need approval stay `pending`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.db.relationaldb.datetimes import japan_now
from concierge.db.relationaldb.models import (
    ActivityEvent, AutomationRule, AutomationRun, Belongs, Booking, ChatSession, Event, IntroRequest, Member,
    MemberProfile, Message, MessageRole, Outreach, OutreachStatus, SpaceReservation, StaffRequest,
    StaffRequestType, User, UserRole, utcnow,
)

logger = logging.getLogger(__name__)

# Goal key -> (label shown to members, CCIFJ services that serve it).
GOALS: dict[str, tuple[str, list[str]]] = {
    "find_clients": ("Find clients, partners or distributors", [
        "Business Connect: CCIFJ identifies prospects for you and organises meetings",
        "The member directory and introductions to other members through CCIFJ",
        "Sector committees and networking events to meet decision-makers",
    ]),
    "network": ("Grow my network in Japan", [
        "Networking events and the Business Leaders Forum",
        "Sector committees (e.g. Hospitality, Brands Forum)",
        "Introductions to other member companies through CCIFJ",
    ]),
    "recruit": ("Recruit staff", [
        "CCIFJ recruitment services and the CV database",
        "V.I.E international internships",
        "Career forums and HR events",
    ]),
    "visibility": ("Promote my company", [
        "Corporate news in CCIFJ publications and newsletters",
        "Speaking at or sponsoring CCIFJ events",
        "Advertising in France Japon Eco",
    ]),
    "set_up": ("Set up or grow my business in Japan", [
        "Company setup support and domiciliation",
        "Market studies and the Expo+ trade-show programme",
        "Back-office services such as VAT recovery",
    ]),
    "learn": ("Training and market insight", [
        "CCIFJ training programmes and workshops",
        "Breakfast talks and conferences on the Japanese market",
        "Publications and the members' economic news",
    ]),
}
CHANNELS = ("in_app", "email", "line")
LANGUAGES = ("en", "fr", "ja")
NEW_EVENT_DAYS = 14
EVENT_MATCH_SCORE = 0.83


@dataclass(frozen=True)
class Rule:
    key: str
    title: str
    description: str
    requires_approval: bool  # default; staff can change it
    staff_task: bool = False


RULES: tuple[Rule, ...] = (
    Rule("welcome", "Welcome", "Sent when staff approve a membership: welcome and a link to the profile.", False),
    Rule("profile_reminder", "Profile reminder", "Day 3 to 30 after joining, while the profile is empty.", False),
    Rule("first_steps", "First steps", "Day 7: services for the member's goals and events that fit.", False),
    Rule("meet_members", "Meet members", "Day 14: three member companies worth meeting, with introductions offered.", False),
    Rule("event_match", "New event match", f"An event added in the last {NEW_EVENT_DAYS} days fits the member's interests.", False),
    Rule("inactive_60", "Inactive for 60 days", "No login, chat, booking or introduction for 60 days: a personal nudge.", True),
    Rule("staff_call_90", "Call after 90 days", "Still inactive after 90 days: a task for staff to call the member.", False, staff_task=True),
)
RULE_BY_KEY = {rule.key: rule for rule in RULES}


@dataclass
class MemberState:
    user: User
    profile: MemberProfile
    company: Optional[Member]
    last_activity: datetime
    last_activity_kind: str

    @property
    def first_name(self) -> str:
        return (self.user.name or "").split(" ")[0] or "there"

    def days_since_join(self, as_of: datetime) -> Optional[int]:
        return None if self.profile.joined_at is None else (as_of - self.profile.joined_at).days

    def days_inactive(self, as_of: datetime) -> int:
        return max(0, (as_of - self.last_activity).days)

    def interest_text(self) -> str:
        goals = [GOALS[key][0] for key in split(self.profile.goals) if key in GOALS]
        parts = [self.profile.interests or "", "Goals: " + ", ".join(goals) if goals else "",
                 f"Sector: {self.company.sector}" if self.company and self.company.sector else ""]
        return ". ".join(part for part in parts if part)


def split(value: Optional[str]) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


# ---------- Settings, profiles, activity ----------

async def rule_settings(session: AsyncSession) -> dict[str, AutomationRule]:
    stored = {row.rule_key: row for row in (await session.exec(select(AutomationRule))).all()}
    for rule in RULES:
        if rule.key not in stored:
            stored[rule.key] = AutomationRule(rule_key=rule.key, enabled=True, requires_approval=rule.requires_approval)
            session.add(stored[rule.key])
    return stored


async def get_profile(session: AsyncSession, user_id: int) -> MemberProfile:
    profile = await session.get(MemberProfile, user_id)
    if profile is None:
        profile = MemberProfile(user_id=user_id)
        session.add(profile)
    return profile


def record_activity(session: AsyncSession, user_id: Optional[int], kind: str) -> None:
    """Add a login/chat activity row; the caller commits."""
    if user_id is not None:
        session.add(ActivityEvent(user_id=user_id, kind=kind[:20], created_at=japan_now()))


async def last_activity(session: AsyncSession, user_ids: list[int], as_of: datetime) -> dict[int, tuple[datetime, str]]:
    """Most recent login, chat, booking, room reservation or introduction request per user."""
    sources = [
        ("login/chat", select(ActivityEvent.user_id, func.max(ActivityEvent.created_at))
         .where(col(ActivityEvent.user_id).in_(user_ids), ActivityEvent.created_at <= as_of).group_by(ActivityEvent.user_id)),
        ("chat", select(ChatSession.user_id, func.max(Message.created_at)).join(Message, Message.session_id == ChatSession.session_id)
         .where(col(ChatSession.user_id).in_(user_ids), Message.role == MessageRole.user, Message.created_at <= as_of)
         .group_by(ChatSession.user_id)),
        ("booking", select(Booking.user_id, func.max(Booking.registered_at))
         .where(col(Booking.user_id).in_(user_ids), Booking.registered_at <= as_of).group_by(Booking.user_id)),
        ("room reservation", select(SpaceReservation.user_id, func.max(SpaceReservation.created_at))
         .where(col(SpaceReservation.user_id).in_(user_ids), SpaceReservation.created_at <= as_of).group_by(SpaceReservation.user_id)),
        ("introduction request", select(IntroRequest.requester_user_id, func.max(IntroRequest.created_at))
         .where(col(IntroRequest.requester_user_id).in_(user_ids), IntroRequest.created_at <= as_of)
         .group_by(IntroRequest.requester_user_id)),
    ]
    latest: dict[int, tuple[datetime, str]] = {}
    for kind, query in sources:
        for user_id, when in (await session.exec(query)).all():
            if isinstance(when, str):
                when = datetime.fromisoformat(when)
            if user_id is not None and when is not None and (user_id not in latest or when > latest[user_id][0]):
                latest[user_id] = (when, kind)
    return latest


async def member_states(session: AsyncSession, as_of: datetime, only_user: Optional[int] = None) -> list[MemberState]:
    """Every member account (an account linked to a member company), staff excluded."""
    query = select(User, Member).join(Belongs, Belongs.user_id == User.user_id).join(Member, Member.member_id == Belongs.member_id)
    if only_user is not None:
        query = query.where(User.user_id == only_user)
    rows: dict[int, tuple[User, Member]] = {}
    for user, member in (await session.exec(query.order_by(col(User.user_id), col(Member.member_id)))).all():
        if user.role != UserRole.staff:
            rows.setdefault(user.user_id, (user, member))
    if not rows:
        return []
    activity = await last_activity(session, list(rows), as_of)
    states = []
    for user_id, (user, company) in rows.items():
        profile = await get_profile(session, user_id)
        candidates = [(activity[user_id][0], activity[user_id][1])] if user_id in activity else []
        for when, kind in ((profile.joined_at, "joined"), (profile.profile_completed_at, "profile update"),
                           (user.created_at, "account created")):
            if when is not None and when <= as_of:
                candidates.append((when, kind))
        when, kind = max(candidates, key=lambda item: item[0]) if candidates else (as_of, "unknown")
        states.append(MemberState(user, profile, company, when, kind))
    return states


# ---------- Message drafts ----------

def _date(value: Optional[datetime]) -> str:
    return f"{value:%a %d %b %Y, %H:%M} (JST)" if value else "date to be confirmed"


async def _events_for(session, state: MemberState, limit=2):
    from concierge import matching
    text = state.interest_text()
    if not text:
        return []
    try:
        return [m for m in await matching.match_events(session, text, limit=limit) if m.score >= EVENT_MATCH_SCORE - 0.02]
    except Exception:
        logger.exception("Event matching unavailable for onboarding")
        return []


async def _members_for(session, state: MemberState, limit=3):
    from concierge import matching
    text = state.interest_text()
    if not text:
        return []
    exclude = {state.company.member_id} if state.company else set()
    try:
        return await matching.match_members(session, text, limit=limit, exclude=exclude)
    except Exception:
        logger.exception("Member matching unavailable for onboarding")
        return []


async def compose(session: AsyncSession, rule: Rule, state: MemberState, as_of: datetime, event=None) -> tuple[str, str]:
    name, company = state.first_name, state.company.name if state.company else "your company"
    sign = "\n\nThe CCIFJ team"
    if rule.key == "welcome":
        count = (await session.exec(select(func.count()).select_from(Member))).one()
        return (f"Welcome to CCIFJ, {name}!",
                f"Hello {name},\n\nWelcome to the French Chamber of Commerce and Industry in Japan. {company} is now part of a "
                f"network of {count} member companies.\n\nTo recommend the right services, events and people, tell us your goals: "
                "open the CCIFJ assistant and complete your member profile (one minute).\n\nAs a member you can use the member "
                "directory, member prices for events, and introductions to other members through CCIFJ." + sign)
    if rule.key == "profile_reminder":
        return ("One minute to personalise your CCIFJ membership",
                f"Hello {name},\n\nWe would like to point you to the services, events and members that matter to {company}. "
                "Please complete your profile in the CCIFJ assistant: your goals, interests and preferred language." + sign)
    if rule.key == "first_steps":
        goals = [key for key in split(state.profile.goals) if key in GOALS] or ["network"]
        services = list(dict.fromkeys(service for key in goals for service in GOALS[key][1]))[:3]
        events = await _events_for(session, state)
        text = f"Hello {name},\n\nBased on your goals, here are good first steps with CCIFJ:\n" + "\n".join(f"- {s}" for s in services)
        if events:
            text += "\n\nUpcoming events that fit you:\n" + "\n".join(
                f"- {e.name}, {_date(datetime.fromisoformat(e.fields['start_at']))}" for e in events)
        return ("Your first steps with CCIFJ", text + "\n\nAsk the CCIFJ assistant to book or to learn more about any of these." + sign)
    if rule.key == "meet_members":
        matches = await _members_for(session, state)
        if not matches:
            return ("Meet other CCIFJ members",
                    f"Hello {name},\n\nThe member directory and CCIFJ introductions can connect {company} with the right "
                    "companies. Tell the CCIFJ assistant who you would like to meet." + sign)
        lines = "\n".join(f"- {m.name} ({m.fields.get('sector') or 'sector not listed'}): {m.why}" for m in matches)
        return ("Three CCIFJ members you may want to meet",
                f"Hello {name},\n\nThese member companies look relevant to {company}:\n{lines}\n\nThese are suggestions to "
                "confirm, not endorsements. Ask the CCIFJ assistant to request an introduction; CCIFJ contacts the company first." + sign)
    if rule.key == "event_match" and event is not None:
        return (f"A new CCIFJ event that fits you: {event.title}",
                f"Hello {name},\n\nWe just added an event that matches your interests:\n- {event.title}\n- {_date(event.start_at)}"
                f"\n- {event.location or 'Location to be confirmed'}\n\nAsk the CCIFJ assistant for details or to book." + sign)
    if rule.key == "inactive_60":
        events = await _events_for(session, state)
        text = (f"Hello {name},\n\nWe have not seen {company} at CCIFJ for a while, and we would like to make sure your "
                "membership is useful to you.")
        if events:
            text += "\n\nComing up soon and relevant to you:\n" + "\n".join(
                f"- {e.name}, {_date(datetime.fromisoformat(e.fields['start_at']))}" for e in events)
        return ("We miss you at CCIFJ", text + "\n\nReply in the CCIFJ assistant or tell us what you need; we are happy to help." + sign)
    if rule.key == "staff_call_90":
        return (f"Re-engagement call: {state.user.name} ({company})",
                f"{state.user.name} <{state.user.email}> from {company} has been inactive for {state.days_inactive(as_of)} days "
                f"(last activity: {state.last_activity_kind} on {state.last_activity:%Y-%m-%d}). A 60-day nudge was already "
                "prepared. Please call or email to understand their needs and suggest relevant services.")
    raise ValueError(f"No template for rule {rule.key}")


# ---------- The job ----------

def _candidates(state: MemberState, as_of: datetime, new_events: list[Event]) -> list[tuple[Rule, str, str, Optional[Event]]]:
    """(rule, dedupe key, reason, event) for every rule this member qualifies for at `as_of`."""
    user_id, out = state.user.user_id, []
    days = state.days_since_join(as_of)
    joined = f"Joined {days} day(s) ago ({state.profile.joined_at:%Y-%m-%d})" if days is not None else ""
    if days is not None and 0 <= days <= 3:  # later joiners were welcomed by hand before onboarding existed
        out.append((RULE_BY_KEY["welcome"], f"welcome:{user_id}", joined + "; membership approved."))
    if days is not None and days >= 0:
        if 3 <= days <= 30 and state.profile.profile_completed_at is None:
            out.append((RULE_BY_KEY["profile_reminder"], f"profile_reminder:{user_id}", joined + "; profile still empty."))
        if 7 <= days <= 45:
            goals = ", ".join(split(state.profile.goals)) or "none given"
            out.append((RULE_BY_KEY["first_steps"], f"first_steps:{user_id}", f"{joined}; goals: {goals}."))
        if 14 <= days <= 60:
            out.append((RULE_BY_KEY["meet_members"], f"meet_members:{user_id}", joined + "."))
    inactive = state.days_inactive(as_of)
    last = f"last activity: {state.last_activity_kind} on {state.last_activity:%Y-%m-%d}"
    if inactive >= 60:
        out.append((RULE_BY_KEY["inactive_60"], f"inactive_60:{user_id}:{state.last_activity:%Y-%m-%d}",
                    f"Inactive for {inactive} days ({last})."))
    if inactive >= 90:
        out.append((RULE_BY_KEY["staff_call_90"], f"staff_call_90:{user_id}:{state.last_activity:%Y-%m-%d}",
                    f"Inactive for {inactive} days ({last})."))
    return [(rule, key, reason, None) for rule, key, reason in out]


async def _event_matches(session, state: MemberState, new_events: list[Event]) -> list[tuple[Event, float]]:
    if not new_events or not state.interest_text():
        return []
    from concierge import matching
    try:
        # Only events among the member's three best matches overall count, and only the best new one
        # is announced per run: e5 scores are close together, so a threshold alone floods members.
        matches = await matching.match_events(session, state.interest_text(), limit=3)
    except Exception:
        logger.exception("Event matching unavailable for engagement")
        return []
    by_id = {event.event_id: event for event in new_events}
    found = [(by_id[m.id], m.score) for m in matches if m.id in by_id and m.score >= EVENT_MATCH_SCORE]
    return found[:1]


async def deliver(session: AsyncSession, outreach: Outreach, when: Optional[datetime] = None) -> None:
    """Send an outreach: in-app/email/LINE are marked sent (mock outbox); a staff task becomes a staff request."""
    if outreach.channel == "staff_task" and outreach.staff_request_id is None:
        from uuid import uuid4
        request = StaffRequest(reference=f"pending-{uuid4().hex[:24]}",
                               request_type=StaffRequestType.team_ticket, category="membership",
                               subject=outreach.subject[:255], message=outreach.body, sender_name="CCIFJ engagement automation",
                               sender_email="automation@ccifj.local")
        session.add(request)
        await session.flush()
        request.reference = f"SR-{japan_now().year}-{request.request_id:06d}"
        outreach.staff_request_id = request.request_id
    outreach.status = OutreachStatus.sent
    outreach.sent_at = when or japan_now()
    session.add(outreach)


async def run_engagement(session: AsyncSession, as_of: Optional[datetime] = None, *, triggered_by: str = "staff",
                         only_user: Optional[int] = None, only_rules: Optional[set[str]] = None,
                         dry_run: bool = False) -> dict:
    """Fire every enabled rule for every member at `as_of` (default: now, Japan time).

    `dry_run` (used for simulated future dates) computes the same result and then rolls back:
    nothing is saved and nothing reaches a member.
    """
    as_of = (as_of or japan_now()).replace(microsecond=0)
    settings = await rule_settings(session)
    run = AutomationRun(as_of=as_of, triggered_by=triggered_by, created_at=japan_now())
    session.add(run)
    await session.flush()
    existing = set((await session.exec(select(Outreach.dedupe_key))).all())
    new_events = list((await session.exec(select(Event).where(
        Event.created_at > as_of - timedelta(days=NEW_EVENT_DAYS), Event.created_at <= as_of, Event.start_at > as_of, col(Event.cancelled).is_(False)))).all())
    created, duplicates = [], 0
    for state in await member_states(session, as_of, only_user):
        candidates = _candidates(state, as_of, new_events)
        for event, score in await _event_matches(session, state, new_events):
            candidates.append((RULE_BY_KEY["event_match"], f"event_match:{state.user.user_id}:{event.event_id}",
                               f"New event '{event.title}' (added {event.created_at:%Y-%m-%d}) fits the member's interests "
                               f"(match {score:.2f}).", event))
        for rule, key, reason, event in candidates:
            setting = settings[rule.key]
            if (only_rules and rule.key not in only_rules) or not setting.enabled:
                continue
            if key in existing:
                duplicates += 1
                continue
            subject, body = await compose(session, rule, state, as_of, event)
            outreach = Outreach(user_id=state.user.user_id, rule_key=rule.key, dedupe_key=key,
                                channel="staff_task" if rule.staff_task else state.profile.preferred_channel,
                                subject=subject[:255], body=body, reason=reason, run_id=run.run_id, created_at=japan_now())
            session.add(outreach)
            existing.add(key)
            if not setting.requires_approval:
                await deliver(session, outreach, as_of)
            created.append(outreach)
    run.created_count, run.duplicate_count = len(created), duplicates
    await session.flush()
    result = {
        "run_id": None if dry_run else run.run_id, "as_of": as_of.isoformat(timespec="minutes"), "dry_run": dry_run,
        "created": len(created), "duplicates": duplicates,
        "items": [{"outreach_id": None if dry_run else o.outreach_id, "user_id": o.user_id, "rule": o.rule_key,
                   "status": o.status, "channel": o.channel, "subject": o.subject, "reason": o.reason} for o in created],
    }
    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    return result


async def approve_membership(session: AsyncSession, user: User, member: Member, joined_at: Optional[datetime] = None) -> dict:
    """Link an account to a member company, start onboarding and send the welcome at once."""
    link = (await session.exec(select(Belongs).where(Belongs.user_id == user.user_id, Belongs.member_id == member.member_id))).first()
    if link is None:
        session.add(Belongs(user_id=user.user_id, member_id=member.member_id))
    profile = await get_profile(session, user.user_id)
    if profile.joined_at is None:
        # Whole seconds, like run_engagement's as_of, so the welcome fires on day 0, not day -1.
        profile.joined_at = (joined_at or japan_now()).replace(microsecond=0)
    await session.commit()
    return await run_engagement(session, profile.joined_at, triggered_by="membership_approval",
                                only_user=user.user_id, only_rules={"welcome"})


async def onboarding_context(session: AsyncSession, user_id: int) -> dict:
    """What the assistant should know this turn: access level and onboarding state."""
    from concierge.auth.access import access_level
    user = await session.get(User, user_id)
    level = (await access_level(session, user)).value
    context: dict = {"access_level": level}
    if level == "member":
        profile = await session.get(MemberProfile, user_id)
        joined = profile.joined_at if profile else None
        context["onboarding"] = {
            "new_member": joined is not None and (japan_now() - joined).days <= 30,
            "joined_at": joined.strftime("%Y-%m-%d") if joined else None,
            "profile_completed": bool(profile and profile.profile_completed_at),
            "goals": [GOALS[key][0] for key in split(profile.goals if profile else None) if key in GOALS],
            "interests": (profile.interests if profile else None) or "",
        }
    return context


# ---------- Engagement overview for staff ----------

def engagement_score(state: MemberState, as_of: datetime) -> int:
    """0-100: recent activity counts most; a completed profile adds a little."""
    inactive = state.days_inactive(as_of)
    score = max(0, 100 - inactive * 100 // 90)
    if state.profile.profile_completed_at is not None:
        score = min(100, score + 10)
    return score


def stage(state: MemberState, as_of: datetime) -> str:
    days = state.days_since_join(as_of)
    if state.days_inactive(as_of) >= 60:
        return "at risk"
    if days is None:
        return "established"
    if state.profile.profile_completed_at is None:
        return "new: profile missing" if days <= 30 else "profile missing"
    return "onboarding" if days <= 60 else "established"
