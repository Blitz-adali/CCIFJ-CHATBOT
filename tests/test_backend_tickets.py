"""Staff requests, approval cards, profile interests, attachments, and startup warm-up."""
import base64
import io
import os
import tempfile
import unittest
import zipfile
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("CCIFJ_AUTH_SECRET", "test-secret-for-contract-checks-only-123456789")
os.environ.setdefault("model_type", "qwen")
os.environ.setdefault("model_name", "test-model")
os.environ.setdefault("router_model_name", "test-model")
os.environ.setdefault("COHERE_API_KEY", "test-cohere-key")

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.db.relationaldb import crud, models


class DatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.directory.name}/test.db")
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self.now = crud._now().replace(second=0, microsecond=0)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.directory.cleanup()

    async def add(self, *rows):
        async with self.sessions() as session:
            session.add_all(rows)
            await session.commit()
            for row in rows:
                await session.refresh(row)
        return rows

    def event(self, title, category, days, **extra):
        return models.Event(title=title, description="", category=category, location="Tokyo",
                            start_at=self.now + timedelta(days=days, minutes=1), **extra)


class StaffRequestTest(DatabaseTest):
    async def test_store_email_with_recipients_and_team_ticket(self):
        user, = await self.add(models.User(name="Jane", email="jane@example.com"))
        chat, contact = await self.add(
            models.ChatSession(user_id=user.user_id),
            models.ContactUs(name="Elena", email="E.Suzuki@ccifj.or.jp", category="events", sub_category="all"),
        )
        async with self.sessions() as session:
            stored = await crud.add_staff_request(
                session, request_type="staff_email", category="membership", subject="Late registration",
                message="Dear Ms Suzuki,\n\nCould I still register?", sender_name="Jane", sender_email="jane@example.com",
                recipient_emails=["e.suzuki@ccifj.or.jp", "barges@ccifj.or.jp", "E.SUZUKI@ccifj.or.jp"],
                user_id=user.user_id, session_id=chat.session_id,
            )
            self.assertRegex(stored.reference, r"^SR-\d{4}-\d{6}$")
            saved = await crud.get_staff_request(session, stored.reference)
            self.assertEqual(saved.message, "Dear Ms Suzuki,\n\nCould I still register?")
            self.assertEqual((saved.user_id, saved.session_id, saved.status), (user.user_id, chat.session_id, models.StaffRequestStatus.new))
            recipients = {(r.email, r.contact_id) for r in await crud.list_staff_request_recipients(session, saved.request_id)}
            self.assertEqual(recipients, {("e.suzuki@ccifj.or.jp", contact.contact_id), ("barges@ccifj.or.jp", None)})

            ticket = await crud.add_staff_request(
                session, request_type="team_ticket", category="other", subject="Question", message="Hello",
                sender_name="Guest", sender_email="guest@example.com",
            )
            self.assertIsNone(ticket.user_id)
            self.assertEqual(await crud.list_staff_request_recipients(session, ticket.request_id), [])
            self.assertNotEqual(ticket.reference, stored.reference)
            with self.assertRaises(ValueError):
                await crud.add_staff_request(session, request_type="staff_email", category="other", subject="s",
                                             message="m", sender_name="n", sender_email="n@example.com")

    def test_migration_creates_staff_tables(self):
        from concierge.db.relationaldb.migrate import upgrade_connection
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{directory}/migrated.db")
            with engine.begin() as connection:
                upgrade_connection(connection)
            tables = set(inspect(engine).get_table_names())
            engine.dispose()
        self.assertTrue({"staff_request", "staff_request_recipient"} <= tables)


