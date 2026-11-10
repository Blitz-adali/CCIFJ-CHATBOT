"""WhatsApp/Slack webhooks and account linking, with a fake agent and no network calls."""
import dataclasses
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from datetime import timedelta
from unittest import mock

os.environ.setdefault("CCIFJ_AUTH_SECRET", "test-secret-for-contract-checks-only-123456789")
os.environ.setdefault("model_type", "qwen")
os.environ.setdefault("model_name", "test-model")
os.environ.setdefault("router_model_name", "test-model")
os.environ.setdefault("COHERE_API_KEY", "test-cohere-key")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge import account_linking, channels
from concierge.api import auth, chat, linking, sessions, webhooks
from concierge.api.agent_backend import LOGIN_REQUIRED, LOGIN_WARNING
from concierge.config import get_settings
from concierge.db.relationaldb.models import ChannelIdentity, ChatSession, LinkToken, utcnow
from concierge.db.relationaldb.session import get_session

PHONE = "819012345678"
SETTINGS = dataclasses.replace(
    get_settings(), public_url="https://concierge.example", link_token_seconds=600,
    whatsapp_access_token="wa-token", whatsapp_phone_number_id="555", whatsapp_app_secret="wa-secret",
    whatsapp_verify_token="wa-verify", slack_bot_token="xoxb-test", slack_signing_secret="slack-secret",
)


class FakeAgent:
    def __init__(self):
        self.user_ids = []

    async def chat(self, history, user_id, thread_id):
        self.user_ids.append(user_id)
        if history[-1].content == "book":
            if user_id is None:
                # What the real backend returns when a guest asks for a booking.
                return {"reply": LOGIN_REQUIRED["en"], "warnings": [LOGIN_WARNING["en"]]}
            return {"reply": "Review this booking", "pending_approvals": [{
                "interrupt_id": "approval-1", "action_requests": [{
                    "name": "book_space", "args": {"space_name": "Salon", "space_id": 1},
                }],
            }]}
        return {"reply": "**Hello** from [CCIFJ](https://www.ccifj.or.jp)"}

    async def resume(self, thread_id, interrupt_id, decision):
        return {"reply": f"Decision: {decision}"}


class AccountLinkingTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = engine = create_async_engine(f"sqlite+aiosqlite:///{self.directory.name}/test.db")

        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def lifespan(app):
            async with engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)
            yield
            await engine.dispose()

        async def database():
            async with AsyncSession(engine, expire_on_commit=False) as session:
                yield session

        app = FastAPI(lifespan=lifespan)
        for router in (auth.router, chat.router, sessions.router, linking.router, webhooks.router):
            app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_session] = database

        self.sent = []
        async def record(platform, to, text):
            self.sent.append((platform, to, text))
            return True

        self.agent = FakeAgent()
        self.patches = [
            mock.patch.object(channels, "get_settings", lambda: SETTINGS),
            mock.patch.object(account_linking, "get_settings", lambda: SETTINGS),
            mock.patch.object(webhooks, "get_settings", lambda: SETTINGS),
            mock.patch.object(channels, "AsyncSessionLocal", lambda: AsyncSession(engine, expire_on_commit=False)),
            mock.patch.object(channels, "send", record),
            mock.patch.object(channels, "_backend", lambda: self.agent),
        ]
        for patch in self.patches:
            patch.start()
        channels._seen.clear()
        self.counter = 0
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        for patch in self.patches:
            patch.stop()
        self.directory.cleanup()

    # ---------- helpers ----------

    def whatsapp(self, text, sender=PHONE, message_id=None, signature=None, kind="text"):
        self.counter += 1
        message = {"from": sender, "id": message_id or f"wamid.{self.counter}", "type": kind}
        if kind == "text":
            message["text"] = {"body": text}
        body = json.dumps({"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "555"}, "messages": [message],
        }}]}]}).encode()
        signature = signature or "sha256=" + hmac.new(b"wa-secret", body, hashlib.sha256).hexdigest()
        return self.client.post("/api/v1/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": signature})

    def slack(self, payload, timestamp=None, secret=b"slack-secret"):
        body = json.dumps(payload).encode()
        timestamp = str(timestamp or int(time.time()))
        signature = "v0=" + hmac.new(secret, b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
        return self.client.post("/api/v1/webhooks/slack", content=body, headers={
            "X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature,
        })

    def account(self, email="member@example.com", name="Aiko"):
        response = self.client.post("/api/v1/auth/register", json={"name": name, "email": email, "password": "password123"})
        return {"Authorization": "Bearer " + response.json()["access_token"]}, response.json()["user"]["user_id"]

    def link_token(self):
        """Ask the bot for a link and read the token out of the message it sent."""
        self.whatsapp("link")
        text = self.sent[-1][2]
        self.assertIn("https://concierge.example/?link=", text)
        return text.split("?link=")[1].split()[0]

    def database(self, operation):
        async def run():
            async with AsyncSession(self.engine, expire_on_commit=False) as session:
                return await operation(session)
        return self.client.portal.call(run)

    def identities(self):
        async def read(session):
            return list((await session.exec(select(ChannelIdentity))).all())
        return self.database(read)

    # ---------- webhooks ----------

    def test_whatsapp_webhook_requires_a_valid_signature(self):
        self.assertEqual(self.whatsapp("hi", signature="sha256=" + "0" * 64).status_code, 403)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.identities(), [])

    def test_whatsapp_verification_handshake(self):
        url = "/api/v1/webhooks/whatsapp"
        ok = self.client.get(url, params={"hub.mode": "subscribe", "hub.verify_token": "wa-verify", "hub.challenge": "1234"})
        self.assertEqual((ok.status_code, ok.text), (200, "1234"))
        bad = self.client.get(url, params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1234"})
        self.assertEqual(bad.status_code, 403)

    def test_unconfigured_channel_is_not_served(self):
        with mock.patch.object(webhooks, "get_settings", lambda: dataclasses.replace(SETTINGS, whatsapp_access_token="", slack_bot_token="")):
            self.assertEqual(self.whatsapp("hi").status_code, 404)
            self.assertEqual(self.slack({"type": "url_verification", "challenge": "x"}).status_code, 404)

    def test_guest_message_is_answered_as_channel_text(self):
        self.assertEqual(self.whatsapp("hi").status_code, 200)
        self.assertEqual(self.sent, [("whatsapp", PHONE, "*Hello* from CCIFJ: https://www.ccifj.or.jp")])
        self.assertEqual(self.agent.user_ids, [None])

    def test_redelivered_message_is_answered_once(self):
        self.whatsapp("hi", message_id="wamid.same")
        self.whatsapp("hi", message_id="wamid.same")
        self.assertEqual(len(self.sent), 1)

    def test_non_text_message_gets_a_hint(self):
        self.whatsapp("", kind="image")
        self.assertIn("text messages", self.sent[-1][2])
        self.assertEqual(self.agent.user_ids, [])

    def test_slack_signature_challenge_and_direct_message(self):
        challenge = {"type": "url_verification", "challenge": "abc"}
        self.assertEqual(self.slack(challenge).json(), {"challenge": "abc"})
        self.assertEqual(self.slack(challenge, secret=b"wrong").status_code, 403)
        self.assertEqual(self.slack(challenge, timestamp=int(time.time()) - 3600).status_code, 403)
        event = {"type": "message", "channel_type": "im", "user": "U1", "channel": "D1", "text": "hi"}
        self.slack({"type": "event_callback", "team_id": "T1", "event_id": "Ev1", "event": event})
        self.assertEqual(self.sent, [("slack", "D1", "*Hello* from <https://www.ccifj.or.jp|CCIFJ>")])
        # The bot's own messages and other workspaces' users are separate or ignored.
        self.slack({"type": "event_callback", "team_id": "T1", "event_id": "Ev2", "event": {**event, "bot_id": "B1"}})
        self.assertEqual(len(self.sent), 1)
        self.slack({"type": "event_callback", "team_id": "T2", "event_id": "Ev3", "event": event})
        self.assertEqual({(i.team_id, i.platform_user_id) for i in self.identities()}, {("T1", "U1"), ("T2", "U1")})

    # ---------- linking ----------

    def test_booking_as_guest_sends_a_link_and_linking_unlocks_it(self):
        self.whatsapp("book")
        self.assertIn("https://concierge.example/?link=", self.sent[-1][2])
        token = self.sent[-1][2].split("?link=")[1].split()[0]
        headers, user_id = self.account()

        preview = self.client.post("/api/v1/auth/link/preview", json={"token": token}, headers=headers)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual((preview.json()["platform"], preview.json()["account_hint"]), ("whatsapp", "•••• 5678"))
        self.assertNotIn(PHONE, preview.text)

        done = self.client.post("/api/v1/auth/link/complete", json={"token": token}, headers=headers)
        self.assertEqual(done.status_code, 200)
        self.assertEqual(self.identities()[0].user_id, user_id)
        # Step 4: the chat is told that linking worked.
        self.assertEqual(self.sent[-1][:2], ("whatsapp", PHONE))
        self.assertIn("Aiko", self.sent[-1][2])

        # The same conversation continues, now as the account, and approvals work by YES/NO.
        self.whatsapp("book")
        self.assertEqual(self.agent.user_ids[-1], user_id)
        self.assertIn("Space name: Salon", self.sent[-1][2])
        self.assertNotIn("Space id", self.sent[-1][2])
        self.assertIn("Reply YES", self.sent[-1][2])
        self.whatsapp("maybe")
        self.assertIn("Reply YES", self.sent[-1][2])
        self.whatsapp("Yes")
        self.assertEqual(self.sent[-1][2], "Decision: approve")

        async def conversations(session):
            return list((await session.exec(select(ChatSession))).all())
        rows = self.database(conversations)
        self.assertEqual([row.user_id for row in rows], [user_id])

    def test_link_needs_a_website_login(self):
        token = self.link_token()
        for path in ("preview", "complete"):
            self.assertEqual(self.client.post(f"/api/v1/auth/link/{path}", json={"token": token}).status_code, 401)
        self.assertIsNone(self.identities()[0].user_id)
        # The link token is not a login.
        self.assertEqual(self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code, 401)

    def test_link_is_single_use_and_only_its_hash_is_stored(self):
        token = self.link_token()
        async def stored(session):
            return [row.token_hash for row in (await session.exec(select(LinkToken))).all()]
        self.assertEqual(self.database(stored), [hashlib.sha256(token.encode()).hexdigest()])
        headers, _ = self.account()
        self.assertEqual(self.client.post("/api/v1/auth/link/complete", json={"token": token}, headers=headers).status_code, 200)
        self.assertEqual(self.database(stored), [])
        other, _ = self.account("other@example.com", "Other")
        again = self.client.post("/api/v1/auth/link/complete", json={"token": token}, headers=other)
        self.assertEqual((again.status_code, again.json()["detail"]["error_code"]), (410, "invalid_token"))

    def test_expired_link_is_rejected(self):
        token = self.link_token()
        async def expire(session):
            row = (await session.exec(select(LinkToken))).one()
            row.expires_at = utcnow() - timedelta(seconds=1)
            session.add(row)
            await session.commit()
        self.database(expire)
        headers, _ = self.account()
        for path in ("preview", "complete"):
            self.assertEqual(self.client.post(f"/api/v1/auth/link/{path}", json={"token": token}, headers=headers).status_code, 410)
        self.assertIsNone(self.identities()[0].user_id)

    def test_a_new_link_cancels_the_previous_one(self):
        first, second = self.link_token(), self.link_token()
        headers, user_id = self.account()
        self.assertEqual(self.client.post("/api/v1/auth/link/complete", json={"token": first}, headers=headers).status_code, 410)
        self.assertEqual(self.client.post("/api/v1/auth/link/complete", json={"token": second}, headers=headers).status_code, 200)
        self.assertEqual(self.identities()[0].user_id, user_id)

    def test_unlink_from_chat_and_from_the_website(self):
        headers, user_id = self.account()
        self.client.post("/api/v1/auth/link/complete", json={"token": self.link_token()}, headers=headers)
        self.whatsapp("link")
        self.assertIn("already linked", self.sent[-1][2])
        self.whatsapp("unlink")
        self.assertIsNone(self.identities()[0].user_id)
        self.whatsapp("hi")
        self.assertIsNone(self.agent.user_ids[-1])

        self.client.post("/api/v1/auth/link/complete", json={"token": self.link_token()}, headers=headers)
        items = self.client.get("/api/v1/auth/links", headers=headers).json()["items"]
        self.assertEqual([(item["platform"], item["account_hint"]) for item in items], [("whatsapp", "•••• 5678")])
        other, _ = self.account("other@example.com", "Other")
        url = f"/api/v1/auth/links/{items[0]['link_id']}"
        self.assertEqual(self.client.delete(url, headers=other).status_code, 404)
        self.assertEqual(self.client.delete(url, headers=headers).status_code, 204)
        self.assertIsNone(self.identities()[0].user_id)

    def test_one_whatsapp_number_per_account(self):
        headers, user_id = self.account()
        self.client.post("/api/v1/auth/link/complete", json={"token": self.link_token()}, headers=headers)
        self.whatsapp("link", sender="33612345678")
        token = self.sent[-1][2].split("?link=")[1].split()[0]
        self.client.post("/api/v1/auth/link/complete", json={"token": token}, headers=headers)
        linked = {identity.platform_user_id: identity.user_id for identity in self.identities()}
        self.assertEqual(linked, {PHONE: None, "33612345678": user_id})


if __name__ == "__main__":
    unittest.main()
