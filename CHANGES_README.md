# Concierge changes, benefits, and tradeoffs

Updated: **5 November 2026**.

This explains the work done since you handed over the working CCIFJ Concierge.
The project already had a chat application, authentication, bookings, approval
flows and staff-request storage. The work below improves those foundations; it
does not claim that the entire application was built from scratch.

## What is published and what is still local?

The last GitHub push confirmed in this conversation was **`a766b34` on
`ECC-branch`**. It combines the concierge improvements with the existing remote
branch's work. No other branch was pushed.

The newest changes—language-request scopes, reference history, fact-relationship
evidence, email-recipient checks, public-search caching and their tests—are
**local changes after that commit**. This README also is not pushed yet.

The sections below cover both stages. “Latest local work” identifies changes
that were not present in the last full live conversation test.

## 1. More reliable membership answers

**Changed**

- Common membership-fee and joining questions in English, French and Japanese
  use the membership records directly, without waiting for the language model.
- Responses use the categories actually stored, including their original EUR
  or JPY currency, rather than the old incomplete four-category FAQ answer.
- Updated the relevant FAQ content and consolidated the main official form links
  in [resources.py](src/concierge/resources.py).
- Fees identify their source; record timestamps are distinguished from proof of
  current website verification. Conflicting or historical prices need confirmation.

**Benefit:** more consistent prices and currencies, complete category lists,
faster answers to common questions, and fewer inconsistent application links.

**Tradeoff:** the application can only be as current as its records and sources.
It does not automatically verify every fee, eligibility rule, tax condition or
proration policy against CCIFJ before answering. Exact common questions get the
fast path; arbitrary wording may still need the model.

## 2. Memory and corrections across a conversation

**Changed**

- Added a bounded conversation brief for company details, products, objectives,
  budgets, deadlines, preferences, hiring details and constraints.
- Remembered updates must quote the latest user message. Model guesses and
  assistant statements cannot be stored as new user facts through this path.
- A correction replaces the previous value in that field. Separate service and
  travel budgets remain separate; an unspecified amount stays unknown.
- The brief persists in SQL conversation metadata and is supplied to routing,
  specialists and synthesis, even when recent message history is trimmed.
- Added bounded original-user statements as backup when the model misses a fact.
- Preserved context through common FAQ answers, guided screens and read-only
  recovery replies.
- Preserved timestamped room-availability checks separately from assistant prose.
  A past check is not a reservation or a guarantee of current availability.

**Benefit:** better long-chat continuity, fewer repeated questions, and more
accurate recaps after changes to budgets, locations, attendees or goals.

**Tradeoff:** memory remains bounded and extraction can still miss or misunderstand
something. The additional context uses tokens. User statements remain unverified
and never prove identity, membership, permission or approval.

## 3. More useful conversational behavior

**Changed**

- Shared instructions prioritize the answer, a relevant recommendation and one
  useful next step rather than a brochure of every service.
- Guidance distinguishes demand validation from incorporation, preparation from
  submission, and directory sector labels from proven distribution activity.
- Added recent optional-draft-offer context so an ignored offer need not be
  repeated every time a contact is mentioned.
- Preserve tentative wording such as “we hope to visit” instead of silently
  upgrading it to a confirmed plan.
- Latest local work removes older instructions that explicitly required an
  email offer whenever a contact was shared. Those rules conflicted with the
  newer guidance against repetition.
- Budget guidance allows requested comparisons with more expensive services,
  while excluding them from recommendations presented as affordable.

**Benefit:** advice is more focused and less pushy, while explicit requests for
help drafting or comparing options remain supported.

**Tradeoff:** these are partly model instructions, not guarantees. The full live
run still showed repeated offers and occasional weak recommendations. Supplying
an offer history does not itself prove that every future reply will be concise.

## 4. Language handling and draft language

**Changed**

- Added a visible EN/FR/Japanese language selector and localized relevant UI text.
- Separated the language of an email draft from the language of explanations.
  A Japanese draft can keep English explanations through later edits.
- A suggested draft language does not count as the visitor selecting it. The
  initial draft is gated until a language is selected.
- Fixed country adjectives such as “Japanese food distributors” being mistaken
  for a request to write in Japanese.
- Latest local work distinguishes task-local reply instructions, persistent
  conversation preferences and preferences for explaining a draft.
- Added recognition for short switches such as “Now in English,” expiry of
  one-answer overrides, and restoration of a prior persistent preference.
- Added counterexamples: switching to Japanese suppliers or discussing
  French-speaking Canada must not become a language instruction.

**Benefit:** more predictable multilingual conversations and fewer unexpected
translations or language switches during drafting.