class UserInterestsTest(DatabaseTest):
    async def test_interests_reminders_and_recommendations(self):
        user, other = await self.add(models.User(name="Sara", email="sara@example.com"), models.User(name="New", email="new@example.com"))
        past = self.event("Past mixer", "Networking", -10)
        soon = self.event("Mixer", "Networking", 3)
        talk = self.event("Talk", "Conference", 5)
        open_mixer = self.event("Open mixer", "Networking", 8)
        closed_mixer = self.event("Closed mixer", "Networking", 9, reg_deadline=self.now - timedelta(days=1))
        gala = self.event("Gala", "Gala", 10)
        await self.add(past, soon, talk, open_mixer, closed_mixer, gala)
        await self.add(*(models.Booking(user_id=user.user_id, event_id=e.event_id) for e in (past, soon, talk)))

        async with self.sessions() as session:
            context = await crud.load_user_context(session, user.user_id)
            fresh = await crud.load_user_context(session, other.user_id)
        self.assertEqual(context["interests"], ["Networking", "Conference"])
        self.assertEqual(context["upcoming_bookings"][0] | {}, {
            "event_id": soon.event_id, "title": "Mixer", "start_at": context["upcoming_bookings"][0]["start_at"],
            "category": "Networking", "location": "Tokyo", "days_until": 3,
        })
        self.assertEqual([e["title"] for e in context["recommended_events"]], ["Open mixer"])
        self.assertEqual(set(context["recommended_events"][0]), {"event_id", "title", "category", "start_at", "reg_deadline", "location"})
        # No booking history: the next open events.
        self.assertEqual(fresh["interests"], [])
        self.assertEqual([e["title"] for e in fresh["recommended_events"]], ["Mixer", "Talk"])


class ApprovalCardTest(DatabaseTest):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from concierge.api.agent_backend import AgentBackend
        self.backend = AgentBackend.__new__(AgentBackend)
        self.backend.pending = lambda result: [dict(item) for item in result.get("__interrupt__", [])]
        self.patch = patch("concierge.db.relationaldb.session.AsyncSessionLocal", self.sessions)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        await super().asyncTearDown()

    async def format(self, name, args, user_id=None, guest=False, locale="en"):
        from langchain_core.messages import AIMessage, HumanMessage
        from concierge.api.agent_backend import response_locale
        result = {
            "messages": [HumanMessage("Please do it"), AIMessage("")],
            "user_id": str(user_id) if user_id else None,
            "__interrupt__": [{"interrupt_id": "i", "action_requests": [{"name": name, "args": args}]}],
        }
        token = response_locale.set(locale)
        try:
            return await self.backend.format_result(result, guest=guest)
        finally:
            response_locale.reset(token)

    async def test_guests_keep_staff_requests_but_not_bookings(self):
        staff = await self.format("submit_staff_request", {"request_type": "team_ticket", "subject": "Hi"}, guest=True, locale="fr")
        self.assertEqual(len(staff["pending_approvals"]), 1)
        self.assertIn("personnel de la CCIFJ", staff["reply"])
        booking = await self.format("book_space", {"space_name": "Room"}, guest=True)
        self.assertEqual(booking["pending_approvals"], [])
        self.assertIn("signed-in account", booking["reply"])

    async def test_booking_and_space_cards(self):
        event, space = await self.add(
            self.event("Summit", "Conference", 4, price=Decimal("5000.00"), currency="JPY", reg_deadline=self.now + timedelta(days=2)),
            models.Space(name="L'Espace@CCIFJ", capacity=80, description="Iidabashi office, 2F"),
        )
        ticket = await self.format("book_event_ticket", {"event_id": str(event.event_id), "attendees": [{"name": "A", "email": "a@example.com"}]}, user_id=1)
        args = ticket["pending_approvals"][0]["action_requests"][0]["args"]
        self.assertEqual((args["event_title"], args["price"], args["currency"], args["ticket_count"]), ("Summit", "5000.00", "JPY", 1))
        self.assertIsNotNone(args["reg_deadline"])
        reserve = await self.format("book_space", {"space_name": "l'espace@ccifj", "start_date": "2026-12-01", "end_date": "2026-12-02"}, user_id=1)
        args = reserve["pending_approvals"][0]["action_requests"][0]["args"]
        self.assertEqual((args["space_name"], args["space_capacity"]), ("L'Espace@CCIFJ", 80))
        self.assertEqual((args["start_at"], args["end_at"]), ("2026-12-01T00:00:00", "2026-12-02T23:59:59.999999"))
        news = await self.format("subscribe_newsletter", {"email": "a@example.com", "newsletter_type": "podcast_studio_ccifj"}, user_id=1)
        self.assertEqual(news["pending_approvals"][0]["action_requests"][0]["args"]["newsletter_name"], "@StudioCCIFJ Podcast Newsletter")

    async def test_cancel_and_change_cards_only_for_the_owner(self):
        owner, stranger = await self.add(models.User(name="O", email="o@example.com"), models.User(name="S", email="s@example.com"))
        old_event, new_event, space = await self.add(self.event("Old", "Talk", 5), self.event("New", "Talk", 6), models.Space(name="Room A", capacity=10))
        booking, = await self.add(models.Booking(user_id=owner.user_id, event_id=old_event.event_id))
        await self.add(models.Ticket(booking_id=booking.booking_id, name="O", email="o@example.com"))
        reservation, = await self.add(models.SpaceReservation(
            space_id=space.space_id, user_id=owner.user_id, title="Board", expected_attendees=6,
            start_at=self.now + timedelta(days=3), end_at=self.now + timedelta(days=3, hours=2),
        ))
        reference = f"CCIFJ-EVT-{booking.booking_id:05d}"

        cancel = await self.format("cancel_event_booking", {"booking_reference": reference}, user_id=owner.user_id, locale="ja")
        args = cancel["pending_approvals"][0]["action_requests"][0]["args"]
        self.assertEqual((args["event_title"], args["attendees"][0]["email"]), ("Old", "o@example.com"))
        self.assertIn("キャンセル", cancel["reply"])

        change = await self.format("change_event_booking", {"booking_reference": reference, "new_event_id": str(new_event.event_id)}, user_id=owner.user_id)
        args = change["pending_approvals"][0]["action_requests"][0]["args"]
        self.assertEqual((args["current"]["event_title"], args["new"]["event_title"]), ("Old", "New"))
        self.assertEqual(args["new"]["attendees"], args["current"]["attendees"])
        self.assertIn("changes to your booking", change["reply"])
        self.assertNotIn("cancel", change["reply"].lower())

        moved = await self.format("change_space_reservation", {
            "reservation_reference": f"CCIFJ-SPC-{reservation.reservation_id:05d}", "new_title": "Board meeting",
        }, user_id=owner.user_id)
        args = moved["pending_approvals"][0]["action_requests"][0]["args"]
        self.assertEqual((args["current"]["title"], args["new"]["title"]), ("Board", "Board meeting"))
        self.assertEqual(args["new"]["start_at"], args["current"]["start_at"])
        self.assertEqual(args["current"]["space_name"], "Room A")

        hidden = await self.format("cancel_event_booking", {"booking_reference": reference}, user_id=stranger.user_id)
        self.assertEqual(hidden["pending_approvals"][0]["action_requests"][0]["args"], {"booking_reference": reference})


