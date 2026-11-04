from langchain_core.messages import HumanMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from concierge.config import get_settings
from .schema_prompts import AgentState


def last_humain_message(messages: list) -> list:
    """Return the most recent HumanMessage from the message list, or an empty list if none found."""
    for mes in messages[::-1]:
        if isinstance(mes, HumanMessage):
            return [mes]
    return []


def do_trim_messages(messages):
    """Trim conversation history up to trim_token count, preserving system instructions and starting on a human turn."""
    return trim_messages(
        messages,
        max_tokens=get_settings().trim_messages_max_token,
        strategy="last",
        token_counter=count_tokens_approximately,
        start_on="human",
        include_system=True,
        allow_partial=False,
    )


def route_from_orchestrator(state: AgentState):
    """Dynamically route from orchestrator to one or more worker nodes in parallel, or END."""
    plan = state.get("plan") or {}
    tasks = plan.get("tasks", [])
    if not tasks:
        return "END"

    node_map = {
        "events": "events_node",
        "membership": "membership_node",
        "general": "general_node",
    }
    targets = [node_map[t["target"]] for t in tasks if t.get("target") in node_map]
    if not targets:
        return "END"
    # Deduplicate while preserving order
    unique_targets = list(dict.fromkeys(targets))
    # If single target, return list with single target so LangGraph handles consistently
    return unique_targets


def route_from_start(state: AgentState):
    """Determine the entry node based on the currently active sub-agent in state."""
    active = state.get("active_agent")
    if active == "events":
        return "events_node"
    elif active == "membership":
        return "membership_node"
    elif active in ("general", "business"):
        return "general_node"
    return "supervisor_node"


def route_after_supervisor(state: AgentState):
    """Route from supervisor to the selected worker node, or END if clarification was returned."""
    route = state.get("target_route", "none")
    if route == "events":
        return "events_node"
    elif route == "membership":
        return "membership_node"
    elif route in ("general", "business"):
        return "general_node"
    return "END"


def route_after_subagent(state: AgentState):
    """Return to supervisor if the sub-agent triggered a handoff, otherwise end turn."""
    if state.get("active_agent") == "supervisor":
        return "supervisor_node"
    return "END"