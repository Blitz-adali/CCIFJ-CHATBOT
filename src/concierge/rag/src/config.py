import os
from pathlib import Path
import dotenv

env_path = dotenv.find_dotenv()
dotenv.load_dotenv(env_path)

EMBED_MODEL_NAME = "intfloat/multilingual-e5-small"
COLLECTION_NAME = "ccifj_documents"
PACKAGE_PATH = Path(__file__).resolve().parents[2]
DB_PATH = str(PACKAGE_PATH / "db" / "vectordb" / "chroma_db")
INDEX_PATH = PACKAGE_PATH / "index"
TOKENIZER_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
PAGES_PATH = PACKAGE_PATH / "data" / "documents.jsonl"
CHUNKS_PATH = PACKAGE_PATH / "data" / "chunks.json"
# Crawled sources that newer CCIFJ documents supersede. Passages from them are dropped at search
# time (no re-index needed), so e.g. the 2017 domiciliation brochure's EUR 480 non-resident fee
# never competes with the current EUR 550 in the membership records and the 2026 brochure.
# Match is by substring of the source URL.
OUTDATED_SOURCE_URLS = (
    "/SAE/Domiciliation_2017_FR.pdf",
    "/Covid-19_-_Protocoles_de_reprise.pdf",
    "/Mesures_financieres_de_soutien_aux_entreprises_20avril2020",
)
COHERE_API_KEY = os.getenv("COHERE_API_KEY")
COHERE_RERANK_MODEL = "rerank-v4.0-fast"
