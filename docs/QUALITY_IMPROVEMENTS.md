# Concierge quality improvements after the 72-turn run

Baseline: `challenge-runs/20261104T123946451220Z.json` completed six continuous
12-turn conversations. All 72 requests answered without transport errors or
recovery responses. Median latency was 15.54 seconds; semantic retrieval was
unavailable on 26 turns. Successful transport does not imply correct answers.

## Changes

- **Memory:** bounded original user statements supplement the extracted brief in
  router and worker prompts. Early context and recent corrections survive long
  chats. Dedicated hiring-role, location, language and salary fields are now
  supported. Statements remain untrusted and never authorize actions.
- **Relevant follow-ups:** the final scope reviewer now sees original user context
  and the brief, rather than judging a short correction in isolation. Routing
  instructions explicitly cover corrections, recaps and language switches.
- **Draft language:** language parsing requires an explicit language request.
  “Japanese food distributors” and “French cheese” no longer choose a document
  language. Japanese drafts with English explanations remain supported.
- **Event uncertainty:** no database deadline means registration is unconfirmed,
  rather than automatically open. Tools and localized cards agree; automatic
  booking prompts are withheld for that status. Conflicting website/database
  deadlines must be disclosed until resolved, rather than silently switching.
- **Useful guidance:** prompts prioritize one feasible action, discourage repeated
  promotion of over-budget packages and avoid repeatedly collecting sender details
  before the visitor asks for a draft.
- **Source integrity:** the persisted vector index contains 5,508 chunks while
  the keyword corpus contains 2,325 under a different layout. Equal numeric IDs
  do not identify the same passage. Vector IDs are now namespaced before fusion,
  preserving each passage's own text and URL. Neither database was rebuilt.
- **Search reliability:** model/index initialization is protected against concurrent
  warmup and search. General/business tools now use the same read-error boundary
  as other agents; approval remains outside the boundary and writes are not retried.

## Validation and remaining limits

**Verified: 94 backend tests and 45 frontend tests passed; the frontend production
build, Python compilation and whitespace checks passed.**

Regression tests cover missing facts outside the router window, contextual scope
review inputs, ambiguous country adjectives, deadline uncertainty, source-ID
collisions and concurrent model loading. Frontend tests cover the unconfirmed
registration card. These checks verify mechanics; mocked model boundaries cannot
prove live conversational quality.

A local offline semantic query against a temporary copy of the vector database
returned three recruitment passages. Corpus consistency checks passed with 2,325
records and no duplicate IDs, missing records or mismatches against chunks.json.
That does not establish freshness or diagnose the 26 semantic failures in the live
API; server tracebacks are still needed if those recur. No speed improvement or
higher concierge score is claimed without a fresh online test.

Restart the API and rerun the same command for a direct comparison:

```bash
python scripts/challenge_conversations.py --base-url http://127.0.0.1:8001 --scenario cosmetics --scenario event_filters --scenario room --scenario recruitment --scenario multilingual --scenario staff_draft --on-approval reject --require-upgrade
```

Review recruitment turns 8–12, multilingual turn 9, the first draft, and event
registration follow-ups closely. Then run the full suite if these improve.

## ECC self-review

This evaluates the engineering delivery, not the chatbot's conversational score.

| Axis | Score / 5 | Evidence and remaining gap |
| --- | --- | --- |
| Accuracy | 4 | Regression assertions check concrete failures; online model behavior remains unverified. |
| Completeness | 3 | Main transcript weaknesses addressed; the cause of live semantic failures remains unconfirmed. |
| Clarity | 4 | Changes and limits documented; source-index layout still needs longer-term consolidation. |
| Actionability | 4 | Changes are implemented with a repeatable comparison command; API restart and host-side evaluation remain. |
| Conciseness | 4 | No new provider or dependency; bounded user-history context adds prompt length. |

Overall: 3.8/5. Highest-impact follow-ups are the online conversation comparison,
server-log diagnosis if semantic warnings recur, and refreshing conflicting event
data. The incomplete live diagnosis is explicitly retained rather than presented
as fixed. The user can verify this assessment against the saved baseline and next
run; no measured score improvement is claimed yet.