**Tradeoff:** explicit-command parsing covers common forms, not every possible
natural-language expression. The router still helps identify language. The latest
scope changes have local tests but have not passed a new live multilingual suite.

## 5. More faithful references and fact relationships — latest local work

**Changed**

- Added bounded earlier-answer context when a visitor refers to “the first one,”
  “the other option,” or a previously mentioned item.
- The context preserves visible conversation order instead of treating the
  newest search ranking as the order previously shown to the visitor.
- Company age, current location and their supporting user statements are kept
  distinct. “Seven years old” must not become “based in this city for seven years.”
- Ambiguous references should prompt clarification instead of declaring that the
  visitor selected an item.

**Benefit:** targets the wrong-first-event and age/location mistakes found in the
210-turn run through general conversation evidence, not hardcoded topic answers.

**Tradeoff:** earlier-answer context is bounded and is not verified source evidence.
The model can still misinterpret a reference; this is not a deterministic entity
resolver or a substitute for checking current records.

## 6. Search reliability and source integrity

**Changed**

- Vector and keyword retrieval can fail independently without discarding the
  working branch. Total retrieval failure is distinguished from no matching data.
- If reranking fails or no Cohere key is configured, results retain their fused
  ranking instead of causing the whole answer to fail.
- The index builder keeps chunk IDs, text and URLs together; malformed records
  are detected rather than treated as valid empty results.
- Namespaced vector IDs before fusion: the vector and keyword indexes use
  different chunk layouts, so equal numeric IDs cannot overwrite unrelated
  passages or attach the wrong URL to text.
- Source lists use retrieved record provenance. Generated URLs and citation
  numbers are not automatically accepted as evidence.
- Read-source tools only open known local corpus pages, not arbitrary URLs.
- Model/tokenizer loading tries the local cache first. Model and keyword-index
  initialization are protected against concurrent startup and chat requests.
- Independent warmup allows one usable retrieval branch to keep the service
  available, while readiness rejects a situation with neither branch usable.

**Benefit:** fewer broken answers, more trustworthy source associations, and useful
retrieval even when an optional provider is unavailable.

**Tradeoff:** fallback ranking can be worse than reranked results. The underlying
crawl may be stale. This does not automatically rebuild all indexes or refresh
all website content. A cited source can support a claim without proving that
claim is still current.

## 7. CPU use: exactly what changed

The small **`intfloat/multilingual-e5-small` search embedding model** now explicitly
runs on **CPU** in the application, indexing and retrieval-evaluation loaders.

The live diagnostics showed `AcceleratorError` inside its BERT inference path.
CPU execution avoided that failing path; subsequent live runs had healthy
semantic retrieval.

**Your main chat model/provider was not switched to CPU by this change.** Its
OpenAI/Ollama configuration remains separate.

**Benefit:** reliable semantic search on the tested machine without changing GPU
drivers or the computer's system configuration.

**Tradeoff:** teammates using this code also get CPU embeddings. A supported GPU
might be faster, especially for large indexing jobs. The precise driver,
compatibility or hardware cause of the accelerator failure was not diagnosed.
An optional GPU setting has not yet been added.

## 8. Speed changes and their limits

**Changed**

- Common read-only membership answers and guided event browsing avoid model
  initialization and unnecessary model calls.
- A single specialist's usable final answer bypasses collector rewriting;
  multi-domain results still receive synthesis. Final scope review remains.
- Approximate token counting avoids unnecessary provider-specific tokenizer
  lookups. Ollama models are kept warm for 600 seconds.
- Latest local work adds a process-local cache for successful public-document
  retrieval: up to 32 entries, with a 120-second lifetime and index-file
  generation checks. Returned passages are copied to prevent accidental mutation.
- Cache keys do not retain raw query text. Partial/fallback/error results are not
  cached through the checked path. SQL availability, bookings, account data and
  writes never use this cache.

**Benefit:** avoids work that does not improve an answer and reduces repeated
public-source retrieval without treating changing transaction data as cached facts.

**Tradeoff:** the last full live run still had a **16.02-second median** and a
**47.5-second maximum** response time. No new end-to-end speed improvement is yet
proven for the latest cache. It helps repeated identical queries, not all questions.
Keeping models warm consumes memory; caching adds bounded memory and may reuse a
public-corpus result briefly. It does not turn a stale crawl into live information.

A local check with the actual CPU encoder and copied vector database returned
identical source text for two repeated queries, with no warnings. Repeated
retrieval took roughly 1 ms; the first calls took about 4,044 ms and 88 ms.
External reranking was bypassed, and no chat-model response was generated, so
these numbers are not conversation response times.

