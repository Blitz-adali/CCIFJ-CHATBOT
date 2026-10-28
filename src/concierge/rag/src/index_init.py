from .config import DB_PATH, COLLECTION_NAME, EMBED_MODEL_NAME, PAGES_PATH, CHUNKS_PATH
from .preprocess import MinimalSource, chunk, blocks_to_markdown
import chromadb
from pathlib import Path
from chromadb.api.models.Collection import Collection
from sentence_transformers import (SentenceTransformer)
from tqdm.auto import tqdm
import bm25s
import json

def index(chunk_size: int = 450):
    """Load, chunk, embed, and index the supported source documents.

    Args:
        chunk_size: Maximum number of tokenizer tokens in each chunk.

    Returns:
        None.
    """
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

    model = SentenceTransformer(EMBED_MODEL_NAME)
    collection = create_collection(overwrite=True)

    # 3. Index documents in vector database and using BM25s
    vector_index(sources, collection, model)
    lexical_index(sources)

def lexical_index(sources: list[MinimalSource]):
    corpus = [src.content for src in sources]

    try:
        retriever = bm25s.BM25(corpus=corpus)
        retriever.index(bm25s.tokenize(corpus))
        index_files = Path("index/")
        index_files.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print("Error Indexing Corpus", e,
                "Check files under data/raw")
        exit()
    try:
        retriever.save(index_files)
        sources_json = json.dumps(
            [src.model_dump(mode="json") for src in sources], indent=4
            )
        with open("data/chunks.json", "w") as f:
            f.write(sources_json)
    except Exception as e:
        print("Error Saving Index", e)
        exit()
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