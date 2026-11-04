"""Adapter over the agent's public functions.

Ordinary turns reconstruct context from SQL history on a fresh graph thread.
State that the graph keeps between turns (uploaded file text and the
out-of-scope counter) is persisted in message metadata and passed back in
through ``agent_inputs``. Approval resumption uses the exact interrupted
thread and never replays writes.
"""

import logging
import re
import threading
from contextvars import ContextVar
from datetime import datetime, time
from functools import lru_cache

from .presentation import linked_resources
from .progress import ProgressTracker

logger = logging.getLogger(__name__)

response_locale = ContextVar("api_response_locale", default="en")
response_events = ContextVar("api_response_events", default=None)
# Per-turn extras: {"context": [...], "agent_message": str | None, "out_of_scope_count": int,
# "user_context": dict | None}
agent_inputs = ContextVar("api_agent_inputs", default=None)

LOGIN_REQUIRED = {
    "en": "This action needs a CCIFJ account. Please sign in: you can stay in this conversation and continue right here afterwards.",
    "fr": "Cette action nécessite un compte CCIFJ. Veuillez vous connecter : vous pourrez rester dans cette conversation et continuer ici ensuite.",
    "ja": "この操作にはCCIFJアカウントが必要です。ログインしてください。ログイン後もこの会話のまま続けられます。",
}
LOGIN_WARNING = {
    "en": "Sign in to book, reserve, or subscribe. Your conversation is kept when you sign in.",
    "fr": "Connectez-vous pour réserver ou vous abonner. Votre conversation est conservée après la connexion.",
    "ja": "予約や登録にはログインが必要です。ログインしても会話はそのまま残ります。",
}
APPROVAL_WARNING = {
    "en": "The action will not run until you approve it.",
    "fr": "L’action ne sera exécutée qu’après votre approbation.",
    "ja": "承認するまで操作は実行されません。",
}
APPROVAL_PROMPTS = {
    "book_event_ticket": {
        "en": "Please review the event and attendee details in the booking card below. Confirming will create the ticket reservation in your CCIFJ account; no payment will be taken.",
        "fr": "Vérifiez les détails de l’événement et des participants dans la carte ci-dessous. La confirmation enregistrera la réservation dans votre compte CCIFJ ; aucun paiement ne sera effectué.",
        "ja": "下の予約カードでイベントと参加者の詳細をご確認ください。確定するとチケット予約がCCIFJアカウントに登録されます。支払いは発生しません。",
    },
    "book_space": {
        "en": "Please review the space reservation details in the card below. Confirming will submit this reservation to CCIFJ.",
        "fr": "Vérifiez les détails de la réservation d’espace dans la carte ci-dessous. La confirmation transmettra cette demande à la CCIFJ.",
        "ja": "下のスペース予約カードをご確認ください。確定するとCCIFJに予約が送信されます。",
    },
    "subscribe_newsletter": {
        "en": "Please review the newsletter subscription details in the card below. Confirming will subscribe your account.",
        "fr": "Vérifiez les détails de l’inscription à la newsletter dans la carte ci-dessous. La confirmation abonnera votre compte.",
        "ja": "下のニュースレター登録カードをご確認ください。確定するとアカウントが登録されます。",
    },
    "submit_staff_request": {
        "en": "Please review your message to CCIFJ staff below. Confirming will store it for the team; no email is sent automatically.",
        "fr": "Vérifiez ci-dessous votre message au personnel de la CCIFJ. La confirmation l’enregistrera pour l’équipe ; aucun e-mail n’est envoyé automatiquement.",
        "ja": "下のCCIFJスタッフへのメッセージをご確認ください。確定するとチーム向けに保存されます。メールは自動送信されません。",
    },
    "cancel_event_booking": {
        "en": "Please check the booking below. Confirming will cancel it and all its tickets.",
        "fr": "Vérifiez la réservation ci-dessous. La confirmation l’annulera, ainsi que tous ses billets.",
        "ja": "下の予約をご確認ください。確定すると、この予約とすべてのチケットがキャンセルされます。",
    },
    "cancel_space_reservation": {
        "en": "Please check the reservation below. Confirming will cancel it.",
        "fr": "Vérifiez la réservation ci-dessous. La confirmation l’annulera.",
        "ja": "下の予約をご確認ください。確定するとキャンセルされます。",
    },
    "change_event_booking": {
        "en": "Please check the changes to your booking below. Confirming will update it.",
        "fr": "Vérifiez ci-dessous les modifications de votre réservation. La confirmation la mettra à jour.",
        "ja": "下の予約の変更内容をご確認ください。確定すると予約が更新されます。",
    },
    "change_space_reservation": {
        "en": "Please check the changes to your reservation below. Confirming will update it.",
        "fr": "Vérifiez ci-dessous les modifications de votre réservation. La confirmation la mettra à jour.",
        "ja": "下の予約の変更内容をご確認ください。確定すると予約が更新されます。",
    },
    None: {
        "en": "Please review the details in the action card below. Confirm only if you want this action to proceed.",
        "fr": "Vérifiez les détails dans la carte ci-dessous. Confirmez uniquement si vous souhaitez poursuivre cette action.",
        "ja": "下の操作カードをご確認ください。実行してよい場合のみ確定してください。",
    },
}


