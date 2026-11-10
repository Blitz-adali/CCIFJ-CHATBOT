"""Files staff upload as agent sources: chunk, embed and add them to the website collection.

Uploaded files are added ON TOP of the scraped website chunks, in the same Chroma collection
(rag config COLLECTION_NAME, "ccifj_documents"). Each upload chunk:

- starts with a first line naming the file it came from: "source: <file name>", then the
  same "# <title>" / "url: <link>" header as the website chunks;
- has metadata {url, doc_id, source_file, kind: "upload"}, so a document's chunks can be
  listed or deleted without touching the website chunks (which have no doc_id);
- has an ID that cannot overlap the website's: UPLOAD_ID_BASE + doc_id * MAX_CHUNKS_PER_DOC + n.
  Website chunk IDs are 0..N (N ≈ 5,500 today); indexing refuses to overwrite any existing
  chunk that belongs to something else.

Keyword search uses a combined BM25 index (website + uploads) under var/knowledge
(or CCIFJ_KNOWLEDGE_DIR), rebuilt after every upload or delete.

The website collection is rebuilt from scratch when the site is re-crawled, and it is tracked
in git. The extracted text of every upload is therefore also kept under var/knowledge/texts,
and `python -m concierge.knowledge reindex` puts all uploads back into the collection.
"""

from __future__ import annotations

import json
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
UPLOAD_KIND = "upload"
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


def texts_dir() -> Path:
    """Extracted text of every upload, so uploads can be re-added after the website index is rebuilt."""
    return store_dir() / "texts"


def document_url(doc_id: int, source_url: str | None) -> str:
    return source_url or f"{INTERNAL_SCHEME}://{doc_id}"


def chunk_id(doc_id: int, index: int) -> int:
    return UPLOAD_ID_BASE + doc_id * MAX_CHUNKS_PER_DOC + index


def chroma_client():
    """One Chroma client for the local stores under var/knowledge (matching indexes)."""
    global _client
    with _lock:
        if _client is None:
            import chromadb
            _client = chromadb.PersistentClient(path=str(store_dir() / "chroma"))
        return _client


def upload_collection():
    """The website collection itself: uploads are stored next to the scraped chunks."""
    global _collection
    with _lock:
        if _collection is None:
            from concierge.rag.src.rag_search import get_collection
            _collection = get_collection()
        return _collection


def reset_for_tests() -> None:
    """Forget cached handles (tests point CCIFJ_KNOWLEDGE_DIR at a temp folder)."""
    global _collection, _client
    with _lock:
        _collection = None
        _client = None
    _base_records.cache_clear()


def split_text(title: str, url: str, text: str, source_file: str | None = None) -> list[str]:
    """Token-bounded chunks. Line 1 names the uploaded file; then the website-style title/url header."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from concierge.rag.src.rag_search import get_tokenizer
    splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
        get_tokenizer(), chunk_size=CHUNK_TOKENS, chunk_overlap=CHUNK_TOKENS // 10,
        separators=["\n\n", "\n", " ", ""],
    )
    chunks = [piece.strip() for piece in splitter.split_text(text) if piece.strip()]
    source = (source_file or title).replace("\n", " ").strip()
    return [f"source: {source}\n# {title}\nurl: {url}\n{piece}" for piece in chunks[:MAX_CHUNKS_PER_DOC]]


def _check_no_overlap(collection, doc_id: int, ids: list[str]) -> None:
    """Refuse to overwrite a chunk that belongs to the website or to another upload."""
    existing = collection.get(ids=ids, include=["metadatas"])
    for chunk, meta in zip(existing["ids"], existing["metadatas"]):
        if (meta or {}).get("doc_id") != doc_id:
            raise RuntimeError(f"Chunk ID {chunk} is already used by other content; upload chunk IDs must not overlap.")


def _save_text(doc_id: int, title: str, url: str, text: str, source_file: str | None) -> None:
    folder = texts_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{doc_id}.json").write_text(
        json.dumps({"doc_id": doc_id, "title": title, "url": url, "source_file": source_file, "text": text},
                   ensure_ascii=False), encoding="utf-8")


def _add_chunks(doc_id: int, url: str, chunks: list[str], source_file: str) -> None:
    from concierge.agents.tools import _get_embed_model
    model = _get_embed_model()
    collection = upload_collection()
    for start in range(0, len(chunks), 16):
        batch = chunks[start:start + 16]
        ids = [str(chunk_id(doc_id, start + n)) for n in range(len(batch))]
        _check_no_overlap(collection, doc_id, ids)
        vectors = model.encode([f"passage: {chunk}" for chunk in batch], normalize_embeddings=True,
                               show_progress_bar=False)
        collection.upsert(
            ids=ids, embeddings=vectors.tolist(), documents=batch,
            metadatas=[{"url": url, "doc_id": doc_id, "source_file": source_file, "kind": UPLOAD_KIND}
                       for _ in batch],
        )


def index_document(doc_id: int, title: str, url: str, text: str, source_file: str | None = None) -> int:
    """Add a document's chunks to the website collection, then rebuild keyword search. Returns the chunk count."""
    source = source_file or title
    chunks = split_text(title, url, text, source)
    if not chunks:
        return 0
    with _lock:
        # Drop leftovers of an earlier, longer version of the same document.
        upload_collection().delete(where={"doc_id": doc_id})
        _add_chunks(doc_id, url, chunks, source)
        _save_text(doc_id, title, url, text, source)
        rebuild_keyword_index()
    return len(chunks)


def remove_document(doc_id: int) -> None:
    with _lock:
        upload_collection().delete(where={"doc_id": doc_id})
        (texts_dir() / f"{doc_id}.json").unlink(missing_ok=True)
        rebuild_keyword_index()


def reindex_uploads() -> int:
    """Put every saved upload back into the website collection (after a re-crawl or a git pull)."""
    count = 0
    with _lock:
        for path in sorted(texts_dir().glob("*.json")):
            saved = json.loads(path.read_text(encoding="utf-8"))
            chunks = split_text(saved["title"], saved["url"], saved["text"], saved.get("source_file"))
            upload_collection().delete(where={"doc_id": saved["doc_id"]})
            _add_chunks(saved["doc_id"], saved["url"], chunks, saved.get("source_file") or saved["title"])
            count += 1
        rebuild_keyword_index()
    return count


@lru_cache(maxsize=1)
def _base_records() -> tuple[dict, ...]:
    """The website chunks from the tracked BM25 index."""
    import bm25s
    from concierge.rag.src.config import INDEX_PATH
    retriever = bm25s.BM25.load(str(INDEX_PATH), load_corpus=True)
    return tuple(retriever.corpus)


def _upload_records() -> list[dict]:
    stored = upload_collection().get(where={"kind": UPLOAD_KIND}, include=["documents", "metadatas"])
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


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["reindex"]:
        print(f"Re-added {reindex_uploads()} uploaded document(s) to the website collection.")
    else:
        print("usage: python -m concierge.knowledge reindex")
