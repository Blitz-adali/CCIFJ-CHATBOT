"""Check the Odoo connector against a real Odoo database: insert, retrieve, update, delete.

Set ODOO_URL, ODOO_DB, ODOO_USER and ODOO_API_KEY (in .env or the shell), then:

    uv run python demo/odoo_check.py

It creates ONE test lead named "[Concierge SR-TEST-...] Connector check", reads it
back, changes it, archives it, and deletes it for good. Use a test database.
"""
import sys
import time

from dotenv import load_dotenv

load_dotenv()
from concierge.integrations import odoo  # noqa: E402

if not odoo.enabled():
    sys.exit("Odoo is not configured: set ODOO_URL, ODOO_DB, ODOO_USER and ODOO_API_KEY.")

reference = f"SR-TEST-{int(time.time())}"
request = {
    "reference": reference, "request_type": "team_ticket", "category": "other",
    "subject": "Connector check", "message": "Test lead created by demo/odoo_check.py.\nSafe to delete.",
    "sender_name": "Concierge Test", "sender_email": "concierge-test@example.com", "recipient_emails": [],
}
steps = [
    ("insert", lambda: odoo.create_lead(request)),
    ("retrieve", lambda: (odoo.find_lead(reference) or {}).get("name")),
    ("update", lambda: odoo.update_lead(reference, {"contact_name": "Concierge Test (updated)"})),
    ("retrieve after update", lambda: (odoo.find_lead(reference) or {}).get("contact_name")),
    ("archive", lambda: odoo.delete_lead(reference)),
    ("delete for good", lambda: odoo.delete_lead(reference, permanent=True)),
    ("gone", lambda: odoo.find_lead(reference) is None),
]
failed = False
for name, step in steps:
    try:
        result = step()
        ok = bool(result)
        print(f"{'ok  ' if ok else 'FAIL'}  {name}: {result}")
        failed = failed or not ok
    except Exception as error:  # show Odoo's own message
        print(f"FAIL  {name}: {type(error).__name__}: {error}")
        failed = True
        break
print("\nAll four operations work on this Odoo." if not failed else "\nThe connector needs adjusting for this Odoo; send the lines above.")
sys.exit(1 if failed else 0)
