from .config import EMBED_MODEL_NAME, COHERE_API_KEY, COHERE_RERANK_MODEL
import cohere
import logging
import os
import threading
import time
from collections import OrderedDict, deque
from typing import Literal
from chromadb.api.models.Collection import Collection
from sentence_transformers import (SentenceTransformer)
from .index_init import create_collection
from transformers import AutoTokenizer
import bm25s

logger = logging.getLogger(__name__)

_collection: Collection | None = None
_collection_lock = threading.Lock()


def get_collection() -> Collection:
    """Open the Chroma collection once per process. Creating a client for every
    search crashes when the agent runs two searches in parallel."""
    global _collection
    with _collection_lock:
        if _collection is None:
            _collection = create_collection(overwrite=False)
        return _collection


# --- Reranking limits -------------------------------------------------------
# Reranking is an external call (Cohere) and must never take an answer down with
# it. A trial key allows 10 calls a minute, and one market-entry question can
# run 6 or more searches across three agents. So: stay under the limit, stop
# calling for a while after a refusal, and fall back to the fused ranking.
#   CCIFJ_RERANK_PER_MINUTE   calls allowed per minute, default 9 (0 = never call).
#   CCIFJ_RERANK_TIMEOUT      seconds to wait for one rerank, default 8.
def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


RERANK_PER_MINUTE = int(_env_number("CCIFJ_RERANK_PER_MINUTE", 9))
RERANK_TIMEOUT = _env_number("CCIFJ_RERANK_TIMEOUT", 8)
_RERANK_PAUSE_SECONDS = 60
_rerank_lock = threading.Lock()
_rerank_calls: deque[float] = deque()
_rerank_paused_until = 0.0
_cohere_client = None

# Identical searches inside a conversation (and across the three agents of one
# turn) reuse the result instead of searching and reranking again.
_SEARCH_CACHE_SIZE = 256
_SEARCH_CACHE_SECONDS = 600
_search_cache: OrderedDict[tuple, tuple[float, list[str]]] = OrderedDict()
_search_cache_lock = threading.Lock()


def _rerank_allowed() -> bool:
    """Reserve one rerank call if the per-minute budget and the pause allow it."""
    if not COHERE_API_KEY or RERANK_PER_MINUTE <= 0:
        return False
    now = time.monotonic()
    with _rerank_lock:
        if now < _rerank_paused_until:
            return False
        while _rerank_calls and now - _rerank_calls[0] >= 60:
            _rerank_calls.popleft()
        if len(_rerank_calls) >= RERANK_PER_MINUTE:
            return False
        _rerank_calls.append(now)
        return True


def _pause_rerank() -> None:
    global _rerank_paused_until
    with _rerank_lock:
        _rerank_paused_until = time.monotonic() + _RERANK_PAUSE_SECONDS


def _get_cohere_client():
    global _cohere_client
    if _cohere_client is None:
        _cohere_client = cohere.ClientV2(api_key=COHERE_API_KEY, timeout=RERANK_TIMEOUT)
    return _cohere_client


def search(retriever: bm25s.BM25, model: SentenceTransformer, query: str, original_n: int, hybrid_n: int, reranked_n: int) -> list[str]:
    """Retrieve and rerank source chunks relevant to a query.

    Args:
        model: Sentence-transformer model used to embed the query.
        query: Text to search for; queries longer than 450 tokenizer tokens
            return a message instead of performing the search.
        original_n: Number of vector-search results to retrieve for reranking.
        reranked_n: Number of reranked results to return.

    Returns:
        Formatted strings containing each reranked result's URL and text.
    """

    if (hybrid_n > original_n):
        return ["hybrid_n can't be greater than original_n"]
    if (reranked_n >= hybrid_n):
        return ["reranked_n can't be equal to or greater than hybrid_n"]

    key = (" ".join(query.split()).casefold(), original_n, hybrid_n, reranked_n)
    now = time.monotonic()
    with _search_cache_lock:
        cached = _search_cache.get(key)
        if cached and now - cached[0] < _SEARCH_CACHE_SECONDS:
            _search_cache.move_to_end(key)
            return list(cached[1])

    collection = get_collection()
    # Either search can fail on its own (a model or index problem); the other
    # one still gives a usable ranking.
    try:
        results1 = semantic_search(query, collection=collection, model=model, limit=original_n)
    except Exception:
        logger.exception("Semantic search failed; using keyword search only")
        results1 = []
    try:
        results2 = lexical_search(retriever, query, limit=original_n)
    except Exception:
        logger.exception("Keyword search failed; using semantic search only")
        results2 = []
    if not results1 and not results2:
        raise RuntimeError("Knowledge search is unavailable: both searches failed.")

    docs = rrf(results1, results2, hybrid_n)
    docs = [{"id": doc[0], "text": doc[1][1]} for doc in docs]
    rerank_docs, reranked = rerank_or_fused(query, docs, reranked_n)
    texts = [doc["text"] for doc in rerank_docs]
    # Keep a fused-only result for a short time: the reranker may be back soon.
    if reranked:
        with _search_cache_lock:
            _search_cache[key] = (now, texts)
            while len(_search_cache) > _SEARCH_CACHE_SIZE:
                _search_cache.popitem(last=False)
    return texts


