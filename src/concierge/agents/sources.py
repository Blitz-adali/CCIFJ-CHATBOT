"""Source tracking: which official page or document each fact in an answer comes from.

How it works, end to end:

1. Tools label what they return. Every knowledge-base passage starts with
   ``# <title>`` and a ``url:`` (web page) or ``document:`` (approved brochure)
   line; record tools (events, membership, directory, contacts) end with a
   ``Source: [title](url)`` line.
2. Each specialist tags its facts with ``(source: <url or document name>)``.
3. Before the final answer is written, the tags are replaced by numbered
   markers such as ``[2]`` and the collector is told to keep them next to the
   facts they support.
4. After the answer is written, only markers that point to a source retrieved
   in this turn are kept, renumbered in order of appearance, and returned with
   the answer as its ``sources`` list.

A source is never taken from the model's own words: a URL or document name the
tools did not return this turn cannot be cited.
"""

import re
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

_PASSAGE_PATTERN = re.compile(r"^# (?P<title>[^\n]+)\n+(?P<kind>url|document): (?P<ref>[^\n]+)$", re.MULTILINE)
_RECORD_PATTERN = re.compile(r"^Source: \[(?P<title>[^\]\n]+)\]\((?P<url>https://[^\s)]+)\)\s*$", re.MULTILINE)
_RECORD_DOCUMENT_PATTERN = re.compile(r"^Source document: (?P<title>[^\n]+?)\s*$", re.MULTILINE)
_TAG_PATTERN = re.compile(r"\s*[\(\[（]\s*sources?\s*[:：]\s*(?P<refs>[^)\]）]+)[\)\]）]", re.IGNORECASE)
_URL_PATTERN = re.compile(r"https://[^\s<>\"'）)\]]+")
_MARKER_PATTERN = re.compile(r"[ \t]?\[(\d{1,2})\](?!\()")
_FENCE_PATTERN = re.compile(r"(```.*?```|~~~.*?~~~)", re.DOTALL)

MAX_SOURCES = 6


def _canonical(url: str) -> str:
    parts = urlsplit(url.strip().rstrip(".,;"))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, ""))


def source_key(source: dict) -> str:
    """One page or document counts once, however it was written."""
    if source.get("url"):
        return _canonical(source["url"])
    return "document:" + " ".join(str(source.get("title", "")).split()).casefold()


def sources_in_tool_output(text: str) -> list[dict]:
    """The sources a tool result names, in the order they appear."""
    found = []
    for match in _PASSAGE_PATTERN.finditer(text or ""):
        title, ref = match.group("title").strip(), match.group("ref").strip()
        if match.group("kind") == "url":
            if ref.startswith("https://"):
                found.append({"title": title, "url": ref.rstrip(".,;")})
        elif ref:
            found.append({"title": ref, "url": None})
    for match in _RECORD_PATTERN.finditer(text or ""):
        found.append({"title": match.group("title").strip(), "url": match.group("url").rstrip(".,;")})
    for match in _RECORD_DOCUMENT_PATTERN.finditer(text or ""):
        found.append({"title": match.group("title").strip(), "url": None})
    return merge_sources([], found)


def merge_sources(current: Optional[list], new: Optional[list]) -> list:
    """State reducer: ``None`` starts a new turn; otherwise add what is not there yet."""
    if new is None:
        return []
    merged = list(current or [])
    seen = {source_key(source) for source in merged}
    for source in new:
        if not isinstance(source, dict) or not source.get("title"):
            continue
        key = source_key(source)
        if key not in seen:
            seen.add(key)
            merged.append({"title": str(source["title"]), "url": source.get("url") or None})
    return merged


def _match_reference(reference: str, sources: list[dict]) -> Optional[int]:
    """Index of the retrieved source a tag refers to, or None when it is not one of them."""
    reference = reference.strip().strip("“”\"'«»「」 ")
    if not reference:
        return None
    url = _URL_PATTERN.search(reference)
    if url:
        wanted = _canonical(url.group(0))
        for index, source in enumerate(sources):
            if source.get("url") and _canonical(source["url"]) == wanted:
                return index
        return None
    name = re.sub(r"^document\s*[:：]?\s*", "", reference, flags=re.IGNORECASE).strip("“”\"'«»「」 ").casefold()
    if len(name) < 4:
        return None
    for index, source in enumerate(sources):
        if source.get("url"):
            continue
        title = source["title"].casefold()
        if name == title or name in title or title in name:
            return index
    return None


