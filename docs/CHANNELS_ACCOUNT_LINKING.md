# WhatsApp, Slack and account linking: what changed

This document describes the changes on the `feat/support-whatsapp-backend` branch.
For the steps to configure and try it, see [CHANNELS_SETUP.md](CHANNELS_SETUP.md).

## Summary

The concierge can now be used from WhatsApp and Slack, not only from the website.
A person writing from a chat app starts as a guest. To do anything that needs a CCIFJ
account (bookings, space reservations, newsletter subscriptions), they link the chat
account to their website account through a one-time link. They log in on the website;
a password is never typed into the chat.

Before this branch the code had no WhatsApp or Slack integration, so the branch adds
both the channels and the linking flow.

## The linking flow

1. **The chat asks for a link.** The person sends `link`, or asks for something that
   needs an account. The bot replies with `{CCIFJ_PUBLIC_URL}/?link=<token>`.
2. **The website receives the token.** It removes the token from the address bar and
   keeps it in memory only. If the person is not logged in, they log in or register
   as usual.
3. **The person confirms.** The website shows which chat account the link belongs to,
   masked (for example `WhatsApp •••• 5678`), and asks for confirmation.
4. **The backend links the accounts.** It attaches the chat account to the logged-in
   user and deletes the token.
5. **The chat is told.** The bot sends a success message to the chat. The same
   conversation continues, now as the account.

Sending `unlink` in the chat disconnects the account. The next message starts a new
guest conversation.

## How a chat conversation works

- Each message goes through the same code as `POST /chat` on the website, so answers,
  guided lookups, history and failure handling are the same.
- The agent's Markdown is converted to what each app displays: `*bold*`, and links as
  `text: url` on WhatsApp or `<url|text>` on Slack. Long answers are split into
  several messages.
- Actions that show an approval card on the website are listed as text. The person
  replies `yes` or `no` (also `oui`/`non`, `はい`/`いいえ`). Any other reply repeats
  the details; nothing runs until they confirm.
- Messages from one sender are answered one at a time, in order.
- A message that the platform delivers twice is answered once.
- The webhook is acknowledged immediately and the answer is sent afterwards, because
  both platforms retry a webhook that is slow.

## Security measures

| Measure | How it is done |
| --- | --- |
| Short-lived token | Valid for `CCIFJ_LINK_TOKEN_SECONDS`, default and maximum 600 (10 minutes). |
| Single use | The token row is deleted when it is used. Of two simultaneous requests, only one succeeds. |
| Not stored in clear | Only the SHA-256 hash of the token is stored. |
| One live link | Asking for a new link cancels the previous one. |
| Not a login | The token lives in its own table and is never accepted as a bearer token. Every linking route also needs the normal website login. |
| Confirmation step | The person sees the masked chat account and a warning before linking. This stops someone from sending their own link to another person to get their phone linked to that person's account. |
| Signed webhooks | WhatsApp requests are checked against `X-Hub-Signature-256`; Slack requests against `X-Slack-Signature`, and are rejected if older than 5 minutes. |
| Off by default | A channel whose variables are not set answers 404. A partly configured channel stops the server at startup with a clear message. |
| Slack workspaces | A Slack user is identified by user ID and team ID together. |

## Differences from the original design note

- **No Redis.** The project does not use Redis, so tokens are stored in a database
  table (`link_token`) with an expiry time.
- **A separate table instead of columns on `user`.** A guest needs a record before
  they have an account, so chat accounts are stored in `channel_identity`. An account
  still has at most one WhatsApp number and one Slack user per workspace; linking a
  second one replaces the first.
- **A confirmation step** was added on the website (see the table above).
- **Unlinking from the website** was added as two API routes, for someone who loses
  their phone. There is no screen for it yet.

## API

All routes are under `/api/v1`. The full description is at the end of
[src/concierge/api/README.md](../src/concierge/api/README.md).

