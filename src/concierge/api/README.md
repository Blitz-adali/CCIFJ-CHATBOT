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
existing booking tools reject unauthenticated booking attempts.

Run one Uvicorn worker. Normal turns restore context from SQL history, including
after a restart, but the existing agent's interrupted approvals are process-local.
Approval state lost after restart returns 409 and is never replayed automatically.
In that case start a new session. Agent failures return 502 (including the session
ID for a failed new conversation); do not automatically retry requests that may
have written bookings. Model initialization failures return 503; configure the
existing model environment variables and service before using `/chat`.
