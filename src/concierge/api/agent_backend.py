"""Adapter over the existing agent's public functions; no agent changes.

Ordinary turns reconstruct context from SQL history on a fresh graph thread.
Approval resumption uses the exact interrupted thread and never replays writes.
"""

from functools import lru_cache


class ApprovalStateUnavailable(Exception):
    pass


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

        messages = [
            (HumanMessage if row.role.value == "user" else AIMessage)(
                content=row.content, id=f"db:{row.message_id}"
            )
            for row in history if row.role.value in {"user", "assistant"}
        ]
        result = await self.graph.ainvoke(
            {"messages": messages, "user_id": str(user_id) if user_id is not None else None},
            config=self.config(thread_id),
        )
        return self.format_result(result)

    async def resume(self, thread_id, interrupt_id, decision):
        snapshot = await self.graph.aget_state(self.config(thread_id))
        interrupts = getattr(snapshot, "interrupts", ())
        if not interrupts:
            interrupts = tuple(i for task in snapshot.tasks for i in task.interrupts)
        if not any(item.id == interrupt_id for item in interrupts):
            raise ApprovalStateUnavailable()
        result = await self.resume_approval(self.config(thread_id), interrupt_id, decision)
        return self.format_result(result)

    def format_result(self, result):
        from langchain_core.messages import AIMessage

        approvals = self.pending(result)
        reply = ""
        if approvals:
            reply = "Please approve or reject the proposed action before it is saved."
        else:
            for message in reversed(result.get("messages", [])):
                if isinstance(message, AIMessage) and not message.tool_calls:
                    content = message.content
                    if isinstance(content, str):
                        reply = content
                    else:
                        reply = "".join(
                            block if isinstance(block, str) else block.get("text", "")
                            for block in content
                            if isinstance(block, (str, dict))
                        )
                    if reply:
                        break
        if not reply:
            raise RuntimeError("Agent returned no reply.")
        return {
            "reply": reply, "pending_approvals": approvals,
            "suggested_questions": result.get("suggested_questions") or [],
        }


@lru_cache
def get_agent_backend() -> AgentBackend:
    return AgentBackend()
