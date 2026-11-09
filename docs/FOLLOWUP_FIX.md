# Follow-up and concierge behavior fixes — 5 November 2026

Later recommendation curation and the expanded 38-turn suite are documented in [RECOMMENDATION_QUALITY_FIX.md](RECOMMENDATION_QUALITY_FIX.md). The validation counts below describe the earlier follow-up fix.

The router previously left questions empty for specialist tasks, assuming the collector would generate them. The single-specialist optimization skips collector synthesis, so these ordinary answers had no recommended prompts. The router now supplies contextual candidate questions and carries them to the single-specialist result without another model call. Plain-text collector recovery also preserves these candidates. Successful structured synthesis still decides its own suggestions after seeing the findings.

The intake rules now permit one relevant question for a genuinely new decision, while avoiding questions already answered, skipped, or declined. Suggested questions may deepen the current service instead of always switching services. Strict-format requests and completed conversations may correctly have no suggestions. These are model instructions, not a guarantee that every generated answer will follow them.

Model-free membership fee/joining answers now have translated follow-ups. Guided event details offer the missing read-only booking-information step. API responses suppress questions while approval is pending, including guest approval attempts that require login. The widget hides suggestion controls across the conversation during a pending approval. Suggestions do not authorize any database write.

Validation: 158 backend tests pass, including router-to-single-specialist flow in English/French/Japanese, collector fallback, model-free paths, approval suppression, and membership suggestions surviving HTTP and saved history. The 47 frontend tests pass and the production build passes. Tests use disposable databases and mocked model boundaries. In this sandbox the backend test runner needs a temporary event-loop heartbeat; application code is unchanged for that workaround.

Live model quality is not yet verified: the API at localhost:8001 was unreachable here. A separate suite contains 3 continuous conversations / 24 prompts, including skipped intake, changing needs, language changes, and strict-format counterexamples. It requires manual review of both the answers and suggested questions; it does not automatically click generated buttons or assign a quality score.

After restarting the API and refreshing/rebuilding the frontend, run:

```bash
cd /home/blitz/CCIFJ-Concierge
python scripts/challenge_conversations.py --base-url http://127.0.0.1:8001 --suite tests/concierge_followup_challenges.json --on-approval reject --require-upgrade
```

ECC self-evaluation: accuracy 4/5 (tested data flow; live generation pending); completeness 4/5 (identified missing-question paths addressed; live review remains); clarity 4/5 (explicit boundary between tests and model behavior); actionability 5/5 (local changes and a runnable continuous-chat suite); conciseness 4/5 (shared existing pipeline, no extra model call). Average 4.2/5, an implementation self-check rather than a chatbot rating. Highest-impact remaining check: run the continuous chats and inspect whether each suggestion helps the current decision without repeating advice. Would the user agree? Likely only after that live review.
