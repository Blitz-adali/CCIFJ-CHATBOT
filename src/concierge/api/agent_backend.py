"""Adapter over the agent's public functions.

Ordinary turns reconstruct context from SQL history on a fresh graph thread.
State that the graph keeps between turns (uploaded file text and the
out-of-scope counter) is persisted in message metadata and passed back in
through ``agent_inputs``. Approval resumption uses the exact interrupted
thread and never replays writes.
"""

import logging
from contextvars import ContextVar
from functools import lru_cache

from .catalog import localized_question
from .presentation import linked_resources
from .progress import ProgressTracker

logger = logging.getLogger(__name__)

response_locale = ContextVar("api_response_locale", default="en")
response_events = ContextVar("api_response_events", default=None)
# Per-turn extras: {"context": [...], "agent_message": str | None, "out_of_scope_count": int,
# "user_context": dict | None}
agent_inputs = ContextVar("api_agent_inputs", default=None)

LOGIN_REQUIRED = {
    "en": "This action needs a signed-in account. Log in, start a new conversation, and ask again.",
    "fr": "Cette action nécessite un compte connecté. Connectez-vous, démarrez une nouvelle conversation, puis réessayez.",
    "ja": "この操作にはログインが必要です。ログイン後、新しい会話を開始して、もう一度お試しください。",
}
LOGIN_WARNING = {
    "en": "Login is required for protected actions. Start a new conversation after signing in.",
    "fr": "La connexion est requise pour les actions protégées. Démarrez une nouvelle conversation après connexion.",
    "ja": "保護された操作にはログインが必要です。ログイン後、新しい会話を開始してください。",
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
    None: {
        "en": "Please review the details in the action card below. Confirm only if you want this action to proceed.",
        "fr": "Vérifiez les détails dans la carte ci-dessous. Confirmez uniquement si vous souhaitez poursuivre cette action.",
        "ja": "下の操作カードをご確認ください。実行してよい場合のみ確定してください。",
    },
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
    names = {
        str(action.get("name", ""))
        for item in approvals
        for action in item.get("action_requests", [])
        if isinstance(action, dict)
    }
    key = next((name for name in ("book_event_ticket", "book_space", "subscribe_newsletter") if name in names), None)
    return APPROVAL_PROMPTS[key][locale]


async def enrich_ticket_approvals(approvals):
    """Add event title/date/location to ticket approvals for the review card."""
    from concierge.db.relationaldb import crud
    from concierge.db.relationaldb.session import AsyncSessionLocal

    for item in approvals:
        for action in item.get("action_requests", []):
            if not isinstance(action, dict) or action.get("name") != "book_event_ticket":
                continue
            args = action.get("args")
            if not isinstance(args, dict):
                continue
            try:
                async with AsyncSessionLocal() as db_session:
                    event = await crud.get_event(db_session, int(args.get("event_id")))
                if event is not None:
                    action["args"] = {
                        **args,
                        "event_title": event.title,
                        "event_start": event.start_at.isoformat() if event.start_at else None,
                        "event_location": event.location,
                    }
            except Exception:
                # The approval card can still show the event ID.
                logger.exception("Could not load event details for a pending ticket approval")


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
            if guest:
                # Booking tools require an account; drop the interrupted thread
                # instead of leaving the guest conversation blocked on it.
                reply = LOGIN_REQUIRED[locale]
                approvals = []
                warnings.append(LOGIN_WARNING[locale])
            else:
                await enrich_ticket_approvals(approvals)
                reply = approval_prompt(approvals, locale)
                warnings.append(APPROVAL_WARNING[locale])
        if not reply:
            raise RuntimeError("Agent returned no reply.")
        # The answer follows the user's language; catalog questions follow the UI locale.
        suggested = [localized_question(q, locale) for q in (result.get("suggested_questions") or [])]
        # At most three buttons under an answer: the agent's actions first, then questions.
        suggested = suggested[:max(0, 3 - len(actions_for_turn))]
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


@lru_cache
def get_agent_backend() -> AgentBackend:
    return AgentBackend()
