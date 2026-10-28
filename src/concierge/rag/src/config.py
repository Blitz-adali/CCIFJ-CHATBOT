import os
import dotenv

env_path = dotenv.find_dotenv()
dotenv.load_dotenv(env_path)

EMBED_MODEL_NAME = "intfloat/multilingual-e5-small"
COLLECTION_NAME = "ccifj_documents"
DB_PATH = "DB/vectordb/chroma_db"
TOKENIZER_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
PAGES_PATH = "data/documents.jsonl"
CHUNKS_PATH = "data/chunks.jsonl"
COHERE_API_KEY = os.getenv("COHERE_API_KEY")
COHERE_RERANK_MODEL = "rerank-v4.0-fast"