# Approvals a guest may answer; every other write tool needs an account.
GUEST_APPROVAL_TOOLS = {"submit_staff_request"}
NEWSLETTER_NAMES = {
    "general_news": "CCIFJ General Corporate News",
    "training_announcements": "CCIFJ Training & Workshops Announcements",
    "podcast_studio_ccifj": "@StudioCCIFJ Podcast Newsletter",
}

NEXT_ACTION_IDS = {
    "event.view", "event.book", "events.browse", "space.book", "membership.join",
    "membership.benefits", "newsletter.subscribe", "contact.staff", "auth.login",
}


class ApprovalStateUnavailable(Exception):
    pass


def next_actions(result) -> list[dict]:
    """Proactive buttons from the agent; the client sends each prompt as a user message."""
    actions = []
    for action in result.get("next_actions") or []:
        if not isinstance(action, dict) or action.get("id") not in NEXT_ACTION_IDS:
            continue
        label, prompt = action.get("label"), action.get("prompt")
        if not isinstance(label, str) or not label.strip() or not isinstance(prompt, str) or not prompt.strip():
            continue
        item = {"id": action["id"], "label": label.strip(), "prompt": prompt.strip()}
        if isinstance(action.get("event_id"), int):
            item["event_id"] = action["event_id"]
        actions.append(item)
    return actions[:2]


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        block if isinstance(block, str) else block.get("text", "")
        for block in content or []
        if isinstance(block, (str, dict))
    )


def approval_prompt(approvals, locale):
    names = _action_names(approvals)
    key = next((name for name in APPROVAL_PROMPTS if name is not None and name in names), None)
    return APPROVAL_PROMPTS[key][locale]


def _action_names(approvals):
    return {
        str(action.get("name", ""))
        for item in approvals
        for action in item.get("action_requests", [])
        if isinstance(action, dict)
    }


def _iso(value):
    return value.isoformat() if value else None


def _amount(value):
    return str(value) if value is not None else None


def _ref_id(reference):
    """'CCIFJ-EVT-00012', '#12' or '12' -> 12, using the agent's rule (last digit group)."""
    digits = re.findall(r"\d+", str(reference or ""))
    return int(digits[-1]) if digits else None


def _schedule(text, end_of_day=False):
    """The agent's 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD' schedule argument as a datetime, or None."""
    try:
        value = datetime.fromisoformat(str(text).strip())
    except (TypeError, ValueError):
        return None
    if end_of_day and len(str(text).strip()) == 10:
        value = datetime.combine(value.date(), time.max)
    return value


def _event_card(event):
    return {
        "event_id": event.event_id,
        "event_title": event.title,
        "event_start": _iso(event.start_at),
        "event_end": _iso(event.end_at),
        "event_location": event.location,
    }


def _people(attendees):
    people = []
    for person in attendees or []:
        if isinstance(person, dict):
            people.append({key: person.get(key) for key in ("name", "email", "telephone")})
    return people


def _reservation_card(reservation, space):
    return {
        "reservation_id": reservation.reservation_id,
        "space_name": space.name if space else None,
        "title": reservation.title,
        "start_at": _iso(reservation.start_at),
        "end_at": _iso(reservation.end_at),
        "expected_attendees": reservation.expected_attendees,
    }


