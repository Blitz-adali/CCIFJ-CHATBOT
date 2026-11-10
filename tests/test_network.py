"""The two extra agents' deterministic parts, editable contacts, and the staff view."""
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("CCIFJ_AUTH_SECRET", "test-secret-for-contract-checks-only-123456789")
os.environ.setdefault("model_type", "qwen")
os.environ.setdefault("model_name", "test-model")
os.environ.setdefault("router_model_name", "test-model")
os.environ.setdefault("COHERE_API_KEY", "test-cohere-key")

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.agents import contacts, network, sources
from concierge.db.relationaldb import crud, models

TODAY = date(2026, 11, 10)


class MatchingTest(unittest.TestCase):
    def test_directory_has_the_50_fictional_companies_with_their_contacts(self):
        directory = network.load_directory()
        self.assertEqual(len(directory), 50)
        first = network.find_company("company 1")
        self.assertEqual(first.name, "COMPANY 01")
        self.assertEqual(first.primary_sector, "Food & Beverage")
        self.assertIn("sophie.dubois@company-01.example.com", [c.email for c in first.contacts])

    def test_a_need_is_matched_on_the_records_own_words(self):
        matches = network.match_companies("French food company with experience in Japanese distribution", limit=5)
        self.assertTrue(matches)
        sectors = {m.company.primary_sector for m in matches}
        self.assertTrue(sectors <= {"Food & Beverage", "Retail & Trade"}, sectors)
        self.assertTrue(all(m.matched_words for m in matches))
        solar = network.match_companies("solar panels for a warehouse", limit=1)[0]
        self.assertEqual(solar.company.name, "COMPANY 19")

    def test_both_sides_of_a_need_and_named_companies_are_kept(self):
        matches = network.match_companies("French food company with experience in Japanese distribution", limit=5)
        self.assertEqual({m.company.primary_sector for m in matches}, {"Food & Beverage", "Retail & Trade"})
        named = network.match_companies("introduce me to COMPANY 25 about jam distribution", limit=3)
        self.assertEqual(named[0].company.name, "COMPANY 25")
        from concierge.agents import tools
        record = tools.get_member_record.invoke({"company": "company 25"})
        self.assertIn("sarah.faure@company-25.example.com", record)
        self.assertIn("No member company", tools.get_member_record.invoke({"company": "COMPANY 99"}))

    def test_evidence_is_copied_from_the_record(self):
        match = network.match_companies("workflow automation software", limit=1)[0]
        text = network.describe_match(match)
        self.assertIn(match.company.description, text)
        self.assertIn("Contact:", text)
        self.assertIn("prefers", text)

    def test_nothing_fits_returns_nothing_rather_than_a_weak_match(self):
        self.assertEqual(network.match_companies("nuclear submarines"), [])

    def test_a_failing_embedding_model_falls_back_to_keywords(self):
        def broken(texts):
            raise RuntimeError("no model")
        network._embeddings = None
        self.assertTrue(network.match_companies("hotel hospitality partners", encode=broken))

    def test_matching_tool_names_its_source(self):
        from concierge.agents import tools
        with patch.object(tools, "_encode_texts", side_effect=RuntimeError("no model in tests")):
            result = tools.find_member_matches.invoke({"need": "food distribution"})
        self.assertIn("COMPANY", result)
        self.assertEqual(sources.sources_in_tool_output(result), [{"title": network.DIRECTORY_NAME, "url": None}])


class EngagementTest(unittest.TestCase):
    def stage(self, **record):
        base = {"joined_on": "2026-10-11", "events_attended": [], "services_used": [], "member_area_login": True, "last_activity_on": "2026-10-11"}
        return network.engagement_stage({**base, **record}, TODAY)

    def test_rules_by_time_since_joining_and_activity(self):
        self.assertEqual(self.stage(joined_on="2026-11-03").key, "welcome")
        self.assertEqual(self.stage().key, "first_event")  # 30 days, no event: the brief's example
        self.assertEqual(self.stage(events_attended=["x"], member_area_login=False).key, "member_area")
        self.assertEqual(self.stage(joined_on="2026-06-15", events_attended=["x"], last_activity_on="2026-10-19").key, "discover_services")
        self.assertEqual(self.stage(joined_on="2026-02-10", last_activity_on="2026-03-24").key, "inactive")
        self.assertEqual(self.stage(joined_on="2025-12-01", events_attended=["x"], services_used=["y"], last_activity_on="2026-10-23").key, "first_anniversary")
        self.assertEqual(self.stage(joined_on="2026-03-15", events_attended=["x"], services_used=["y"], last_activity_on="2026-11-02").key, "active")

    def test_the_rule_is_stated_with_its_numbers(self):
        stage = self.stage()
        self.assertIn("30 days", stage.why)
        self.assertIn("no event", stage.why)

    def test_follow_up_list_is_urgent_first_and_leaves_active_members_alone(self):
        due = network.members_to_follow_up(TODAY)
        self.assertEqual([stage.priority for _, stage in due], sorted(stage.priority for _, stage in due))
        self.assertNotIn("active", {stage.key for _, stage in due})
        self.assertIn("Luc NAKAMURA", [record["contact"] for record, _ in due])

    def test_profile_uses_the_directory_record_and_unknown_members_are_not_invented(self):
        text = network.describe_engagement(network.find_engagement("Luc Nakamura"), TODAY)
        self.assertIn("Business contacts in Japan", text)
        self.assertIn("Preferred language: English", text)
        self.assertIn("STAGE: No event yet", text)
        self.assertIsNone(network.find_engagement("Jean Valjean"))


