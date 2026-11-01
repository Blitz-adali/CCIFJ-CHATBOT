"""Small, bounded PDF and image text extraction for chat attachments.

PDF text is read directly; scan-only pages and images are OCRed with
Tesseract (eng+fra+jpn). Third-party readers load on first use so the API
can start without them.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import warnings

from fastapi import HTTPException

MAX_FILES_PER_CONVERSATION = 4
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_TEXT_CHARS_PER_FILE = 12_000
OCR_LANGUAGES = "eng+fra+jpn"
MAX_IMAGE_PIXELS = 20_000_000
ALLOWED_TYPES = {"application/pdf", "image/png", "image/jpeg"}


def _ocr(image) -> str:
    import pytesseract
    from pytesseract import TesseractError

    try:
        return pytesseract.image_to_string(image, lang=OCR_LANGUAGES).strip()
    except (TesseractError, pytesseract.TesseractNotFoundError) as exc:
        raise HTTPException(
            503,
            "OCR is unavailable: install Tesseract and its English, French, and Japanese language data.",
        ) from exc


def extract_attachment(item: dict) -> dict:
    name = str(item.get("name", "attachment"))[:200]
    mime = str(item.get("mime_type", "")).lower()
    if mime not in ALLOWED_TYPES:
        raise HTTPException(400, f"{name}: only PDF, PNG, and JPEG files are supported.")
    try:
        raw = base64.b64decode(item.get("data_base64", ""), validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise HTTPException(400, f"{name}: the uploaded file could not be decoded.") from None
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise HTTPException(413, f"{name}: files must be between 1 byte and 5 MB.")

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
            if len(reader.pages) > MAX_PDF_PAGES:
                raise HTTPException(413, f"{name}: PDFs are limited to 20 pages.")
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

    text = "\n\n".join(pages)[:MAX_TEXT_CHARS_PER_FILE]
    if not text.strip():
        raise HTTPException(400, f"{name}: no readable text was found.")
    return {
        "id": hashlib.sha256(raw).hexdigest(),
        "name": name,
        "mime_type": mime,
        "text": text,
    }
