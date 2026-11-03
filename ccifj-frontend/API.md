# Frontend API contract

All API routes used by this frontend are declared in **src/endpoints.ts**. The client is **src/api.ts**; TypeScript types are in **src/types.ts**. The backend is the `concierge` API in this repository; its full reference is `../src/concierge/api/README.md`.

| Method | Endpoint | Client function | Purpose |
| --- | --- | --- | --- |
| GET | `/api/v1/status` | `getApiStatus()` | API process readiness |
| GET | `/api/v1/suggestions?locale=` | `getSuggestedQuestions()`, `getStarterPaths()` | Localized starter questions and guided journeys |
| POST | `/api/v1/chat/stream` | `sendChatStream(request)` | Stream progress and receive the answer (used by the widget) |
| POST | `/api/v1/chat` | `sendChat(request)` | Non-streaming chat request |
| POST | `/api/v1/auth/register` | `registerAccount()` | Create an account and receive a bearer token |
| POST | `/api/v1/auth/login` | `loginAccount()` | Sign in and receive a bearer token |
| POST | `/api/v1/sessions/{session_id}/approvals` | `resolveApproval()` | Approve or reject a paused booking |
| POST | `/api/v1/sessions/{session_id}/reconcile` | `reconcileSession()` | Confirm an uncertain outcome was checked |

## Base URL

Development and preview: requests use relative `/api/...` URLs and Vite forwards them to `CCIFJ_API_TARGET` (default `http://127.0.0.1:8000`).

Direct/deployed mode: set `VITE_API_BASE_URL=https://your-backend.example` before building. The backend must accept the frontend origin through `CCIFJ_CORS_ORIGINS`. Chat requests time out after `VITE_API_TIMEOUT_MS` (default 600000 ms); status requests use 6000 ms. `CCIFJ_PROXY_TIMEOUT_MS` controls the Vite-to-backend timeout. The backend itself stops an agent turn after 180 seconds.

## GET /api/v1/suggestions

`locale` is `en`, `fr` or `ja`. Returns `suggested_questions` (the predefined catalog, localized) and `starter_paths` (`[{id, label}]`) shown on the welcome screen. If the request fails, the bundled catalog is used.

## POST /api/v1/chat and /api/v1/chat/stream

Request (unknown fields are rejected):

```json
{
  "session_id": null,
  "message": "What are the benefits of membership?",
  "locale": "en",
  "path_step_id": "events.upcoming",
  "path_event_id": "23",
  "path_context": {}
}
```

- `session_id`: `null` for a new conversation; afterwards send the returned value unchanged. Session IDs are signed opaque strings; keep them private.
- `locale`: `en`, `fr` or `ja`.
- `attachments`: optional `[{name, mime_type, data_base64}]` files (see below).
- `path_step_id`, `path_event_id`, `path_context`: optional guided-journey fields. Selecting a starter path or a `path_actions` button sends its `id`, `event_id` and `data`. When a response has `active_path_step`, the next free-text message continues that step with the returned `path_context` (for example the space-availability date range).

The backend returns the answer as `reply`; `api.ts` normalizes it to `answer` for the UI. Other fields: `mode` (`agent` or `guided_path`), `intent`, `suggested_questions` (in the UI locale; together with `next_actions`, at most three), `sources`, `actions` (HTTPS links), `warnings`, `pending_approvals` (rendered as one card per action: ticket, space, newsletter, staff request, cancel, and change cards showing current → new; guests may approve staff requests only), `path_actions`, `next_actions` (agent-proposed buttons; clicking one sends its `prompt` as a normal message, and `auth.login` opens sign-in), `active_path_step`, `path_context`, `attachment_count`, `request_id`.

The stream emits `progress` (`{"messages": [...]}` localized labels for the concierge, specialist agents and tools), `delta` (reserved; not emitted today, the answer arrives in `result`), `result` (the chat response) and `error` (`status`, `message`, and possibly `outcome_unknown`).

## Authentication and approvals

Registration accepts `name`, `email` and a password of at least 8 characters. Login and registration return a bearer token valid for one hour, which the frontend stores locally and sends with requests. Expired tokens are dropped automatically; if the API rejects a token, the site signs out and opens the login dialog. Signing in or out starts a new conversation.

Booking actions pause for review: the response includes `pending_approvals` with each action's tool name and arguments (ticket bookings include `event_title`, `event_start` and `event_location`). The decision is sent to the session's approvals route as `{"interrupt_id", "decision": "approve" | "reject", "locale"}`. Guests are asked to log in instead of receiving an approval card.

## Failures and uncertain outcomes

Error responses carry `detail` as a string or as an object with `message`, `session_id` and `outcome_unknown`. The client throws `ApiError` with the HTTP status and `outcomeUnknown`.

If a turn fails, times out, or is cancelled, the server cannot be sure whether a booking ran, and it refuses further messages in that session (409). The widget then shows a notice asking the user to check their bookings, with a button that calls the reconcile route. The server never replays the request; the user can also start a new conversation.

## Attachments and edited messages

Attached files (PDF, PNG, JPEG, DOCX, TXT, MD, CSV, JSON, XML, HTML; up to 4 per conversation, 5 MB each) are sent base64-encoded in the `attachments` request field. The server extracts their text (OCR for images and scanned PDF pages) and keeps it as context for the rest of the conversation; the user's message stays exactly as typed, and the bubble shows the file names as chips. If the server has no Tesseract, its 503 message is shown. Editing a sent prompt resubmits a correction in the same session.

## Conversation history in the frontend

The history drawer saves signed-in conversations in browser `localStorage`, scoped by account ID. The backend also stores every conversation and exposes it through `GET /api/v1/sessions/{session_id}/messages`; the drawer reopens a saved conversation with its original session ID, so the agent keeps its context.
