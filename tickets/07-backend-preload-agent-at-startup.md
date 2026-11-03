# 07 · Load the agent when the server starts, not on the first message

**Area:** Backend (`src/concierge/main.py`) · **Priority:** Medium

## Problem
The first chat message after the server starts is slow, because the agent and the search models are only loaded at that moment.

Measured on the dev machine:
| Step | When it happens today | Time |
|---|---|---|
| `import concierge.agents` (LangGraph graph, LangChain, sentence-transformers, transformers, chromadb, cohere) | First chat message, inside `get_agent_backend()` → `AgentBackend.__init__` | **~12 s** with a warm disk; **~74 s** right after a reboot |
| Embedding model `SentenceTransformer(EMBED_MODEL_NAME)` | First question that uses `search_vectordb` | **~6 s** |
| BM25 index `bm25s.BM25.load` | Same | ~0.1 s |

## Cause
- `src/concierge/api/agent_backend.py:253`: `get_agent_backend()` is an `@lru_cache` singleton, created on the first request. Its `__init__` imports `concierge.agents`, which builds the whole graph. This was done on purpose so `/health`, auth and history work without loading the models.
- `src/concierge/agents/tools.py`: `_get_embed_model()` and `_get_retriever()` load on first use.

## Fix
The agent side is ready: `concierge.agents.warm_up()` loads the embedding model and the BM25 index (synchronous, safe to call more than once).

In `lifespan` (`src/concierge/main.py`), start a **background** warm-up after the DB setup, so the server still starts quickly and `/health` answers right away:

```python
import asyncio, logging
from concierge.api.agent_backend import get_agent_backend

logger = logging.getLogger(__name__)

def _warm_agent():
    get_agent_backend()               # imports and builds the agent graph (cached singleton)
    from concierge.agents import warm_up
    warm_up()                         # embedding model + BM25 index

@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()
    await create_db_and_tables()
    warm_task = asyncio.create_task(asyncio.to_thread(_warm_agent))
    warm_task.add_done_callback(
        lambda t: t.exception() and logger.error("Agent warm-up failed", exc_info=t.exception())
    )
    try:
        yield
    finally:
        await engine.dispose()
```

Notes:
- Run it in a thread (`asyncio.to_thread`) so the event loop isn't blocked while the models load.
- If a chat request arrives while warm-up is still running, `get_agent_backend()` (lru_cache) and the module import lock make it wait for the same load instead of loading twice. Optional: add a `threading.Lock` around `get_agent_backend()` to be fully safe.
- If warm-up fails (for example a missing model file), log it and keep the server running. The first chat request will try again and show the real error, the same as today.
- Optional: add an `"agent_ready": true/false` field to `/health` so the frontend or `start.sh` can wait for it.
- Use a single worker, as before. Each worker would load its own copy of the models.

## Acceptance
- After the server starts and warm-up has finished (log line, or `agent_ready: true`), the first message is answered about as fast as later messages.
- `/health` still answers immediately while warm-up runs.
- A warm-up failure doesn't stop the server.