def rerank_or_fused(query: str, docs: list[dict], n: int) -> tuple[list[dict], bool]:
    """Rerank with Cohere when possible; otherwise keep the fused order.

    Returns the documents and whether they were reranked. Never raises: a rate
    limit, a timeout, or a missing key must not fail the user's answer.
    """
    if not docs:
        return [], False
    if not _rerank_allowed():
        return docs[:n], False
    try:
        return rerank(query, docs, n), True
    except Exception as error:
        status = getattr(error, "status_code", None)
        if status == 429:
            _pause_rerank()
            logger.warning("Rerank rate limit reached; using the fused ranking for %ss", _RERANK_PAUSE_SECONDS)
        else:
            logger.warning("Rerank failed (%s); using the fused ranking", type(error).__name__)
        return docs[:n], False

def rrf(results1, results2, top_n: int, k: int = 60) -> list[tuple[Literal["id"], tuple[int, str]]]:
    final_ranks = {}
    for rank, result in enumerate(results1):
        final_ranks[int(result["id"])] = (final_ranks.get(int(result["id"]), (0, ""))[0] + (1 / (k + rank + 1)), result["content"])

    for rank, result in enumerate(results2):
        final_ranks[int(result["id"])] = (final_ranks.get(int(result["id"]), (0, ""))[0] + (1 / (k + rank + 1)), result["text"])

    return sorted(final_ranks.items(), key=lambda x: x[1][0], reverse=True)[:top_n]

def lexical_search(retriever: bm25s.BM25, query: str, limit: int):
    query_tokens = bm25s.tokenize(query)
    docs, scores = retriever.retrieve(query_tokens, k=limit)
    return docs.tolist()[0]

def rerank(query: str, docs: list[dict], n: int = 5):
    if not COHERE_API_KEY:
        raise ValueError("COHERE_API_KEY is missing")

    response = _get_cohere_client().rerank(
        model=COHERE_RERANK_MODEL,
        query=query,
        documents=[doc["text"] for doc in docs],
        top_n=n,
        # Fail fast: the caller falls back to the fused ranking.
        request_options={"max_retries": 0, "timeout_in_seconds": max(1, int(RERANK_TIMEOUT))},
    )

    return [
        {
            "id": docs[item.index]["id"],
            "text": docs[item.index]["text"],
            "score": item.relevance_score,
        }
        for item in response.results
    ]

def semantic_search(
    query: str,
    collection: Collection,
    model: SentenceTransformer,
    limit: int = 5,
) -> list[dict]:
    """Retrieve vector-search results ordered by ascending distance.

    Args:
        query: Text to encode and search for.
        collection: ChromaDB collection containing indexed document chunks.
        model: Sentence-transformer model used to embed the query.
        limit: Maximum number of matching chunks to retrieve.

    Returns:
        Result dictionaries containing each chunk's ID, content, URL, and
        distance from the query.
    """
    query_embedding = model.encode(
        f"query: {query}",
        normalize_embeddings=True,
    )
    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=limit,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {
            "id": result_id,
            "content": content,
            "url": metadata["url"],
            "distance": distance,
        }
        for result_id, content, metadata, distance in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        )
    ]
