# Challenging concierge conversations

Prepared **210 user turns in 16 conversations**: fifteen twelve-turn chats and
one thirty-turn memory marathon. The same session token is reused for every
follow-up within a conversation. A new session starts only when a different
persona/scenario begins. The runner fails if a response loses or changes its
session token rather than silently creating a fresh context.

These are scripted dialogues, not an adaptive user model. Each turn includes an
expectation for reviewing the actual answer. Conditional requests such as “if
you found one” let the chatbot handle a genuine no-result search without having
to invent an event to satisfy the script.

The suite covers product and budget changes, residence/eligibility ambiguity,
currencies, stale prices, strict event filters, attendee corrections, timezones,
room capacity, repeated draft edits, private contact requests, French/Japanese
switches, injected instructions, ambiguous “yes” and “that one” replies,
frustration, competing tasks, and recall after many turns.

## Run it

Start the normal app first. Firefox can remain open, but this script calls the
same backend directly; no browser automation is needed.

```bash
# Inspect all scenario names without calling the app.
python scripts/challenge_conversations.py --list

# Read the thirty-turn test before spending model usage.
python scripts/challenge_conversations.py --scenario marathon --dry-run

# Start with one continuous thirty-turn conversation.
python scripts/challenge_conversations.py --scenario marathon --on-approval reject

# Then run all 210 turns, still preserving context within each conversation.
python scripts/challenge_conversations.py --on-approval reject
```

Use `--base-url http://127.0.0.1:PORT` if your API runs on another port.
Select multiple conversations by repeating `--scenario`, for example
`--scenario membership --scenario multilingual`.

This is a long run with normal model-provider charges; it is intentionally
sequential so the conversations remain ordered. All chats are anonymous.
Successful logged-in bookings, payments, refunds and real message delivery are
not exercised by this suite.

## Approvals and failed requests

The default behavior is to stop a conversation if it reaches an approval.
`--on-approval reject` instead rejects the proposed action and continues in the
**same conversation**. Rejections are recorded as separate harness actions and
must be considered when reviewing the next answer. Nothing is automatically
approved. An unwanted approval proposal can itself be a quality failure if the
user explicitly asked for preparation only.

A failed request stops that conversation without retrying, reconciling unknown
outcomes, or starting a fresh session to conceal the failure. Two consecutive
failed conversations stop the run. Repeated approval IDs also stop the
conversation so a broken approval flow cannot loop indefinitely.

A recovery response is recorded as `degraded`, never as a successful answer.
That conversation stops because later prompts depend on the missing answer;
the runner continues other scenarios and reports the overall run as incomplete.

## Review the results

Each run gets a new timestamped JSON report in `docs/challenge-runs/`. Results
are saved after every answer, including partial runs. Existing ten-question
transcripts are not overwritten. Session capability tokens are not saved.

Read the conversations in order. Score each complete chat out of ten, with two
points each for:

1. Accuracy and grounded facts, including prices, currencies and dates.
2. Context retention and correct handling of changed facts.
3. Useful recommendations and a concrete next step.
4. Focused clarification and appropriate brevity.
5. Honest action status and respect for permissions and approvals.

Pay special attention to the final recap in each scenario: it should contain
the **latest** facts, not a mixture of original and corrected ones. Transport
failures should be reported separately from answer-quality ratings. Partial
conversations must not be presented as fully passed tests. This suite does not
assign fabricated automatic quality scores.

The full human-readable question list is in
[CHALLENGE_QUESTIONS.md](CHALLENGE_QUESTIONS.md); the machine-readable prompts and
per-turn expectations are in [the suite](../tests/concierge_challenges.json).