class AttachmentTypesTest(unittest.TestCase):
    def extract(self, mime, raw, name="file"):
        from concierge.api.attachments import extract_attachment
        return extract_attachment({"name": name, "mime_type": mime, "data_base64": base64.b64encode(raw).decode()})

    def test_text_html_and_docx(self):
        self.assertEqual(self.extract("text/plain; charset=utf-8", "café, 東京".encode())["text"], "café, 東京")
        html = self.extract("text/html", b"<html><style>p{}</style><body><p>Hello</p><script>x()</script><p>World</p></body></html>")
        self.assertEqual(html["text"], "Hello\nWorld")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                '<w:p><w:r><w:t>Line one</w:t></w:r></w:p><w:p><w:r><w:t>Line</w:t><w:tab/><w:t>two</w:t></w:r></w:p>'
                '</w:body></w:document>'
            ))
        docx = self.extract("application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer.getvalue())
        self.assertEqual(docx["text"], "Line one\nLine\ttwo")

    def test_rejects_bad_text_and_unknown_types(self):
        for mime, raw in (("text/plain", b"\xff\xfe\xfa"), ("application/zip", b"PK\x03\x04"),
                          ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"not a zip")):
            with self.assertRaises(HTTPException) as caught:
                self.extract(mime, raw)
            self.assertEqual(caught.exception.status_code, 400)


class WarmUpTest(unittest.IsolatedAsyncioTestCase):
    async def test_status_reports_readiness_and_failures_are_only_logged(self):
        import asyncio
        import importlib
        from concierge.api import agent_backend, health
        main = importlib.import_module("concierge.main")
        self.assertFalse((await health.status())["agent_ready"])
        with patch.object(agent_backend, "_build_agent_backend", side_effect=RuntimeError("missing model")):
            task = asyncio.create_task(asyncio.to_thread(agent_backend.warm_agent))
            with self.assertLogs("concierge.main", "ERROR"):
                task.add_done_callback(main._log_warm_up_failure)
                with self.assertRaises(RuntimeError):
                    await task
                await asyncio.sleep(0)
        self.assertFalse(agent_backend.agent_ready())


if __name__ == "__main__":
    unittest.main()
