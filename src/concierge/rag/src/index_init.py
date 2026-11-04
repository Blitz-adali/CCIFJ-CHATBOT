from __future__ import annotations
from .config import DB_PATH, COLLECTION_NAME, EMBED_MODEL_NAME, PAGES_PATH, CHUNKS_PATH, INDEX_PATH
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .preprocess import MinimalSource
    from chromadb.api.models.Collection import Collection
    from sentence_transformers import SentenceTransformer
import json

def index(chunk_size: int = 450):
    """Load, chunk, embed, and index the supported source documents.

    Args:
        chunk_size: Maximum number of tokenizer tokens in each chunk.

    Returns:
        None.
    """
    from .preprocess import chunk, blocks_to_markdown
    from sentence_transformers import SentenceTransformer

    # 1. Load and parse the documents
    with open(PAGES_PATH, 'r') as f:
        docs_json = [json.loads(line) for line in f if line.strip()]

    en_pages = []
    for doc in docs_json:
        if doc.get("language") == "en" or doc.get("language") == "unknown":
            blocks = doc.pop("blocks", [{}])
            doc["content"] = blocks_to_markdown(blocks)
            en_pages.append(doc)

    # 2. Chunk documents and setup sentence transformer + collection
    sources = chunk(chunk_size, en_pages)
    print(f"Created {len(sources)} text chunks.")

    model = SentenceTransformer(EMBED_MODEL_NAME, device="cpu")
    collection = create_collection(overwrite=True)

    # 3. Index documents in vector database and using BM25s
    vector_index(sources, collection, model)
    lexical_index(sources)

def lexical_index(sources: list[MinimalSource]):
    import bm25s
    corpus = [src.content or "" for src in sources]

    try:
        # Retrieval fusion needs stable IDs shared with Chroma, not bare text.
        records = [{"id": src.id, "text": src.content or "", "url": str(src.url)} for src in sources]
        retriever = bm25s.BM25(corpus=records)
        retriever.index(bm25s.tokenize(corpus))
        index_files = INDEX_PATH
        index_files.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise RuntimeError("Could not build BM25 index") from e
    try:
        retriever.save(index_files)
        sources_json = json.dumps(
            [src.model_dump(mode="json") for src in sources], indent=4
            )
        with open(CHUNKS_PATH, "w", encoding="utf-8") as f:
            f.write(sources_json)
    except Exception as e:
        raise RuntimeError("Could not save BM25 index") from e
    return sources


def create_collection(
    db_path: str = DB_PATH,
    collection_name: str = COLLECTION_NAME,
    overwrite: bool = False,
) -> Collection:
    """Open or create the persistent ChromaDB collection.

    Args:
        db_path: Filesystem path used for persistent ChromaDB storage.
        collection_name: Name of the collection to open or create.
        overwrite: Whether to delete an existing collection before creating it.

    Returns:
        The requested ChromaDB collection.
    """
    import chromadb
    client = chromadb.PersistentClient(path=db_path)
    if overwrite:
        try:
            client.delete_collection(name=collection_name)

        except Exception as e:
            pass
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

def vector_index(
    sources: list[MinimalSource],
    collection: Collection,
    model: SentenceTransformer,
    batch_size: int = 8,
) -> None:
    """Embed source chunks in batches and upsert them into ChromaDB.

    Args:
        sources: Chunks to embed and index.
        collection: ChromaDB collection receiving the chunks.
        model: Sentence-transformer model used to create embeddings.
        batch_size: Maximum number of chunks to encode in each batch.

    Returns:
        None.
    """
    from tqdm.auto import tqdm
    pbar = tqdm(unit="chunk", desc="Indexing Chunks", total=len(sources))
    for start in range(0, len(sources), batch_size):
        batch = sources[start: start + batch_size]
        texts = [source.content or "" for source in batch]
        embeddings = model.encode(
            [f"passage: {text}" for text in texts],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        collection.upsert(
            ids=[str(source.id) for source in batch],
            embeddings=embeddings.tolist(),
            documents=texts,
            metadatas=[{"url": source.url} for source in batch],
        )
        pbar.update(len(batch))
    pbar.close()

if __name__ == "__main__":
    index()