async def _enrichment(db, name, args, user_id):
    """Extra, read-only card fields for one pending action, or {} when nothing applies.

    Bookings and reservations are only described when they belong to ``user_id``.
    """
    from concierge.db.relationaldb import crud

    if name == "book_event_ticket":
        event = await crud.get_event(db, _ref_id(args.get("event_id")) or 0)
        if event is None:
            return {}
        return {
            **_event_card(event),
            "price": _amount(event.price),
            "member_price": _amount(event.member_price),
            "non_member_price": _amount(event.non_member_price),
            "currency": event.currency,
            "reg_deadline": _iso(event.reg_deadline),
            "ticket_count": len(_people(args.get("attendees"))),
        }
    if name == "book_space":
        spaces = await crud.list_spaces(db)
        wanted = str(args.get("space_name") or "").casefold()
        space = next((s for s in spaces if s.name.casefold() == wanted), None)
        start, end = _schedule(args.get("start_date")), _schedule(args.get("end_date"), end_of_day=True)
        extra = {"start_at": _iso(start), "end_at": _iso(end)}
        if space is not None:
            # The space table has no location column; its description says where it is.
            extra |= {"space_name": space.name, "space_capacity": space.capacity, "space_description": space.description}
        return extra
    if name == "subscribe_newsletter":
        kind = args.get("newsletter_type") or "general_news"
        return {"newsletter_name": NEWSLETTER_NAMES.get(kind, kind)}
    if user_id is None:
        return {}
    if name in {"cancel_event_booking", "change_event_booking"}:
        booking = await crud.get_booking(db, _ref_id(args.get("booking_reference")) or 0)
        if booking is None or booking.user_id != user_id:
            return {}
        event = await crud.get_event(db, booking.event_id)
        tickets = await crud.get_booking_tickets(db, user_id, booking.booking_id)
        current = {
            "booking_id": booking.booking_id,
            **(_event_card(event) if event else {"event_id": booking.event_id}),
            "attendees": [{"name": t.name, "email": t.email, "telephone": t.telephone} for t in tickets],
        }
        if name == "cancel_event_booking":
            return current
        target = event
        new_event_id = args.get("new_event_id")
        if new_event_id not in (None, ""):
            target = await crud.get_event(db, _ref_id(new_event_id) or 0)
        new = {
            **(_event_card(target) if target else {"event_id": new_event_id}),
            "attendees": _people(args.get("attendees")) or current["attendees"],
        }
        return {"current": current, "new": new}
    if name in {"cancel_space_reservation", "change_space_reservation"}:
        reservation = await crud.get_reservation(db, _ref_id(args.get("reservation_reference")) or 0)
        if reservation is None or reservation.user_id != user_id:
            return {}
        current = _reservation_card(reservation, await crud.get_space(db, reservation.space_id))
        if name == "cancel_space_reservation":
            return current
        new = dict(current)
        if args.get("new_start_date"):
            new["start_at"] = _iso(_schedule(args["new_start_date"]))
        if args.get("new_end_date"):
            new["end_at"] = _iso(_schedule(args["new_end_date"], end_of_day=True))
        if args.get("new_title"):
            new["title"] = str(args["new_title"]).strip()
        if args.get("new_expected_attendees") is not None:
            new["expected_attendees"] = args["new_expected_attendees"]
        return {"current": current, "new": new}
    return {}


async def enrich_approvals(approvals, user_id=None):
    """Add what each pending action will save or change, for the review card."""
    from concierge.db.relationaldb.session import AsyncSessionLocal

    for item in approvals:
        for action in item.get("action_requests", []):
            if not isinstance(action, dict) or not isinstance(action.get("args"), dict):
                continue
            try:
                async with AsyncSessionLocal() as db_session:
                    extra = await _enrichment(db_session, action.get("name"), action["args"], user_id)
            except Exception:
                # The card can still show the agent's own arguments.
                logger.exception("Could not load details for a pending %s approval", action.get("name"))
                continue
            if extra:
                action["args"] = {**action["args"], **extra}


