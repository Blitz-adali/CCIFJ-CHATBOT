"""The optional Odoo CRM connector: off until configured; insert, retrieve, update and
delete a lead through JSON-2 (Odoo 19+) or XML-RPC (Odoo 18 and earlier); never raises
into the chat. Both servers are simulated: no call here reaches a real Odoo."""
import os
import unittest
from unittest.mock import patch


class _FakeOdoo:
    """An in-memory crm.lead table that accepts the calls Odoo's ORM accepts and rejects others."""

    def __init__(self):
        self.leads, self.next_id, self.calls = {}, 41, []

    def run(self, method, **kw):
        self.calls.append(method)
        if method == "create":
            ids = []
            for values in kw["vals_list"]:
                assert values.get("name"), "crm.lead needs a name"
                self.next_id += 1
                self.leads[self.next_id] = {"id": self.next_id, "active": True, **values}
                ids.append(self.next_id)
            return ids
        if method == "search_read":
            marker = next(value for field, _, value in kw["domain"] if field == "name")
            archived = any(field == "active" for field, _, _ in kw["domain"])
            rows = [lead for lead in self.leads.values() if marker in lead["name"] and (archived or lead["active"])]
            return [{field: lead.get(field) for field in kw["fields"]} for lead in rows][: kw.get("limit") or None]
        if method == "write":
            for lead_id in kw["ids"]:
                self.leads[lead_id].update(kw["vals"])
            return True
        if method == "unlink":
            for lead_id in kw["ids"]:
                del self.leads[lead_id]
            return True
        raise AssertionError(f"unexpected method {method}")

    # JSON-2: POST /json/2/crm.lead/<method> with named arguments and a bearer key
    def post_json(self, url, payload, headers, timeout):
        assert "/json/2/crm.lead/" in url and headers["Authorization"] == "bearer key" and headers["X-Odoo-Database"] == "ccifj"
        return 200, self.run(url.rsplit("/", 1)[1], **payload)

    # XML-RPC: positional arguments, as execute_kw passes them
    def proxy(self, url, path, timeout):
        fake = self

        class Server:
            def authenticate(self, db, user, key, context):
                return 7 if key == "key" else False

            def execute_kw(self, db, uid, key, model, method, args, kwargs):
                assert (model, uid) == ("crm.lead", 7)
                if method == "create":
                    return fake.run("create", vals_list=args[0] if isinstance(args[0], list) else [args[0]])[0]
                if method == "search_read":
                    return fake.run("search_read", domain=args[0], **kwargs)
                if method == "write":
                    return fake.run("write", ids=args[0], vals=args[1])
                return fake.run("unlink", ids=args[0])
        return Server()


class OdooConnectorTest(unittest.TestCase):
    REQUEST = {
        "reference": "SR-2026-000007", "request_type": "staff_email", "category": "business_support",
        "subject": "Market study for a cosmetics brand", "message": "Hello,\nwe would like a quote <today>.",
        "sender_name": "Jane Doe", "sender_email": "jane@example.com",
        "recipient_emails": ["appui.entreprises@ccifj.or.jp"],
    }
    ODOO = {"ODOO_URL": "https://odoo.example.test", "ODOO_DB": "ccifj", "ODOO_USER": "bot", "ODOO_API_KEY": "key"}

    def crud(self, odoo):
        """Insert, retrieve, update, archive, delete: the same steps on either API."""
        lead_id = odoo.create_lead(self.REQUEST)
        lead = odoo.find_lead("SR-2026-000007")
        self.assertEqual(lead["id"], lead_id)
        self.assertEqual(lead["email_from"], "jane@example.com")
        self.assertIn("SR-2026-000007", lead["name"])
        self.assertIn("Hello,<br>we would like a quote &lt;today&gt;.", lead["description"])  # HTML field: escaped, with breaks
        self.assertIn("appui.entreprises@ccifj.or.jp", lead["description"])
        self.assertTrue(odoo.update_lead("SR-2026-000007", {"contact_name": "Jane D."}))
        self.assertEqual(odoo.find_lead("SR-2026-000007")["contact_name"], "Jane D.")
        self.assertTrue(odoo.delete_lead("SR-2026-000007"))  # archived, still retrievable
        self.assertFalse(odoo.find_lead("SR-2026-000007")["active"])
        self.assertIsNone(odoo.find_lead("SR-2026-000007", include_archived=False))
        self.assertTrue(odoo.delete_lead("SR-2026-000007", permanent=True))
        self.assertIsNone(odoo.find_lead("SR-2026-000007"))
        self.assertFalse(odoo.update_lead("SR-2026-000007", {"contact_name": "x"}))  # nothing to update

    def test_off_until_configured(self):
        from concierge.integrations import odoo
        with patch.dict(os.environ, {name: "" for name in self.ODOO}):
            self.assertFalse(odoo.enabled())
            self.assertIsNone(odoo.push_staff_request(self.REQUEST))

    def test_insert_retrieve_update_delete_through_json2(self):
        from concierge.integrations import odoo
        fake = _FakeOdoo()
        with patch.dict(os.environ, self.ODOO), patch.object(odoo, "_post_json", side_effect=fake.post_json), \
                patch.object(odoo, "_proxy", side_effect=AssertionError("XML-RPC must not be used when JSON-2 answers")):
            self.crud(odoo)
        self.assertEqual(fake.calls[0], "create")

    def test_falls_back_to_xmlrpc_on_servers_without_json2(self):
        from concierge.integrations import odoo
        fake = _FakeOdoo()
        with patch.dict(os.environ, self.ODOO), patch.object(odoo, "_post_json", return_value=(404, "<html>Not Found</html>")), \
                patch.object(odoo, "_proxy", side_effect=fake.proxy):
            self.crud(odoo)

    def test_the_chat_creates_a_lead_and_gets_its_id(self):
        from concierge.integrations import odoo
        fake = _FakeOdoo()
        with patch.dict(os.environ, self.ODOO), patch.object(odoo, "_post_json", side_effect=fake.post_json):
            self.assertEqual(odoo.push_staff_request(self.REQUEST), 42)

    def test_a_wrong_key_is_reported_not_retried_on_the_other_api(self):
        from concierge.integrations import odoo
        refused = (401, {"name": "werkzeug.exceptions.Unauthorized", "message": "Invalid apikey"})
        with patch.dict(os.environ, self.ODOO), patch.object(odoo, "_post_json", return_value=refused), \
                patch.object(odoo, "_proxy", side_effect=AssertionError("no fallback after a refusal")):
            with self.assertRaises(odoo.OdooError) as raised:
                odoo.create_lead(self.REQUEST)
        self.assertIn("Invalid apikey", str(raised.exception))

    def test_an_odoo_outage_is_logged_and_never_raised_to_the_chat(self):
        from concierge.integrations import odoo
        with patch.dict(os.environ, self.ODOO), patch.object(odoo, "_post_json", side_effect=OSError("down")), \
                self.assertLogs("concierge.integrations.odoo", level="ERROR"):
            self.assertIsNone(odoo.push_staff_request(self.REQUEST))


if __name__ == "__main__":
    unittest.main()
