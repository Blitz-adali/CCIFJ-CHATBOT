"""Files staff upload as agent sources: chunk, embed and index them next to the website index.

The website crawl index under src/concierge (Chroma + BM25) is tracked in git and never
changes here. Uploads go to a separate store under var/knowledge (or CCIFJ_KNOWLEDGE_DIR):

- a Chroma collection with one vector per chunk (metadata: url, doc_id), and
- a combined BM25 index (website chunks + upload chunks), rebuilt after every upload or
  delete. Search loads it instead of the website-only index when it exists.

Chunk IDs must be integers (search drops other IDs), so upload chunks use
UPLOAD_ID_BASE + doc_id * MAX_CHUNKS_PER_DOC + n, far above the website's chunk IDs.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

UPLOAD_ID_BASE = 10_000_000
MAX_CHUNKS_PER_DOC = 10_000
CHUNK_TOKENS = 450
COLLECTION = "ccifj_uploads"
# Uploads without a public link are cited by this internal reference (no clickable link).
INTERNAL_SCHEME = "ccifj-doc"

_lock = threading.RLock()
_collection = None
_client = None


def store_dir() -> Path:
    configured = os.getenv("CCIFJ_KNOWLEDGE_DIR", "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parents[2] / "var" / "knowledge"


def bm25_dir() -> Path:
    return store_dir() / "bm25"


def document_url(doc_id: int, source_url: str | None) -> str:
    return source_url or f"{INTERNAL_SCHEME}://{doc_id}"


def chunk_id(doc_id: int, index: int) -> int:
    return UPLOAD_ID_BASE + doc_id * MAX_CHUNKS_PER_DOC + index


def chroma_client():
    """One Chroma client for everything stored under var/knowledge (uploads, matching)."""
    global _client
    with _lock:
        if _client is None:
            import chromadb
            _client = chromadb.PersistentClient(path=str(store_dir() / "chroma"))
        return _client


def upload_collection():
    global _collection
    with _lock:
        if _collection is None:
            _collection = chroma_client().get_or_create_collection(name=COLLECTION, metadata={"hnsw:space": "cosine"})
        return _collection


def reset_for_tests() -> None:
    """Forget cached handles (tests point CCIFJ_KNOWLEDGE_DIR at a temp folder)."""
    global _collection, _client
    with _lock:
        _collection = None
        _client = None
    _base_records.cache_clear()


def split_text(title: str, url: str, text: str) -> list[str]:
    """Token-bounded chunks, each starting with the title and source like the website chunks."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from concierge.rag.src.rag_search import get_tokenizer
    splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
        get_tokenizer(), chunk_size=CHUNK_TOKENS, chunk_overlap=CHUNK_TOKENS // 10,
        separators=["\n\n", "\n", " ", ""],
    )
    chunks = [piece.strip() for piece in splitter.split_text(text) if piece.strip()]
    return [f"# {title}\nurl: {url}\n{piece}" for piece in chunks[:MAX_CHUNKS_PER_DOC]]


def index_document(doc_id: int, title: str, url: str, text: str) -> int:
    """Embed and store a document's chunks, then rebuild keyword search. Returns the chunk count."""
    from concierge.agents.tools import _get_embed_model
    chunks = split_text(title, url, text)
    if not chunks:
        return 0
    model = _get_embed_model()
    with _lock:
        collection = upload_collection()
        for start in range(0, len(chunks), 16):
            batch = chunks[start:start + 16]
            vectors = model.encode([f"passage: {chunk}" for chunk in batch], normalize_embeddings=True,
                                   show_progress_bar=False)
            collection.upsert(
                ids=[str(chunk_id(doc_id, start + n)) for n in range(len(batch))],
                embeddings=vectors.tolist(), documents=batch,
                metadatas=[{"url": url, "doc_id": doc_id} for _ in batch],
            )
        rebuild_keyword_index()
    return len(chunks)


def remove_document(doc_id: int) -> None:
    with _lock:
        upload_collection().delete(where={"doc_id": doc_id})
        rebuild_keyword_index()


@lru_cache(maxsize=1)
def _base_records() -> tuple[dict, ...]:
    """The website chunks from the tracked BM25 index."""
    import bm25s
    from concierge.rag.src.config import INDEX_PATH
    retriever = bm25s.BM25.load(str(INDEX_PATH), load_corpus=True)
    return tuple(retriever.corpus)


def _upload_records() -> list[dict]:
    stored = upload_collection().get(include=["documents", "metadatas"])
    return [{"id": int(chunk), "text": text, "url": (meta or {}).get("url", "")}
            for chunk, text, meta in zip(stored["ids"], stored["documents"], stored["metadatas"])]


def rebuild_keyword_index() -> None:
    """Combine website and upload chunks into one BM25 index and make search use it."""
    import bm25s
    with _lock:
        uploads = _upload_records()
        target = bm25_dir()
        if not uploads:
            shutil.rmtree(target, ignore_errors=True)
            retriever = None
        else:
            records = [*_base_records(), *uploads]
            retriever = bm25s.BM25(corpus=records)
            retriever.index(bm25s.tokenize([record.get("text") or "" for record in records]), show_progress=False)
            staging = target.with_name("bm25.tmp")
            shutil.rmtree(staging, ignore_errors=True)
            retriever.save(str(staging))
            shutil.rmtree(target, ignore_errors=True)
            staging.rename(target)
        _use_retriever(retriever)


def _use_retriever(retriever) -> None:
    """Swap the search tool's cached keyword index and drop cached search results."""
    from concierge.agents import tools
    from concierge.rag.src.search_cache import public_search_cache
    with tools._retriever_lock:
        tools._retriever = retriever  # None makes the next search reload from disk
    public_search_cache.clear()


def semantic_upload_results(query_embedding: list[float], limit: int) -> list[dict]:
    """Nearest upload chunks, in the same shape as rag_search.semantic_search."""
    collection = upload_collection()
    count = collection.count()
    if not count:
        return []
    results = collection.query(query_embeddings=[query_embedding], n_results=min(limit, count),
                               include=["documents", "metadatas", "distances"])
    return [{"id": rid, "content": content, "url": (meta or {}).get("url", ""), "distance": distance}
            for rid, content, meta, distance in zip(results["ids"][0], results["documents"][0],
                                                     results["metadatas"][0], results["distances"][0])]
