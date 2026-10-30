"""Public answer presentation only: no tool calls or changes to agent execution."""
import re
from urllib.parse import urlsplit


def linked_resources(reply):
    """Expose only links actually present in the answer; never invent citations."""
    sources, actions, seen = [], [], set()
    links = [(match.group(1), match.group(2)) for match in re.finditer(r"\[([^\]\n]+)\]\((https://[^\s)]+)\)", reply)]
    links += [(None, url) for url in re.findall(r"https://[^\s<>\])]+", reply)]
    for title, url in links:
        url = url.rstrip(".,;!?")
        try:
            parsed = urlsplit(url)
            if url in seen or parsed.scheme != "https" or not parsed.hostname:
                continue
        except ValueError:
            continue
        seen.add(url)
        title = title or parsed.netloc
        sources.append({"title": title, "url": url})
        actions.append({"type": "external_link", "label": title, "url": url})
    return sources, actions
