# Database

SQLite at `relationaldb/system.db`. Tables are defined in `relationaldb/models.py`,
queries live in `relationaldb/crud.py`, and schema changes go through Alembic
migrations (see "Schema updates" in the root README).

## Chat session lifecycle

A row in `session` is one conversation. `session.user_id` is `NULL` for a guest
and set to the user's id once someone who is logged in owns it. The frontend
never sees the integer id: it holds a signed session token that expires
`session_token_seconds` (30 days by default) after the session is created.

| Event | What happens in the DB |
| --- | --- |
| First message (guest) | `session` row created with `user_id = NULL`. |
| First message (logged in) | `session` row created with `user_id` set. |
| Guest logs in | The frontend calls `POST /sessions/{token}/claim` right away, without waiting for a new message, so `user_id` is set immediately. If the guest's first message is still running, it claims as soon as the reply arrives. |
| Logged-in user sends a message to a guest session | `user_id` is set too (the chat endpoint also claims it). |
| Guest closes the tab or reloads | The page sends `POST /sessions/{token}/discard` (a `sendBeacon` on `pagehide`). The session, its `message` rows, and the link from any `staff_request` are removed. |
| Guest session left behind | Deleted by the hourly sweep once it is older than the token lifetime, since nobody can open it again. |
| Logged-in user closes the app | Nothing is deleted. |

### Rules the delete keeps

- Only sessions with `user_id IS NULL` are ever deleted. `discard` returns
  `204` and does nothing for an owned session, an unknown session, or a bad or
  expired token.
- `discard` waits for the session's running turn (the same per-session lock as
  chat and claim), so a reply still being generated is never cut off by the
  delete.
- `staff_request` rows a guest sent are kept for staff; their `session_id` is
  set to `NULL`.
- Foreign keys are enforced (`PRAGMA foreign_keys=ON`) and nothing cascades, so
  `crud._delete_guest_sessions` removes the dependent rows explicitly in one
  transaction.

### Where it lives

| Piece | Location |
| --- | --- |
| Delete one guest session / sweep old ones | `crud.delete_guest_session`, `crud.delete_guest_sessions_created_before` |
| Claim a guest session | `crud.claim_chat_session` |
| `discard` and `claim` endpoints | `api/sessions.py` |
| Hourly sweep | `sweep_expired_guest_sessions` in `main.py` (also runs at startup) |
| Frontend claim on login, discard on close | `ccifj-frontend/src/WidgetChat.tsx`, `ccifj-frontend/src/api.ts` |

### Known limits

- "Closing" is per tab: each tab has its own guest chat.
- A page the browser keeps in its back/forward cache (for example, after
  navigating to another site) is not discarded, so the chat still works if the
  user comes back. The sweep removes it later.
- Starting a "New conversation" as a guest does not delete the previous guest
  session; the sweep removes it later.
- The agent's checkpointer state is process-local memory and is not touched by
  these deletes.

## Changelog

### 2026-11-03: guest session cleanup and claim on login

- Added `POST /sessions/{token}/discard` and the frontend `pagehide` beacon
  that calls it, so guest sessions are deleted when the guest closes the app.
- Added an hourly sweep of guest sessions older than the session token
  lifetime, as a backstop when the beacon never arrives.
- Logging in no longer clears the open chat: the frontend keeps it, claims it
  immediately with `POST /sessions/{token}/claim`, and saves it to the user's
  history.
- No schema change and no migration.