def number_tags(findings: dict[str, str], retrieved: list[dict]) -> tuple[dict[str, str], list[dict]]:
    """Replace each ``(source: ...)`` tag in the specialists' findings with a numbered marker.

    Returns the rewritten findings and the numbered sources (marker ``[n]`` is
    ``sources[n - 1]``). Tags that name something the tools did not return are removed.
    """
    numbered: list[dict] = []
    positions: dict[str, int] = {}

    def number_for(index: int) -> int:
        key = source_key(retrieved[index])
        if key not in positions:
            numbered.append(retrieved[index])
            positions[key] = len(numbered)
        return positions[key]

    def replace(match: re.Match) -> str:
        references = re.split(r"\s*[,;]\s*|\s+and\s+|\s+(?=https://)", match.group("refs"))
        numbers = []
        for reference in references:
            index = _match_reference(reference, retrieved)
            if index is not None and len(numbered) < 12:
                number = number_for(index)
                if number not in numbers:
                    numbers.append(number)
        return "".join(f" [{number}]" for number in numbers)

    rewritten = {domain: _TAG_PATTERN.sub(replace, str(text)) for domain, text in findings.items()}
    return rewritten, numbered


def sources_note(numbered: list[dict]) -> str:
    """The numbered source list shown to the collector."""
    if not numbered:
        return ""
    lines = [
        f"[{number}] {source['title']}" + (f" ({source['url']})" if source.get("url") else " (CCIFJ document)")
        for number, source in enumerate(numbered, start=1)
    ]
    return "\nNumbered sources for the markers in the findings:\n" + "\n".join(lines) + "\n"


def apply_citations(answer: str, numbered: list[dict]) -> tuple[str, list[dict]]:
    """Keep only valid markers, renumber them in order of appearance, and return the cited sources."""
    cited: list[dict] = []
    new_numbers: dict[int, int] = {}

    def replace(match: re.Match) -> str:
        old = int(match.group(1))
        if not 1 <= old <= len(numbered):
            return ""
        if old not in new_numbers:
            if len(cited) >= MAX_SOURCES:
                return ""
            cited.append(numbered[old - 1])
            new_numbers[old] = len(cited)
        return f" [{new_numbers[old]}]"

    parts = _FENCE_PATTERN.split(answer or "")
    for index, part in enumerate(parts):
        if index % 2:
            # A draft or code block is the user's own text: no markers inside it.
            parts[index] = _MARKER_PATTERN.sub("", part)
        else:
            part = _MARKER_PATTERN.sub(replace, part)
            part = re.sub(r"(\[\d+\])(?:\s*\1)+", r"\1", part)
            # A marker after a table row's closing pipe would add a stray cell: move it inside.
            parts[index] = re.sub(r"[ \t]*\|[ \t]*((?:\[\d+\][ \t]*)+)$", lambda m: f" {m.group(1).strip()} |", part, flags=re.MULTILINE)
    # A stray tag the collector copied instead of a marker must not reach the user.
    text = _TAG_PATTERN.sub("", "".join(parts))
    return text.strip(), cited


_TRACKING_PATTERN = re.compile(r"([?&])utm_[a-z]+=[^&\s)#\]]*(&?)")


def strip_tracking(text: str) -> str:
    """Remove the tracking parameters web search adds to links (``?utm_source=...``)."""
    def replace(match: re.Match) -> str:
        return match.group(1) if match.group(2) else ""
    return _TRACKING_PATTERN.sub(replace, text or "")


def strip_markers(text: str) -> str:
    """Remove citation markers, for text that is fed back to a model as history."""
    return re.sub(r"[ \t]?\[\d{1,2}\](?!\()", "", text or "")


def is_official(url: Optional[str]) -> bool:
    """CCIFJ's own pages and documents, as opposed to public web pages."""
    if not url:
        return True
    host = (urlsplit(url).hostname or "").lower()
    return host == "ccifj.or.jp" or host.endswith(".ccifj.or.jp") or host.endswith(".ccifi.net") or host == "ccifi.net"
