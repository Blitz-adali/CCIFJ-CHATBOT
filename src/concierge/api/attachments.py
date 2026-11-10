"""Small, bounded text extraction for chat attachments.

PDF text is read directly; scan-only pages and images are OCRed with
Tesseract (eng+fra+jpn). Text formats are decoded as UTF-8 and DOCX text is
read from the document XML. Third-party readers load on first use so the API
can start without them.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import warnings
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree

from fastapi import HTTPException

MAX_FILES_PER_CONVERSATION = 4
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_TEXT_CHARS_PER_FILE = 12_000
OCR_LANGUAGES = "eng+fra+jpn"
MAX_IMAGE_PIXELS = 20_000_000
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
HTML_TYPES = {"text/html", "application/xhtml+xml"}
TEXT_TYPES = {
    "text/plain", "text/markdown", "text/x-markdown", "text/csv",
    "application/json", "application/xml", "text/xml", *HTML_TYPES,
}
ALLOWED_TYPES = {"application/pdf", "image/png", "image/jpeg", DOCX_TYPE, *TEXT_TYPES}
_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class _HtmlText(HTMLParser):
    """Visible text of an HTML file, without scripts and styles."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _decode_text(name: str, raw: bytes, mime: str) -> str:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, f"{name}: text files must be UTF-8 encoded.") from None
    if mime in HTML_TYPES:
        parser = _HtmlText()
        parser.feed(text)
        text = "\n".join(parser.parts)
    return text


def _docx_text(name: str, raw: bytes) -> str:
    """Paragraph text from word/document.xml, bounded against zip bombs."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise HTTPException(413, f"{name}: the document is too large to process safely.")
            xml = archive.read(info)
    except (zipfile.BadZipFile, KeyError):
        raise HTTPException(400, f"{name}: the file is not a valid DOCX document.") from None
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise HTTPException(400, f"{name}: the document contains unsupported XML declarations.")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        raise HTTPException(400, f"{name}: the file is not a valid DOCX document.") from None
    paragraphs = []
    for paragraph in root.iter(f"{_WORD_NS}p"):
        text = "".join(
            node.text or "" if node.tag == f"{_WORD_NS}t" else "\t" if node.tag == f"{_WORD_NS}tab" else "\n"
            for node in paragraph.iter()
            if node.tag in {f"{_WORD_NS}t", f"{_WORD_NS}tab", f"{_WORD_NS}br"}
        )
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _ocr(image) -> str:
    import pytesseract
    from pytesseract import TesseractError
    from concierge import ocr

    ocr.configure()  # finds Tesseract off the PATH too, and the project's own language files
    try:
        return pytesseract.image_to_string(image, lang=OCR_LANGUAGES).strip()
    except (TesseractError, pytesseract.TesseractNotFoundError) as exc:
        raise HTTPException(
            503,
            "OCR is unavailable: install Tesseract and its English, French, and Japanese language data "
            "(run `bash scripts/install_ocr.sh`, or scripts\\install_ocr.ps1 on Windows). Text PDFs and Word files work without it.",
        ) from exc


def extract_attachment(item: dict, *, max_bytes: int = MAX_FILE_BYTES, max_pages: int = MAX_PDF_PAGES,
                       max_chars: int = MAX_TEXT_CHARS_PER_FILE) -> dict:
    """Chat attachments use the defaults; staff knowledge uploads pass larger limits."""
    name = str(item.get("name", "attachment"))[:200]
    # Browsers may add parameters, e.g. "text/plain;charset=utf-8".
    mime = str(item.get("mime_type", "")).split(";")[0].strip().lower()
    if mime not in ALLOWED_TYPES:
        raise HTTPException(400, f"{name}: supported files are PDF, PNG, JPEG, DOCX, and text (TXT, MD, CSV, JSON, XML, HTML).")
    try:
        raw = base64.b64decode(item.get("data_base64", ""), validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise HTTPException(400, f"{name}: the uploaded file could not be decoded.") from None
    if not raw or len(raw) > max_bytes:
        raise HTTPException(413, f"{name}: files must be between 1 byte and {max_bytes // (1024 * 1024)} MB.")

    if mime in TEXT_TYPES or mime == DOCX_TYPE:
        if mime == DOCX_TYPE and not raw.startswith(b"PK\x03\x04"):
            raise HTTPException(400, f"{name}: the file is not a valid DOCX document.")
        text = _docx_text(name, raw) if mime == DOCX_TYPE else _decode_text(name, raw, mime)
        return _attachment(name, mime, raw, text, max_chars)

    is_pdf = mime == "application/pdf"
    if is_pdf and not raw.startswith(b"%PDF-"):
        raise HTTPException(400, f"{name}: the file is not a valid PDF.")
    if mime == "image/png" and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(400, f"{name}: the file is not a valid PNG image.")
    if mime == "image/jpeg" and not raw.startswith(b"\xff\xd8\xff"):
        raise HTTPException(400, f"{name}: the file is not a valid JPEG image.")

    import pypdfium2 as pdfium
    import pytesseract
    from PIL import Image, UnidentifiedImageError
    from pypdf import PdfReader
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    pages: list[str] = []
    try:
        if is_pdf:
            reader = PdfReader(io.BytesIO(raw), strict=False)
            if len(reader.pages) > max_pages:
                raise HTTPException(413, f"{name}: PDFs are limited to {max_pages} pages.")
            pdf = pdfium.PdfDocument(raw)
            for index, page in enumerate(reader.pages):
                text = (page.extract_text() or "").strip()
                # Scan-only or near-empty pages are rendered and OCRed once.
                if len(re.sub(r"\s", "", text)) < 20:
                    bitmap = pdf[index].render(scale=2)
                    text = _ocr(bitmap.to_pil())
                if text:
                    pages.append(f"[Page {index + 1}]\n{text}")
            pdf.close()
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.width * image.height > MAX_IMAGE_PIXELS:
                        raise HTTPException(413, f"{name}: images are limited to 20 megapixels.")
                    image.load()
                    pages.append(_ocr(image.convert("RGB")))
    except HTTPException:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(400, f"{name}: the image is invalid or too large to process safely.") from exc
    except Exception as exc:
        if isinstance(exc, pytesseract.TesseractNotFoundError):
            raise HTTPException(503, "OCR is unavailable: install Tesseract and its English, French, and Japanese language data.") from exc
        raise HTTPException(400, f"{name}: could not read this file.") from exc

    return _attachment(name, mime, raw, "\n\n".join(pages), max_chars)


def _attachment(name: str, mime: str, raw: bytes, text: str, max_chars: int = MAX_TEXT_CHARS_PER_FILE) -> dict:
    text = text[:max_chars]
    if not text.strip():
        raise HTTPException(400, f"{name}: no readable text was found.")
    return {
        "id": hashlib.sha256(raw).hexdigest(),
        "name": name,
        "mime_type": mime,
        "text": text,
    }
