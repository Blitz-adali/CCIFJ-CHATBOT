# Concierge behavior upgrade

> Historical milestone report. Counts and limitations below describe that stage; see [Changes README](../CHANGES_README.md) for the latest consolidated status.

This change addresses the observed ten-turn baseline: incomplete membership
fees, inconsistent currencies, long generic answers, unnecessary rewriting,
short worker context and awkward login handoffs.

## Resulting behavior

- **Live database answers for common questions.** Exact fees and joining
  questions in English, French and Japanese read every stored category and
  preserve its currency. Prices are not copied into another static price list.
  The response identifies its database-snapshot status and links its supporting
  source. Changed database fees appear on the next request. Joining gives the
  application link without repeating the full fee table.
- **One source for official form links.** Forms use `resources.py`. The
  membership URL was checked against the official
  [application](https://survey.ccifi.net/232729?lang=en), and the database rates
  match the published
  [2026 fee announcement](https://www.ccifj.or.jp/en/publications/news/news-detail/news/cci-france-japon-general-meeting-2026-a-look-back-at-the-event.html).
  These checks do not make future rate changes impossible.
- **Persistent user facts within the conversation.** The existing router call
  extracts a bounded brief for products, goals, company details, separate service
  and travel budgets, constraints and preferences. Updates require exact quotes
  from the latest user message. Latest corrections replace earlier slot values;
  unknown amounts stay unknown. The brief is stored with SQL conversation
  metadata and reaches routing, specialists and synthesis after history trimming.
  It is untrusted user data, never proof of membership, identity or authorization.
- **More focused guidance.** Shared worker instructions call for a concise
  answer or one best recommendation, at most one focused question, respect for
  supplied information, and realistic budget-aware next steps. They explicitly
  distinguish market testing from incorporation, directory sectors from proven
  distributor capability, and preparation from submission.
- **Less avoidable latency.** Common read-only answers need no model
  initialization. One-domain specialist answers bypass collector rewriting;
  multiple-domain answers still receive synthesis. Final scope review remains.
  Approximate token counting avoids tokenizer downloads/provider-specific model
  lookup; Ollama models remain warm. Misconfiguration no longer silently switches
  from OpenAI to Ollama.
- **Stricter event filtering.** Search accepts result limits, original-currency
  budgets and open-registration constraints. A member-only discount does not
  satisfy a guest's budget. Unknown prices do not satisfy a strict cap.
  Closed registration is separate from unknown remaining ticket capacity.
- **Smoother guest handling.** Actual booking requests offer login without
  asking the visitor to discard this conversation. Old booking discussion does
  not prevent later read-only event questions. Preparation-only language remains
  separate from consent to save a booking.
- **Version-aware testing.** `/api/v1/status` includes an import-time source
  fingerprint and feature flags. The challenge runner can require that the
  running backend matches this checkout before spending model calls.

## Validation and limits

- **64 backend tests passed.** Regression checks include real graph-node behavior with model calls
  mocked at the boundary, brief correction/forgetting rules, cross-turn SQL
  persistence, live fee changes, currencies, guest discounts, login actions,
  no-rewrite draft preservation and continued scope review.
- **42 frontend tests** and the TypeScript/Vite build passed, including display of
  database-provenance source links. Python compilation, shell syntax and corpus
  integrity checks passed.
- [Twelve actual API turns](upgrade-smoke.json) completed in a single session
  against disposable copies of the application's databases. They covered fees,
  joining, booking guidance and event browsing in three languages. These exact
  common questions needed no model calls. Their recorded timings are in-process
  API timings, not browser or free-form LLM latency measurements.
- The long challenge suite was attempted through the real API/agent via an
  in-process transport because local TCP binding is restricted here. Both first
  free-form conversations failed when the configured model provider could not be
  reached. The runner stopped and saved a partial report rather than fabricating
  answers or silently resetting sessions. **The 210-turn suite has not passed.**
- Full live assessment of memory extraction, naturalness, multilingual dialogue
  and real model latency remains necessary. Prompt improvements and unit tests
  are not evidence that all those conversations now succeed.
- No existing user or vector database was intentionally edited by these checks;
  the API smoke and attempted challenge run used temporary copies. Existing
  workspace database changes predated this work and were preserved.

## Run against the updated app

Restart the API so it imports the new code. Then, from the repository root:

```bash
# One continuous thirty-turn conversation first.
python scripts/challenge_conversations.py --scenario marathon --on-approval reject --require-upgrade

# Full 210-turn suite, preserving one session per conversation.
python scripts/challenge_conversations.py --on-approval reject --require-upgrade
```

The version check stops before sending challenge prompts if the server is stale.
Proposed writes are rejected, never approved. Reports are timestamped under
`docs/challenge-runs/` and partial results are saved after every turn.
