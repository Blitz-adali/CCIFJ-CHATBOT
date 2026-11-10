#!/usr/bin/env bash
# Run the live tests against a running concierge and store every answer.
#
#   ./start.sh                 # in one terminal
#   ./demo/run_tests.sh        # in another
#
# Answers are saved in demo/runs/<date-time>/ as JSON, one file per test set.
# The tests use the API keys in .env and create test rows (an @example.test
# account, a booking that is cancelled, stored drafts) in the local database.
#
# Optional: ./demo/run_tests.sh quick     only the 25 questions (about 6 minutes)
#           API=http://host:port ./demo/run_tests.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

MODE="${1:-all}"
# Use API=... when given; otherwise find the concierge on the usual ports.
if [[ -z "${API:-}" ]]; then
  API="http://127.0.0.1:8000"
  for port in 8000 8001 8002; do
    if [[ "$(curl -fsS --max-time 3 "http://127.0.0.1:$port/status" 2>/dev/null || true)" == *'"agent_ready"'* ]]; then
      API="http://127.0.0.1:$port"
      break
    fi
  done
fi
echo "Testing the concierge at $API"
OUT="demo/runs/$(date +%Y-%m-%d_%H-%M-%S)"

status="$(curl -fsS --max-time 5 "$API/status" 2>/dev/null || true)"
if [[ "$status" != *'"agent_ready":true'* ]]; then
  echo "The concierge is not ready at $API. Start it with ./start.sh and wait about 20 seconds." >&2
  exit 1
fi

# One real question first: a clear message beats 25 failures if a key or credits are missing.
probe="$(curl -sS --max-time 200 -o /dev/null -w '%{http_code}' -X POST "$API/api/v1/chat" \
  -H 'Content-Type: application/json' -d '{"locale":"en","message":"hello"}' || true)"
if [[ "$probe" != "200" ]]; then
  echo "The concierge answered HTTP $probe to a test message. Check the terminal running ./start.sh" >&2
  echo "(an OpenAI message about credits or a rate limit means the account needs topping up)." >&2
  exit 1
fi

mkdir -p "$OUT"
echo "Saving answers to $OUT"
echo "1/3  25 questions (20 from CCIFJ's question bank + 5 fallback probes)…"
python3 demo/live_eval.py "$API" "$OUT/questions.json" | tee "$OUT/questions.log"
if [[ "$MODE" != "quick" ]]; then
  echo "2/3  Member matching and member engagement agents…"
  python3 demo/agents_eval.py "$API" "$OUT/extra_agents.json" | tee "$OUT/extra_agents.log"
  echo "3/3  Bookings, approvals, safety cases, four conversations at once…"
  python3 demo/flows_eval.py "$API" "$OUT/flows.json" | tee "$OUT/flows.log"
fi
echo
echo "Done. Answers are in $OUT"
