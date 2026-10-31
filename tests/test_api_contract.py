"""Contract checks with a fake agent and disposable database, no model calls."""
import os
import tempfile
import unittest

os.environ.setdefault("CCIFJ_AUTH_SECRET", "test-secret-for-contract-checks-only-123456789")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.api import auth, chat, health, sessions
from concierge.db.relationaldb.session import get_session


class FakeAgent:
    async def chat(self, history, user_id, thread_id):
        if history[-1].content == "fail":
            raise RuntimeError("Simulated uncertain action")
        if history[-1].content == "book":
            return {"reply": "Review this booking", "pending_approvals": [{
                "interrupt_id": "approval-1", "action_requests": [{
                    "name": "book_space", "args": {"space_id": 1}, "description": "Reserve space",
                }],
            }]}
        return {"reply": "Hello", "suggested_questions": ["What is CCIFJ?"]}

    async def resume(self, thread_id, interrupt_id, decision):
        return {"reply": f"Decision: {decision}"}


class ApiContractTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.directory.name}/test.db", pool_size=1, max_overflow=0, pool_timeout=1)
        engine = self.engine

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
        for router in (auth.router, chat.router, health.router, sessions.router):
            app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_session] = database
        self.backend = FakeAgent()
        app.dependency_overrides[chat.agent_backend] = lambda: self.backend
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.directory.cleanup()

    def test_health_catalog_and_auth(self):
        self.assertEqual(self.client.get("/api/v1/health").text, "ok")
        self.assertEqual(self.client.get("/api/v1/status").json()["status"], "ok")
        self.assertIn("What is CCIFJ?", self.client.get("/api/v1/suggestions").json()["suggested_questions"])
        account = {"name": "Test", "email": "test@example.com", "password": "password123"}
        created = self.client.post("/api/v1/auth/register", json=account)
        self.assertEqual(created.status_code, 201)
        login = self.client.post("/api/v1/auth/login", json={k: account[k] for k in ("email", "password")})
        self.assertEqual(login.status_code, 200)
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        result = self.client.post("/api/v1/chat", json={"message": "hi", "locale": "fr"}, headers=headers)
        self.assertEqual(result.status_code, 200)
        token = result.json()["session_id"]
        self.assertEqual(self.client.get(f"/api/v1/sessions/{token}/messages").status_code, 404)
        self.assertEqual(len(self.client.get(f"/api/v1/sessions/{token}/messages", headers=headers).json()["messages"]), 2)
        with self.client.websocket_connect("/api/v1/chat/ws") as socket:
            socket.send_json({"access_token": login.json()["access_token"]})
            self.assertEqual(socket.receive_json()["type"], "ready")
            socket.send_json({"message": "hi again", "session_id": token})
            self.assertEqual(socket.receive_json()["type"], "progress")
            self.assertEqual(socket.receive_json()["session_id"], token)

    def test_approval_details_and_resume(self):
        result = self.client.post("/api/v1/chat", json={"message": "book", "locale": "en"}).json()
        self.assertEqual(result["pending_approvals"][0]["actions"][0]["args"], {"space_id": 1})
        token = result["session_id"]
        path = f"/api/v1/sessions/{token}/approvals"
        self.assertEqual(self.client.get(path + "/approval-1").status_code, 200)
        self.assertEqual(self.client.post("/api/v1/chat", json={"session_id": token, "message": "hi"}).status_code, 409)
        self.assertEqual(self.client.post(path, json={"interrupt_id": "approval-1", "decision": "reject"}).json()["reply"], "Decision: reject")
        self.assertEqual(self.client.get(path).json()["pending_approvals"], [])
        self.assertEqual(self.client.post(path, json={"interrupt_id": "approval-1", "decision": "approve"}).status_code, 409)

    def test_sse(self):
        result = self.client.post("/api/v1/chat/stream", json={"message": "hi", "locale": "ja"})
        self.assertIn("text/event-stream", result.headers["content-type"])
        self.assertIn("event: progress", result.text)
        self.assertIn("event: result", result.text)
        self.assertIn('"reply": "Hello"', result.text)
        failure = self.client.post("/api/v1/chat/stream", json={"message": "hi", "session_id": "bad"})
        self.assertIn("event: error", failure.text)
        self.assertIn('"status": 404', failure.text)

    def test_socket_and_history(self):
        with self.client.websocket_connect("/api/v1/chat/ws") as socket:
            socket.send_json({})
            self.assertEqual(socket.receive_json()["type"], "ready")
            socket.send_json({"message": "hi", "locale": "en"})
            self.assertEqual(socket.receive_json()["type"], "progress")
            result = socket.receive_json()
            self.assertEqual(result["reply"], "Hello")
            socket.send_json({"message": "again", "session_id": result["session_id"]})
            self.assertEqual(socket.receive_json()["type"], "progress")
            self.assertEqual(socket.receive_json()["session_id"], result["session_id"])
        history = self.client.get(f'/api/v1/sessions/{result["session_id"]}/messages').json()
        self.assertEqual(len(history["messages"]), 4)
        with self.client.websocket_connect("/api/v1/chat/ws") as socket:
            socket.send_json({"access_token": "invalid"})
            self.assertEqual(socket.receive_json()["status"], 401)


    def test_failed_turn_requires_explicit_reconciliation_and_keeps_request_id(self):
        token = self.client.post("/api/v1/sessions").json()["session_id"]
        body = {"session_id": token, "message": "fail", "request_id": "request-1"}
        failure = self.client.post("/api/v1/chat", json=body)
        self.assertEqual(failure.status_code, 502)
        self.assertTrue(failure.json()["detail"]["outcome_unknown"])
        self.assertEqual(failure.json()["detail"]["session_id"], token)
        history = self.client.get(f"/api/v1/sessions/{token}/messages").json()
        self.assertEqual(history["turn_status"], "unknown")
        self.assertEqual(self.client.post("/api/v1/chat", json={**body, "message": "hi"}).status_code, 409)
        path = f"/api/v1/sessions/{token}/reconcile"
        self.assertEqual(self.client.post(path, json={"checked_outcome": False}).status_code, 422)
        self.assertEqual(self.client.post(path, json={"checked_outcome": True}).status_code, 200)
        success = self.client.post("/api/v1/chat", json={**body, "message": "hi", "request_id": "request-2"})
        self.assertEqual(success.status_code, 200)
        history = self.client.get(f"/api/v1/sessions/{token}/messages").json()
        self.assertEqual(history["turn_status"], "ready")
        self.assertEqual(history["last_request_id"], "request-2")
        self.assertEqual(history["messages"][-1]["response"]["reply"], "Hello")

    def test_claim_retains_pending_guest_approval_and_enforces_ownership(self):
        token = self.client.post("/api/v1/chat", json={"message": "book"}).json()["session_id"]
        account = self.client.post("/api/v1/auth/register", json={"name": "Member", "email": "member@example.com", "password": "password123"}).json()
        headers = {"Authorization": "Bearer " + account["access_token"]}
        path = f"/api/v1/sessions/{token}"
        self.assertEqual(self.client.post(path + "/claim").status_code, 401)
        self.assertEqual(self.client.post(path + "/claim", headers=headers).status_code, 200)
        self.assertEqual(self.client.get(path + "/messages").status_code, 404)
        self.assertEqual(len(self.client.get(path + "/messages", headers=headers).json()["pending_approvals"]), 1)
        result = self.client.post(path + "/approvals", json={"interrupt_id": "approval-1", "decision": "reject", "locale": "ja", "request_id": "decision-1"}, headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["request_id"], "decision-1")

    def test_approval_failure_cannot_be_replayed_without_reconciliation(self):
        async def fail(*args):
            raise RuntimeError("Simulated write uncertainty")
        self.backend.resume = fail
        token = self.client.post("/api/v1/chat", json={"message": "book"}).json()["session_id"]
        path = f"/api/v1/sessions/{token}"
        decision = {"interrupt_id": "approval-1", "decision": "approve"}
        self.assertEqual(self.client.post(path + "/approvals", json=decision).status_code, 502)
        self.assertEqual(self.client.get(path + "/messages").json()["turn_status"], "unknown")
        self.assertEqual(self.client.post(path + "/approvals", json=decision).status_code, 409)

    def test_websocket_public_host_origin_and_cross_origin_rejection(self):
        from starlette.websockets import WebSocketDisconnect
        with self.client.websocket_connect("/api/v1/chat/ws", headers={"host": "127.0.0.1:5173", "origin": "http://127.0.0.1:5173"}) as socket:
            socket.send_json({})
            self.assertEqual(socket.receive_json()["type"], "ready")
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/api/v1/chat/ws", headers={"origin": "https://untrusted.example"}):
                pass

    def test_disconnecting_socket_records_uncertainty_without_claiming_a_rollback(self):
        import asyncio
        from threading import Event
        started = Event()
        async def waiting(*args):
            started.set()
            await asyncio.Event().wait()
        self.backend.chat = waiting
        token = self.client.post("/api/v1/sessions").json()["session_id"]
        with self.client.websocket_connect("/api/v1/chat/ws") as socket:
            socket.send_json({})
            self.assertEqual(socket.receive_json()["type"], "ready")
            socket.send_json({"session_id": token, "message": "long request"})
            self.assertEqual(socket.receive_json()["type"], "progress")
            self.assertTrue(started.wait(3))
        history = self.client.get(f"/api/v1/sessions/{token}/messages").json()
        self.assertEqual(history["turn_status"], "unknown")
        self.assertEqual(self.client.post("/api/v1/chat", json={"session_id": token, "message": "retry"}).status_code, 409)

    def test_presentation_failure_keeps_completed_approval_and_allows_next_turn(self):
        async def failed_presentation(result):
            raise RuntimeError("Presentation model offline")
        self.backend.present_result = failed_presentation
        token = self.client.post("/api/v1/chat", json={"message": "book"}).json()["session_id"]
        result = self.client.post(f"/api/v1/sessions/{token}/approvals", json={
            "interrupt_id": "approval-1", "decision": "approve", "request_id": "approved-once",
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["reply"], "Decision: approve")
        self.assertTrue(result.json()["warnings"])
        history = self.client.get(f"/api/v1/sessions/{token}/messages").json()
        self.assertEqual(history["turn_status"], "ready")
        self.assertEqual(history["pending_approvals"], [])
        self.assertEqual(history["last_request_id"], "approved-once")
        self.assertTrue(history["messages"][-1]["response"]["warnings"])
        self.assertEqual(self.client.post("/api/v1/chat", json={"message": "continue", "session_id": token}).status_code, 200)

    def test_agent_and_presentation_do_not_hold_database_connections(self):
        original_chat, original_resume = self.backend.chat, self.backend.resume
        presentation_connections = []
        async def checked_chat(*args):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return await original_chat(*args)
        async def checked_resume(*args):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return await original_resume(*args)
        async def checked_presentation(result):
            presentation_connections.append(self.engine.pool.checkedout())
            return result
        self.backend.chat, self.backend.resume, self.backend.present_result = checked_chat, checked_resume, checked_presentation
        token = self.client.post("/api/v1/chat", json={"message": "book"}).json()["session_id"]
        result = self.client.post(f"/api/v1/sessions/{token}/approvals", json={"interrupt_id": "approval-1", "decision": "reject"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["warnings"], [])
        self.assertEqual(presentation_connections, [0, 0])

    def test_idle_authenticated_websockets_release_the_single_connection(self):
        from contextlib import ExitStack
        account = self.client.post("/api/v1/auth/register", json={"name": "Idle", "email": "idle@example.com", "password": "x" * 200}).json()
        with ExitStack() as stack:
            for _ in range(20):
                socket = stack.enter_context(self.client.websocket_connect("/api/v1/chat/ws"))
                socket.send_json({"access_token": account["access_token"]})
                self.assertEqual(socket.receive_json()["type"], "ready")
            self.assertEqual(self.engine.pool.checkedout(), 0)
            response = self.client.post("/api/v1/sessions", headers={"Authorization": "Bearer " + account["access_token"]})
            self.assertEqual(response.status_code, 201)
        login = self.client.post("/api/v1/auth/login", json={"email": "idle@example.com", "password": "x" * 200})
        self.assertEqual(login.status_code, 200)

    def test_disconnect_during_presentation_preserves_completed_raw_answer(self):
        import asyncio
        from threading import Event
        started = Event()
        async def waiting(result):
            started.set()
            await asyncio.Event().wait()
        self.backend.present_result = waiting
        token = self.client.post("/api/v1/sessions").json()["session_id"]
        with self.client.websocket_connect("/api/v1/chat/ws") as socket:
            socket.send_json({})
            self.assertEqual(socket.receive_json()["type"], "ready")
            socket.send_json({"session_id": token, "message": "hello", "request_id": "durable"})
            self.assertEqual(socket.receive_json()["type"], "progress")
            self.assertTrue(started.wait(3))
        history = self.client.get(f"/api/v1/sessions/{token}/messages").json()
        self.assertEqual(history["turn_status"], "ready")
        self.assertEqual(history["last_request_id"], "durable")
        self.assertEqual(history["messages"][-1]["content"], "Hello")

    def test_sse_transmits_answer_deltas_and_error_context(self):
        from concierge.api.agent_backend import response_events
        async def stream_reply(*args):
            response_events.get().put_nowait({"type": "delta", "text": "Bon"})
            response_events.get().put_nowait({"type": "delta", "text": "jour"})
            return {"reply": "Bonjour"}
        self.backend.chat = stream_reply
        result = self.client.post("/api/v1/chat/stream", json={"message": "hello", "locale": "fr"})
        self.assertIn('event: delta\ndata: {"text": "Bon"}', result.text)
        self.assertIn('"reply": "Bonjour"', result.text)
        self.assertLess(result.text.index("event: delta"), result.text.index("event: result"))


class PresentationTest(unittest.IsolatedAsyncioTestCase):
    async def test_api_language_renderer_streams_selected_language_without_tools(self):
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch
        from concierge.api.agent_backend import AgentBackend, response_locale, response_events
        import asyncio
        class Model:
            async def astream(self, messages):
                self.messages = messages
                for text in ("Bonjour ", "[CCIFJ](https://www.ccifj.or.jp/en/)"):
                    yield SimpleNamespace(content=text)
        model = Model()
        module = ModuleType("concierge.agents.model")
        module.llm = model
        queue = asyncio.Queue()
        locale = response_locale.set("fr")
        events = response_events.set(queue)
        try:
            with patch.dict(sys.modules, {"concierge.agents.model": module}):
                backend = AgentBackend.__new__(AgentBackend)
                result = await backend.present_result({"reply": "Hello", "pending_approvals": [], "suggested_questions": []})
            self.assertIn("French", model.messages[0]["content"])
            self.assertEqual(result["sources"], [{"title": "CCIFJ", "url": "https://www.ccifj.or.jp/en/"}])
            self.assertEqual(result["actions"][0]["type"], "external_link")
            self.assertEqual(queue.get_nowait()["text"], "Bonjour ")
        finally:
            response_locale.reset(locale)
            response_events.reset(events)


if __name__ == "__main__":
    unittest.main()
