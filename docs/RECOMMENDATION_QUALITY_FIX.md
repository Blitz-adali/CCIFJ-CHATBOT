# Curated concierge recommendations — 5 November 2026

## Causes and changes

The routing prompt required three specialist tasks whenever an expansion query had a known industry. This produced unsolicited directory leads, membership advice, and a calendar alongside the actual market-entry recommendation. Routing now follows the user's current decision; the example teaching forced fanout was replaced. Answer instructions prioritize one useful recommendation, its reason, and one next step. Explicit requests for multiple services still support multiple specialists.

The membership-context prompt and shared service rules instructed the model to disclose conflicting prices, contradicting another instruction to use structured membership records. They now consistently use the structured snapshot rate, with confirmation of the current fee before applying. This resolves instruction conflict; it does not make the snapshot live or independently verified.

Directory searches match sectors and locations, not commercial roles. Tool results now explicitly warn that these are not verified buyer/importer/distributor leads, preserve more of the company description, and require explicit role/product evidence before a named commercial recommendation. Explicit sector-peer browsing remains available. These semantic recommendations still depend on model adherence. The local Legrand Japon record points to caves-legrand.com; the reviewer’s electrical-company assumption is not supported by that record. Neither example record establishes the requested organic-wine buying role. No company was deleted or reclassified from an unverified assumption.

Previously, event presentation decorated every retrieved event ID, regardless of whether the final answer recommended it. Agent cards now require the event title in the answer, are capped at three, and exclude closed or unconfirmed registration for ordinary advice. Explicit event browsing may show unconfirmed status honestly; an open-only request overrides that. Explicit history requests may show closed events. Guided upcoming-event browsing also drops closed registrations and caps its cards at three. Exact-title matching is conservative: a heavily shortened title can result in no card rather than an unrelated one.

The frontend omits missing price/location fields, shows booking controls only for confirmed-open registration, and displays the payment disclaimer only when booking controls exist. It still provides Details for an explicitly requested closed/unconfirmed event.

A bare late “skip, just answer” after substantive advice now uses a read-only summarization path: the router model receives the prior answer, may not introduce new prices/timelines/recommendations, and cannot call tools. Actual skipped intake and requests such as “skip events; find rooms” retain normal routing. The detector is intentionally conservative and does not handle every paraphrase. Factual fidelity of the generated summary still requires live review.

For regulated products, the bot should flag relevant import-channel, licensing, labeling, or certification checks for qualified specialists. It must not invent Japan-specific legal conclusions when authoritative evidence is absent. No wine-specific canned answer or assumed legal rule was added.

## Validation and remaining check

Final checks: 166 backend tests and 48 frontend tests pass; the production frontend build, Python compilation, and `git diff --check` pass.

Regression coverage includes event selection across unrelated topics, closed-event history, explicit open-only requests, guided shortlist size, directory evidence limitations, late-skip routing, and frontend missing-field/booking controls. Model boundaries are mocked; tests verify execution and presentation, not a measured improvement in live answer quality. Runtime databases were left untouched by these tests.

The continuous-chat suite now has five conversations / 38 turns, including the winery example and an equipment-manufacturer transfer case. The original 210-turn suite is unchanged. Restart the backend, refresh/rebuild the frontend, and use a new test conversation: saved earlier answers are not rewritten.

```bash
cd /home/blitz/CCIFJ-Concierge
python scripts/challenge_conversations.py --base-url http://127.0.0.1:8001 --suite tests/concierge_followup_challenges.json --on-approval reject --require-upgrade
```

Live API/provider checks remain outstanding because localhost:8001 was unreachable from this environment. Manually inspect whether the first advice is focused, named commercial leads have actual role evidence, cards match the answer and constraints, and the late short version preserves rather than changes the recommendation.

ECC implementation self-evaluation: accuracy 4/5 (code and regression evidence, no live-model claim); completeness 4/5 (reported paths addressed, live semantic review pending); clarity 4/5 (explicit deterministic/model boundary); actionability 5/5 (local changes and executable continuous suite); conciseness 4/5 (existing routing reused, no additional research on late skip). Average 4.2/5, not a chatbot rating. Highest-impact improvement is live review of role matching and summary fidelity across both industry cases. Would the user agree? The implementation addresses the reported causes; the final quality judgment needs those live answers.
