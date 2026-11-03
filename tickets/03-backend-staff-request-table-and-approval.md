# 03 · Staff requests: DB table, persistence, and approval support (guests included)

**Area:** Backend / DB / API · **Priority:** High. **This blocks guests from submitting staff requests** (logged-in users already reach the approval step and get the mock reference). · **Related tasks:** 4, 8

## Context (already done in the agent)
The general agent has a new tool, `submit_staff_request` (`src/concierge/agents/tools.py`), which needs approve/reject (`_write_approval`, `langchain_agents.py`). The agent writes an email (to a known CCIFJ contact) or a ticket (for the CCIFJ team when no contact exists) together with the user. After the user agrees and approves, the tool runs.
For now it returns a **mock** reference `MOCK-YYYYMMDDHHMMSS` and stores nothing.

Tool arguments:
| arg | type |
|---|---|
| `request_type` | `"staff_email"` (known contact) \| `"team_ticket"` (no contact) |
| `category` | `events, membership, business_support, recruitment_and_hr, media_and_news, booking_change_or_refund, other` |
| `subject` | str |
| `message` | str |
| `sender_name` | str |
| `sender_email` | str |
| `recipient_emails` | list[str] \| None. Every contact in the draft's To line; at least one for `staff_email`, empty for `team_ticket` |

## 1. API fixes
1. **Nice to have: a specific approval message.** `approval_prompt` (`src/concierge/api/agent_backend.py:101-109`) falls back to the generic `APPROVAL_PROMPTS[None]` text for `submit_staff_request`. That works (tested logged in). For a clearer message, add a `"submit_staff_request"` entry (en/fr/ja), for example: "Please review your message to CCIFJ staff below. Confirming will store it for the team; no email is sent automatically." Also add it to the tuple of names checked in `approval_prompt`.
2. **Blocking: guest approvals are dropped.** In `format_result`, the `if guest:` branch replaces every guest approval with `LOGIN_REQUIRED`. Staff requests must work **without login**. Only drop guest approvals for the tools that need an account (`book_event_ticket`, `book_space`, `subscribe_newsletter`), and keep `submit_staff_request` approvals for guests.
3. Check that `POST /sessions/{id}/approve` works for a guest session. `CurrentUser` is optional, so confirm that `authorize_session` lets the guest who owns the session approve.

## 2. Table `staff_request` (new Alembic migration `0003_staff_requests`)
| column | type | notes |
|---|---|---|
| `request_id` | int PK | |
| `reference` | str(32) unique | e.g. `SR-2026-000123`, shown to the user |
| `request_type` | enum `staff_email`, `team_ticket` | |
| `category` | str(50) | values as in the tool's `Literal` |
| `subject` | str(255) | |
| `message` | text | the exact agreed text |
| `sender_name` | str(100) | |
| `sender_email` | str(255) | |
| `user_id` | int FK → `user.user_id`, nullable | null for guests |
| `session_id` | FK → chat session, nullable | so staff can read the conversation |
| `status` | enum `new`, `in_review`, `closed` | default `new` |
| `created_at` / `updated_at` | naive UTC | same pattern as the other tables |

A request can go to **several** contacts (for example, Funabashi and Barges for membership), so store the recipients in a child table, **`staff_request_recipient`**:

| column | type | notes |
|---|---|---|
| `request_id` | int FK → `staff_request.request_id` | part of the PK |
| `email` | str(255) | part of the PK; stored even when there is no `contact_us` match (contacts are hard-coded in `get_ccifj_contact`) |
| `contact_id` | int FK → `contact_us.contact_id`, nullable | set when the email matches a `contact_us` row |

A `team_ticket` has no recipient rows.

Add `crud.add_staff_request(...)` and `crud.get_staff_request(reference)`.
**Only store it. No email sending** (product decision).

## 3. Replace the mock in the tool
In `submit_staff_request`, replace the `MOCK-` block with `crud.add_staff_request`, then verify the row exists, following the same pattern as `subscribe_newsletter` (create, then re-read; return an "may have been saved, do not retry" message on failure). Fill in `user_id` from `runtime.state['user_id']` (add a `runtime: ToolRuntime = None` parameter, as `book_event_ticket` does). Keep the return text starting with `Staff request stored. Reference: ...`, because `agents/body.py` (`_STAFF_REQUEST_PATTERN`) relies on it.

## Acceptance
- A guest and a logged-in user can both write a request, see the approval card, approve it, and get a real reference.
- Rejecting it stores nothing, and the agent says nothing was stored.
- The row has the exact agreed subject and message, plus sender and session, with one recipient row per contact in the draft's To line.
