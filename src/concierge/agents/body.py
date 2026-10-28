from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from .schema_prompts import (
        AgentState,
        EventsState,
        MembershipState,
        GeneralState,
        OrchestratorPlan,
        SupervisorDecision,
        TaskItem,
        ORCHESTRATOR_SYSTEM_PROMPT,
        COLLECTOR_SYSTEM_PROMPT,
        routing_system_prompt,
        _ALL_QUESTIONS,
        PREDEFINED_QA_LOOKUP
    )
from .model import llm, llm_routing
from .utlis import (
    last_humain_message,
    do_trim_messages,
    route_from_orchestrator,
    route_from_start,
    route_after_supervisor,
    route_after_subagent,
)
from .langchain_agents import (
    event_agent,
    membership_agent,
    general_agent,
)


#############################
#>>>>> Orchestrator Node <<<<#
#############################

async def orchestrator_node(state: AgentState):
    """
    Analyzes the user's input, detects language, and decomposes compound or single-intent
    queries into domain tasks for specialist workers.
    Also predicts up to 3 likely follow-up questions from the predefined catalog.
    """
    # Route the latest user turn with a small amount of recent conversational
    # context. Keeping only human/assistant text avoids sending tool payloads
    # back through the router, while the window keeps follow-ups such as
    # "what about tickets?" understandable without replaying the whole chat.
    conversation = [
        message
        for message in state.get("messages", [])
        if isinstance(message, (HumanMessage, AIMessage))
        and isinstance(message.content, str)
        and message.content.strip()
    ]
    last_msgs = last_humain_message(conversation)
    if not last_msgs:
        return {
            "messages": [AIMessage(content="How can I help you with CCIFJ?")],
            "plan": {"tasks": []},
            "worker_results": {},
            "active_agent": "orchestrator",
            "target_route": "none",
            "suggested_questions": [],
        }

    recent_context = conversation[-8:]
    # A malformed/interrupted state can end with an assistant turn. The router
    # must always receive the current user message as its final message.
    if recent_context[-1] is not last_msgs[0]:
        recent_context = [message for message in recent_context if message is not last_msgs[0]]
        recent_context.append(last_msgs[0])

    current_question = last_msgs[0].content.strip()
    matched_question = next(
        (question for question in _ALL_QUESTIONS if question.casefold() == current_question.casefold()),
        None,
    )
    if matched_question is not None:
        direct_response = PREDEFINED_QA_LOOKUP[matched_question]
        question = matched_question.casefold()
        if any(term in question for term in ("event", "ticket", "room", "space", "speaker", "sponsor")):
            follow_up_order = [
                "What events are coming up at CCIFJ?",
                "How can I book a ticket for a CCIFJ event?",
                "Can I rent a meeting room or event space at CCIFJ?",
                "How can I become a speaker at a CCIFJ event?",
                "How can I sponsor a CCIFJ event?",
            ]
        elif any(term in question for term in ("member", "membership", "fees", "benefits", "companies")):
            follow_up_order = [
                "How do I become a CCIFJ member?",
                "What are the CCIFJ membership fees?",
                "What are the benefits of CCIFJ membership?",
                "How can I search for French companies in Japan?",
            ]
        else:
            follow_up_order = [
                "How can I set up a company in Japan through CCIFJ?",
                "How do I post a job offer or find candidates through CCIFJ?",
                "How do I subscribe to the CCIFJ newsletter?",
                "Where is the CCIFJ office located?",
                "How do I submit corporate news to CCIFJ?",
                "What is CCIFJ?",
            ]
        suggested = [item for item in follow_up_order if item != matched_question][:3]
        return {
            "messages": [AIMessage(content=direct_response)],
            # Clear turn-local state so a previous turn's worker plan cannot
            # route this direct FAQ answer back through an agent.
            "plan": {"tasks": []},
            "worker_results": {},
            "active_agent": "orchestrator",
            "target_route": "none",
            "suggested_questions": suggested,
        }
    structured_llm = llm_routing.with_structured_output(OrchestratorPlan)
    response: OrchestratorPlan = await structured_llm.ainvoke(
        [{"role": "system", "content": ORCHESTRATOR_SYSTEM_PROMPT}] + recent_context
    )
    suggested = response.suggested_questions or []

    # If no domain tasks (e.g. greeting, clarification, out-of-scope), reply directly
    if not response.tasks:
        return {
            "plan": response.model_dump(),
            "messages": [AIMessage(content=response.clarification_response)],
            "lang": response.lang,
            "worker_results": {},
            "active_agent": "orchestrator",
            "target_route": "none",
            "suggested_questions": suggested,
        }

    return {
        "plan": response.model_dump(),
        "lang": response.lang,
        "worker_results": {},
        "active_agent": "orchestrator",
        "target_route": response.tasks[0].target if response.tasks else None,
        "suggested_questions": suggested,
    }

