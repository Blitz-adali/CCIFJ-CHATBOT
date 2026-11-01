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
