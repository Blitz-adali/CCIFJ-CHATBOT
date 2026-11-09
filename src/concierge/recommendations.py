"""Read-only conversation controls, independent of industry or service topic."""
import re
from langchain_core.messages import AIMessage, HumanMessage

_SKIP = re.compile(r'(?:skip(?:\s*,?\s*just answer)?|just answer|passer|passez|réponds simplement|スキップ|そのまま答えて)[.!。!\s]*',re.I)
_INTAKE = re.compile(r'you can (?:also )?skip|could you share|pouvez-vous préciser|vous pouvez.*(?:passer|ignorer)|教えてください',re.I)


def late_skip_source(messages):
    """A bare late skip requests a shorter existing answer, never fresh actions.

    Skipping actual intake and requests such as 'skip X and find Y' stay on the
    normal router. Short ambiguous prior messages also stay there.
    """
    if not messages or not isinstance(messages[-1],HumanMessage):
        return None
    text=messages[-1].content
    if not isinstance(text,str) or not _SKIP.fullmatch(text.strip()):
        return None
    previous=next((m.content for m in reversed(messages[:-1]) if isinstance(m,AIMessage) and not m.tool_calls and isinstance(m.content,str)),None)
    if not previous or _INTAKE.search(previous) or '```' in previous or '~~~' in previous:
        return None
    if '\n\n' not in previous and len(previous.split())<80:
        return None
    return previous