# Backward compatibility alias
async_supervisor_node = orchestrator_node


#############################
#>>>>>>> Worker Nodes <<<<<<#
#############################

async def events_node(state: AgentState, config: RunnableConfig = None) -> dict:
    """Specialist worker for events, summits, ticket bookings, space rentals, and sponsorships."""
    plan = state.get("plan") or {}
    tasks = plan.get("tasks", [])
    event_tasks = [t for t in tasks if t.get("target") == "events"]

    sub_query = " | ".join(t.get("sub_query", "") for t in event_tasks) if event_tasks else ""
    agent_messages = do_trim_messages(state["messages"])

    if sub_query:
        input_messages = agent_messages + [
            HumanMessage(content=f"[Focus Task for Events Specialist]: {sub_query}")
        ]
    else:
        input_messages = agent_messages

    result = await event_agent.ainvoke(
        {"messages": input_messages, "user_id": state.get("user_id")}, config=config
    )
    new_messages = result["messages"][len(input_messages):]

    response_text = ""
    for msg in reversed(new_messages):
        if isinstance(msg, AIMessage) and msg.content:
            response_text = msg.content
            break

    if not response_text and new_messages:
        last_m = new_messages[-1]
        response_text = getattr(last_m, "content", "")

    return {
        "worker_results": {"events": response_text},
    }


async def membership_node(state: AgentState, config: RunnableConfig = None) -> dict:
    """Specialist worker for membership tiers, company directory searches, and joining forms."""
    plan = state.get("plan") or {}
    tasks = plan.get("tasks", [])
    mem_tasks = [t for t in tasks if t.get("target") == "membership"]

    sub_query = " | ".join(t.get("sub_query", "") for t in mem_tasks) if mem_tasks else ""
    agent_messages = do_trim_messages(state["messages"])

    if sub_query:
        input_messages = agent_messages + [
            HumanMessage(content=f"[Focus Task for Membership Specialist]: {sub_query}")
        ]
    else:
        input_messages = agent_messages

    result = await membership_agent.ainvoke(
        {"messages": input_messages, "user_id": state.get("user_id")}, config=config
    )
    new_messages = result["messages"][len(input_messages):]

    response_text = ""
    for msg in reversed(new_messages):
        if isinstance(msg, AIMessage) and msg.content:
            response_text = msg.content
            break

    if not response_text and new_messages:
        last_m = new_messages[-1]
        response_text = getattr(last_m, "content", "")

    return {
        "worker_results": {"membership": response_text},
    }


async def general_node(state: AgentState, config: RunnableConfig = None) -> dict:
    """Specialist worker for business setup, RAG knowledge searches, HR recruitment, and press/media."""
    plan = state.get("plan") or {}
    tasks = plan.get("tasks", [])
    gen_tasks = [t for t in tasks if t.get("target") in ("general", "business")]

    sub_query = " | ".join(t.get("sub_query", "") for t in gen_tasks) if gen_tasks else ""
    agent_messages = do_trim_messages(state["messages"])

    if sub_query:
        input_messages = agent_messages + [
            HumanMessage(content=f"[Focus Task for General & B2B Specialist]: {sub_query}")
        ]
    else:
        input_messages = agent_messages

    result = await general_agent.ainvoke(
        {"messages": input_messages, "user_id": state.get("user_id")}, config=config
    )
    new_messages = result["messages"][len(input_messages):]

    response_text = ""
    for msg in reversed(new_messages):
        if isinstance(msg, AIMessage) and msg.content:
            response_text = msg.content
            break

    if not response_text and new_messages:
        last_m = new_messages[-1]
        response_text = getattr(last_m, "content", "")

    return {
        "worker_results": {"general": response_text},
    }


