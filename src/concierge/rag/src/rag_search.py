from .config import EMBED_MODEL_NAME, COHERE_API_KEY, COHERE_RERANK_MODEL
import cohere
from typing import Literal
from chromadb.api.models.Collection import Collection
from sentence_transformers import (SentenceTransformer)
from .index_init import create_collection
from transformers import AutoTokenizer
import bm25s



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
    tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL_NAME)
    token_ids = tokenizer.encode(
    query,
    add_special_tokens=False,
    )
    if (len(token_ids) > 450):
        return ["Query length is larger than 450 tokens"]
    collection = create_collection(overwrite=False)
    results1 = semantic_search(query, collection=collection, model=model, limit=original_n)
    results2 = lexical_search(retriever, query, limit=original_n)

    docs = rrf(results1, results2, hybrid_n)
    docs = [{"id": doc[0], "text": doc[1][1]} for doc in docs]
    rerank_docs = rerank(query, docs, reranked_n)
    return [doc["text"] for doc in rerank_docs]

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

    client = cohere.ClientV2(api_key=COHERE_API_KEY)

    response = client.rerank(
        model=COHERE_RERANK_MODEL,
        query=query,
        documents=[doc["text"] for doc in docs],
        top_n=n,
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
