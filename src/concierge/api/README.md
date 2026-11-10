# Running the API

Use Python 3.12+ and the project's existing dependencies (`uv sync`). Set
`CCIFJ_AUTH_SECRET` in your local environment or `.env` to a private random
value of at least 32 bytes. Generate one with:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
uv run uvicorn concierge.main:app --app-dir src --reload
```

Keep the same secret between restarts so issued tokens remain valid. Never
commit it. The server applies the database migrations before accepting requests.
Interactive request schemas are available at `/docs`.

If the frontend uses a different origin, set `CCIFJ_CORS_ORIGINS` to a comma
separated list of exact origins, such as `http://localhost:5173`.

## Authentication

`POST /auth/register` accepts:

```json
{"name":"Demo User","email":"demo@example.com","password":"a-long-demo-password"}
```

`POST /auth/login` accepts `email` and `password`. Both return an `access_token`,
`token_type`, `expires_in` (seconds), and public `user` information. Passwords
use salted scrypt hashes. Access tokens are HS256 JWTs and expire after one hour.
Send `Authorization: Bearer <access_token>` on authenticated requests.

## Sessions and chat

`POST /chat` accepts:

```json
{"session_id":null,"message":"What is CCIFJ?"}
```

Omit `session_id`, or send `null` or `""`, to create a conversation automatically.
The response includes `session_id`, `reply`, `suggested_questions`, and
`pending_approvals`. Send the returned ID unchanged on subsequent requests.

Session IDs are signed opaque strings, not database integers. They expire after
30 days and act as access capabilities for anonymous conversations: keep them
private. User-owned conversations additionally require that user's access token.
When an authenticated user sends a new message in an anonymous conversation,
it is atomically attached to that user. Logging out does not make it public.

`POST /sessions` creates an empty conversation and returns its `session_id`.
No request body is required; an optional bearer token attaches the current user.

`GET /sessions/{session_id}/messages` returns `session_id`, ordered `messages`,
`next_after_id`, and any `pending_approvals`. Each message contains `message_id`,
`role`, `content`, and `created_at` (UTC). Pagination accepts `after_id` and
`limit` (1–100). When `next_after_id` is non-null, pass it as `after_id` to fetch
the next page. Store the session ID in the frontend to restore after refresh.

`GET /health` returns plain text `ok` and never loads the language model.

## Approving bookings and other writes

The existing agent requires explicit approval before a database write. Display
the action requests in `pending_approvals` and send:

```http
POST /sessions/{session_id}/approvals
Content-Type: application/json
Authorization: Bearer <access_token>

{"interrupt_id":"<returned interrupt_id>","decision":"approve"}
```

Use `"reject"` to decline. The response has the same shape as `/chat`; further
approvals may be required. New chat messages return 409 until outstanding
approvals are resolved. Guests retain `user_id=None` in agent state, so the
existing booking tools reject unauthenticated booking attempts. Guests may only
answer `submit_staff_request` approvals (omit `Authorization`; the session token
proves ownership); any other approval for a guest is replaced by a login prompt.

