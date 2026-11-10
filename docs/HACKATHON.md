# Hackathon runbook

The demo story: **a multilingual concierge turns a business question into a
reviewed booking or a request that CCIFJ staff can actually find.**

## Start and verify

1. Copy `.env.example` to `.env` if needed. Set a private auth secret and the
   configured model credentials. Cohere is optional: without it, retrieval uses
   hybrid fused ranking. Keep model names that your provider account supports.
2. Run `./start.sh`. It installs locked dependencies, migrates the database and
   starts both services. Reload is disabled by default so editing a file does
   not unexpectedly discard a pending approval. Use `NO_RELOAD=0 ./start.sh`
   only during development. Keep one API worker.
3. Wait for `curl -fsS http://127.0.0.1:8001/api/v1/ready` to succeed (`start.sh`
   serves the API on port 8001; a manual `uvicorn` start uses 8000) before
   rehearsing model-backed answers. Guided event browsing can work before this.
4. Run `./scripts/verify.sh` for backend contracts, corpus consistency, frontend
   build and frontend tests. It makes no model calls and uses disposable test
   databases. Install frontend dependencies with `npm ci` first if you have not
   used the start script.

## Enable the staff inbox

For a demo, sign in as `staff@demo.ccifj.test` (created by `uv run concierge-db seed-demo`;
the password is in the README) and skip the rest of this section. Delete that account
before going public.

Otherwise staff access is disabled by default. Register a dedicated operator account and
read its numeric `user_id` from the login response or `/api/v1/auth/me` using
that account's bearer token. Add only that existing account ID to `.env`:

```dotenv
CCIFJ_STAFF_USER_IDS=123
```

Replace `123` with the actual account ID, then restart the API and sign in again.
Use a separate browser profile for the staff view so the visitor can stay signed
in during the demo. The header then shows **Staff inbox**. It lists approved
stored requests, recipients, references, timestamps and existing statuses, with
refresh, filtering and pagination. It is read-only; no email is sent by opening
or refreshing it. Server-side authorization protects every inbox request.

The **Conversations** tab shows what visitors asked and what the agent answered,
with the sources it cited; each request also opens the conversation it came
from. It never shows the agent's internal tool messages. Signed-in conversations
are always kept. A guest conversation is deleted when the page closes, unless the
visitor confirmed a request: then it is kept with the request, and the approval
card says so before the visitor confirms.

Account IDs are intentional: this signup flow does not verify email ownership,
so allowing staff access merely by email address would be unsafe.

## Three-minute walkthrough

| Time | Action | What it demonstrates |
|---|---|---|
| 0:00–0:40 | Ask in French: “Nous sommes une entreprise française qui souhaite se développer au Japon. Comment la CCIFJ peut-elle nous accompagner ?” Open the retrieved sources. | Business relevance and evidence from the knowledge base. |
| 0:40–1:00 | Change the language selector to 日本語. Browse upcoming events, open one and inspect its venue, prices, registration status and Japan-local time. | Visible multilingual value and database-backed facts. |
| 1:00–1:50 | Sign in as the visitor, choose **Prepare booking**, supply attendee details and review the approval card. Confirm once; ask to see your bookings and show the reference. | A complete action with explicit user approval. |
| 1:50–2:40 | Ask to draft a market-entry inquiry for staff, choose the writing language, review it and approve storage. | A useful escalation with an inspectable reference. |
| 2:40–3:00 | In the operator profile, refresh the staff inbox and open that request. | The service journey reaches the human team. |

Use the same selected event throughout; do not memorize an ID that may change.
Rehearse rejecting an approval too, and verify that no booking is created.
Do not describe reservations as payment transactions or stored requests as sent
email. Before judging, record this walkthrough on the actual working demo
machine as a backup.

## Measure quality without inventing numbers

The corpus check (`--mode corpus` below) verifies the indexed records against the
chunk file and confirms labeled source coverage for the 100 labelled questions.
It does **not** measure retrieval accuracy or answer correctness.

```bash
# Offline integrity check; no credentials or model packages needed.
python scripts/evaluate.py --mode corpus

# Full retriever, with raw multilingual queries and optional Cohere reranking.
uv run python scripts/evaluate.py --mode retrieval --output /tmp/retrieval-report.json

# Production routing/query rewriting through a running, warmed API.
# These calls use your configured model provider and may incur charges.
uv run python scripts/evaluate.py --mode api --limit 10 --output /tmp/api-sample.json
uv run python scripts/evaluate.py --mode api --output /tmp/api-report.json
```

Reports separate languages, source hit rate at five unique URLs, errors, median
latency and p95 latency. API mode measures returned source coverage after
routing; retrieval mode measures raw-query retrieval. Those are different
measurements. A source hit does not prove the answer is correct. Manually review
the demo questions for supported claims and current facts. The live run of the
organisers' question bank is recorded in
[demo/live_eval_results.md](../demo/live_eval_results.md).

Backend INFO logs include `agent_stage`, `agent_turn` and `retrieval` durations
without question text, attachment contents or credentials. Use those measurements
to identify slow stages before changing models.

## Browser smoke test

This uses a disposable API fixture, a fake agent and test accounts, not the live
database or model provider. It covers language persistence, narrow-screen event
cards, details, cancellation recovery, login, approvals and staff inbox access.
Run these commands in separate terminals from the repository root:

```bash
CCIFJ_AUTH_SECRET=browser-test-secret-at-least-32-bytes-long CCIFJ_STAFF_USER_IDS=1 \
  PYTHONPATH=src uv run uvicorn api_browser_fixture:app --app-dir tests --port 8800

cd ccifj-frontend
CCIFJ_API_TARGET=http://127.0.0.1:8800 npm run dev -- --port 5188

# From the repository root; use your installed Chromium path if applicable.
CCIFJ_CHROMIUM=/usr/bin/chromium uv run --with playwright python tests/api_browser_smoke.py
```

The fixture creates a test operator and one future event. Never use its known
passwords against the real application. The script saves mobile-event and inbox
screenshots under `/tmp`.

## Remaining boundaries

Implementation checks pass locally: 279 backend tests, 48 frontend tests and the
TypeScript typecheck. Live-model results are not included in those counts; they
are in [demo/live_eval_results.md](../demo/live_eval_results.md).

- Approval checkpoints remain process-local. A restart expires them; recovery
  releases the stale approval without replaying a write. Check bookings or
  request references before preparing the action again.
- Source provenance proves which records were retrieved. It does not certify
  each generated claim. Unsupported generated links in retrieval-backed answers
  are removed; tool-provided official form links remain usable.
- The source corpus and event database are snapshots. Confirm the demo event
  and prices with official information before presenting them as current.
- Production deployment and the backup recording still need the working demo
  environment.
