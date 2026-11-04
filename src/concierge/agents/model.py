"""Use the validated provider explicitly; never silently switch providers."""
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
