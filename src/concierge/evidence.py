"""Request-local evidence from tools, never inferred from model prose.

The mutable record is shared with child async tasks and tool worker threads via
contextvars. Every API turn installs a fresh record and resets it afterwards.
"""
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class TurnEvidence:
    space_checks: list[dict] = field(default_factory=list)
    write_started: bool = False
    read_failures: dict[str, int] = field(default_factory=dict)
    passages: list[dict] = field(default_factory=list)
    event_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    database_sources: list[dict] = field(default_factory=list)


turn_evidence: ContextVar[TurnEvidence | None] = ContextVar("turn_evidence", default=None)


def record_passages(passages: list[dict]) -> None:
    evidence = turn_evidence.get()
    if evidence is not None:
        evidence.passages.extend(dict(p) for p in passages)


def record_events(ids) -> None:
    evidence = turn_evidence.get()
    if evidence is not None:
        evidence.event_ids.extend(int(event_id) for event_id in ids)


def record_warning(code: str) -> None:
    evidence = turn_evidence.get()
    if evidence is not None and code not in evidence.warnings:
        evidence.warnings.append(code)


def record_resource(url: str) -> None:
    evidence = turn_evidence.get()
    if evidence is not None and url not in evidence.resources:
        evidence.resources.append(url)


def record_database_source(title: str, url: str, record_type: str) -> None:
    evidence = turn_evidence.get()
    if evidence is not None:
        item = {'title': title, 'url': url, 'record_type': record_type, 'provenance': 'database'}
        if item not in evidence.database_sources:
            evidence.database_sources.append(item)


def space_check_memory(previous, current=()):
    """Bounded tool-owned checks, never booking state or consent."""
    result = {}
    fields = ('space_id', 'name', 'start', 'end', 'checked_at', 'free', 'capacity')
    for item in [*(previous or []), *(current or [])]:
        if not isinstance(item, dict) or not all(key in item for key in fields):
            continue
        if type(item['free']) is not bool:
            continue
        key = (item['space_id'], item['start'], item['end'])
        result.pop(key, None)
        result[key] = {field: item[field] for field in fields}
    return list(result.values())[-4:]


def record_space_check(space, start, end, free, checked_at):
    evidence = turn_evidence.get()
    if evidence is not None:
        evidence.space_checks = space_check_memory(evidence.space_checks, [{
            'space_id': space.space_id, 'name': space.name, 'start': start.isoformat(),
            'end': end.isoformat(), 'checked_at': checked_at.isoformat(), 'free': bool(free),
            'capacity': space.capacity}])


def source_candidates(evidence, prior_urls=()):
    """Stable source IDs shared by collector and API; retain evidence text privately."""
    import hashlib
    from concierge.api.presentation import evidence_sources, _canonical
    from concierge.source_context import source_memory, page_passage

    passages = list(evidence.get("passages") or [])
    passages.extend(p for url in source_memory(prior_urls) if (p := page_passage(url)))
    candidates = {}
    for source in [*evidence_sources(passages), *(evidence.get("database_sources") or [])]:
        url = source.get("url", "")
        if not url.startswith("https://"):
            continue
        key = _canonical(url)
        source_id = "source_" + hashlib.sha256(key.encode()).hexdigest()[:16]
        if source_id not in candidates:
            candidates[source_id] = {**source, "source_id": source_id, "text": ""}
        texts = [str(p.get("text") or "") for p in passages if _canonical(p.get("url", "")) == key]
        candidates[source_id]["text"] = "\n".join(dict.fromkeys(texts))
    return candidates


def selected_sources(candidates, selected_ids, reply):
    """Validate model selections; on failure retain only explicit verified citations."""
    from concierge.api.presentation import linked_resources, _canonical
    linked, _ = linked_resources(reply)
    cited = {_canonical(item["url"]) for item in linked}
    selected = set(selected_ids or [])
    return [{k: v for k, v in source.items() if k != "text"}
            for source_id, source in candidates.items()
            if source_id in selected or _canonical(source["url"]) in cited]
