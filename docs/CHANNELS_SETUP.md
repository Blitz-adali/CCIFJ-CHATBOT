# WhatsApp, Slack and account linking: setup before trying it

What to configure before the WhatsApp and Slack channels and the account-linking flow
can be tried. For what was built and why, see
[CHANNELS_ACCOUNT_LINKING.md](CHANNELS_ACCOUNT_LINKING.md).

You can set up WhatsApp, Slack, or both. A channel you leave unconfigured stays off
(its webhook answers 404) and the website works as before.

## 1. What you need

- The project running locally as usual (`uv`, Node, a filled-in `.env`).
- A public HTTPS address for the API. Meta and Slack must be able to reach your
  webhook, so `localhost` is not enough. For local testing use a tunnel such as
  ngrok or Cloudflare Tunnel pointing at the API port.
- A website address that opens on the device where the link will be tapped. If you
  test WhatsApp on a phone, `http://localhost:5173` will not open there; use a tunnel
  or a deployed frontend.
- For WhatsApp: a Meta developer app with the WhatsApp product.
- For Slack: permission to create and install an app in a Slack workspace.

## 2. `.env` settings

Add these to `.env` (they are listed at the end of `.env.example`).

### Needed for either channel

| Variable | Value |
| --- | --- |
| `CCIFJ_PUBLIC_URL` | Address of the website, without a trailing slash, e.g. `https://concierge.example.org`. Linking links point here. If empty, the first entry of `CCIFJ_CORS_ORIGINS` is used. |
| `CCIFJ_CORS_ORIGINS` | Must include the website address, as it already does for the website to call the API. |
| `CCIFJ_LINK_TOKEN_SECONDS` | Optional. Lifetime of a link in seconds. Default 600, which is also the maximum. |

### WhatsApp (set all four, or none)

| Variable | Where to find it |
| --- | --- |
| `WHATSAPP_ACCESS_TOKEN` | Meta app → WhatsApp → API Setup. The temporary token expires after about a day; use a system-user token for anything longer. |
| `WHATSAPP_PHONE_NUMBER_ID` | Meta app → WhatsApp → API Setup → "Phone number ID". This is the ID, not the phone number. |
| `WHATSAPP_APP_SECRET` | Meta app → App settings → Basic → "App secret". Used to check webhook signatures. |
| `WHATSAPP_VERIFY_TOKEN` | Any private value you choose. You enter the same value in Meta's webhook settings (step 4). |
| `WHATSAPP_API_VERSION` | Optional. Graph API version, default `v23.0`. Set it to the version shown in your Meta app if sending fails with a version error. |

### Slack (set both, or none)

| Variable | Where to find it |
| --- | --- |
| `SLACK_BOT_TOKEN` | Slack app → OAuth & Permissions → "Bot User OAuth Token" (starts with `xoxb-`). |
| `SLACK_SIGNING_SECRET` | Slack app → Basic Information → App Credentials → "Signing Secret". |

If a channel is only partly filled in, or `CCIFJ_PUBLIC_URL` is missing while a channel
is configured, the server refuses to start and prints what to fix. Check with:

```bash
uv run python -m concierge.config
```

## 3. Start the API and the website

```bash
uv run uvicorn concierge.main:app --app-dir src --reload
```

Keep one worker, as before. The new database tables are created automatically at
startup (migration `0010`); there is nothing to run by hand.

Start or deploy the frontend as usual (`npm run dev` in `ccifj-frontend`), reachable at
the address you put in `CCIFJ_PUBLIC_URL`.

Then expose the API port over HTTPS (for example `ngrok http 8000`) and note the
public address. Below it is written as `https://YOUR-API`.

## 4. Connect WhatsApp

In the Meta developer dashboard, in your app:

1. Go to WhatsApp → Configuration → Webhook and choose Edit.
2. Callback URL: `https://YOUR-API/api/v1/webhooks/whatsapp`
3. Verify token: the value of `WHATSAPP_VERIFY_TOKEN`.
4. Save. Meta calls the URL once; the API must already be running with the WhatsApp
   variables set, or verification fails.
5. Under Webhook fields, subscribe to `messages`.
6. If you use Meta's test number, add your own phone under API Setup → "To" so it is
   allowed to receive messages.

## 5. Connect Slack

In the Slack app settings (api.slack.com/apps):

1. OAuth & Permissions → Bot Token Scopes: add `chat:write` and `im:history`.
2. App Home → enable the Messages tab and allow users to send messages to the app.
3. Event Subscriptions → turn on, and set the Request URL to
   `https://YOUR-API/api/v1/webhooks/slack`. Slack checks the URL at once; the API
   must already be running with the Slack variables set.
4. Under "Subscribe to bot events", add `message.im`.
5. Install (or reinstall) the app to the workspace, then copy the bot token into
   `.env` if it changed and restart the API.

## 6. Try it

Use an account that exists on the website (register one first if needed).

1. Send `hi` to the WhatsApp number, or as a direct message to the Slack app.
   Expected: a normal concierge answer, as a guest.
2. Send `link`.
   Expected: a message with a link that is valid for 10 minutes.
3. Open the link. Log in on the website if asked.
   Expected: a dialog naming the chat account, masked (for example `•••• 5678`).
4. Choose "Link account".
   Expected: the dialog confirms, and the chat receives a success message.
5. Ask for something that needs an account, for example a space reservation.
   Expected: the details as text, ending with "Reply YES to confirm or NO to cancel".
6. Reply `yes` or `no`.
   Expected: the action runs or is cancelled.
7. Send `unlink`.
   Expected: the chat is disconnected; the next message is answered as a guest.

Also worth checking:

- Open the same link a second time: it is refused as expired or already used.
- As a guest, ask to book something without sending `link` first: the answer ends
  with a linking link.

## 7. If something does not work

| What you see | Likely cause |
| --- | --- |
| Server stops at startup with "partly configured" | Some, but not all, of a channel's variables are set. |
| Meta or Slack cannot verify the URL | The API is not reachable at that address, the path is wrong (it must include `/api/v1`), or the channel's variables are not set (the route answers 404). For Meta, the verify token differs. |
| Webhook returns 403 | Wrong `WHATSAPP_APP_SECRET` or `SLACK_SIGNING_SECRET`. For Slack, also a server clock that is off by more than 5 minutes. |
| The message arrives but no answer is sent | Look in the API log for "Could not deliver a whatsapp/slack message". Usual causes: expired WhatsApp access token, wrong phone number ID, an unsupported `WHATSAPP_API_VERSION`, a recipient not on the test number's allowed list, or a missing `chat:write` scope on Slack. |
| WhatsApp messages are ignored | The webhook is for a different phone number ID than `WHATSAPP_PHONE_NUMBER_ID`, or the `messages` field is not subscribed. |
| Slack messages are ignored | The message was not a direct message to the app, or `message.im` is not subscribed. |
| The link opens the wrong site or does not open | `CCIFJ_PUBLIC_URL` is wrong or not reachable from that device. |
| The dialog says the link could not be used | The link is older than 10 minutes, was already used, or a newer link was requested. Send `link` again. |
| The website cannot reach the API after opening the link | The website address is missing from `CCIFJ_CORS_ORIGINS`, or `VITE_API_BASE_URL` in the frontend does not point at the API. |

## 8. Run the automated tests

These need no WhatsApp or Slack account and make no network calls.

```bash
uv run python -m unittest tests.test_account_linking
```

The first run can take a few minutes while the environment is prepared.
