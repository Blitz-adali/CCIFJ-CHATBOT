# Failure recovery and verification

The original challenge run had five HTTP 502 failures. Their exact causes remain
unconfirmed without the corresponding server tracebacks. These changes address
failure propagation and recovery paths found during review.

- Read-only tool exceptions become explicit unavailable results, with bounded
  attempts. Unavailable data is distinguished from a valid empty search.
- Vector and keyword retrieval can survive failure of the other branch.
  Reranking retains the existing ranking when unavailable.
- Read-only model errors and deadlines produce a localized recovery response,
  preserve the conversation, and include a reference for diagnosis.
- Writes are never automatically retried. Approval interrupts propagate normally.
  Errors after a write starts retain unknown-outcome protection and reconciliation.
- Partial lookup failures receive a localized UI notice. Raw exceptions stay in logs.
- Conversation language and draft language are tracked separately. An initial
  draft requires a selected draft language.
- Evaluation reports mark recovery as degraded and incomplete, never as passed.

## Verified locally

86 backend tests and 44 frontend tests passed; the frontend production build passed.
Backend tests cover real LangChain tool middleware, approval pause/resume, no write
retry, read-only timeout, session continuation, streaming recovery, independent
retrieval failures, and draft/reply language separation.

An in-process check exercised the actual API and backend against copied databases.
The model provider was unreachable in this execution environment. All five targeted
scenarios returned a recovery response instead of HTTP 502. Each stopped on its
first turn and is correctly recorded as degraded; this does not validate online
answer quality or complete the planned 60 turns. See
environment check (`challenge-runs/recovery-environment-check.json`, generated locally).

## Online retest

Restart the API using the normal project startup command, then run:

```bash
python scripts/challenge_conversations.py --base-url http://127.0.0.1:8001 --scenario cosmetics --scenario event_filters --scenario room --scenario recruitment --scenario multilingual --scenario staff_draft --on-approval reject --require-upgrade
```

Review complete conversations for answer quality, especially English explanations
with Japanese drafts. If these pass, run all scenarios by omitting the scenario
flags. Keep the API terminal logs if anything fails; reference IDs help correlate
errors. External outages, startup failures, and uncertain write outcomes can still
occur; recovery makes read-only failures usable without claiming the task succeeded.
