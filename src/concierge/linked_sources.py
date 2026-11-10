"""Knowledge sources that staff LINK instead of uploading: fetched now, re-checked on a schedule.

Staff paste a URL in the admin dashboard; the document is read from that URL and indexed like
an upload (see concierge.knowledge). A background job (and a "Sync now" button) fetches it again
and re-indexes it only when its content changed, so staff update the source once and the agent
follows.

Supported links:

- a direct file (PDF, DOCX, text) or a public web page: the whole content is read;
- a Calameo publication (calameo.com/read/<id> or /books/<id>). Calameo does not expose the text
  of a publication publicly or through its API, so we index the title and the full summary from
  the public overview page and use the cover asset key to detect a replaced document. For the
  full text, staff give a `download_url`: a link to the same PDF (Calameo download link when
  "Allow download" is on, the PDF on ccifj.or.jp, a shared drive link, ...).
  With CALAMEO_API_KEY / CALAMEO_API_SECRET (Premium/Platinum accounts), the official API is used
  instead of the overview page, which also covers private publications.

Only public https hosts are fetched (no localhost or private network addresses).
"""

from __future__ import annotations

import base64
import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlparse

logger = logging.getLogger(__name__)

MAX_BYTES = 40 * 1024 * 1024  # same as KNOWLEDGE_MAX_BYTES for uploads
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 60
USER_AGENT = "CCIFJ-Concierge-KnowledgeSync/1.0"
CALAMEO_API = "https://api.calameo.com/1.0"
_CALAMEO_PATH = re.compile(r"^/(?:read|books)/([0-9a-z]{10,40})/?$", re.IGNORECASE)
_EXTENSION_TYPES = {
    ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".html": "text/html", ".htm": "text/html",
}


class LinkError(ValueError):
    """The link cannot be used as a knowledge source; the message is shown to staff."""


@dataclass
class FetchedSource:
    title: str
    filename: str
    mime_type: str
    text: str
    version: str  # changes when the source changes; compared on every sync
    note: str = ""  # shown to staff, e.g. "summary only"


# ---------- Fetching ----------

