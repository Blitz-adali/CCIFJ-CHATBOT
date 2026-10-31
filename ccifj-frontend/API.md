# Concierge API contract

The FastAPI app is `concierge.main:app` in `../src`. All frontend routes use
`/api/v1`; the original unprefixed backend routes remain available.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/health` | Plain text `ok`, no model initialization |
| GET | `/status` | JSON `{ "status": "ok", "mode": "live" }`, process health only |
| GET | `/suggestions` | `{ "suggested_questions": string[] }` from the agent's predefined Q&A catalog |
| POST | `/auth/register` | `{ name, email, password }`, returns bearer token and user |
| POST | `/auth/login` | `{ email, password }`, returns bearer token and user |
| POST | `/chat` | JSON chat turn |
| POST | `/chat/stream` | SSE chat turn |
| WS | `/chat/ws` | WebSocket chat turns |
| POST | `/sessions` | Create a session before sending a turn |
| POST | `/sessions/{session_id}/claim` | Attach the current guest conversation to the signed-in user |
| POST | `/sessions/{session_id}/reconcile` | Acknowledge a checked uncertain outcome: `{ checked_outcome: true }` |
| GET | `/sessions/{session_id}/messages` | Conversation history and pending approvals |
| GET | `/sessions/{session_id}/approvals` | Pending approval list |
| GET | `/sessions/{session_id}/approvals/{interrupt_id}` | Pending approval details |
| POST | `/sessions/{session_id}/approvals` | `{ interrupt_id, decision: "approve" \| "reject", locale, request_id? }` |

Prefix every route above with `/api/v1` when calling from the frontend.
HTTP authentication uses `Authorization: Bearer <access_token>`.

Chat requests contain `{ session_id: string | null, message: string, locale: "en" | "fr" | "ja" }`.
The API adapter renders the completed answer in the selected locale using the existing
configured text model without tools. This adds one presentation model pass; the agent
graph and its tools are unchanged. Optional `request_id` values correlate completed
turns with history after a reconnect. Responses contain `{ session_id, reply, suggested_questions,
pending_approvals, sources, actions, request_id }`. The client maps `reply` to the UI's `answer`. Approvals expose
`interrupt_id` and `actions`, each containing `name`, `args`, and any agent description.
The panel displays the exact tool name and arguments for review.

SSE emits `event: progress` with `{ message }`, `event: delta` with `{ text }` for
public answer chunks, `event: result` with the full chat response, or `event: error`
with `{ message, status, session_id?, outcome_unknown?, safe_to_retry? }`. Internal
agent reasoning and tool execution are not streamed. Source and external-action
arrays expose links present in the answer, rather than fabricated retrieval citations.

For WebSockets, the first client frame is `{ "access_token": "..." }` for a signed-in
user or `{}` for a guest. The server responds `{ "type": "ready" }`. Send the chat
request next. Responses use `type: "progress" | "delta" | "result" | "error"`, with the same
payloads as SSE. Multiple sequential turns can share a connection. Tokens never
appear in the URL. The widget opens a connection per turn so account changes apply
on the next request. It uses WebSockets by default; set `VITE_CHAT_TRANSPORT=sse`
to use SSE, or `json` for ordinary HTTP. A WebSocket connection failure before the
chat request is sent can fall back to SSE; a missing SSE route (HTTP 404/405 before
execution) can fall back to JSON. Once dispatched, a failed turn is never replayed
automatically.

For separate frontend/backend origins, configure `CCIFJ_CORS_ORIGINS` on the backend
and `VITE_API_BASE_URL` before building. WebSocket origins follow the same allowlist;
same-origin requests are also permitted. The Vite proxy preserves Host and forwards
WebSocket upgrades. Production proxies should preserve the public Host too.
Production reverse proxies must do the same and disable buffering for SSE.

Run one backend worker: approval checkpoint state is held in process memory.
Stopping a stream is not a guarantee that an external action was undone. Inspect
history and any bookings before retrying an interrupted operation.


## Restoration, authentication, and uncertain outcomes

The frontend persists only the session pointer and request correlation data, under
separate guest/account keys, and loads paginated history from the server. History
includes `turn_status` (`ready`, `processing`, `unknown`), `last_request_id`, pending
approvals, and saved response metadata for source/action/follow-up rendering.

Guest login claims the current conversation, including any pending approvals.
Logout and account switching invalidate outstanding requests and hide the previous
account's messages. Guest approval decisions are allowed; individual backend tools
still enforce their own authentication requirements. One current approval set is
rendered, and the composer stays blocked while approvals are outstanding.

Authentication keeps an in-memory credential when storage is unavailable, checks
JWT expiry, and expires the UI state on a timer. HTTP/SSE/WebSocket 401 responses
clear only the credential used for that request and prompt login. The backend's
`expires_in` is used for opaque tokens (default one hour), not a twelve-hour client
assumption. Re-login restores the saved account conversation.

Stop waiting aborts the client transport, without claiming a server rollback.
History is refreshed after a failed or stopped turn. Processing requests are polled;
uncertain outcomes block new chat and approval requests. An old answer with a
different request ID does not count as completion. After checking bookings or other
side effects, the user can explicitly acknowledge the outcome with `/reconcile`,
which does not replay any action. Starting a new conversation is also available.
Unknown writes never receive a generic retry button. API health is checked again
every 15 seconds and recovers automatically when the backend returns.


Execution results are saved before the optional language-presentation pass. If that
pass fails or times out, the original answer and completed approval state remain
available, with a warning explaining that language formatting was unavailable.
The frontend displays response warnings alongside the answer.

Safe pre-execution failures retain the question, error, and explicit retry control.
A failed initial session creation also permits retry: no chat/action was dispatched,
although the interrupted session request may have created an unused empty session.
Known uncertain action outcomes retain the existing reconciliation requirement.

Authentication changes synchronize across same-origin tabs through storage events.
The newest storage snapshot wins over delayed events; receiving tabs update their
account UI and invalidate in-flight conversation work without writing the event
back to storage. In-memory authentication still works when storage is unavailable;
cross-tab synchronization requires functioning shared browser storage. The password
input supports the backend's 1,024-character limit for login and registration.
