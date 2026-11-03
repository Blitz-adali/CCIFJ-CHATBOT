# 04 · Approval card must show exactly what the user is confirming

**Area:** Frontend (+ small backend enrichment) · **Priority:** Medium · **Related task:** 6

## Current state
`ccifj-frontend/src/ApprovalPanel.tsx`:
- `book_event_ticket` has a detailed layout (title, date, location, event ID, attendees). The backend already fills in title, date and location from the DB (`enrich_ticket_approvals` in `src/concierge/api/agent_backend.py:112`). Check it end to end after pulling `origin/main`. If the card still shows only the ID, the enrichment is failing (see the exception logged there).
- `book_space`, `subscribe_newsletter` and the new `submit_staff_request` only show a collapsed `<details>` with raw JSON, plus a generic sentence and two buttons.

## Fix
### Backend: enrich more actions (same place as `enrich_ticket_approvals`; rename it to `enrich_approvals`)
- `book_event_ticket`: also add `price` / `currency` and `reg_deadline`.
- `book_space`: add the space name, capacity and location from the `space` table, and format `start_date` / `end_date`.
- `subscribe_newsletter`: add a readable newsletter name for `newsletter_type`.
- `submit_staff_request`: no DB lookup is needed; the args already contain everything.

### Frontend: one detail component per action
- **Ticket booking:** event title, date and time, venue, price ("free" or the amount), attendees (name, email, phone), and the number of tickets.
- **Space booking:** space name, location, start → end dates, number of days.
- **Newsletter:** the email and the newsletter name.
- **Staff request:** type badge ("Email to CCIFJ contact" or "Ticket for CCIFJ team"), To (recipient or "CCIFJ team"), From (name and email), Subject, and the full message in a readable box (`white-space: pre-wrap`). Reuse the email card from ticket 05.
- Action-specific button labels, for example "Confirm booking", "Confirm reservation", "Subscribe", "Store my message", and the matching "Don't …" labels (en/fr/ja).
- Keep the raw-JSON `<details>` only as a fallback for unknown actions.

## Acceptance
For each of the 4 actions, the card shows every value that will be saved, in the UI language, before the user approves.
