"""Source fingerprint for matching a running demo to the local checkout."""
from hashlib import sha256
from pathlib import Path


def build_signature():
    root=Path(__file__).resolve().parent
    files=('main.py','api/health.py','api/answers.py','api/agent_backend.py','api/chat.py','agents/body.py',
           'agents/schema_prompts.py','agents/langchain_agents.py','agents/model.py','agents/tools.py',
           'conversation.py','recommendations.py','drafts.py','source_context.py','resources.py','recovery.py','agents/reliability.py','evidence.py',
           'rag/src/rag_search.py','rag/src/diagnostics.py','rag/src/search_cache.py','db/relationaldb/crud.py',
           'api/catalog.py','api/events.py','api/paths.py','data/predefined_qa.json','data/predefined_qa_i18n.json')
    return sha256(b''.join((root/path).read_bytes() for path in files)).hexdigest()[:12]
