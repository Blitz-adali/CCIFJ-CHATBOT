from langchain.agents import create_agent
from langchain.agents import AgentState
from langchain.agents.middleware import HumanInTheLoopMiddleware
from typing_extensions import NotRequired
from . import schema_prompts
from . import tools as t
from . import model


class ConciergeAgentState(AgentState):
    """Carry the parent graph's authenticated user ID into tool runtimes."""

    user_id: NotRequired[str | None]


def _write_approval(tool_names: list[str]) -> HumanInTheLoopMiddleware:
    """Require an explicit approve/reject decision before database writes run."""
    return HumanInTheLoopMiddleware(
        interrupt_on={
            name: {"allowed_decisions": ["approve", "reject"]}
            for name in tool_names
        },
        description_prefix="Review this database change before it is saved",
        interrupt_mode="per_call",
    )

#################################################
# 1. Events Agent
#################################################
event_agent = create_agent(
    model.llm,
    tools=[
        t.list_available_events,
        t.get_event_details,
        t.book_event_ticket,
        t.book_space,
        t.get_ccifj_contact,
        t.search_vectordb,
        t.return_to_main_menu,
    ],
    middleware=[_write_approval(["book_event_ticket", "book_space"])],
    state_schema=ConciergeAgentState,
    system_prompt=schema_prompts.EVENT_AGENT_PROMPT,
)
# Alias for backwards-compatibility with body.py
envet_agent = event_agent


#################################################
# 2. Membership Agent
#################################################
membership_agent = create_agent(
    model.llm,
    tools=[
        t.get_membership_tier_info,
        t.search_member_directory,
        t.list_available_industries_and_regions,
        t.get_official_form,
        t.return_to_main_menu,
    ],
    state_schema=ConciergeAgentState,
    system_prompt=schema_prompts.MEMBERSHIP_AGENT_PROMPT,
)


#################################################
# 3. General & Business Agent
#################################################
general_agent = create_agent(
    model.llm,
    tools=[
        t.search_vectordb,
        t.get_ccifj_contact,
        t.get_official_form,
        t.subscribe_newsletter,
        t.return_to_main_menu,
    ],
    middleware=[_write_approval(["subscribe_newsletter"])],
    state_schema=ConciergeAgentState,
    system_prompt=schema_prompts.GENERAL_AGENT_PROMPT,
)
# Alias for backwards-compatibility with body.py
business_agent = general_agent