Before an approval is returned, the API adds card fields to the action's `args`
(read-only lookups, and only for the signed-in user's own records):

| Action | Added fields |
|---|---|
| `book_event_ticket` | `event_title`, `event_start`, `event_end`, `event_location`, `price`, `member_price`, `non_member_price`, `currency`, `reg_deadline`, `ticket_count` |
| `book_space` | `start_at`, `end_at`, and when the space is known `space_name`, `space_capacity`, `space_description` (the space table has no location column) |
| `subscribe_newsletter` | `newsletter_name` |
| `submit_staff_request` | none; the arguments already hold the full draft |
| `cancel_event_booking` | `booking_id`, the event fields above, `attendees` |
| `cancel_space_reservation` | `reservation_id`, `space_name`, `title`, `start_at`, `end_at`, `expected_attendees` |
| `change_event_booking` / `change_space_reservation` | `current` and `new`, each with the cancel fields above; unchanged values are copied into `new` |

A change is one action: show it as current → new, never as a cancellation and a
new booking. Approved staff requests are stored in `staff_request` (reference
`SR-<year>-<id>`, recipients in `staff_request_recipient`); no email is sent.

Run one Uvicorn worker. Normal turns restore context from SQL history, including
after a restart, but the existing agent's interrupted approvals are process-local.
Approval state lost after restart returns 409 and is never replayed automatically.
In that case start a new session. Agent failures return 502 (including the session
ID for a failed new conversation); do not automatically retry requests that may
have written bookings. Model initialization failures return 503; configure the
existing model environment variables and service before using `/chat`.


## Frontend and live transports

Every route is also available under `/api/v1`. The React frontend uses that prefix.
`GET /status` provides JSON process health, with `agent_ready` turning true once
the agent and search models finish loading in the background at startup (`/health`
answers immediately either way); `GET /suggestions` reads the starter
question catalog (`data/predefined_qa*.json`) for the welcome screen without
loading any models.
Chat and approval decisions accept `locale` (`en`, `fr`, `ja`), the UI language.
The agent answers in the language of the user's message, whatever it is, and the
API returns that answer unchanged; there is no second model call. Suggested
questions under an answer are written by the model in the user's language and
returned unchanged; model-free answers and fixed texts (approval, login, warnings)
use `locale`.

`POST /chat/stream` accepts the same body and authorization as `/chat`, and emits
SSE `progress`, `delta`, `result`, and `error` events. The result contains the normal chat
response. Errors after the stream starts carry `status` and `message` in the event.
The answer currently arrives whole in `result`; `delta` (public answer text
chunks) is reserved for streaming and is not emitted today. Internal agent/tool
streams are not exposed.

`WS /chat/ws` starts with a JSON authentication frame: `{}` for guests or
`{"access_token":"<token>"}` for accounts. Wait for `{"type":"ready"}`, then send
the normal chat request. Events use `type: progress`, `delta`, `result`, or `error`.
The connection supports sequential turns. Tokens are checked on each turn and
never placed in URLs. Configure `CCIFJ_CORS_ORIGINS` for cross-origin WebSockets;
same-origin connections are permitted. Reverse proxies must forward upgrades.

Both live transports call the same persisted chat operation as HTTP, retaining
session ownership, pending-approval checks, and per-session turn serialization.
Neither automatically retries failed operations. A disconnect cannot undo a write
that already completed. Check history and bookings before retrying.

`GET /sessions/{session_id}/approvals` lists pending approvals, and
`GET /sessions/{session_id}/approvals/{interrupt_id}` retrieves one action's details.
Both apply the usual session authorization. Approval items include `actions`
(normalized from the agent's `action_requests`) with tool names and arguments.

Contract tests use a disposable SQLite database and fake agent:

```bash
PYTHONPATH=src uv run --with httpx python -m unittest discover -s tests -v
```


### Session recovery and action outcomes

`POST /sessions/{session_id}/claim` requires authentication and attaches a guest
conversation to the current user, retaining its history and pending approval thread.
The frontend calls it as soon as a guest logs in, so the chat is kept even if no
new message is sent.

`POST /sessions/{session_id}/discard` takes no body or auth and returns 204. It
deletes a guest conversation (sent by the page's `pagehide` beacon) and does
nothing for an owned session or an invalid token. See `db/README.md` for the
full session lifecycle.
History now includes `turn_status` (`ready`, `processing`, `unknown`),
`last_request_id`, and saved public response metadata on assistant messages.
Optional `request_id` on chat/approval bodies is returned with the completed response
and history. This is correlation data, not an idempotency guarantee.

The API persists processing/uncertain markers using existing message metadata.
A disconnect, failed agent action, or processing marker left after restart makes
the outcome uncertain. New messages and approval decisions return 409 until the
user checks the outcome. `POST /sessions/{session_id}/reconcile` with
`{"checked_outcome":true}` clears the uncertainty marker, preserving pending
approvals, and never executes or replays any action. Running requests cannot be
reconciled. Acknowledgment is the user's confirmation of checking external effects,
not an assertion by the server that a booking was rolled back.

Structured errors preserve `session_id`, `outcome_unknown`, and `safe_to_retry`
where available. SSE and WebSocket errors also carry their HTTP-style `status`.
Source and external-link action cards use actual links from the answer; they do
not claim to expose retrieval provenance that the agent does not return.

The development proxy preserves the public Host for same-origin WebSocket checks.
Cross-origin production sites must still configure `CCIFJ_CORS_ORIGINS`, and HTTPS
sites must use secure WebSockets. Keep one API worker because approval checkpoints
and active-task tracking are process-local.


An optional real-browser smoke test is provided in `tests/api_browser_smoke.py`,
with a disposable API in `tests/api_browser_fixture.py`. It uses a fake agent and
isolated database and drives `ccifj-frontend` through the Vite proxy: chat replies,
starter prompts, cancellation and outcome acknowledgement, registration,
approvals, and rejected logins. Install Playwright (or set `CCIFJ_CHROMIUM` to an
installed Chromium), start the fixture and Vite as documented in the script, then
run it. Production model quality and actual external booking side effects are
outside this smoke test.


### Durable execution and optional presentation

The adapter returns the agent's execution result before language presentation.
The API commits that original reply and the new pending-approval set first, then
performs the optional presentation pass with a separate 30-second timeout. If
presentation fails, the API returns the saved original answer and a localized
warning. Disconnecting during presentation leaves the completed result available
in history; it does not turn a completed booking into an uncertain action or
restore a consumed approval. A presentation failure never replays the graph.

Authorization/history reads are committed before waiting for a session lock or
calling the agent. The post-insert refresh transaction is also released before
presentation, and authenticated WebSockets release their user lookup transaction
before waiting for messages. This avoids reserving pooled SQL connections during
model generation, language formatting, or idle socket time.


## Features ported from the main branch

### Agent

The agent graph (`concierge/agents`) classifies each request as in scope
(CCIFJ events, membership, business support), out of scope, or unclear, and a
final policy node reviews answers before they are returned. Every question,
including the welcome-screen starters, goes through the agent; there are no
pre-written answers. For advisory questions the agent may first ask for the
user's details (industry, country, stage). The events agent can fall back to the
CCIFJ knowledge base (`search_vectordb`) when the live events database has no
match; database facts win when the two disagree.

Each turn still runs on a fresh graph thread rebuilt from SQL history. State
the graph needs across turns is saved in message metadata: extracted file
text (`attachment_context` on user messages) and the out-of-scope counter
(`agent_state` on assistant messages).

### Starter prompts

`GET /suggestions?locale=fr` returns localized `suggested_questions`. The
welcome screen's starter prompts live in the frontend (`WidgetChat.tsx`) and
are sent as ordinary chat messages; there are no guided paths.

Agent answers include `intent.code` with the routed specialist domains.

### Next actions

For signed-in users, every agent turn loads the user's profile (membership
with the company's industry, country and region, upcoming bookings and space
reservations, newsletter status) and passes it to
the agent as `user_context`. Agent answers include up to two `next_actions`,
`{"id", "label", "prompt", "event_id"?}`, with `id` one of `event.view`,
`event.book`, `events.browse`, `space.book`, `membership.join`,
`membership.benefits`, `newsletter.subscribe`, `contact.staff`, or
`auth.login`. Send `prompt` back as an ordinary chat message. `auth.login` opens sign-in instead. `next_actions` is empty
while an approval is pending. Buttons: the actions first, then
`suggested_questions` (written by the model in the user's language), three
buttons in all but always room for two questions. Action prompts name events by
title, never by ID. Only a guest who wants to book gets `auth.login`; after
signing in, the conversation continues in the same session.

### Attachments

Chat requests accept up to four `attachments` per request
(`{"name", "mime_type", "data_base64"}`): PDF, PNG, JPEG, DOCX, or UTF-8 text
(TXT, MD, CSV, JSON, XML, HTML), 5 MB each, PDFs up to 20 pages. Text is
extracted on the server (HTML without scripts and styles); images and scan-only
PDF pages are OCRed with Tesseract (`eng+fra+jpn`). Install the Tesseract executable and
those language packs on the API host; without them, image OCR returns 503.
Extracted text (12,000 characters per file) stays available to the agent for
the rest of the conversation, up to four distinct files, as untrusted
reference data. Responses report `attachment_count`.

### Progress, approvals, and accounts

SSE and WebSocket `progress` events now carry `messages`: localized labels for
the concierge, each specialist agent (including parallel work), and each tool
in use. Heartbeats repeat the current label.

Ticket approvals include `event_title`, `event_start`, and `event_location` in
the action arguments for the review card, and the reply explains the specific
action in the request locale. When a guest's request reaches a protected
action, the API returns a login prompt instead of an approval and leaves the
session usable.

`GET /auth/me` returns the signed-in user.
