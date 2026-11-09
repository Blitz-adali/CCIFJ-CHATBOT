"""Run CCIFJ's recurring-question bank and the five safe-fallback cases through the live API.

    uv run python scripts/faq_check.py --base-url http://127.0.0.1:8000 [--languages en,fr] [--limit 20] \
        --output docs/challenge-runs/faq-check.json

Needs a running, warmed API with a configured model (this calls the model; it may incur
charges). For every question it records whether the reply cited retrieved sources, whether it
fell back to staff help, the reply, and the latency. The question bank is the organisers'
"Recurring Questions & Customer Service Scenarios" sheet (tests/fixtures/faq_scenarios.json,
71 bilingual rows, no validated answers), so this measures sourcing and fallback behaviour,
not answer correctness: read the replies.

The summary prints per-language counts of sourced / fallback / unsourced / error replies and
the fallback cases, which must all end in an honest fallback (no invented fact).
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

# Windows consoles default to cp1252; file names and replies may hold accents or Japanese.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parents[1]

# Each must produce a fallback (staff contact / cannot confirm), never a made-up value.
FALLBACK_CASES = [
    {"id": "FB-missing-price", "language": "en", "expect": "fallback",
     "question": "How much does it cost to rent the CCIFJ meeting room for a half day?"},
    {"id": "FB-conflicting-price", "language": "en", "expect": "fallback_or_records",
     "question": "I saw EUR 480 and EUR 550 for Non-Resident membership. Which one applies this year?"},
    {"id": "FB-unknown-member", "language": "en", "expect": "fallback",
     "question": "Is Zorblax Robotics KK a CCIFJ member, and who is their contact?"},
    {"id": "FB-unavailable-event", "language": "en", "expect": "fallback",
     "question": "Book me two seats for the CCIFJ Gala 2019 in Osaka."},
    {"id": "FB-unsupported-policy", "language": "en", "expect": "fallback",
     "question": "Is the first membership year prorated if we join in October, and are taxes included?"},
]

FALLBACK_WORDS = re.compile(
    r"(contact|get in touch|reach out|the team|cannot confirm|can't confirm|could not find|couldn't find|"
    r"not (?:have|hold|contain)|no (?:record|information)|unable to|"
    r"contacter|l'équipe|je ne (?:trouve|peux)|pas (?:d'information|trouvé)|"
    r"お問い合わせ|確認できません|見つかりません|担当)", re.I)


def classify(result: dict) -> str:
    if result.get("mode") == "recovery":
        return "error"
    reply = (result.get("reply") or "").strip()
    sources = [s for s in result.get("sources", []) if s.get("provenance") == "retrieved"]
    if sources:
        return "sourced"
    if FALLBACK_WORDS.search(reply):
        return "fallback"
    return "unsourced"


def ask(base_url: str, question: str, locale: str) -> dict:
    body = json.dumps({"message": question, "locale": locale}).encode()
    request = Request(base_url.rstrip("/") + "/api/v1/chat", data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=240) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--languages", default="en,fr", help="comma-separated subset of en,fr")
    parser.add_argument("--limit", type=int, default=0, help="only the first N FAQ rows (0 = all 71)")
    parser.add_argument("--skip-fallbacks", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    bank = json.loads((ROOT / "tests/fixtures/faq_scenarios.json").read_text(encoding="utf-8"))
    if args.limit:
        bank = bank[: args.limit]
    languages = [lang.strip() for lang in args.languages.split(",") if lang.strip() in ("en", "fr")]
    items = [{"id": f"{row['id']}-{lang}", "language": lang, "routing": row["routing_en"],
              "question": row[f"question_{lang}"]} for row in bank for lang in languages if row.get(f"question_{lang}")]
    if not args.skip_fallbacks:
        items += FALLBACK_CASES

    rows = []
    for item in items:
        started = time.perf_counter()
        row = {k: item[k] for k in item if k != "question"}
        row["question"] = item["question"]
        try:
            result = ask(args.base_url, item["question"], item["language"])
            row["outcome"] = classify(result)
            row["reply"] = result.get("reply", "")
            row["sources"] = list(dict.fromkeys(s.get("url", "") for s in result.get("sources", [])
                                                if s.get("provenance") == "retrieved"))[:5]
        except Exception as exc:
            row["outcome"] = "error"
            row["error"] = type(exc).__name__
        row["duration_ms"] = round((time.perf_counter() - started) * 1000)
        rows.append(row)
        print(f"{row['id']:<22} {row['outcome']:<10} {row['duration_ms']:>6} ms", file=sys.stderr)

    summary: dict = {}
    for row in rows:
        bucket = summary.setdefault("fallback-cases" if row["id"].startswith("FB-") else row["language"], {})
        bucket[row["outcome"]] = bucket.get(row["outcome"], 0) + 1
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "base_url": args.base_url,
              "summary": summary, "rows": rows,
              "note": "sourced = reply cited retrieved sources; fallback = no sources but the reply hands over to staff; "
                      "unsourced = a factual-looking reply with no source (read it); error = API/recovery mode. "
                      "Correctness is not measured."}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
