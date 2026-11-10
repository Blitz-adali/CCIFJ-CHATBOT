"""Contract checks with a fake agent and disposable database, no model calls."""
import os
import tempfile
import unittest

os.environ.setdefault("CCIFJ_AUTH_SECRET", "test-secret-for-contract-checks-only-123456789")
os.environ.setdefault("model_type", "qwen")
os.environ.setdefault("model_name", "test-model")
os.environ.setdefault("router_model_name", "test-model")
os.environ.setdefault("COHERE_API_KEY", "test-cohere-key")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.api import auth, chat, health, sessions
from concierge.db.relationaldb.session import get_session


class FakeAgent:
    async def chat(self, history, user_id, thread_id):
        from concierge.api.agent_backend import agent_inputs
        self.user_context = (agent_inputs.get() or {}).get("user_context")
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

    def test_user_context_is_loaded_for_each_turn(self):
        self.client.post("/api/v1/chat", json={"message": "hi"})
        self.assertIsNone(self.backend.user_context)
        account = {"name": "Test", "email": "context@example.com", "password": "password123"}
        self.client.post("/api/v1/auth/register", json=account)
        login = self.client.post("/api/v1/auth/login", json={k: account[k] for k in ("email", "password")})
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        first = self.client.post("/api/v1/chat", json={"message": "hi"}, headers=headers)
        self.assertEqual(self.backend.user_context["email"], "context@example.com")
        self.assertFalse(self.backend.user_context["newsletter_subscribed"])
        self.assertEqual(first.json()["next_actions"], [])

        async def subscribe():
            from concierge.db.relationaldb import crud
            from concierge.db.relationaldb.models import SubscriptionType
            async with AsyncSession(self.engine, expire_on_commit=False) as session:
                await crud.add_subscription(session, "context@example.com", SubscriptionType.newsletter)
        self.client.portal.call(subscribe)
        self.client.post("/api/v1/chat", json={"message": "again", "session_id": first.json()["session_id"]}, headers=headers)
        self.assertTrue(self.backend.user_context["newsletter_subscribed"])

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

    def test_agent_does_not_hold_database_connections(self):
        original_chat, original_resume = self.backend.chat, self.backend.resume
        async def checked_chat(*args):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return await original_chat(*args)
        async def checked_resume(*args):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return await original_resume(*args)
        self.backend.chat, self.backend.resume = checked_chat, checked_resume
        token = self.client.post("/api/v1/chat", json={"message": "book"}).json()["session_id"]
        result = self.client.post(f"/api/v1/sessions/{token}/approvals", json={"interrupt_id": "approval-1", "decision": "reject"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["warnings"], [])

    def test_answer_is_returned_in_the_agent_language_without_rewriting(self):
        async def arabic_reply(*args):
            return {"reply": "مرحبا بكم في غرفة التجارة"}
        self.backend.chat = arabic_reply
        result = self.client.post("/api/v1/chat", json={"message": "مرحبا", "locale": "en"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["reply"], "مرحبا بكم في غرفة التجارة")
        self.assertEqual(result.json()["warnings"], [])

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

    # --- Features ported from the main branch -------------------------------

    def _seed_space(self, name="L'Espace@CCIFJ"):
        import asyncio
        from concierge.db.relationaldb.models import Space

        async def seed():
            async with AsyncSession(self.engine, expire_on_commit=False) as session:
                space = Space(name=name, capacity=80)
                session.add(space)
                await session.commit()
                return space.space_id
        return asyncio.run(seed())

    def test_localized_suggestions_starter_paths_and_current_user(self):
        french = self.client.get("/api/v1/suggestions?locale=fr").json()
        self.assertIn("Qu’est-ce que la CCIFJ ?", french["suggested_questions"])
        self.assertEqual([path["id"] for path in french["starter_paths"]], ["events.upcoming", "spaces.start"])
        self.assertEqual(french["starter_paths"][0]["label"], "Événements CCIFJ à venir")
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        account = self.client.post("/api/v1/auth/register", json={"name": "Me", "email": "me@example.com", "password": "password123"}).json()
        me = self.client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + account["access_token"]})
        self.assertEqual(me.json()["email"], "me@example.com")

    def test_membership_followups_survive_http_and_saved_history(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from concierge.api.catalog import localized_question
        account=self.client.post('/api/v1/auth/register',json={
            'name':'Example','email':'followups@example.test','password':'password123'}).json()
        headers={'Authorization':'Bearer '+account['access_token']}
        category=SimpleNamespace(category_name='Example',annual_price=100,currency='JPY')
        question=localized_question('What are the CCIFJ membership fees?','fr')
        with patch('concierge.api.answers.crud.list_member_categories',AsyncMock(return_value=[category])):
            response=self.client.post('/api/v1/chat',headers=headers,json={'message':question,'locale':'fr'})
        self.assertEqual(response.status_code,200)
        body=response.json()
        self.assertEqual(len(body['suggested_questions']),2)
        self.assertIn(localized_question('What are the benefits of CCIFJ membership?','fr'),body['suggested_questions'])
        history=self.client.get('/api/v1/sessions/'+body['session_id']+'/messages',headers=headers).json()
        self.assertEqual(history['messages'][-1]['response']['suggested_questions'],body['suggested_questions'])

    def test_guided_space_path_runs_without_agent_and_is_saved(self):
        calls = []
        original = self.backend.chat
        async def recording(*args):
            calls.append(args)
            return await original(*args)
        self.backend.chat = recording
        space_id = self._seed_space()
        start = self.client.post("/api/v1/chat", json={"message": "Check space availability", "path_step_id": "spaces.start", "locale": "en"}).json()
        self.assertEqual(start["mode"], "guided_path")
        self.assertEqual(start["active_path_step"], "spaces.availability")
        token = start["session_id"]
        bad = self.client.post("/api/v1/chat", json={"session_id": token, "message": "tomorrow", "path_step_id": "spaces.availability"}).json()
        self.assertEqual(bad["active_path_step"], "spaces.availability")
        available = self.client.post("/api/v1/chat", json={
            "session_id": token, "message": "2026-11-12 09:00 to 2026-11-12 12:00", "path_step_id": "spaces.availability",
        }).json()
        self.assertEqual(available["path_actions"][0]["data"], {
            "space_id": space_id, "space_name": "L'Espace@CCIFJ", "start_date": "2026-11-12 09:00", "end_date": "2026-11-12 12:00",
        })
        details = self.client.post("/api/v1/chat", json={
            "session_id": token, "message": "Continue", "path_step_id": "spaces.booking_details",
            "path_context": available["path_actions"][0]["data"],
        }).json()
        self.assertEqual(details["active_path_step"], "spaces.submit")
        self.assertEqual(details["path_context"]["space_id"], space_id)
        guest_submit = self.client.post("/api/v1/chat", json={
            "session_id": token, "message": "Board meeting, 10 people", "path_step_id": "spaces.submit", "path_context": details["path_context"],
        }).json()
        self.assertIn("sign in", guest_submit["reply"])
        self.assertEqual(calls, [])
        history = self.client.get(f"/api/v1/sessions/{token}/messages").json()
        self.assertEqual(len(history["messages"]), 10)
        self.assertEqual(history["turn_status"], "ready")
        self.assertEqual(self.client.post("/api/v1/chat", json={"message": "x", "path_step_id": "unknown.step"}).status_code, 400)
        missing = self.client.post("/api/v1/chat", json={"message": "x", "path_step_id": "spaces.booking_details", "path_context": {"space_id": "1"}})
        self.assertEqual(missing.status_code, 400)

    def test_space_submit_hands_structured_request_to_agent(self):
        from concierge.api.agent_backend import agent_inputs
        seen = []
        original = self.backend.chat
        async def recording(*args):
            seen.append(dict(agent_inputs.get()))
            return await original(*args)
        self.backend.chat = recording
        account = self.client.post("/api/v1/auth/register", json={"name": "Booker", "email": "booker@example.com", "password": "password123"}).json()
        context = {"space_id": 1, "space_name": "Room", "start_date": "2026-11-12 09:00", "end_date": "2026-11-12 12:00"}
        result = self.client.post("/api/v1/chat", json={"message": "Board meeting, 10 people", "path_step_id": "spaces.submit", "path_context": context},
                                  headers={"Authorization": "Bearer " + account["access_token"]})
        self.assertEqual(result.status_code, 200)
        self.assertIn("book_space", seen[0]["agent_message"])
        self.assertIn("expected_attendees=10", seen[0]["agent_message"])

    def test_attachment_context_and_agent_state_persist_across_turns(self):
        from unittest.mock import patch
        from concierge.api.agent_backend import agent_inputs
        seen = []
        async def recording(history, user_id, thread_id):
            seen.append(dict(agent_inputs.get()))
            return {"reply": "Read", "attachment_count": len(agent_inputs.get()["context"]), "agent_state": {"out_of_scope_count": len(seen)}}
        self.backend.chat = recording
        def fake_extract(item):
            return {"id": item["name"], "name": item["name"], "mime_type": item["mime_type"], "text": "Invoice total 5000 JPY"}
        upload = {"name": "invoice.png", "mime_type": "image/png", "data_base64": "aGVsbG8="}
        with patch("concierge.api.chat.extract_attachment", fake_extract):
            first = self.client.post("/api/v1/chat", json={"message": "What is this?", "attachments": [upload]}).json()
            self.assertEqual(first["attachment_count"], 1)
            token = first["session_id"]
            second = self.client.post("/api/v1/chat", json={"session_id": token, "message": "And the total?"}).json()
            self.assertEqual(second["attachment_count"], 1)
            self.assertEqual(seen[1]["context"][0]["text"], "Invoice total 5000 JPY")
            self.assertEqual(seen[1]["out_of_scope_count"], 1)
            # Re-sending the same file does not count twice; a fifth distinct file is refused.
            self.client.post("/api/v1/chat", json={"session_id": token, "message": "again", "attachments": [upload]})
            self.assertEqual(len(seen[2]["context"]), 1)
            others = [{**upload, "name": f"page{i}.png"} for i in range(4)]
            too_many = self.client.post("/api/v1/chat", json={"session_id": token, "message": "more", "attachments": others})
            self.assertEqual(too_many.status_code, 413)
        self.assertEqual(self.client.get(f"/api/v1/sessions/{token}/messages").json()["turn_status"], "ready")
        unsupported = self.client.post("/api/v1/chat", json={"message": "x", "attachments": [{**upload, "mime_type": "application/zip"}]})
        self.assertEqual(unsupported.status_code, 400)

    def test_stream_progress_is_localized(self):
        from concierge.api.agent_backend import response_events
        async def progress_reply(*args):
            response_events.get().put_nowait({"type": "progress", "messages": ["Checking our events database…"]})
            return {"reply": "Done"}
        self.backend.chat = progress_reply
        result = self.client.post("/api/v1/chat/stream", json={"message": "events", "locale": "fr"})
        self.assertIn("Le concierge CCIFJ examine votre demande", result.text)
        self.assertIn("Checking our events database", result.text)


class AgentBackendFormattingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from concierge.api.agent_backend import AgentBackend
        self.backend = AgentBackend.__new__(AgentBackend)
        self.backend.pending = lambda result: [dict(item) for item in result.get("__interrupt__", [])]

    async def test_suggestions_do_not_repeat_questions_already_asked_or_each_other(self):
        from langchain_core.messages import HumanMessage, AIMessage
        result={'messages':[HumanMessage('What are my options?'),AIMessage('Earlier answer'),
                            HumanMessage('Tell me the next step.'),AIMessage('A useful next step.')],
                'suggested_questions':[' WHAT ARE MY OPTIONS？ ', 'How should I prepare?',
                                       'how should I prepare？', 'Can I compare the fees?']}
        formatted=await self.backend.format_result(result)
        self.assertEqual(formatted['suggested_questions'],['How should I prepare?','Can I compare the fees?'])

    async def test_reply_is_limited_to_the_current_turn(self):
        from langchain_core.messages import AIMessage, HumanMessage
        result = {"messages": [HumanMessage("old"), AIMessage("Old answer"), HumanMessage("new"), AIMessage("")]}
        with self.assertRaises(RuntimeError):
            await self.backend.format_result(result)

    async def test_intent_scope_state_and_guest_approvals(self):
        from langchain_core.messages import AIMessage, HumanMessage
        from concierge.api.agent_backend import response_locale
        result = {
            "messages": [HumanMessage("Any events?"), AIMessage("See https://www.ccifj.or.jp/en/events.html")],
            "plan": {"tasks": [{"target": "events"}, {"target": "events"}]},
            "suggested_questions": ["a", "b", "c", "d"], "out_of_scope_count": 2, "context": [{"id": "x"}],
        }
        formatted = await self.backend.format_result(result)
        self.assertEqual(formatted["intent"], {"code": "events"})
        self.assertEqual(formatted["suggested_questions"], ["a", "b", "c"])
        self.assertEqual(formatted["path_actions"], [])
        self.assertEqual(formatted["agent_state"], {"out_of_scope_count": 2})
        self.assertEqual(formatted["attachment_count"], 1)
        # Model-generated links are actions, not retrieved source evidence.
        self.assertEqual(formatted["sources"], [])
        self.assertEqual(formatted["actions"][0]["url"], "https://www.ccifj.or.jp/en/events.html")
        token = response_locale.set("ja")
        try:
            guest = await self.backend.format_result({**result, "__interrupt__": [{"interrupt_id": "i", "action_requests": [{"name": "book_space", "args": {}}]}]}, guest=True)
        finally:
            response_locale.reset(token)
        self.assertEqual(guest["pending_approvals"], [])
        self.assertIn("ログイン", guest["reply"])

    async def test_next_actions_are_filtered_and_hidden_during_approvals(self):
        from langchain_core.messages import AIMessage, HumanMessage
        result = {
            "messages": [HumanMessage("Any events?"), AIMessage("Networking Night is on Friday.")],
            "suggested_questions": ["Which event fits my goals?"],
            "next_actions": [
                {"id": "run.sql", "label": "Unknown", "prompt": "drop"},
                {"id": "event.book", "label": " Book Networking Night ", "prompt": "I want to book a ticket for Networking Night", "event_id": 12},
                {"id": "membership.join", "label": "Join", "prompt": ""},
                {"id": "auth.login", "label": "Log in", "prompt": "I want to log in"},
                {"id": "contact.staff", "label": "Contact staff", "prompt": "Contact staff"},
            ],
        }
        formatted = await self.backend.format_result(result)
        self.assertEqual(formatted["next_actions"], [
            {"id": "event.book", "label": "Book Networking Night", "prompt": "I want to book a ticket for Networking Night", "event_id": 12},
            {"id": "auth.login", "label": "Log in", "prompt": "I want to log in"},
        ])
        self.assertEqual(formatted["suggested_questions"], ["Which event fits my goals?"])
        pending = await self.backend.format_result({**result, "__interrupt__": [{"interrupt_id": "i", "action_requests": [{"name": "book_space", "args": {}}]}]}, guest=True)
        self.assertEqual(pending["next_actions"], [])
        self.assertEqual(pending["suggested_questions"], [])

    async def test_model_suggestions_pass_through_and_fill_up_to_three_buttons(self):
        from langchain_core.messages import AIMessage, HumanMessage
        from concierge.api.agent_backend import response_locale
        question = "Which committee fits a cosmetics brand?"
        result = {
            "messages": [HumanMessage("What networking events are coming up?"), AIMessage("Networking Night is on Friday.")],
            "next_actions": [
                {"id": "event.book", "label": "Book", "prompt": "I want to book a ticket for Networking Night", "event_id": 12},
                {"id": "events.browse", "label": "Browse", "prompt": "Show me more events"},
            ],
            "suggested_questions": [question, " Can CCIFJ help me find a distributor? ", ""],
        }
        token = response_locale.set("fr")
        try:
            with_actions = await self.backend.format_result(result)
            questions_only = await self.backend.format_result({**result, "next_actions": []})
        finally:
            response_locale.reset(token)
        self.assertEqual(len(with_actions["next_actions"]), 2)
        # Suggestions are written by the model in the user's language: never re-localized.
        self.assertEqual(with_actions["suggested_questions"], [question, "Can CCIFJ help me find a distributor?"])
        self.assertEqual(with_actions["path_actions"], [])
        self.assertEqual(questions_only["suggested_questions"], [question, "Can CCIFJ help me find a distributor?"])


class UserContextTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.directory.name}/test.db")
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.directory.cleanup()

    async def test_profile_shape_membership_and_upcoming_items(self):
        from datetime import timedelta
        from decimal import Decimal
        from concierge.db.relationaldb import crud, models
        now = crud._now().replace(second=0, microsecond=0)
        async with AsyncSession(self.engine, expire_on_commit=False) as session:
            self.assertIsNone(await crud.load_user_context(session, None))
            member_user = models.User(name="Sara Martin", email="sara@example.com")
            guest_user = models.User(name="Non Member", email="other@example.com")
            category = models.MemberCategory(category_name="Premium", category_description="", annual_price=Decimal("250000.00"))
            space = models.Space(name="Room A")
            session.add_all([member_user, guest_user, category, space])
            await session.flush()
            company = models.Member(name="ACME KK", category_id=category.category_id, sector="IT", country="Japan", region="Tokyo")
            events = [models.Event(title=f"Event {i}", description="", category="tech", start_at=now + timedelta(days=i, hours=1)) for i in range(-1, 7)]
            session.add_all([company, *events])
            await session.flush()
            session.add(models.Belongs(user_id=member_user.user_id, member_id=company.member_id))
            for event in events:
                session.add(models.Booking(user_id=member_user.user_id, event_id=event.event_id))
            session.add(models.Booking(user_id=member_user.user_id, event_id=events[1].event_id, status=models.BookingStatus.cancelled))
            for hours in (-3, 1, 2, 3, 4):
                session.add(models.SpaceReservation(space_id=space.space_id, user_id=member_user.user_id, title=f"Meeting {hours}",
                    start_at=now + timedelta(hours=hours * 10), end_at=now + timedelta(hours=hours * 10 + 2)))
            session.add(models.Subscription(user_email="sara@example.com", sub_type=models.SubscriptionType.newsletter))
            await session.commit()

            context = await crud.load_user_context(session, member_user.user_id)
            self.assertEqual(set(context), {"name", "email", "membership", "upcoming_bookings", "upcoming_space_reservations", "newsletter_subscribed", "interests", "recommended_events"})
            self.assertEqual(context["membership"], {"company": "ACME KK", "industry": "IT", "country": "Japan", "region": "Tokyo", "tier": "Premium", "annual_price": "250000", "currency": "JPY"})
            self.assertEqual([b["title"] for b in context["upcoming_bookings"]], ["Event 0", "Event 1", "Event 2", "Event 3", "Event 4"])
            self.assertEqual(context["upcoming_bookings"][0]["start_at"], (now + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"))
            self.assertEqual([r["title"] for r in context["upcoming_space_reservations"]], ["Meeting 1", "Meeting 2", "Meeting 3"])
            self.assertTrue(context["newsletter_subscribed"])

            other = await crud.load_user_context(session, guest_user.user_id)
            self.assertIsNone(other["membership"])
            self.assertEqual(other["upcoming_bookings"], [])
            self.assertFalse(other["newsletter_subscribed"])


class ProgressTrackerTest(unittest.TestCase):
    def test_parallel_workers_and_tools(self):
        from concierge.api.progress import ProgressTracker
        tracker = ProgressTracker("en")
        node = lambda name, event, run: {"event": event, "run_id": run, "metadata": {"langgraph_node": name}}
        self.assertIsNone(tracker.update(node("orchestrator_node", "on_chain_start", "o")))
        self.assertEqual(tracker.update(node("events_node", "on_chain_start", "1")), ["Our Events Agent is finding relevant events for you…"])
        self.assertEqual(tracker.update(node("general_node", "on_chain_start", "2")), ["Our Events and Business Support Agents are working in parallel…"])
        tool = tracker.update({"event": "on_tool_start", "name": "search_vectordb", "run_id": "t", "metadata": {}})
        self.assertEqual(tool[0], "Looking through CCIFJ information…")
        self.assertIsNone(tracker.update({"event": "on_tool_start", "name": "internal_helper", "run_id": "u", "metadata": {}}))


class EventSearchTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.directory.name}/test.db")
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.directory.cleanup()

    async def test_keywords_and_locations_match_any_term(self):
        from datetime import timedelta
        from concierge.db.relationaldb import crud, models
        soon = crud._now() + timedelta(days=3)
        async with AsyncSession(self.engine, expire_on_commit=False) as session:
            session.add_all([
                models.Event(title="Ask me Anything", description="Leaders from the Food & Beverage industry",
                             category="Business event", location="Nihonbashi, Tokyo", start_at=soon),
                models.Event(title="GALA KANSAI 2026", description="French gastronomy evening",
                             category="Networking", location="Higashiyama-ku, Kyoto Japan", start_at=soon),
                models.Event(title="HR Trends", description="Salaries and recruitment",
                             category="Business event", location="Chuo-ku, Tokyo", start_at=soon),
            ])
            await session.commit()
            food = await crud.search_events(session, keywords=["wine", "gastronomy", "beverage"])
            kansai = await crud.search_events(session, locations=["Osaka", "Kyoto"])
            categories = await crud.list_event_categories(session)
        self.assertEqual({e.title for e in food}, {"Ask me Anything", "GALA KANSAI 2026"})
        self.assertEqual([e.title for e in kansai], ["GALA KANSAI 2026"])
        self.assertEqual(categories, ["Business event", "Networking"])


class MemberRegionTest(unittest.TestCase):
    def test_regions_match_stored_wards_and_cities_as_whole_words(self):
        from concierge.agents.tools import _member_region_pattern
        tokyo, kansai = _member_region_pattern("Tokyo"), _member_region_pattern("Kansai")
        self.assertTrue(tokyo.search("CHIYODA-KU"))
        self.assertTrue(tokyo.search("shibuya ku Japon"))
        self.assertTrue(tokyo.search("町田市 Japon"))
        self.assertFalse(tokyo.search("Kitaazumi-gun Hakuba"))
        self.assertTrue(kansai.search("Osaka-shi"))
        self.assertFalse(kansai.search("Narashino-shi Japon"))
        self.assertIsNone(_member_region_pattern("Japan"))


class BookingButtonTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_book_button_for_an_event_booked_earlier_in_the_turn(self):
        """The profile is loaded before the approval, so only the database knows
        the user just booked; the button must follow the database."""
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        from unittest import mock
        from concierge.agents import body

        @asynccontextmanager
        async def fake_session():
            yield None

        from datetime import datetime
        event = SimpleNamespace(event_id=56, title="Kansai International Career Forum", reg_deadline=datetime(2099, 1, 1))
        state = {"user_id": "7", "lang": "en", "user_context": {"upcoming_bookings": []}}
        findings = "Booked.\n\n[Event IDs from the events database this turn: ID 56 = Kansai International Career Forum]"
        with mock.patch("concierge.db.relationaldb.session.AsyncSessionLocal", fake_session), \
                mock.patch("concierge.db.relationaldb.crud.list_user_bookings", mock.AsyncMock(return_value=[(None, event)])), \
                mock.patch("concierge.db.relationaldb.crud.get_event", mock.AsyncMock(return_value=event)), \
                mock.patch("concierge.agents.tools._registration_problem", return_value=None):
            booked = await body._booked_event_ids(state)
            after_booking = await body._with_booking_action([], state, findings, booked)
            before_booking = await body._with_booking_action([], state, findings, set())
        self.assertEqual(booked, {56})
        self.assertEqual(after_booking, [])
        self.assertEqual([a["id"] for a in before_booking], ["event.book"])


class LinkLabelTest(unittest.TestCase):
    def test_generic_and_repeated_link_texts_get_distinct_page_names(self):
        from concierge.api.presentation import linked_resources
        reply = (
            "[event page](https://www.ccifj.or.jp/en/events/upcoming-events/event-details/event/ask-me-anything.html) "
            "[event page](https://www.ccifj.or.jp/en/events/upcoming-events/event-details/event/gala-kansai-2026.html) "
            "[Contact us](https://www.ccifj.or.jp/en/events/contact-us.html) "
            "[Contact us](https://www.ccifj.or.jp/en/membership/contact-us.html) "
            "https://www.ccifj.or.jp/en/membership/contact-us.html/"
        )
        labels = [action["label"] for action in linked_resources(reply)[1]]
        self.assertEqual(labels, ["Ask me anything", "Gala kansai 2026", "Events · Contact us", "Membership · Contact us"])


if __name__ == "__main__":
    unittest.main()
