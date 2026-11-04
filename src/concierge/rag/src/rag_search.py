from __future__ import annotations
from .config import EMBED_MODEL_NAME, COHERE_API_KEY, COHERE_RERANK_MODEL
import logging
import time
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from concierge.evidence import record_warning
from .diagnostics import record as diagnose

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import bm25s
    from chromadb.api.models.Collection import Collection
    from sentence_transformers import SentenceTransformer


@lru_cache(maxsize=1)
def get_tokenizer():
    from transformers import AutoTokenizer
    try:
        return AutoTokenizer.from_pretrained(EMBED_MODEL_NAME, local_files_only=True)
    except OSError:
        return AutoTokenizer.from_pretrained(EMBED_MODEL_NAME)


@lru_cache(maxsize=1)
def get_rerank_client():
    import cohere
    return cohere.ClientV2(api_key=COHERE_API_KEY, timeout=8)

import threading

_collection: Collection | None = None
_collection_lock = threading.Lock()


def get_collection() -> Collection:
    """Open the Chroma collection once per process. Creating a client for every
    search crashes when the agent runs two searches in parallel."""
    global _collection
    with _collection_lock:
        if _collection is None:
            from .index_init import create_collection
            _collection = create_collection(overwrite=False)
        return _collection



def search_passages(retriever, model, query: str, original_n=20, hybrid_n=10, reranked_n=5) -> list[dict]:
    """Hybrid retrieval with stable chunk IDs, source URLs and bounded reranking.

    Reranker downtime degrades to fused ranking; it never discards retrieved
    evidence or lets the model improvise an answer in place of a failed lookup.
    """
    if not 0 < reranked_n <= hybrid_n <= original_n:
        raise ValueError("Expected 0 < reranked_n <= hybrid_n <= original_n")
    if not query.strip() or len(query) > 4000:
        raise ValueError("Search query must be nonempty and at most 4000 characters")
    try:
        if len(get_tokenizer().encode(query, add_special_tokens=False)) > 450:
            raise ValueError("Search query exceeds 450 tokens")
    except ValueError:
        raise
    except Exception as exc:
        diagnose('tokenizer', exc)
        # Keyword lookup does not need a Hugging Face tokenizer. Its failure
        # must not take down otherwise usable local evidence.
        logger.exception("Search tokenizer unavailable; using character bound")
        record_warning('retrieval_tokenizer_unavailable')
    started = time.perf_counter()
    results1, results2, failed = [], [], False
    try:
        if model is None:
            raise RuntimeError('Embedding model unavailable')
        collection = get_collection()
        count = collection.count()
        results1 = semantic_search(query, collection, model, min(original_n, count)) if count else []
        diagnose('semantic')
    except Exception as exc:
        diagnose('semantic', exc)
        logger.exception('Semantic retrieval unavailable; keeping keyword search')
        record_warning('retrieval_semantic_unavailable')
        failed = True
    try:
        if retriever is None:
            raise RuntimeError('Keyword index unavailable')
        results2 = lexical_search(retriever, query, original_n)
        diagnose('lexical')
    except Exception as exc:
        diagnose('lexical', exc)
        logger.exception('Keyword retrieval unavailable; keeping semantic search')
        record_warning('retrieval_lexical_unavailable')
        failed = True
    had_records = bool(results1 or results2)
    results1 = valid_records(results1, 'content')
    results2 = valid_records(results2, 'text')
    # The persisted vector index and BM25 corpus can use independent chunk
    # layouts. Equal numeric IDs do not prove equal text or source URLs.
    results1 = [{**r, 'id': f"vector:{r['id']}"} for r in results1]
    if not results1 and not results2 and (failed or had_records):
        raise RetrievalUnavailable('No retrieval backend could provide evidence')
    fused = rrf(results1, results2, hybrid_n)
    metadata = {str(r["id"]): r for r in [*results1, *results2]}
    docs = []
    for chunk_id, (_, text) in fused:
        record = metadata.get(str(chunk_id), {})
        # Older checked-in BM25 records store their URL inside the passage.
        url = record.get("url") or next((line[5:].strip() for line in text.splitlines() if line.startswith("url: ")), "")
        try:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname:
                continue
        except ValueError:
            continue
        title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")), parsed.hostname)
        docs.append({"id": str(chunk_id), "text": text, "url": url, "title": title})
    if not docs:
        if results1 or results2:
            raise RetrievalUnavailable('Retrieved records contain no usable source URLs')
        return []
    try:
        ranked = rerank(query, docs, min(reranked_n, len(docs)))
        diagnose('rerank')
    except Exception as exc:
        diagnose('rerank', exc)
        logger.warning("Reranking unavailable (%s); using fused ranking", type(exc).__name__)
        record_warning("retrieval_rerank_fallback")
        ranked = docs[:reranked_n]
    from concierge.source_context import event_facts
    ranked = [{**p, "text": p["text"] + event_facts(p["url"])} for p in ranked]
    logger.info("retrieval duration_ms=%.1f candidates=%d returned=%d", (time.perf_counter()-started)*1000, len(docs), len(ranked))
    return ranked


def search(retriever, model, query, original_n, hybrid_n, reranked_n) -> list[str]:
    """Compatibility interface for CLI and existing callers."""
    return [f"Source: {p['url']}\nChunk: {p['id']}\n{p['text']}" for p in search_passages(retriever, model, query, original_n, hybrid_n, reranked_n)]

class RetrievalUnavailable(RuntimeError):
    """A failed lookup is not evidence that nothing matches."""


def valid_records(records, text_field):
    valid=[]
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get(text_field), str):
            record_warning('retrieval_invalid_record')
            continue
        try:
            int(record['id'])
        except (KeyError, TypeError, ValueError):
            record_warning('retrieval_invalid_record')
            continue
        valid.append(record)
    return valid


def rrf(results1, results2, top_n: int, k: int = 60) -> list[tuple[int | str, tuple[float, str]]]:
    final_ranks = {}
    for records, text_field in ((results1, 'content'), (results2, 'text')):
        for rank, result in enumerate(records):
            raw_id = str(result['id'])
            key = int(raw_id) if raw_id.isdecimal() else raw_id
            final_ranks[key] = (final_ranks.get(key, (0, ''))[0] + 1 / (k + rank + 1), result[text_field])

    return sorted(final_ranks.items(), key=lambda x: x[1][0], reverse=True)[:top_n]

def lexical_search(retriever: bm25s.BM25, query: str, limit: int):
    import bm25s
    query_tokens = bm25s.tokenize(query)
    corpus = getattr(retriever, "corpus", None)
    if corpus is not None:
        limit = min(limit, len(corpus))
    if limit <= 0:
        return []
    docs, scores = retriever.retrieve(query_tokens, k=limit)
    return docs.tolist()[0]

def rerank(query: str, docs: list[dict], n: int = 5):
    if not COHERE_API_KEY:
        raise ValueError("COHERE_API_KEY is missing")

    client = get_rerank_client()

    response = client.rerank(
        model=COHERE_RERANK_MODEL,
        query=query,
        documents=[doc["text"] for doc in docs],
        top_n=n,
    )

    return [
        {
            **docs[item.index],
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
            "url": (metadata or {}).get("url", ""),
            "distance": distance,
        }
        for result_id, content, metadata, distance in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        )
    ]
