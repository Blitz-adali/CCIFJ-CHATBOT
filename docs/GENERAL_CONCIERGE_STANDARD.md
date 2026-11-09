# General concierge decision quality

The shared advice standard now asks the router, specialists, and collector to identify the visitor’s goal, completed preparation, constraints, and next unresolved decision. It replaces the business worker’s fixed example sequence with stage-sensitive guidance. The service map remains a capability reference, not a required sales journey.

For advice, the model should choose one provisional next step, explain why it fits, and name something practical to prepare, verify, compare, or decide. It should not hand the visitor several equally weighted conditional branches unless they request a comparison. Where simple preparation is sufficient, it should not push a paid engagement or handoff. Missing optional details are not an intake checklist; one question is justified only if it materially changes the recommendation. Direct facts, explicit comparisons, drafting, transactions, and strict formats retain their own requirements.

Three new bounded conversation-brief fields preserve current stage, completed work, and existing relationships. They use the existing exact-quote validation: model updates must quote the latest user statement, corrections replace the field, and ungrounded updates are discarded. These user-reported facts are neither verified outcomes nor authorization. Existing protection for tool-confirmed prices, availability, ownership, approvals, and writes remains in force. This gives later specialists access to progress even when the original turn leaves the router’s recent-message window.

The API also removes suggested questions that exactly repeat earlier user questions or each other, after normalization of case, whitespace, Unicode width and trailing punctuation. This is a deterministic guard, not semantic duplicate detection; paraphrases still depend on the model.

No industry-specific answer template was added. Existing deterministic FAQ paths and narrowly scoped conversation controls remain. Generated recommendation quality still depends on the model, available source evidence, and extraction of the relevant user facts. These changes do not guarantee error-free answers.

Validation: 170 backend tests pass. New tests cover progress propagation through real router/context code across business support, recruitment, membership and events; correction and quote validation; untrusted relationship claims; and duplicate suggestions. Model calls are mocked at their boundaries, so these checks do not establish a live quality score. Python compilation and diff whitespace checks also pass. No frontend code changed in this update; its preceding verification was 48 passing tests and a successful production build.

A separate transfer suite adds eight continuous conversations / 48 prompts across advanced business support, employers, job seekers, training, existing members, events, rooms, and direct facts/drafting. It includes counterexamples where comparison, a direct answer, or no follow-up is appropriate. Suite syntax and runner loading were checked. It has not been run against the live model because localhost:8001 is unreachable here. The existing 210-turn suite and earlier 38-turn suite are unchanged.

After restarting the API, test new conversations:

```bash
cd /home/blitz/CCIFJ-Concierge
python scripts/challenge_conversations.py --base-url http://127.0.0.1:8001 --suite tests/concierge_service_quality_challenges.json --on-approval reject --require-upgrade
```

Review whether recommendations advance the visitor’s actual decision, avoid restarting completed work, preserve constraints, and provide a practical next step without unnecessary intake. The runner records continuous chats and rejects approvals; it does not assign a fabricated quality score.

ECC implementation self-evaluation: accuracy 4/5 (passing execution tests, live semantics pending); completeness 4/5 (shared behavior and cross-service coverage, no universal-error-free claim); clarity 4/5 (explicit model vs deterministic boundaries); actionability 5/5 (local implementation plus runnable suite); conciseness 4/5 (reuses the current router and memory, no extra normal-turn model call). Average 4.2/5, not a chatbot rating. Highest-impact remaining check is live cross-service review, especially advanced users who should skip initial work. Would the user agree? The design addresses general causes; actual generated conversations must confirm the benefit.
