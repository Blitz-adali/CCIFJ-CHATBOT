# Actual answer review

Tested the actual `AgentBackend.chat` and production LangGraph with ten prompts.
Eight returned answers from the built-in FAQ or scope-guard paths. Two required
the configured model and failed with a provider connection error. No generated
answers were mocked, and no booking or staff-request approval was submitted.
See [the transcripts](answer-review-transcripts.json).

The answer-quality ratings below are subjective and apply to the answers
actually observed. They do not establish factual accuracy against the current
official website or measure free-form conversation quality.

| Prompt | Rating / 10 | Assessment |
|---|---:|---|
| What is CCIFJ? | 7 | Concise introduction. Helpful orientation, but little tailored guidance. |
| What are the CCIFJ membership fees? | 3 | Does not provide prices. Says there are four tiers and annual dues are in JPY, while the local membership database contains seven categories and includes an EUR-denominated Non-Resident category. This is an internal consistency defect, independent of whether the source snapshot is current. |
| How can I set up a company in Japan through CCIFJ? | 5 | Lists relevant services and a contact, but does not explain a sequence, ask about the company's situation, or link the mentioned inquiry form. |
| How can I book a ticket for a CCIFJ event? | 6 | Gives a next step, but omits the app's login, attendee-detail and explicit-approval requirements. |
| How do I become a CCIFJ member? | 5 | Includes an application link but repeats the incomplete four-tier list. Its form hostname differs from the tool's configured form hostname; verify the official link before changing either. |
| French membership-benefits starter question | 7 | Natural and relevant French, but broad rather than personalized. |
| Japanese membership-benefits starter question | 7 | Understandable, relevant Japanese. Still a general list rather than a guided recommendation. |
| Calculate 937 * 28 | 8 | Correctly stays in scope and redirects toward supported services. This checks boundaries, not concierge helpfulness. |

**Provisional rating for the answered paths: about 6/10.** A complete concierge
rating remains unmeasured: the realistic cosmetics-market-entry prompt and a
non-exact French benefits question could not reach the configured model. Their
connection failures were not scored as bad answers. The earlier 7.5/10 estimate
was based on implementation capabilities, not an observed conversation benchmark.

Highest-value next fixes from this test:

1. Answer price questions from the membership database rather than the static
   four-tier FAQ. Preserve currencies and ask about eligibility when necessary.
2. Align canned booking guidance with the actual login and approval flow.
3. Replace generic service lists with one focused question or a concrete next
   step, while retaining the existing suggested-question buttons.
4. Verify the membership application URL and keep it in one shared source.

To test real free-form and multi-turn conversations from the demo machine,
start the app and run:

```bash
python scripts/review_conversations.py
```

If the API is on another port, pass `--base-url http://127.0.0.1:PORT`.
The script sends ten realistic test turns across membership, market entry,
events, French, Japanese and booking boundaries. It preserves session context
within each scenario, never approves actions, and saves the actual answers to
`docs/live-answer-transcripts.json` for review. Model calls incur the app's normal
provider charges. It stops after two consecutive failures.