def _check_public_https(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise LinkError("The link must start with https://")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except socket.gaierror:
        raise LinkError(f"{parsed.hostname}: this site could not be found.") from None
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise LinkError(f"{parsed.hostname}: only public websites can be linked.")
    return url.strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are followed by http_get itself, so every hop is checked by _check_public_https."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def http_get(url: str, *, params: dict | None = None) -> tuple[bytes, str, str]:
    """GET a public https URL (checking every redirect). Returns (body, content type, final URL)."""
    current = _check_public_https(url + ("?" + urlencode(params) if params else ""))
    for _ in range(MAX_REDIRECTS + 1):
        request = urllib.request.Request(current, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        try:
            response = _opener.open(request, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                target = urljoin(current, exc.headers["Location"])
                if not target.startswith("https://"):
                    raise LinkError(f"The link redirects to a non-https address ({target[:120]}).") from None
                current = _check_public_https(target)
                continue
            raise LinkError(f"The link answered HTTP {exc.code}. Is it public?") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LinkError(f"The link could not be reached ({getattr(exc, 'reason', exc)}).") from None
        with response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise LinkError(f"The linked file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
            content_type = response.headers.get_content_type().lower() if response.headers.get("Content-Type") else ""
            return body, content_type, current
    raise LinkError("The link redirects too many times.")


def _mime_for(raw: bytes, content_type: str, url: str) -> str:
    if raw.startswith(b"%PDF-"):
        return "application/pdf"
    if raw.startswith(b"PK\x03\x04") and url.lower().split("?")[0].endswith(".docx"):
        return _EXTENSION_TYPES[".docx"]
    if content_type and content_type != "application/octet-stream":
        return content_type
    suffix = os.path.splitext(urlparse(url).path)[1].lower()
    return _EXTENSION_TYPES.get(suffix, "text/html")


def _filename(url: str, fallback: str) -> str:
    name = os.path.basename(urlparse(url).path.rstrip("/"))
    return (name or fallback)[:200]


def read_document(url: str, *, max_pages: int, max_chars: int) -> FetchedSource:
    """Fetch a direct file or a web page and extract its text with the upload extractor."""
    from concierge.api.attachments import extract_attachment
    raw, content_type, final_url = http_get(url)
    mime = _mime_for(raw, content_type, final_url)
    name = _filename(final_url, urlparse(final_url).hostname or "link")
    extracted = extract_attachment(
        {"name": name, "mime_type": mime, "data_base64": base64.b64encode(raw).decode()},
        max_bytes=MAX_BYTES, max_pages=max_pages, max_chars=max_chars)
    title = _html_title(raw) if mime == "text/html" else ""
    return FetchedSource(title=title or name.rsplit(".", 1)[0], filename=name, mime_type=extracted["mime_type"],
                         text=extracted["text"], version="sha256:" + hashlib.sha256(raw).hexdigest())


def _html_title(raw: bytes) -> str:
    match = re.search(rb"<title[^>]*>(.*?)</title>", raw[:200_000], re.IGNORECASE | re.DOTALL)
    return html.unescape(match.group(1).decode("utf-8", "replace")).strip()[:255] if match else ""


# ---------- Calameo ----------

def calameo_code(url: str) -> str | None:
    """The publication ID of a calameo.com/read/<id> or /books/<id> link, else None."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host != "calameo.com" and not host.endswith(".calameo.com"):
        return None
    match = _CALAMEO_PATH.match(parsed.path)
    return match.group(1).lower() if match else None


class _CalameoAbout(HTMLParser):
    """Text of the full summary (<p class="about ...">) on a publication's overview page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if self._depth:
            self._depth += tag == "p"
            if tag == "br":
                self.parts.append("\n")
        elif tag == "p" and "about" in classes:
            self._depth = 1

    def handle_endtag(self, tag):
        if self._depth and tag == "p":
            self._depth -= 1

    def handle_data(self, data):
        if self._depth:
            self.parts.append(data)


def parse_calameo_page(page: str) -> dict:
    """Name, page count, cover asset key and summary from a public calameo.com/books/<id> page."""
    # A JS literal, `var Calameo = {Data: {...json...}};`: decode the JSON value after "Data:".
    match = re.search(r"var Calameo\s*=\s*\{\s*Data\s*:\s*", page)
    try:
        data = json.JSONDecoder().raw_decode(page, match.end())[0] if match else {}
    except json.JSONDecodeError:
        data = {}
    book = data.get("book") or {}
    image = ((book.get("image") or {}).get("large") or "")
    asset = re.search(r"calameoassets\.com/([^/]+)/", image)
    about = _CalameoAbout()
    about.feed(page)
    summary = re.sub(r"[ \t]+", " ", "".join(about.parts))
    lines = [line.strip() for line in summary.splitlines() if line.strip()]
    while lines and lines[-1].lower() in {"less", "moins", "read more", "more"}:  # the page's collapse button
        lines.pop()
    summary = "\n".join(lines)
    if not book.get("name"):
        title = re.search(r'property="og:title"\s+content="([^"]*)"', page)
        book["name"] = html.unescape(title.group(1)) if title else ""
    return {"name": book.get("name") or "", "pages": (book.get("document") or {}).get("pages"),
            "asset": asset.group(1) if asset else "", "summary": summary}


def calameo_signature(secret: str, params: dict) -> str:
    """MD5 of secret + sorted key/value pairs (developer.calameo.com, "How to sign your requests")."""
    joined = "".join(f"{key}{params[key]}" for key in sorted(params))
    return hashlib.md5((secret + joined).encode("utf-8")).hexdigest()


def calameo_api(action: str, **params) -> dict:
    key, secret = os.getenv("CALAMEO_API_KEY", "").strip(), os.getenv("CALAMEO_API_SECRET", "").strip()
    query = {"apikey": key, "action": f"API.{action}", "output": "JSON", **params}
    query["signature"] = calameo_signature(secret, query)
    raw, _, _ = http_get(CALAMEO_API, params=query)
    response = json.loads(raw.decode("utf-8").rstrip().rstrip(";")).get("response", {})
    if response.get("status") != "ok":
        raise LinkError(f"Calameo API error: {(response.get('error') or {}).get('message', 'unknown error')}")
    return response.get("content") or {}


def _calameo_metadata(code: str) -> dict:
    if os.getenv("CALAMEO_API_KEY") and os.getenv("CALAMEO_API_SECRET"):
        book = calameo_api("getBookInfos", book_id=code)
        return {"name": book.get("Name", ""), "pages": book.get("Pages"), "summary": book.get("Description", ""),
                "asset": f"{book.get('Modification', '')}|{book.get('PosterUrl', '')}"}
    raw, _, _ = http_get(f"https://www.calameo.com/books/{code}")
    return parse_calameo_page(raw.decode("utf-8", "replace"))


def read_calameo(url: str, code: str, download_url: str | None, *, max_pages: int, max_chars: int) -> FetchedSource:
    meta = _calameo_metadata(code)
    if not meta["name"] and not meta["summary"]:
        raise LinkError("This Calameo publication could not be read. Is it public?")
    title = meta["name"] or f"Calameo {code}"
    if download_url:
        document = read_document(download_url, max_pages=max_pages, max_chars=max_chars)
        return FetchedSource(title=title, filename=document.filename, mime_type=document.mime_type,
                             text=f"{title}\n{meta['summary']}\n\n{document.text}".strip(),
                             version=f"calameo:{meta['asset']}|{document.version}")
    pages = f" ({meta['pages']} pages)" if meta.get("pages") else ""
    text = f"{title}{pages}\nCalameo publication: {url}\n\n{meta['summary']}".strip()
    return FetchedSource(title=title, filename=f"calameo-{code}", mime_type="text/plain", text=text,
                         version=f"calameo:{meta['asset']}|" + hashlib.sha256(text.encode()).hexdigest(),
                         note="Summary only: add a PDF download link to index the full publication.")


def fetch_source(url: str, download_url: str | None = None, *, max_pages: int, max_chars: int) -> FetchedSource:
    """Read a linked source. Raises LinkError (shown to staff) or HTTPException from the extractor."""
    _check_public_https(url)
    if download_url:
        _check_public_https(download_url)
    code = calameo_code(url)
    if code:
        return read_calameo(url, code, download_url, max_pages=max_pages, max_chars=max_chars)
    return read_document(download_url or url, max_pages=max_pages, max_chars=max_chars)


def link_digest(url: str) -> str:
    """KnowledgeDocument.sha256 of a linked source: one row per URL (its content changes over time)."""
    return hashlib.sha256(f"link:{url.strip()}".encode()).hexdigest()


def error_text(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    text = detail if isinstance(detail, str) else str(exc) or exc.__class__.__name__
    return text[:500]


# ---------- Sync ----------

async def sync_document(session, doc, *, force: bool = False) -> bool:
    """Fetch a linked document again; re-index it if its content changed. Returns True when re-indexed.

    A failed fetch keeps the indexed version and records the error for staff to see.
    """
    import asyncio
    from concierge import knowledge
    from concierge.api.admin_content import KNOWLEDGE_MAX_CHARS, KNOWLEDGE_MAX_PAGES
    from concierge.db.relationaldb.models import utcnow

    doc.checked_at = utcnow()
    try:
        fetched = await asyncio.to_thread(fetch_source, doc.sync_url, doc.download_url,
                                          max_pages=KNOWLEDGE_MAX_PAGES, max_chars=KNOWLEDGE_MAX_CHARS)
        changed = force or fetched.version != doc.version
        if changed:
            doc.chunk_count = await asyncio.to_thread(
                knowledge.index_document, doc.doc_id, doc.title, knowledge.document_url(doc.doc_id, doc.source_url),
                fetched.text, fetched.filename)
            doc.version, doc.changed_at = fetched.version, doc.checked_at
            doc.char_count, doc.mime_type, doc.filename = len(fetched.text), fetched.mime_type, fetched.filename
        doc.sync_error = fetched.note or None
    except Exception as exc:  # network, extraction or indexing: keep the last good version
        logger.warning("Sync of knowledge document %s (%s) failed: %s", doc.doc_id, doc.sync_url, exc)
        doc.sync_error = "Last check failed: " + error_text(exc)
        changed = False
    session.add(doc)
    await session.commit()
    return changed


async def sync_all(session) -> dict:
    from sqlmodel import select
    from concierge.db.relationaldb.models import KnowledgeDocument
    docs = (await session.exec(select(KnowledgeDocument).where(KnowledgeDocument.sync_url.is_not(None)))).all()
    changed = 0
    for doc in docs:
        changed += await sync_document(session, doc)
    return {"checked": len(docs), "changed": changed}
