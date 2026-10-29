# Frontend API contract

All API routes used by this frontend are declared in **src/endpoints.ts**. The client is **src/api.ts**; full TypeScript types are in **src/types.ts**. No dashboard endpoints are called.

| Method | Endpoint | Client function | Purpose |
| --- | --- | --- | --- |
| GET | `/api/v1/status` | `getApiStatus()` | Assistant/model readiness |
| GET | `/api/v1/suggestions` | `getSuggestedQuestions()` | Starter questions from `data/predefined_qa.json` |
| POST | `/api/v1/chat` | `sendChat(request)` | Send a message and receive an answer |
| POST | `/api/v1/auth/register` | `registerAccount()` | Create an account and receive a bearer token |
| POST | `/api/v1/auth/login` | `loginAccount()` | Sign in and receive a bearer token |
| POST | `/api/v1/chat/approval` | `resolveApproval()` | Approve or reject a paused tool call |

## Base URL

Development and preview: requests use relative `/api/...` URLs and Vite forwards them to `CCIFJ_API_TARGET` (default `http://127.0.0.1:8000`). This target can be set in `.env.local`.

Direct/deployed mode: set `VITE_API_BASE_URL=https://your-backend.example` before building. The backend must accept the frontend origin through CORS. Chat requests time out after `VITE_API_TIMEOUT_MS` (default 600000 milliseconds); status requests use 6000 milliseconds. For local Vite proxy mode, `CCIFJ_PROXY_TIMEOUT_MS` controls the Vite-to-backend timeout (default 600000 milliseconds). Restart Vite after changing either value.

## GET /api/v1/status

No body. Expected JSON includes `status: "ok"` when the local API and agent module are loaded. This endpoint does not verify model-provider connectivity; a failed model call is returned by the chat endpoint. Other optional fields include `mode`, `data_freshness` and `version`.

## GET /api/v1/suggestions

No body. Returns every starter question in the agent's `data/predefined_qa.json` catalog. The frontend displays these as clickable prompts.

## POST /api/v1/chat

Headers: `Accept: application/json`, `Content-Type: application/json`.

Request:

```json
{
  "session_id": null,
  "message": "What are the benefits of membership?",
  "locale": "en"
}
```

- `session_id`: null for a new conversation; reuse the response ID for later turns.
- `message`: nonempty text, maximum 4000 characters.
- `locale`: `en`, `fr` or `ja`, taken from the language selector.

Illustrative response structure (not a live answer):

```json
{
  "session_id": "8f6a21fd-2d5b-48b7-9bdf-1ab86cb60e78",
  "message_id": "message-id",
  "answer": "The backend-generated answer appears here.",
  "suggested_questions": ["How can I learn about CCIFJ events?"],
  "sources": [
    {"id":"source-id","title":"Source title","url":"https://www.ccifj.or.jp/","snippet":"Optional excerpt"}
  ],
  "actions": [
    {"type":"external_link","label":"Open source","url":"https://www.ccifj.or.jp/","description":"Optional description"}
  ],
  "warnings": []
}
```

`answer` is required. Missing array fields default to empty arrays. The UI displays up to three `suggested_questions` as clickable follow-up buttons, plus source references and HTTPS external-link actions. Additional response fields supported by the contract are documented in `src/types.ts`. “New conversation” clears the client-side session reference; it does not delete backend history.

## Authentication and approvals

Registration accepts `name`, `email`, and a password of at least 8 characters. Login and registration return a 12-hour bearer token, which the frontend stores locally and sends with chat requests. The backend derives `user_id` from the signed token; it does not accept a user ID supplied in the chat body.

When an authenticated chat pauses for a protected write, the response includes `pending_approvals` with the requested tool and arguments. The frontend shows the review details and approve/reject buttons. The decision endpoint requires the same authenticated account that owns the in-memory conversation. A guest must log in, start a new conversation, and ask again before starting an authenticated write workflow.

## Failures

Non-2xx responses can include `detail`, `message` or `error` strings. The client throws `ApiError`, retains the HTTP status and optional `x-request-id`, and exposes retry in the chat. Invalid responses, network failures and timeouts are surfaced; no mock answer is substituted.

## Example direct request

```bash
curl http://127.0.0.1:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":null,"message":"Hello","locale":"en"}'
```

The backend runs separately. The repository's local test backend is documented in `../backend/README.md`. Live answer quality and response language depend on the agent model configuration.
