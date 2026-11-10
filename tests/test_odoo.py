"""The optional Odoo CRM connector: off until configured, creates a lead, never raises."""
import os
import unittest
from unittest.mock import patch


class OdooConnectorTest(unittest.TestCase):
    REQUEST = {
        "reference": "SR-2026-000007", "request_type": "staff_email", "category": "business_support",
        "subject": "Market study for a cosmetics brand", "message": "Hello, we would like a quote.",
        "sender_name": "Jane Doe", "sender_email": "jane@example.com",
        "recipient_emails": ["appui.entreprises@ccifj.or.jp"],
    }
    ODOO = {"ODOO_URL": "https://odoo.example.test", "ODOO_DB": "ccifj", "ODOO_USER": "bot", "ODOO_API_KEY": "key"}

    def test_off_until_configured(self):
        from concierge.integrations import odoo
        with patch.dict(os.environ, {name: "" for name in self.ODOO}):
            self.assertFalse(odoo.enabled())
            self.assertIsNone(odoo.push_staff_request(self.REQUEST))

    def test_creates_a_lead_with_the_request_and_its_reference(self):
        from concierge.integrations import odoo
        calls = []

        class FakeServer:
            def __init__(self, path):
                self.path = path

            def authenticate(self, db, user, key, context):
                calls.append(("authenticate", db, user))
                return 7

            def execute_kw(self, db, uid, key, model, method, args):
                calls.append((model, method, uid, args[0]))
                return 42

        with patch.dict(os.environ, self.ODOO), patch.object(odoo, "_proxy", side_effect=lambda url, path, timeout: FakeServer(path)):
            self.assertEqual(odoo.push_staff_request(self.REQUEST), 42)
        self.assertEqual(calls[0], ("authenticate", "ccifj", "bot"))
        model, method, uid, lead = calls[1]
        self.assertEqual((model, method, uid), ("crm.lead", "create", 7))
        self.assertEqual(lead["email_from"], "jane@example.com")
        self.assertIn("SR-2026-000007", lead["name"])
        self.assertIn("appui.entreprises@ccifj.or.jp", lead["description"])

    def test_an_odoo_outage_is_logged_and_never_raised(self):
        from concierge.integrations import odoo
        with patch.dict(os.environ, self.ODOO), patch.object(odoo, "_proxy", side_effect=OSError("down")), \
                self.assertLogs("concierge.integrations.odoo", level="ERROR"):
            self.assertIsNone(odoo.push_staff_request(self.REQUEST))


if __name__ == "__main__":
    unittest.main()