## 9. More consistent event information

**Changed**

- Event filtering supports budgets, currencies, result limits, registration
  constraints and the appropriate member/non-member price basis.
- Unknown prices do not qualify as verified within-budget options. Remaining
  ticket capacity is kept distinct from registration deadlines.
- Added structured event cards with dates, Japan-local time, venue, prices,
  registration status and preparation actions.
- A missing database deadline does not automatically mean registration is open.
- Retrieved event passages include structured crawl metadata, including locations
  that were missing from text excerpts.
- Known event source URLs persist across follow-ups and are reconstructed from
  source records. Complete indexed-page lookup can check an omitted detail.
- Structured future-event lookup provides an alternative to semantic search.
- For an exact title/start-time match, a known expired source deadline blocks
  booking even if the database omitted that deadline. Enforcement reaches the
  booking persistence function, not just the answer wording.

**Benefit:** fewer open/closed contradictions, fewer lost venues, and clearer
separation of candidates from verified matches and completed bookings.

**Tradeoff:** website snapshots do not confirm live seats or registration acceptance.
Exact matching can leave inconsistent or differently named records unresolved.
Conservative treatment may require staff confirmation, including a genuinely
extended deadline. Strict wording such as “after 18:00” still needs careful model
handling; no new universal numeric/time constraint engine was added.

## 10. Safer, more helpful failure handling

**Changed**

- Read-only tool exceptions become explicit unavailable results with bounded
  repeat attempts; approval interrupts still propagate normally.
- Verified no-write model failures and deadlines can return a localized recovery
  response, save it, retain conversation context and let the visitor continue.
- Write tools are not automatically retried. Failures after a write starts retain
  unknown-outcome protection and the need to reconcile before retrying.
- Added user-facing partial-service notices and diagnostic references without
  exposing raw exceptions in the chat.
- Added sanitized retrieval health information to `/api/v1/status` and a local
  `var/retrieval-health.json` artifact. It omits queries, credentials, raw exception
  messages and full paths; public status also omits stack locations.

**Benefit:** a temporary read outage need not become an unexplained HTTP 502 or
force a new conversation, while uncertain writes are not falsely called failures
or silently replayed.

**Tradeoff:** outages and HTTP errors remain possible, particularly startup errors,
SQL failures and uncertain writes. Recovery is an incomplete answer, not success.
Friendly recovery can make an underlying bug less obvious unless operators inspect
logs and diagnostics. It does not prove the cause of every earlier 502.

## 11. Drafts and staff follow-up

**Changed**

- Added a read-only staff inbox UI/API for the project's existing stored staff
  requests: references, recipients, messages, timestamps, status filters and paging.
- Staff access is disabled by default and explicitly granted to registered
  account IDs through `CCIFJ_STAFF_USER_IDS`. Signup cannot grant itself staff
  access by claiming an email address or submitting an extra staff flag.
- Latest local work checks fenced email drafts for a real recipient header.
  A greeting in `To:` is not accepted as an address.
- A missing address can come from one direct contact-tool result, or be retained
  from the most recent selected draft during an edit. Multiple possible contacts
  are not silently reduced to one; otherwise a clearly incomplete placeholder
  and notice are shown. The sender is not used as a recipient fallback.

**Benefit:** approved requests can be found by staff, and an incomplete email draft
is less likely to be presented as ready to submit.

**Tradeoff:** **storing a staff request still does not send an email**. Inbox access
needs operator setup. Recipient checks do not verify mailbox deliverability or
prove that the selected officer is the right one. Ambiguous or missing contact
information still needs clarification. The signup flow is not a new verified-email
identity system.

## 12. Login, interface and deployment details

**Changed**

- Improved preparation-versus-booking handling and kept login continuation in
  the same chat. Earlier booking discussion does not block a later read-only query.
- Preserved server approval/authentication controls; memory and apparent agreement
  do not replace them. Stale approval handling avoids silently replaying a write.
- Added the language selector, event cards, source list, localized partial-service
  notice and staff-inbox access to the existing chat interface.
- Startup uses locked Python dependencies, API port **8001** by default, and no
  automatic reload by default. Readiness is distinct from a liveness check.
- Provider selection uses validated settings instead of silently falling back
  from an OpenAI initialization error to Ollama. Explicit timeouts are configured.

**Benefit:** a clearer demo, fewer confusing login interruptions and fewer
accidental approval losses during development.

**Tradeoff:** pending approvals remain process-local, so the API requires one
worker. Restarting it loses paused approvals, although ordinary conversation
history and the persisted brief remain in SQL. Configuration problems now need
fixing rather than being hidden by a silent provider switch.

