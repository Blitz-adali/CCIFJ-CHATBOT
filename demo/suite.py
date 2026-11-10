"""Run one set of test conversations against a running concierge and store every answer.

    python3 demo/suite.py A     # CCIFJ's recurring-question bank, questions 1-36 (English / French)
    python3 demo/suite.py B     # the bank, questions 37-71
    python3 demo/suite.py C     # 36 hard cases: the brief's examples, multi-turn requests,
                                # false premises, safety, Japanese

Run each set in its own terminal to go three times faster. Answers are written to
demo/runs/<date-time>-set<X>/answers.json after every conversation, so an interrupted
run keeps what it finished. Guest sessions only; no approval is granted, so nothing is
booked or stored. Uses the API keys in .env (about 5 model calls per answer).
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
name = (sys.argv[1] if len(sys.argv) > 1 else "").upper()
sets = json.loads((HERE / "suite_questions.json").read_text(encoding="utf-8"))
if name not in sets:
    sys.exit(f"Usage: python3 demo/suite.py {{{'|'.join(sets)}}} [api-url]")


def get(url, timeout=4):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read().decode()
    except Exception:
        return ""


if len(sys.argv) > 2:
    API = sys.argv[2].rstrip("/")
else:
    API = next((f"http://127.0.0.1:{port}" for port in (8001, 8000, 8002)
                if '"agent_ready":true' in get(f"http://127.0.0.1:{port}/status").replace(" ", "")), "")
if not API:
    sys.exit("The concierge is not running (tried ports 8001, 8000, 8002). Start it with ./start.sh and wait 20 seconds.")


def chat(body, timeout=240):
    request = urllib.request.Request(API + "/api/v1/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}"), round(time.time() - started, 1)
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors="replace")
        try:
            return error.code, json.loads(raw), round(time.time() - started, 1)
        except ValueError:
            return error.code, {"detail": raw[:300]}, round(time.time() - started, 1)
    except Exception as error:
        return 0, {"detail": repr(error)}, round(time.time() - started, 1)


out_dir = HERE / "runs" / f"{datetime.now():%Y-%m-%d_%H-%M-%S}-set{name}"
out_dir.mkdir(parents=True)
out_file = out_dir / "answers.json"
cases = sets[name]
print(f"Set {name}: {len(cases)} conversations against {API}\nSaving to {out_file}\n", flush=True)

results, failures_in_a_row = [], 0
for number, case in enumerate(cases, 1):
    session, turns = None, []
    for message in case["turns"]:
        body = {"locale": case["locale"], "message": message}
        if session:
            body["session_id"] = session
        status, data, seconds = chat(body)
        if status == 200:
            session = data.get("session_id", session)
        turns.append({
            "message": message, "status": status, "seconds": seconds,
            "reply": data.get("reply") if status == 200 else None,
            "error": None if status == 200 else data,
            "intent": (data.get("intent") or {}).get("code") if status == 200 else None,
            "sources": [{"title": s.get("title"), "url": s.get("url")} for s in data.get("sources", [])] if status == 200 else [],
            "next_actions": [a.get("id") for a in data.get("next_actions", [])] if status == 200 else [],
            "pending_approvals": [a.get("name") for item in data.get("pending_approvals", []) for a in item.get("action_requests", [])] if status == 200 else [],
            "warnings": data.get("warnings", []) if status == 200 else [],
        })
        if status != 200:
            break
        if data.get("pending_approvals"):
            break  # an approval card is waiting; this run never approves
    results.append({**{k: v for k, v in case.items() if k != "turns"}, "turns": turns})
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    line = " ".join(f"{t['status']}/{t['seconds']}s" for t in turns)
    print(f"[{number}/{len(cases)}] {case['id']} {case['locale']}  {line}", flush=True)
    failures_in_a_row = failures_in_a_row + 1 if turns[-1]["status"] != 200 else 0
    if failures_in_a_row >= 4:
        print("\nFour conversations failed in a row. Stopping: check the ./start.sh terminal "
              "(an OpenAI message about credits means the account needs topping up).", flush=True)
        break

print(f"\nDone. {len(results)} conversations saved in {out_dir}", flush=True)
