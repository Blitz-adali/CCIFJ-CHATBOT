# 05 · Show email and ticket drafts as a distinct card in answers

**Area:** Frontend · **Priority:** Medium · **Related task:** 5

## Context (already done in the agent)
When the agent writes a message for CCIFJ staff, it now always outputs the draft as a fenced block, with the language tag `email` (to a known contact) or `ticket` (for the CCIFJ team):

````
```email
To: Elena SUZUKI <e.suzuki@ccifj.or.jp>
From: Jane Doe <jane@example.com>
Subject: Late registration for the Tokyo Summit

Dear Ms Suzuki,
...
```
````

The collector is told to copy this block exactly. The agent also now uses `###` headings, bullet lists and **bold** key facts more consistently.

## Fix
In `ccifj-frontend/src/AnswerText.tsx`, when a fenced block's language is `email` or `ticket`, render a card instead of a code block:
- A header badge: "Email draft" or "Ticket for CCIFJ team" (en/fr/ja).
- The `To` / `From` / `Subject` lines as a labelled header grid.
- The body in a readable font (not monospace), with `white-space: pre-wrap` and `dir="auto"`.
- A "Copy" button that copies the subject and body.
- Keep the card working while the text streams in (an unclosed fence shows the card in a "drafting…" state).

Optional: export this card so the staff-request approval card (ticket 04) can reuse it.

## Acceptance
- Drafts are clearly separate from the rest of the answer in all three locales.
- Other code fences still render as before.
