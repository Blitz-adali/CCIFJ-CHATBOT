# Review of the first long-conversation run

Reviewed the actual answers in
[the run](challenge-runs/20261104T071634555558Z.json), against backend build
`c31dd840efa8`. These are observed responses, not generated example answers.
The ratings below are subjective; current official facts were not independently
reverified for every answer.

## Execution result

- Planned: 16 conversations, 210 question turns.
- Attempted: 9 conversations, 61 question turns.
- Returned answers: 56. HTTP 502 failures: 5.
- Fully completed: membership, booking, staff drafting and directory (12 turns each).
- Room availability reached 8 answered turns, then failed on the capacity question.
- Seven conversations, including the thirty-turn marathon, never started.
- Median successful-answer latency: 13.26 seconds. Maximum: 38.32 seconds.
- No approval proposals or automatic approvals were recorded.

The runner stopped as designed after recruitment and multilingual both failed.
**Finishing the script did not mean finishing the full suite.** These timings
come from a different workload than the earlier ten-question run, so they do not
establish a controlled before/after speed improvement.

## Answer quality

| Conversation | Assessment | Evidence |
|---|---|---|
| Membership | 8.5/10 | Correct original currencies, seven categories, careful treatment of conflicting €480/€550 figures and founder residence, and correct reconsideration after company age changed from six months to four years. Some replies remain repetitive and longer than necessary. |
| Booking preparation | 8.5/10 | Preserved the selected event across twelve turns, removed Alex when requested, changed Morgan's email correctly, refused an organizer's claimed permission to bypass login, and distinguished preparation from a completed booking. |
| Staff drafting | 7/10 | Preserved cheese/distributor intent, March 2027, the regulatory uncertainty, corrected email and non-commitment to purchase. However, it drafted in English before the user actually selected a language, then eventually switched the surrounding explanation into Japanese despite the earlier explicit request for English explanations. |
| Directory | 7/10 | Distinguished industry labels from verified distribution capability, admitted there was no evidence-based ranking, protected private contact information and ultimately reported that no lead met the requested evidence threshold. The initial brand-heavy lead list was weak for third-party distribution, and the private-number refusal used an off-topic explanation rather than a privacy explanation. |
| Room | Incomplete | Handled the first/second Tuesday correction, changed hours, preserved eight attendees and separated availability from reservation. The December 8, 2026 Paris/Japan conversion is consistent with the local timezone database. The capacity follow-up failed, so the final requirements recap was never tested. |

The completed conversations average roughly **7.8/10 for answer quality**.
That is not a readiness score: five server errors and incomplete coverage remain
material blockers. The thirty-turn memory claim is still untested live.

## Failure diagnosis

Failed prompts:

1. Cosmetics market entry, first turn (11.02 seconds).
2. Budget-filtered Tokyo networking events, first turn (7.36 seconds).
3. Room capacity changed to twenty attendees, turn nine (8.64 seconds).
4. Hiring a first Japanese employee, first turn (7.31 seconds).
5. French recycling-equipment partner search, first turn (9.03 seconds).

The failures cluster around questions that may require knowledge-base retrieval,
but this is a hypothesis, not a confirmed root cause. A direct local hybrid-search
probe returned five source passages successfully; external reranking could not
connect from the review sandbox and fell back to fused ranking as designed.
The saved challenge report has HTTP codes only, not server tracebacks. An actual
API-terminal traceback is needed to distinguish tool errors, model failures,
validation problems or other faults. These failures must not be assumed to have
the same cause as the review sandbox's own network restriction.

## Next work

1. Capture and fix the HTTP 502 root cause using API tracebacks, then rerun the
   failed scenarios first. Do not automatically retry uncertain writes.
2. Separate conversation language from draft language in routing and memory;
   preserve an explicit English-explanation preference across Japanese draft edits.
3. Enforce asking for a draft language before creating the first draft when
   the user has not selected one. A recommendation is not a selection.
4. Reduce repeated eligibility/contact paragraphs after facts are established.
5. Run the seven untouched conversations and the marathon after the failures are
   fixed. Keep their sessions intact and review final recaps against corrected facts.
