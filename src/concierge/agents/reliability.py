"""Tool failures are data for the agent; interrupted/uncertain writes propagate."""
import json
import logging
from secrets import token_hex

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp

from concierge.evidence import turn_evidence, record_warning

logger = logging.getLogger(__name__)
WRITE_TOOLS = frozenset({
    'book_event_ticket','book_space','cancel_event_booking','cancel_space_reservation',
    'change_event_booking','change_space_reservation','subscribe_newsletter','submit_staff_request',
})


class ConciergeToolBoundary(AgentMiddleware):
    async def awrap_tool_call(self, request, handler):
        name = request.tool_call['name']
        evidence = turn_evidence.get()
        if name in WRITE_TOOLS:
            # This wrapper is reached on actual execution, after HITL permits
            # the action. An exception afterwards can mean an uncertain write.
            if evidence is not None:
                evidence.write_started = True
            return await handler(request)
        try:
            if evidence is not None and evidence.read_failures.get(name, 0) >= 2:
                return self.unavailable(request)
            return await handler(request)
        except GraphBubbleUp:
            # Approvals are control flow, never ordinary tool failures.
            raise
        except Exception:
            reference = token_hex(6)
            logger.exception('Read tool failed tool=%s reference=%s', name, reference)
            record_warning('tool_unavailable')
            if evidence is not None:
                evidence.read_failures[name] = evidence.read_failures.get(name, 0) + 1
            return self.unavailable(request)

    @staticmethod
    def unavailable(request):
        return ToolMessage(
            content=json.dumps({'status':'unavailable','tool':request.tool_call['name'],
                                'message':'This lookup could not be completed. This is not an empty search result. Explain the limitation; do not invent facts, prices, capacity or availability. Offer a relevant staff inquiry if useful. Do not keep repeating the failing lookup.'}),
            tool_call_id=request.tool_call['id'], name=request.tool_call['name'], status='error',
        )
