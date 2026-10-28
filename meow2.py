from dotenv import load_dotenv

load_dotenv()

import os

print("Tracing:", os.getenv("LANGSMITH_TRACING"))
print("API key loaded:", bool(os.getenv("LANGSMITH_API_KEY")))
print("LANGSMITH_AGENT_ID:", os.getenv("LANGSMITH_AGENT_ID"))

from langchain_core.runnables import RunnableLambda

chain = RunnableLambda(lambda x: {"result": x["value"] * 2})

result = chain.invoke({"value": 21})

print(result)

import sys
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

print("Python executable:", sys.executable)
print("Working directory:", Path.cwd())
print("Found .env:", find_dotenv())
print("Tracing:", os.getenv("LANGSMITH_TRACING"))
print("Project:", os.getenv("LANGSMITH_PROJECT"))
print("API key loaded:", bool(os.getenv("LANGSMITH_API_KEY")))