"""Public answer presentation only: no tool calls or changes to agent execution."""
import re
from urllib.parse import unquote, urlsplit, urlunsplit


def _canonical(url):
    """The same page written two ways (trailing slash, #anchor) counts once."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, ""))


def _segment_title(segment):
    words = re.sub(r"\.html?$", "", unquote(segment)).replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else ""


def _path_titles(url):
    """Readable titles for a URL's path, most specific first: '/en/events/contact-us.html' -> ['Contact us', 'Events']."""
    segments = [s for s in urlsplit(url).path.split("/") if s and s not in {"en", "fr", "ja", "jp"}]
    return [title for title in map(_segment_title, reversed(segments)) if title]


# Link texts that say nothing about the page; the page's own name is used instead.
_GENERIC_TITLES = {
    "page", "event page", "this page", "web page", "website", "link", "here", "click here",
    "details", "event details", "more", "more info", "more information", "read more", "learn more",
}
# Path folders that are not sections a user would recognise (e.g. /event-details/event/<slug>).
_GENERIC_SEGMENTS = {"event", "events details", "event details", "e", "p", "n", "detail", "details"}
_LABEL_LIMIT = 60


def _short(label):
    """Cut a long page name at a word boundary; the chip shows the full URL on hover."""
    if len(label) <= _LABEL_LIMIT:
        return label
    return label[:_LABEL_LIMIT].rsplit(" ", 1)[0].rstrip(" ,:;-") + "…"


def _base_label(title, url):
    """The link text, or the page's own name when there is no text or it is generic."""
    path_titles = _path_titles(url)
    title = (title or "").strip()
    if title and title.casefold() not in _GENERIC_TITLES:
        return _short(title)
    return _short(path_titles[0]) if path_titles else "CCIFJ website"


def _unique_labels(links):
    """Button labels that tell the links apart. When several links share a label,
    each gets its page name (or failing that, its section) so no two look alike."""
    counts = {}
    for title, url in links:
        key = _base_label(title, url).casefold()
        counts[key] = counts.get(key, 0) + 1
    labels, taken = [], set()
    for title, url in links:
        label = _base_label(title, url)
        if counts[label.casefold()] > 1:
            path_titles = _path_titles(url)
            page = _short(path_titles[0]) if path_titles else ""
            section = next((t for t in path_titles[1:] if t.casefold() not in _GENERIC_SEGMENTS), "")
            if page and page.casefold() != label.casefold() and page.casefold() not in taken:
                label = page
            elif section:
                label = f"{section} · {label}"
        base, number = label, 2
        while label.casefold() in taken:
            label, number = f"{base} {number}", number + 1
        taken.add(label.casefold())
        labels.append(label)
    return labels


def linked_resources(reply):
    """Expose only links actually present in the answer; never invent citations."""
    unique, seen = [], set()
    links = [(match.group(1), match.group(2)) for match in re.finditer(r"\[([^\]\n]+)\]\((https://[^\s)]+)\)", reply)]
    links += [(None, url) for url in re.findall(r"https://[^\s<>\])]+", reply)]
    for title, url in links:
        url = url.rstrip(".,;!?")
        try:
            parsed = urlsplit(url)
            key = _canonical(url)
            if key in seen or parsed.scheme != "https" or not parsed.hostname:
                continue
        except ValueError:
            continue
        seen.add(key)
        # A Markdown link whose text is just the URL is a bare URL.
        if title and title.strip().startswith("https://"):
            title = None
        unique.append((title, url))
    labels = _unique_labels(unique)
    sources = [{"title": label, "url": url} for label, (_, url) in zip(labels, unique)]
    actions = [{"type": "external_link", "label": label, "url": url} for label, (_, url) in zip(labels, unique)]
    return sources, actions
