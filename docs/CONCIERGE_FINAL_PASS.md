# Concierge reliability and continuity pass

This pass addresses the complete evidence from both six-conversation runs, rather
than treating HTTP success as answer quality. The latest baseline,
`challenge-runs/20261104T131409059193Z.json`, answered 72/72 turns but still had
contradictory event venues, 32 semantic-search fallbacks, and a temporary English
request that incorrectly overrode a later French turn.

## Event data and source continuity

The F&B event's exact Nihonbashi address was already in `events_jsonld` inside the
local crawl. Text-only search excerpts omitted it. Search results now include
structured event metadata, and agents can read the complete indexed source page
before declaring a field absent. No arbitrary URL fetch is allowed.

Known event source URLs survive SQL-backed chat turns, common FAQs, guided screens
and read-only recovery. Follow-ups reconstruct the source facts from the crawl,
so a shorter excerpt cannot erase a known venue. Citations to genuinely retrieved
prior sources retain their provenance. Stored URLs never confer permissions.

Structured future-event lookup provides a path independent of semantic search.
Crawl candidates are explicitly distinguished from live database matches, known
prices, eligibility and current ticket availability.

For an exact title and start-time match, a known expired crawl deadline blocks
booking even when the database deadline is missing. This is enforced in the
booking persistence function as well as tools and event cards. A future crawl
deadline alone still leaves current registration unconfirmed. The databases were
not rebuilt or rewritten as part of this pass. Source snapshots still need normal
refreshing, and unresolved conflicts remain conservative.

## Language, questions and search

One-turn requests such as “now answer in English” expire for subsequent turns;
English explanations around a Japanese draft remain a separate persistent choice.
Instructions now require clarification questions that the visitor can answer,
rather than asking the visitor whether CCIFJ offers an unverified service.

Embedding/tokenizer initialization tries the installed local cache first, avoiding
unnecessary remote metadata access when model files are already available. Search
resources warm independently: a failed embedding model does not stop keyword
warmup. Readiness requires at least one usable retrieval path. Initialization stays
protected against concurrent warmup and chat requests.

Sanitized retrieval diagnostics are available in `/api/v1/status` and persisted by
the running server in ignored `var/retrieval-health.json`. The local file records
exception classes and stack locations, with no query text, exception messages,
credentials or full paths. Public status omits stack locations. Diagnostics do not
turn a successful lookup into a failed request if their file cannot be written.

The precise cause of the 32 prior semantic failures is still unconfirmed: those
transcripts contain warning codes, not the originating traceback. Cache-first
loading and independent warmup address concrete failure paths; diagnostics will
make recurrence inspectable without manually copying terminal output.

## Validation

The real cached embedding model, copied Chroma database, actual keyword index and
search tool returned five passages for each of three concurrent fixed queries,
with no retrieval warnings. The F&B query included its structured Nihonbashi
location. Reranking was bypassed to avoid external provider access; this was a
retrieval integration check, not a live language-model answer-quality evaluation.
Results: retrieval verification (`retrieval-local-verification.json`, generated locally).

Regression tests cover the real transcript failures: missing venues, expired
source deadlines, future deadlines with unconfirmed registration, blocked writes,
source persistence through API/FAQ/guided transitions, prior-source citations,
transient reply languages, independent warmup and diagnostics privacy. Frontend
checks and a production build were also run. Final result: **111 backend tests and 45 frontend tests passed**, along with
the frontend production build, Python compilation, shell syntax and whitespace checks.

This execution environment cannot connect to the host API or model provider, so
no new online concierge score or speed improvement is claimed.

## Full live verification

Restart the API, then run the complete 16-conversation, 210-turn suite:

```bash
python scripts/challenge_conversations.py --base-url http://127.0.0.1:8001 --on-approval reject --require-upgrade
```

The run preserves one session per conversation and includes the 30-turn memory
marathon. Review actual answers, including contradictions and unnecessary offers;
completed transport is not an automatic quality pass. If retrieval warnings recur,
`var/retrieval-health.json` now provides a safe diagnostic artifact alongside the
transcript. It reflects the latest server process, so retain it before another
restart if diagnosing a failure.

## ECC engineering self-review

| Axis | Score / 5 | Evidence and limitation |
| --- | --- | --- |
| Accuracy | 4 | Real source metadata and retrieval integration checked; live model behavior remains unverified. |
| Completeness | 3 | Data continuity, language and recovery paths addressed together; exact prior semantic exception remains unavailable. |
| Clarity | 4 | Snapshot versus live evidence is explicit; users still need staff confirmation of missing prices/capacity. |
| Actionability | 4 | Implementation, regression checks and a full evaluation command are supplied; host API restart is required. |
| Conciseness | 4 | No additional model call is required for source memory; retained source context adds bounded prompt length. |

Overall: 3.8/5 for the engineering delivery, not a chatbot score. The highest-impact
remaining verification is the full online run and, only if warnings recur, review
of the new diagnostics. The visitor-question rule still needs live evaluation.
The limits are explicit so the user can judge the result against the saved runs.

## Follow-up run: 20261104T135223925075Z

This was another selected six-chat/72-turn run, not the full 210-turn suite.
All 72 requests answered without HTTP errors or recovery responses. Event
registration remained consistently closed for Romain Millet; the F&B venue,
per-person budget interpretation, recruitment brief, French budget correction
and final Japanese clarification improved. Median latency was 17.34 seconds
(previously 14.805), with a 49.31-second maximum. Semantic warnings remained on
28 turns, so no speed or complete-search-success claim is justified.

The new diagnostics identified `AcceleratorError` inside BERT forward execution.
The embedding encoder now explicitly uses CPU in the API, indexing and evaluation
loaders. The conversation model/provider configuration is unchanged. The exact
underlying accelerator/driver fault was not identified; CPU avoids that execution
path rather than attempting a system-level driver change.

The room recap also denied earlier checks. Timestamped tool-owned room check
results now persist through normal API turns and guided/FAQ paths, with a bounded
four-check history. This establishes that a check happened without claiming a
reservation, consent, guaranteed current availability or unknown seating capacity.
Private reservation titles and attendee details are not retained in this ledger.

Both changes postdate this transcript and require API restart. The earlier
diagnostics have been preserved beside the run, and the human review is in
`challenge-runs/20261104T135223925075Z-review.json`.

Follow-up verification: **113 backend tests passed**. The actual CPU retrieval
check returned five passages for each of three fixed queries with zero warnings.
This is local verification; the CPU and room-memory fixes are not yet reflected
in a host-side conversation transcript.
