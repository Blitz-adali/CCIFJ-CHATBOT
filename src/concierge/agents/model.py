from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import os

load_dotenv()
model_name = os.getenv("model_name")
model_type = os.getenv("model_type")
router_model_name = os.getenv("router_model_name")
trim_token = int(os.getenv("trim_messages_max_token", 1000))


try:
    if model_type.lower() == "qwen":
        llm = ChatOllama(model=model_name, temperature=0)
        llm_routing = ChatOllama(model=router_model_name, temperature=0, think=False, keep_alive=0)
    else:
        llm = ChatOpenAI(model=model_name)
        llm_routing = ChatOpenAI(model=router_model_name)
except:
    llm = ChatOllama(model=model_name, temperature=0)
    llm_routing = ChatOllama(model=router_model_name, temperature=0, think=False, keep_alive=0)


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
