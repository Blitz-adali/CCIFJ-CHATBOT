#!/usr/bin/env python3
"""Repeatable corpus integrity, retrieval, or live answer-source evaluation.

Corpus mode is offline and uses only the standard library. Retrieval mode uses
raw multilingual queries (no translation). API mode exercises production routing
and may incur configured model-provider charges. Neither mode grades factual
answer correctness: source hit rate is a separate, narrower measurement.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["language"]].append(row)
        groups["all"].append(row)
    return {language: {
        "questions": len(items), "errors": sum(bool(row.get("error")) for row in items),
        "source_hit_at_5": sum(row.get("hit_at_5", False) for row in items) / len(items),
        "median_ms": statistics.median([row["duration_ms"] for row in items]),
        "p95_ms": percentile([row["duration_ms"] for row in items], .95),
    } for language, items in sorted(groups.items())}


def corpus_report(dataset):
    corpus = [json.loads(line) for line in (ROOT / "src/concierge/index/corpus.jsonl").read_text().split("\n") if line.strip()]
    chunks = json.loads((ROOT / "src/concierge/data/chunks.json").read_text())
    by_id = {str(chunk["id"]): chunk for chunk in chunks}
    urls = {chunk["url"] for chunk in chunks}
    mismatches = [str(row.get("id")) for row in corpus if str(row.get("id")) not in by_id or row.get("text") != by_id[str(row.get("id"))]["content"]]
    missing = [row["id"] for row in dataset if not set(row["relevant_urls"]) & urls]
    return {"corpus_records": len(corpus), "chunks": len(chunks), "duplicate_corpus_ids": len(corpus)-len({str(row.get("id")) for row in corpus}),
            "duplicate_chunk_ids": len(chunks)-len(by_id), "mismatched_records": mismatches,
            "missing_corpus_ids": sorted(set(by_id)-{str(row.get("id")) for row in corpus}),
            "questions": len(dataset), "questions_missing_labeled_source": missing,
            "note": "Corpus consistency only. Chroma alignment, retrieval accuracy and answer correctness have not been measured."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("corpus", "retrieval", "api"), default="corpus")
    parser.add_argument("--dataset", type=Path, default=ROOT / "tests/fixtures/retrieval_eval_100.json")
    parser.add_argument("--limit", type=int, default=0, help="0 runs all questions")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    dataset = json.loads(args.dataset.read_text())
    if args.limit:
        dataset = dataset[:args.limit]
    report = {"mode": args.mode, "generated_at": datetime.now(timezone.utc).isoformat()}
    if args.mode == "corpus":
        report.update(corpus_report(dataset))
    else:
        if args.mode == "retrieval":
            from concierge.rag.src.rag_search import search_passages
            from concierge.rag.src.config import EMBED_MODEL_NAME, INDEX_PATH
            from sentence_transformers import SentenceTransformer
            import bm25s
            model = SentenceTransformer(EMBED_MODEL_NAME, device="cpu")
            retriever = bm25s.BM25.load(str(INDEX_PATH), load_corpus=True)
        rows = []
        for item in dataset:
            started = time.perf_counter()
            row = {"id": item["id"], "language": item["language"]}
            try:
                if args.mode == "retrieval":
                    sources = search_passages(retriever, model, item["question"])
                else:
                    # New anonymous conversation per question avoids history leakage.
                    body = json.dumps({"message": item["question"], "locale": item["language"]}).encode()
                    request = Request(args.base_url.rstrip("/") + "/api/v1/chat", data=body, headers={"Content-Type": "application/json"})
                    with urlopen(request, timeout=200) as response:
                        result = json.load(response)
                    if result.get('mode') == 'recovery':
                        row.update(error='AnswerUnavailable', degraded=True)
                    sources = [source for source in result.get("sources", []) if source.get("provenance") == "retrieved"]
                    row["answer_present"] = bool(result.get("reply", "").strip())
                    row["warnings"] = result.get("warnings", [])
                returned = list(dict.fromkeys(source["url"] for source in sources))[:5]
                row.update(retrieved_urls=returned, hit_at_5=bool(set(returned) & set(item["relevant_urls"])))
            except Exception as error:
                # Avoid serializing credentials, provider responses or user text.
                row.update(error=type(error).__name__, hit_at_5=False)
            row["duration_ms"] = round((time.perf_counter()-started)*1000, 2)
            rows.append(row)
            print(f"{item['id']}: {'error' if row.get('error') else 'hit' if row['hit_at_5'] else 'miss'}", file=sys.stderr)
        report.update(summary=summarize(rows), rows=rows,
                      note="Source hit rate is not answer correctness. Retrieval mode uses raw multilingual queries; API mode includes query routing and rewriting. API timings include cold starts if the server is not prewarmed.")
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    print(rendered)
    if args.mode == "corpus" and any(report[key] for key in ("mismatched_records", "missing_corpus_ids", "duplicate_corpus_ids", "duplicate_chunk_ids", "questions_missing_labeled_source")):
        return 1
    if args.mode != "corpus" and report.get("summary", {}).get("all", {}).get("errors"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
