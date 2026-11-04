"""Public answer presentation only: no tool calls or changes to agent execution."""
import re
from urllib.parse import urlsplit


def evidence_sources(passages):
    """Source provenance comes from retrieved records, not generated links."""
    sources = {}
    for passage in passages:
        url = passage.get("url", "")
        try:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname:
                continue
        except ValueError:
            continue
        if url not in sources:
            sources[url] = {"id": str(passage.get("id", "")), "title": passage.get("title") or parsed.hostname,
                            "url": url, "chunk_ids": [], "provenance": "retrieved"}
        chunk_id = str(passage.get("id", ""))
        if chunk_id not in sources[url]["chunk_ids"]:
            sources[url]["chunk_ids"].append(chunk_id)
    return list(sources.values())


def grounded_links(reply, allowed_urls):
    """Unverified links in a knowledge answer remain plain text, not citations."""
    reply = re.sub(r"\[([^\]\n]+)\]\((https://[^\s)]+)\)",
                   lambda match: match.group(0) if match.group(2) in allowed_urls else match.group(1), reply)
    reply = re.sub(r"https://[^\s<>\])]+", lambda match: match.group(0)
                  if match.group(0).rstrip(".,;!?") in allowed_urls else "", reply)
    # There is no model-independent mapping from generated [1] markers to
    # retrieval order. Keep verified URL citations and the source list instead.
    return re.sub(r"\[\d+\](?!\()", "", reply)


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
