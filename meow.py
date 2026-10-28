from rag import index, search, EMBED_MODEL_NAME
import bm25s
from sentence_transformers import SentenceTransformer

model = SentenceTransformer(EMBED_MODEL_NAME)
retriever = bm25s.BM25.load("index/", load_corpus=True)
sources = search(retriever, model, "What are members categories ?"
"", 20, 10, 5)
for x in sources:
    print("=" * 80 + "\n")
    print(x)