class AgentBackend:
    def __init__(self):
        # Keep /health, auth, history, and startup independent of model loading.
        from concierge.agents import compiled_parent, get_pending_approvals, resume_parent_approval

        self.graph = compiled_parent
        self.pending = get_pending_approvals
        self.resume_approval = resume_parent_approval

    @staticmethod
    def config(thread_id):
        return {"configurable": {"thread_id": thread_id}}

    async def chat(self, history, user_id, thread_id):
        from langchain_core.messages import AIMessage, HumanMessage

        inputs = agent_inputs.get() or {}
        messages = [
            (HumanMessage if row.role.value == "user" else AIMessage)(
                content=row.content, id=f"db:{row.message_id}"
            )
            for row in history if row.role.value in {"user", "assistant"}
        ]
        override = inputs.get("agent_message")
        if override and messages and isinstance(messages[-1], HumanMessage):
            # A guided path turns structured selections into an explicit request.
            messages[-1] = HumanMessage(content=override, id=messages[-1].id)
        graph_input = {
            "messages": messages,
            "user_id": str(user_id) if user_id is not None else None,
            "context": list(inputs.get("context") or []),
            "out_of_scope_count": int(inputs.get("out_of_scope_count") or 0),
            "user_context": inputs.get("user_context"),
        }
        config = self.config(thread_id)
        queue = response_events.get()
        if queue is None:
            result = await self.graph.ainvoke(graph_input, config=config)
        else:
            tracker = ProgressTracker(response_locale.get())
            async for event in self.graph.astream_events(graph_input, config=config, version="v2"):
                progress = tracker.update(event)
                if progress:
                    queue.put_nowait({"type": "progress", "messages": progress})
            snapshot = await self.graph.aget_state(config)
            result = dict(snapshot.values or {})
            interrupts = [i for task in snapshot.tasks for i in getattr(task, "interrupts", ())]
            if interrupts:
                result["__interrupt__"] = interrupts
        return await self.format_result(result, guest=user_id is None)

    async def resume(self, thread_id, interrupt_id, decision):
        snapshot = await self.graph.aget_state(self.config(thread_id))
        interrupts = getattr(snapshot, "interrupts", ())
        if not interrupts:
            interrupts = tuple(i for task in snapshot.tasks for i in task.interrupts)
        if not any(item.id == interrupt_id for item in interrupts):
            raise ApprovalStateUnavailable()
        result = await self.resume_approval(self.config(thread_id), interrupt_id, decision)
        return await self.format_result(result)

    async def format_result(self, result, guest=False):
        from langchain_core.messages import AIMessage, HumanMessage

        locale = response_locale.get()
        approvals = self.pending(result)
        # A pending approval (even a guest's dropped one) must be answered first.
        actions_for_turn = [] if approvals else next_actions(result)
        messages = result.get("messages", [])
        # Only consider this turn: history contains earlier assistant replies.
        latest_human = next(
            (index for index in range(len(messages) - 1, -1, -1) if isinstance(messages[index], HumanMessage)),
            -1,
        )
        reply = ""
        for message in reversed(messages[latest_human + 1:]):
            if isinstance(message, AIMessage) and not message.tool_calls:
                reply = _text(message.content).strip()
                if reply:
                    break
        warnings = []
        if approvals:
            if guest and not _action_names(approvals) <= GUEST_APPROVAL_TOOLS:
                # Booking tools require an account; drop the interrupted thread
                # instead of leaving the guest conversation blocked on it.
                reply = LOGIN_REQUIRED[locale]
                approvals = []
                warnings.append(LOGIN_WARNING[locale])
            else:
                await enrich_approvals(approvals, None if guest else _ref_id(result.get("user_id")))
                reply = approval_prompt(approvals, locale)
                warnings.append(APPROVAL_WARNING[locale])
        if not reply:
            raise RuntimeError("Agent returned no reply.")
        # Written by the model for this user, already in the user's language.
        suggested = [q.strip() for q in (result.get("suggested_questions") or []) if isinstance(q, str) and q.strip()]
        # Actions first, then questions: three buttons in all, but always room for two questions.
        suggested = suggested[:max(2, 3 - len(actions_for_turn))]
        plan = result.get("plan") or {}
        tasks = plan.get("tasks", []) if isinstance(plan, dict) else []
        intent = ", ".join(dict.fromkeys(t.get("target", "") for t in tasks if isinstance(t, dict) and t.get("target")))
        sources, actions = linked_resources(reply)
        return {
            "reply": reply, "pending_approvals": approvals, "sources": sources, "actions": actions,
            "suggested_questions": suggested, "warnings": warnings,
            "intent": {"code": intent} if intent else None,
            # Guided journeys start from the welcome screen, not under agent answers.
            "path_actions": [],
            "next_actions": actions_for_turn,
            "attachment_count": len(result.get("context") or []),
            "agent_state": {"out_of_scope_count": int(result.get("out_of_scope_count") or 0)},
        }


_backend_lock = threading.Lock()
_agent_ready = threading.Event()


@lru_cache
def _build_agent_backend() -> AgentBackend:
    return AgentBackend()


def get_agent_backend() -> AgentBackend:
    # One load even when startup warm-up and a first request race for it.
    with _backend_lock:
        return _build_agent_backend()


def warm_agent() -> None:
    """Build the agent graph and load the search models (blocking; run in a thread)."""
    get_agent_backend()
    from concierge.agents import warm_up

    warm_up()
    _agent_ready.set()
    logger.info("Agent warm-up finished; the agent is ready")


def agent_ready() -> bool:
    return _agent_ready.is_set()