| Route | Purpose |
| --- | --- |
| `GET /webhooks/whatsapp` | Meta's verification handshake. |
| `POST /webhooks/whatsapp` | Incoming WhatsApp messages (Meta WhatsApp Cloud API). |
| `POST /webhooks/slack` | Incoming Slack direct messages (Events API), and Slack's URL check. |
| `POST /auth/link/preview` | `{ "token" }` → `platform`, `account_hint`, `expires_in`. Does not use up the link. |
| `POST /auth/link/complete` | `{ "token", "locale" }` → links the chat account to the signed-in user. |
| `GET /auth/links` | The signed-in user's linked chat accounts. |
| `DELETE /auth/links/{link_id}` | Remove one link. |

The four `/auth/...` routes need `Authorization: Bearer <access_token>`. An expired or
used token returns 410; a chat account already linked to another user returns 409.

## Database

Migration `0010_channel_identities` adds two tables. It runs automatically at startup.

- **`channel_identity`**: one row per chat sender. Holds `platform` (`whatsapp` or
  `slack`), `platform_user_id` (phone number or Slack user ID), `team_id` (Slack
  workspace, empty for WhatsApp), `reply_to` (Slack DM channel), `user_id` (empty
  until linked), `session_id` (the current conversation) and `linked_at`.
  `(platform, team_id, platform_user_id)` is unique.
- **`link_token`**: `token_hash`, `identity_id`, `expires_at`, `created_at`.

## Files

New:

| File | Content |
| --- | --- |
| `src/concierge/account_linking.py` | Tokens, linking and unlinking. |
| `src/concierge/channels.py` | Signature checks, webhook parsing, sending, text rendering, the conversation handler. |
| `src/concierge/api/linking.py` | The `/auth/link/...` and `/auth/links` routes. |
| `src/concierge/api/webhooks.py` | The `/webhooks/...` routes. |
| `src/concierge/db/relationaldb/migrations/versions/0010_channel_identities.py` | The migration. |
| `ccifj-frontend/src/LinkAccount.tsx` | The linking dialog on the website. |
| `tests/test_account_linking.py` | 14 tests with a fake agent and no network calls. |

Changed:

| File | Change |
| --- | --- |
| `src/concierge/config.py` | New settings for the public URL, link lifetime, WhatsApp and Slack. |
| `src/concierge/db/relationaldb/models.py` | `ChannelIdentity` and `LinkToken`. |
| `src/concierge/main.py` | Registers the two new routers under `/api/v1`. |
| `src/concierge/api/README.md` | New section on channels and linking. |
| `.env.example` | The new variables. |
| `ccifj-frontend/src/App.tsx` | Reads `?link=` and shows the dialog. |
| `ccifj-frontend/src/api.ts`, `endpoints.ts`, `styles.css` | API helpers, routes and dialog styles. |

## Testing status

- `uv run python -m unittest discover -s tests -p 'test_*.py'`: 250 tests pass.
- `npm run build` and `npm test` in `ccifj-frontend`: build succeeds, 48 tests pass.
- The migration was applied to a copy of `system.db`.
- **Not yet tried against real WhatsApp or Slack.** The tests replace the network
  calls and the agent.

## Known limits

- Only text messages are handled. An image or voice note gets a "text only" reply.
- The language of the bot's fixed messages (link invitation, yes/no prompt) is guessed
  from each message, so a bare `yes` gets English prompts. The agent's own answers
  follow the conversation language as on the website.
- After a turn whose outcome is unknown, the bot tells the person to check their
  bookings and then unblocks the conversation. The website waits for an explicit
  acknowledgement instead.
- One Slack bot token is configured, so one Slack workspace is served, although the
  data model already separates workspaces.
- Event cards, sources and suggested questions are not shown in the chat; only the
  answer text is.
- WhatsApp only allows free-form replies within 24 hours of the person's last message.
  The bot only ever replies to a message, so this is normally not reached.
- The server must still run with one worker, as before.
