"""Add brochures and other documents to the agent's knowledge base from the command line.

    uv run python scripts/ingest_documents.py "path/to/folder or file" [more paths...] [--dry-run]

Does the same as the staff dashboard's knowledge upload (PDF, DOCX, text; scanned PDFs go
through OCR), without the browser's 15 MB limit, so a folder of brochures can be loaded in
one go. Each file is read, deduplicated by SHA-256 against what is already in the knowledge
base, stored as a KnowledgeDocument and indexed for search. Duplicate copies (the same
brochure in two folders, or inside a ZIP) are skipped. Staff can review or delete the
documents in the dashboard afterwards.

Files that yield almost no text (image-only PDFs where OCR found nothing) are reported and
not indexed.
"""

import argparse
import asyncio
import base64
import hashlib
import sys
from pathlib import Path

# Windows consoles default to cp1252; file names and replies may hold accents or Japanese.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlmodel import select  # noqa: E402

from concierge.db.relationaldb.models import KnowledgeDocument, utcnow  # noqa: E402
from concierge.db.relationaldb.session import AsyncSessionLocal  # noqa: E402

MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".html": "text/html", ".json": "application/json",
}
MAX_BYTES = 64 * 1024 * 1024
MAX_PAGES = 300
MAX_CHARS = 2_000_000
MIN_CHARS = 200  # below this the extraction failed (image-only pages, empty file)


def collect(paths: list[Path]) -> list[Path]:
    files = []
    for path in paths:
        if path.is_dir():
            files += sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in MIME_BY_SUFFIX)
        elif path.suffix.lower() in MIME_BY_SUFFIX:
            files.append(path)
        else:
            print(f"skip {path.name}: unsupported type")
    return files


async def ingest(files: list[Path], dry_run: bool) -> None:
    from concierge import knowledge
    from concierge.api.attachments import extract_attachment

    seen: set[str] = set()
    async with AsyncSessionLocal() as session:
        known = set((await session.exec(select(KnowledgeDocument.sha256))).all())
        for path in files:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen or digest in known:
                print(f"skip {path.name}: already in the knowledge base (or a duplicate in this run)")
                continue
            seen.add(digest)
            if len(raw) > MAX_BYTES:
                print(f"skip {path.name}: larger than {MAX_BYTES // (1024 * 1024)} MB")
                continue
            mime = MIME_BY_SUFFIX[path.suffix.lower()]
            try:
                extracted = await asyncio.to_thread(
                    extract_attachment, {"name": path.name, "mime_type": mime, "data_base64": base64.b64encode(raw).decode()},
                    max_bytes=MAX_BYTES, max_pages=MAX_PAGES, max_chars=MAX_CHARS)
            except Exception as exc:  # HTTPException from the extractor, or a parser error
                print(f"FAIL {path.name}: {getattr(exc, 'detail', exc)}")
                continue
            text = extracted["text"]
            if len(text) < MIN_CHARS:
                print(f"FAIL {path.name}: only {len(text)} characters of text; needs OCR or a text version")
                continue
            title = path.stem
            if dry_run:
                print(f"would add {path.name}: {len(text):,} characters")
                continue
            doc = KnowledgeDocument(title=title, filename=path.name[:200], mime_type=extracted["mime_type"],
                                    sha256=digest, char_count=len(text), created_at=utcnow())
            session.add(doc)
            await session.commit()
            await session.refresh(doc)
            try:
                doc.chunk_count = await asyncio.to_thread(
                    knowledge.index_document, doc.doc_id, title, knowledge.document_url(doc.doc_id, None), text,
                    path.name)
            except Exception as exc:
                await asyncio.to_thread(knowledge.remove_document, doc.doc_id)
                await session.delete(doc)
                await session.commit()
                print(f"FAIL {path.name}: indexing failed ({type(exc).__name__})")
                continue
            session.add(doc)
            await session.commit()
            print(f"added {path.name}: {len(text):,} characters, {doc.chunk_count} chunks")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="read and report, but store nothing")
    args = parser.parse_args()
    files = collect(args.paths)
    if not files:
        sys.exit("No PDF, DOCX or text files found.")
    asyncio.run(ingest(files, args.dry_run))


if __name__ == "__main__":
    main()