#############################
#>>>>>> Collector Node <<<<<#
#############################

async def collector_node(state: AgentState, config: RunnableConfig = None) -> dict:
    """
    Fan-in barrier node: Gathers worker outputs, resolves multi-intent queries,
    and synthesizes a single, polished response in the user's detected language.
    """
    results = state.get("worker_results") or {}
    if not results:
        return {"worker_results": {}}

    lang = state.get("lang", "en")
    last_human = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            last_human = msg.content
            break

    # Build the synthesis context from all active workers
    user_context = f"The user asked: {last_human}\n\nDomain specialist findings:\n"
    for domain, content in results.items():
        user_context += f"\n--- [{domain.upper()} SPECIALIST FINDINGS] ---\n{content}\n"

    clickable_questions = state.get("suggested_questions") or []
    if clickable_questions:
        user_context += (
            "\nClickable follow-up questions already shown separately to the user "
            "(do not repeat these in the closing offer):\n"
            + "\n".join(f"- {question}" for question in clickable_questions)
            + "\n"
        )

    user_context += (
        f"\nTask: Synthesize the above specialist findings into one unified, elegant, and structured response in '{lang}' language. "
        "Preserve and format all links, form URLs, email addresses, and specific dates clearly."
    )

    synthesis_prompt = COLLECTOR_SYSTEM_PROMPT.format(lang=lang)

    synthesized_response = await llm.ainvoke(
        [
            {"role": "system", "content": synthesis_prompt},
            {"role": "user", "content": user_context},
        ],
        config=config,
    )

    return {
        "messages": [synthesized_response],
        "worker_results": {},  # Reset for next turn
        "active_agent": "orchestrator",
        "target_route": None,
    }


#############################
#>>>>>>> Parent Graph <<<<<<#
#############################

parent_builder = StateGraph(AgentState)
Parent_memory = MemorySaver()

# Add Nodes
parent_builder.add_node("orchestrator_node", orchestrator_node)
parent_builder.add_node("events_node", events_node)
parent_builder.add_node("membership_node", membership_node)
parent_builder.add_node("general_node", general_node)
parent_builder.add_node("collector_node", collector_node)

# Connect START -> orchestrator_node
parent_builder.add_edge(START, "orchestrator_node")

# Conditional edges from orchestrator -> one or more workers, or END
parent_builder.add_conditional_edges(
    "orchestrator_node",
    route_from_orchestrator,
    {
        "events_node": "events_node",
        "membership_node": "membership_node",
        "general_node": "general_node",
        "END": END,
    },
)

# Fan-in: All workers -> collector_node
parent_builder.add_edge("events_node", "collector_node")
parent_builder.add_edge("membership_node", "collector_node")
parent_builder.add_edge("general_node", "collector_node")

# Collector -> END
parent_builder.add_edge("collector_node", END)

# Compile graph with checkpointer
compiled_parent = parent_builder.compile(checkpointer=Parent_memory)


def get_pending_approvals(result: dict) -> list[dict]:
    """Return human-readable approval payloads from a paused graph result."""
    pending = []
    for interrupt_item in result.get("__interrupt__", ()):
        value = getattr(interrupt_item, "value", interrupt_item)
        interrupt_id = getattr(interrupt_item, "id", None)
        if isinstance(value, dict):
            pending.append({"interrupt_id": interrupt_id, **value})
        else:
            pending.append({"interrupt_id": interrupt_id, "value": value})
    return pending


async def resume_parent_approval(
    config: RunnableConfig, interrupt_id: str, decision: str
):
    """Resume one pending database-write approval on its original thread."""
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be 'approve' or 'reject'.")
    if not interrupt_id:
        raise ValueError("interrupt_id is required.")
    return await compiled_parent.ainvoke(
        Command(resume={interrupt_id: {"type": decision}}),
        config=config,
    )

if __name__ == "__main__":
    from IPython.display import Image
    Image(compiled_parent.get_graph().draw_mermaid_png())