## 13. Testing, evaluation and GitHub hygiene

**Changed**

- Added a repeatable 210-question suite: 16 continuous chats, including a 30-turn
  memory marathon. One session is reused within each scenario.
- Added a separate 32-turn transfer suite with new topics and counterexamples
  where a comparison or draft should be permitted.
- Added backend regression checks, frontend checks, fault injection, real graph
  middleware tests and corpus consistency/source evaluation scripts.
- Reports are saved during a run. Recovery is marked degraded, not a successful
  answer. Proposed tool writes can be rejected automatically, never approved.
- Added an import-time source fingerprint so the runner can stop before sending
  prompts to an old backend. Test-source labels live under `tests/fixtures`.
- Added operating, review and handoff documentation.
- Fixed the malformed ignore file and excluded secrets, venv, installed frontend
  dependencies, build output, generated transcripts and local diagnostics from
  new project commits. This session's runtime database edits were not published.
  The repository still has seed databases/index files already tracked by the team.

**Benefit:** reproducible evidence, fewer false “passed” claims, and a cleaner
shared repository.

**Tradeoff:** the scenarios are scripted, not an adaptive human evaluator. Unit
tests often mock model boundaries. Live evaluation takes time and uses provider
credits. These checks are not a complete security audit, nor proof that real
payments, email delivery or every authenticated write work.

## Work integrated from your teammates

When `ECC-branch` already had remote work, I merged it rather than force-pushing
over it. Preserved changes include contextual scope checks, login continuation,
model-written follow-up suggestions, clearer link chips, event topic/region
searching, directory region handling, shared Chroma initialization, and cleanup
of obsolete root files. Some areas overlap work on both sides.

The merge was checked with **124 backend tests, 47 frontend tests, a production
build and corpus consistency checks**. This section distinguishes integration
from claiming authorship of the team's work.

## What testing actually shows

| Evidence | Result | What it does not establish |
|---|---|---|
| Last full live run: `20261104T151919823939Z` | All 210 turns across 16 chats answered; no HTTP/recovery failures; one turn used reranker fallback after rate limiting | Not every answer was correct; no proof of every real authenticated write |
| Same live run | Semantic/keyword retrieval healthy; median 16.02 s, maximum 47.5 s | Not a latency measurement of the newest local changes |
| Manual review of that full run | About **8.2/10**, with specific remaining language, reference and wording errors | Subjective assessment, not an automated score or a score for the current untested live build |
| Latest local backend regression run | **151 tests passed** | Mechanical and integration coverage, not proof of natural live answer quality |
| Frontend validation at the merge | **47 tests and production build passed** | A complete Firefox/manual usability audit; latest changes are backend-only |
| New 32-turn transfer suite | Prepared | Has not been live-run yet |

The full live run predates the merge and the newest local changes. Its results
must not be presented as validation of all current code.

## What still needs work

1. Run fresh conversations against the current code, including the transfer suite,
   to check improvement without rewarding memorization of the original prompts.
2. Measure routing, tool and generation times before claiming another speed gain.
3. Keep crawl data, event prices and eligibility information current; code cannot
   manufacture missing official information.
4. Check ambiguous references, recipient selection and fact relationships with
   different wording, languages and longer chats.
5. Consider optional GPU embeddings for teammates only after checking support;
   retain the proven CPU default for the current machine.

No model was fine-tuned. No payment/refund processor, SMTP delivery service,
universal live-data refresh or guarantee of perfect concierge behavior was added.

## Relevant files and commands

Main code: [conversation context](src/concierge/conversation.py),
[agent orchestration](src/concierge/agents/body.py),
[tools](src/concierge/agents/tools.py),
[source context](src/concierge/source_context.py),
[retrieval](src/concierge/rag/src/rag_search.py),
[public-search cache](src/concierge/rag/src/search_cache.py),
[draft checks](src/concierge/drafts.py),
[API](src/concierge/api), and [frontend](ccifj-frontend/src).

Restart the API before testing changed code. From the repository root:

```bash
# Full original suite: should print 16 conversations, 210 question turns.
python scripts/challenge_conversations.py --base-url http://127.0.0.1:8001 --on-approval reject --require-upgrade

# Transfer suite: should print 4 conversations, 32 question turns.
python scripts/challenge_conversations.py --base-url http://127.0.0.1:8001 --suite tests/concierge_transfer_challenges.json --on-approval reject --require-upgrade
```

These commands use the configured provider and reject proposed writes. Generated
reports remain local unless deliberately shared. Current work is on `ECC-branch`;
this README and the latest local improvements need a separate commit/push to
appear on GitHub.
