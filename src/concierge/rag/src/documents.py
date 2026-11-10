"""Approved CCIFJ documents in the knowledge base, managed without writing code.

The knowledge base has two parts: the crawled website pages, and a folder of
approved documents (brochures, rate cards, calendars, fact sheets) kept as
plain Markdown files in ``src/concierge/data/documents/``. CCIFJ staff update
what the concierge knows by adding, editing, or deleting a file there and
running one command:

    uv run concierge-kb add "Media Kit 2027.pdf"     # PDF, DOCX, TXT or MD -> a Markdown file, then sync
    uv run concierge-kb add path/to/folder           # every supported file in a folder
    uv run concierge-kb sync                         # after editing or deleting a Markdown file by hand
    uv run concierge-kb list                         # what is in the folder and in the index
    uv run concierge-kb remove "Media Kit 2026"      # delete one document, then sync

Restart the API afterwards so it loads the new index. Answers cite these
documents by name (see ``agents/sources.py``).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

from .config import CHUNKS_PATH, EMBED_MODEL_NAME, PACKAGE_PATH

DOCUMENTS_PATH = PACKAGE_PATH / "data" / "documents"
SUPPORTED = {".pdf", ".docx", ".txt", ".md"}
OCR_LANGUAGES = "eng+fra+jpn"
# A page with less text than this is treated as a scan and read with OCR.
_MIN_PAGE_CHARS = 40
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


# --- Reading files -----------------------------------------------------------

def _pdf_text(path: Path) -> str:
    import logging

    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # font-table notes, not problems
    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    scans = [index for index, text in enumerate(pages) if len(text) < _MIN_PAGE_CHARS]
    if scans:
        try:
            import pypdfium2
            import pytesseract

            pdf = pypdfium2.PdfDocument(str(path))
            for index in scans:
                image = pdf[index].render(scale=2).to_pil()
                pages[index] = pytesseract.image_to_string(image, lang=OCR_LANGUAGES).strip()
            pdf.close()
        except Exception as error:  # OCR is optional; keep the text pages
            print(f"  note: could not OCR {len(scans)} scanned page(s) of {path.name}: {error}", file=sys.stderr)
    return "\n\n".join(text for text in pages if text)


def _docx_text(path: Path) -> str:
    import zipfile
    from xml.etree import ElementTree

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs = ("".join(node.text or "" for node in paragraph.iter(f"{namespace}t")) for paragraph in root.iter(f"{namespace}p"))
    return "\n".join(text for text in paragraphs if text.strip())


def read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _pdf_text(path)
    elif suffix == ".docx":
        text = _docx_text(path)
    else:
        text = path.read_text(encoding="utf-8-sig")
        text = _FRONT_MATTER.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --- The documents folder ----------------------------------------------------

def _title(path: Path) -> str:
    """A readable document name from a file name: '#3 - I am establishing' -> 'I am establishing'."""
    title = unicodedata.normalize("NFKC", path.stem)
    title = re.sub(r"^[#\d\s\-–_.]+(?=\D)", "", title) if re.match(r"^#", title) else title
    title = re.sub(r"【[^】]*】", "", title).replace("_", " ")
    return " ".join(title.split()) or path.stem


def _slug(title: str) -> str:
    slug = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or "document"


def _parse(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8-sig")
    meta: dict[str, str] = {}
    match = _FRONT_MATTER.match(raw)
    if match:
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            if value.strip():
                meta[key.strip().lower()] = value.strip()
        raw = raw[match.end():]
    return {"title": meta.get("title") or _title(path), "content": raw.strip(), "file": path.name, **meta}


def load_documents() -> list[dict]:
    if not DOCUMENTS_PATH.is_dir():
        return []
    documents = [_parse(path) for path in sorted(DOCUMENTS_PATH.glob("*.md")) if path.name.lower() != "readme.md"]
    return [document for document in documents if document["content"]]


def add_files(paths: list[Path]) -> list[Path]:
    """Convert each file to a Markdown document in the folder. Returns the files written."""
    DOCUMENTS_PATH.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in SUPPORTED and p.is_file()))
        elif path.suffix.lower() in SUPPORTED and path.is_file():
            files.append(path)
        else:
            print(f"  skipped {path}: not a PDF, DOCX, TXT or MD file", file=sys.stderr)
    written = []
    for path in files:
        print(f"  reading {path.name} …")
        text = read_text(path)
        if len(text) < 80:
            print(f"  skipped {path.name}: no readable text (is it an image-only file without OCR?)", file=sys.stderr)
            continue
        title = _title(path)
        target = DOCUMENTS_PATH / f"{_slug(title)}.md"
        target.write_text(
            f"---\ntitle: {title}\nsource_file: {path.name}\nadded: {date.today().isoformat()}\n---\n\n{text}\n",
            encoding="utf-8",
        )
        written.append(target)
        print(f"  wrote {target.name} ({len(text):,} characters)")
    return written


# --- Indexing ----------------------------------------------------------------

def _is_document_chunk(chunk: dict) -> bool:
    return not chunk.get("url")


def sync() -> tuple[int, int]:
    """Rebuild the document part of the index from the folder; website chunks are kept as they are.

    Returns (documents, document chunks). Chunk ids stay equal to their position
    in the corpus, which the hybrid search relies on.
    """
    from sentence_transformers import SentenceTransformer

    from .index_init import create_collection, lexical_index, vector_index
    from .preprocess import MinimalSource, chunk

    with open(CHUNKS_PATH, encoding="utf-8") as handle:
        existing = json.load(handle)
    site = [item for item in existing if not _is_document_chunk(item)]
    old_document_ids = [str(item["id"]) for item in existing if _is_document_chunk(item)]
    for position, item in enumerate(site):
        if item["id"] != position:
            raise RuntimeError("Website chunk ids are not contiguous; rebuild the whole index first.")

    documents = load_documents()
    new_chunks = chunk(450, [{**document, "url": "", "kind": "document"} for document in documents], start_id=len(site))

    try:
        model = SentenceTransformer(EMBED_MODEL_NAME)
        model.encode("query: test", normalize_embeddings=True)
    except Exception:
        model = SentenceTransformer(EMBED_MODEL_NAME, device="cpu")
    collection = create_collection(overwrite=False)
    if old_document_ids:
        collection.delete(ids=old_document_ids)
    if new_chunks:
        vector_index(new_chunks, collection, model)
    lexical_index([MinimalSource(**item) for item in site] + new_chunks)
    return len(documents), len(new_chunks)


def indexed_documents() -> dict[str, int]:
    """Document name -> number of chunks currently in the index."""
    counts: dict[str, int] = {}
    with open(CHUNKS_PATH, encoding="utf-8") as handle:
        for item in json.load(handle):
            if _is_document_chunk(item):
                match = re.search(r"^document: (.+)$", item.get("content") or "", re.MULTILINE)
                name = match.group(1).strip() if match else "?"
                counts[name] = counts.get(name, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="concierge-kb", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="add PDF, DOCX, TXT or MD files (or a folder of them), then sync")
    add.add_argument("paths", nargs="+", type=Path)
    add.add_argument("--no-sync", action="store_true", help="only convert the files; run 'sync' later")
    commands.add_parser("sync", help="rebuild the document part of the index from the documents folder")
    commands.add_parser("list", help="show the documents in the folder and in the index")
    remove = commands.add_parser("remove", help="delete a document by title or file name, then sync")
    remove.add_argument("name")
    args = parser.parse_args(argv)

    if args.command == "list":
        indexed = indexed_documents()
        documents = load_documents()
        print(f"Documents folder: {DOCUMENTS_PATH}")
        for document in documents:
            state = f"{indexed.get(document['title'], 0)} chunks" if document["title"] in indexed else "NOT indexed (run: concierge-kb sync)"
            print(f"  {document['title']}  [{document['file']}]  {state}")
        for name in sorted(set(indexed) - {document["title"] for document in documents}):
            print(f"  {name}  [file deleted]  still indexed (run: concierge-kb sync)")
        if not documents and not indexed:
            print("  (none)")
        return 0

    if args.command == "add":
        if not add_files(args.paths):
            print("No document was added.", file=sys.stderr)
            return 1
        if args.no_sync:
            return 0
    elif args.command == "remove":
        wanted = args.name.casefold()
        matches = [
            document for document in load_documents()
            if wanted in {document["title"].casefold(), document["file"].casefold(), Path(document["file"]).stem.casefold()}
        ]
        if not matches:
            print(f"No document named {args.name!r}. Run: concierge-kb list", file=sys.stderr)
            return 1
        for document in matches:
            (DOCUMENTS_PATH / document["file"]).unlink()
            print(f"  deleted {document['file']}")

    print("Updating the index …")
    documents, chunks = sync()
    print(f"Done: {documents} document(s), {chunks} passage(s) in the index. Restart the API to load them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
