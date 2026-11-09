"""Agent analytics for staff, from the app's own database (no external service needed).

Every assistant reply is stored with its API response (topic, sources, warnings), so the
period's questions, topics, answer quality, response time and outcomes can be computed
here. LangSmith adds model-level numbers (tokens, cost, errors) when a valid key is set.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.db.relationaldb.datetimes import japan_now
from concierge.db.relationaldb.models import (
    Belongs, Booking, BookingStatus, ChatSession, IntroRequest, Message, MessageRole, Outreach, OutreachStatus,
    SpaceReservation, StaffRequest, User, UserRole, utcnow,
)

TOPIC_LABELS = {"events": "Events", "membership": "Membership & directory", "general": "Services & general",
                "space_booking": "Room booking", None: "Out of scope / clarification"}


def _response(tool_calls) -> dict | None:
    items = tool_calls if isinstance(tool_calls, list) else json.loads(tool_calls or "[]")
    return next((item.get("response") for item in items if isinstance(item, dict) and item.get("type") == "api_response"), None)


async def summary(session: AsyncSession, days: int = 14) -> dict:
    # Messages and records use UTC timestamps; days are grouped in Japan time.
    since = utcnow() - timedelta(days=days)
    rows = (await session.exec(
        select(Message.session_id, Message.role, Message.created_at, Message.content, Message.tool_calls)
        .where(Message.created_at >= since).order_by(col(Message.session_id), col(Message.created_at))
    )).all()
    owners = dict((await session.exec(select(ChatSession.session_id, ChatSession.user_id).where(
        col(ChatSession.session_id).in_({r[0] for r in rows})))).all()) if rows else {}
    user_ids = {uid for uid in owners.values() if uid is not None}
    staff = set((await session.exec(select(User.user_id).where(col(User.user_id).in_(user_ids), User.role == UserRole.staff))).all()) if user_ids else set()
    members = set((await session.exec(select(Belongs.user_id).where(col(Belongs.user_id).in_(user_ids)))).all()) if user_ids else set()

    def audience(session_id) -> str:
        uid = owners.get(session_id)
        return "guest" if uid is None else "staff" if uid in staff else "member" if uid in members else "user"

    daily: Counter = Counter()
    audiences: Counter = Counter()
    topics: Counter = Counter()
    sessions_seen, latencies, recent = set(), [], []
    replies = with_sources = with_warnings = proposed_actions = 0
    pending_question: dict[int, datetime] = {}
    for session_id, role, created, content, tool_calls in rows:
        if role == MessageRole.user:
            day = (created + timedelta(hours=9)).date().isoformat()
            daily[day] += 1
            audiences[audience(session_id)] += 1
            sessions_seen.add(session_id)
            pending_question[session_id] = created
            recent.append({"text": (content or "")[:200], "at": (created + timedelta(hours=9)).isoformat(timespec="minutes"),
                           "audience": audience(session_id)})
        elif role == MessageRole.assistant:
            asked = pending_question.pop(session_id, None)
            if asked is not None:
                latencies.append((created - asked).total_seconds())
            try:
                response = _response(tool_calls)
            except (TypeError, ValueError):
                response = None
            if response is None:
                continue
            replies += 1
            intent = (response.get("intent") or {}).get("code") if isinstance(response.get("intent"), dict) else None
            for code in ([part.strip() for part in intent.split(",")] if intent else [None]):
                topics[TOPIC_LABELS.get(code, code)] += 1
            with_sources += bool(response.get("sources"))
            with_warnings += bool(response.get("warnings"))
            proposed_actions += len(response.get("pending_approvals") or [])

    start_day = (japan_now() - timedelta(days=days - 1)).date()
    series = [{"day": (start_day + timedelta(days=i)).isoformat(),
               "questions": daily.get((start_day + timedelta(days=i)).isoformat(), 0)} for i in range(days)]

    async def count(model, column, *where) -> int:
        return (await session.exec(select(func.count()).select_from(model).where(column >= since, *where))).one()

    outcomes = {
        "event_bookings": await count(Booking, Booking.registered_at, Booking.status == BookingStatus.confirmed),
        "room_reservations": await count(SpaceReservation, SpaceReservation.created_at),
        "staff_requests": await count(StaffRequest, StaffRequest.created_at),
        "introduction_requests": await count(IntroRequest, IntroRequest.created_at),
        "onboarding_messages_sent": await count(Outreach, Outreach.created_at, Outreach.status == OutreachStatus.sent),
        "actions_proposed": proposed_actions,
    }
    ordered = sorted(latencies)
    return {
        "days": days,
        "totals": {"questions": sum(daily.values()), "conversations": len(sessions_seen), "replies": replies},
        "daily": series,
        "audience": {key: audiences.get(key, 0) for key in ("guest", "user", "member", "staff")},
        "topics": [{"topic": topic, "count": n} for topic, n in topics.most_common()],
        "quality": {
            "answers_with_sources": round(100 * with_sources / replies) if replies else None,
            "answers_with_fallback_warnings": round(100 * with_warnings / replies) if replies else None,
            "median_response_seconds": round(statistics.median(ordered), 1) if ordered else None,
            "p90_response_seconds": round(ordered[int(0.9 * (len(ordered) - 1))], 1) if ordered else None,
        },
        "outcomes": outcomes,
        "recent_questions": list(reversed(recent))[:12],
    }


# ---------- LangSmith (optional) ----------

_cache: dict[int, tuple[datetime, dict]] = {}
_lock = __import__("threading").Lock()
TOOL_RUN_CAP = 300


def langsmith_summary(days: int = 7) -> dict:
    """Model-level numbers from LangSmith; cached 5 minutes. Never raises.

    Concurrent requests share one fetch (the lock) instead of each paging through LangSmith.
    """
    with _lock:
        return _langsmith_summary(days)


def _langsmith_summary(days: int) -> dict:
    import os
    cached = _cache.get(days)
    # Good results are kept 5 minutes; failures 1 minute, so a busy LangSmith is not hammered.
    if cached and utcnow() - cached[0] < timedelta(minutes=5 if cached[1].get("status") == "connected" else 1):
        return cached[1]
    tracing = os.getenv("LANGSMITH_TRACING", "").strip().lower() in {"1", "true", "yes"}
    # Same project the traces go to: explicit name, else <LANGSMITH_AGENT_ID>-<LANGSMITH_AGENT_ENVIRONMENT>.
    agent, environment = os.getenv("LANGSMITH_AGENT_ID", "").strip(), os.getenv("LANGSMITH_AGENT_ENVIRONMENT", "").strip()
    project = (os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT")
               or (f"{agent}-{environment}" if agent and environment else "default"))
    result: dict = {"tracing": tracing, "project": project, "status": "off", "days": days}
    if not os.getenv("LANGSMITH_API_KEY"):
        result["message"] = "No LANGSMITH_API_KEY is set."
        return result
    try:
        from datetime import timezone
        from langsmith import Client
        from langsmith.utils import LangSmithAuthError, LangSmithNotFoundError
        client = Client()
        list(client.list_projects(limit=1))  # checks the key before looking up a project
        if not any(True for _ in client.list_projects(name=project, limit=1)):
            raise LangSmithNotFoundError(project)
        since = datetime.now(timezone.utc) - timedelta(days=days)
        project_id = client.read_project(project_name=project).id
        # Aggregates are computed by LangSmith in one call each; no run pages are downloaded.
        turns = client.get_run_stats(project_ids=[project_id], start_time=since, is_root=True, filter='eq(name, "LangGraph")')
        everything = client.get_run_stats(project_ids=[project_id], start_time=since, is_root=True)
        from itertools import islice
        tools = Counter(run.name for run in islice(
            client.list_runs(project_id=project_id, run_type="tool", start_time=since, select=["name"]), TOOL_RUN_CAP))
    except LangSmithAuthError:
        result.update(status="auth_error", message="LangSmith rejected the API key. Create a new key and update LANGSMITH_API_KEY.")
        _cache[days] = (utcnow(), result)
        return result
    except LangSmithNotFoundError:
        result.update(status="no_project", message=f"No LangSmith project named '{project}' yet: it appears after the "
                      "first traced chat. Set LANGSMITH_PROJECT to use another project.")
        _cache[days] = (utcnow(), result)
        return result
    except Exception as exc:  # rate limits, network and other failures
        busy = type(exc).__name__ == "LangSmithRateLimitError"
        result.update(status="error", message="LangSmith is busy (rate limit). Try again in a minute." if busy
                      else f"LangSmith is unreachable ({type(exc).__name__}).")
        _cache[days] = (utcnow(), result)
        return result
    def number(stats, key, digits=1):
        value = stats.get(key)
        return round(float(value), digits) if value is not None else None
    result.update(
        status="connected",
        chat_turns=int(turns.get("run_count") or 0),
        median_latency_seconds=number(turns, "latency_p50"), p99_latency_seconds=number(turns, "latency_p99"),
        error_rate=number(turns, "error_rate", 3),
        runs=int(everything.get("run_count") or 0),
        total_tokens=int(everything.get("total_tokens") or 0),
        total_cost=number(everything, "total_cost", 2),
        tool_sample=sum(tools.values()),
        top_tools=[{"tool": name, "calls": n} for name, n in tools.most_common(8)],
    )
    _cache[days] = (utcnow(), result)
    return result