class ContactsTest(unittest.TestCase):
    def test_contacts_are_read_from_the_data_file(self):
        text = contacts.describe("become_sponsor")
        self.assertIn("Marie OZAKI", text)
        self.assertIn("m.ozaki@ccifj.or.jp", text)

    def test_an_edit_is_used_by_the_next_lookup_without_restart(self):
        original = contacts.load_contacts()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contacts.json"
            path.write_text(json.dumps(original), encoding="utf-8")
            with patch.object(contacts, "CONTACTS_PATH", path):
                edited = json.loads(json.dumps(original))
                edited["events"]["people"] = [{"name": "New Person", "role": "Events Lead", "phone": "", "email": "new.person@ccifj.or.jp"}]
                contacts.save_contacts(edited)
                from concierge.agents import tools
                answer = tools.get_ccifj_contact.invoke({"topic": "events"})
        self.assertIn("New Person", answer)
        self.assertNotIn("Elena SUZUKI", answer)
        self.assertIn("Source: [Events: contact us]", answer)

    def test_bad_contacts_are_refused(self):
        broken = contacts.load_contacts()
        broken["events"]["people"][0]["email"] = "not-an-email"
        with self.assertRaises(ValueError):
            contacts.validate(broken)
        with self.assertRaises(ValueError):
            contacts.validate({"events": broken["events"]})


class StaffViewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.directory.name}/test.db")
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.directory.cleanup()

    def test_staff_view_is_off_without_a_key_and_needs_the_right_one(self):
        from concierge.api.staff import require_staff
        with patch.dict(os.environ, {"CCIFJ_STAFF_KEY": ""}):
            with self.assertRaises(HTTPException) as off:
                require_staff("anything")
        self.assertEqual(off.exception.status_code, 404)
        with patch.dict(os.environ, {"CCIFJ_STAFF_KEY": "a-long-staff-key-123"}):
            with self.assertRaises(HTTPException) as wrong:
                require_staff("wrong")
            self.assertEqual(wrong.exception.status_code, 401)
            self.assertIsNone(require_staff("a-long-staff-key-123"))

    async def test_guest_conversation_with_a_request_is_kept_for_staff(self):
        async with self.sessions() as session:
            kept = await crud.create_chat_session(session, None)
            dropped = await crud.create_chat_session(session, None)
            for chat in (kept, dropped):
                await crud.add_message(session, chat.session_id, models.MessageRole.user, "Can you help my company find customers in Japan?")
            request = await crud.add_staff_request(
                session, request_type="team_ticket", category="business_support", subject="Find customers",
                message="Please call me.", sender_name="Jane", sender_email="jane@example.com", session_id=kept.session_id,
            )
            self.assertTrue(await crud.delete_guest_session(session, dropped.session_id))
            self.assertFalse(await crud.delete_guest_session(session, kept.session_id))
            listed = await crud.list_conversations(session)
            self.assertEqual([c["session_id"] for c in listed], [kept.session_id])
            self.assertEqual(listed[0]["staff_requests"], [request.reference])
            self.assertIsNone(listed[0]["user"])
            requests = await crud.list_staff_requests(session)
            self.assertEqual(requests[0][0].session_id, kept.session_id)
            updated = await crud.set_staff_request_status(session, request.reference, "in_review")
            self.assertEqual(updated.status, models.StaffRequestStatus.in_review)
            self.assertEqual(len(await crud.list_staff_requests(session, "new")), 0)


if __name__ == "__main__":
    unittest.main()
