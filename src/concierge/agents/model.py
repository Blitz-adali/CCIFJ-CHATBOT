"""Use the validated provider explicitly; never silently switch providers."""
import os

from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from concierge.config import get_settings

settings = get_settings()
if settings.uses_ollama:
    llm = ChatOllama(model=settings.model_name, temperature=0, keep_alive=600)
    llm_routing = ChatOllama(model=settings.router_model_name, temperature=0, think=False, keep_alive=600)
else:
    llm = ChatOpenAI(model=settings.model_name, timeout=60, max_retries=1)
    llm_routing = ChatOpenAI(model=settings.router_model_name, timeout=30, max_retries=1)


# OpenAI's built-in web search, given to the agents that need fresh information.
# It runs on OpenAI's side (Responses API), so it only exists for OpenAI models.
#   CCIFJ_WEB_SEARCH          on/off, default on.
#   CCIFJ_WEB_SEARCH_DOMAINS  comma-separated allowed domains (e.g. ccifj.or.jp);
#                             default "*" searches the whole web.
_DEFAULT_WEB_DOMAINS = "*"


def web_search_tools() -> list[dict]:
    """The web search tool definition for `create_agent`, or [] when unavailable."""
    if isinstance(llm, ChatOllama):
        return []
    if os.getenv("CCIFJ_WEB_SEARCH", "on").strip().lower() in {"0", "false", "no", "off"}:
        return []
    raw = os.getenv("CCIFJ_WEB_SEARCH_DOMAINS", _DEFAULT_WEB_DOMAINS).strip()
    tool: dict = {"type": "web_search"}
    if raw and raw != "*":
        domains = [d.strip() for d in raw.split(",") if d.strip()]
        tool["filters"] = {"allowed_domains": domains}
    return [tool]


web_search_enabled = bool(web_search_tools())
