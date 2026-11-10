"""Staff requests in Odoo CRM as leads: create, read, update, delete (optional, off by default).

CCIFJ uses Odoo as its CRM. When the four variables below are set, every staff
request a visitor approves in the chat is also created in Odoo as a
``crm.lead``, so the team follows it up where it already works. The request is
always stored in the concierge database first; Odoo being slow or down never
loses a request or fails the user's answer.

    ODOO_URL        e.g. https://ccifj.odoo.com
    ODOO_DB         database name
    ODOO_USER       login of a technical user allowed to create leads (XML-RPC only)
    ODOO_API_KEY    that user's API key (Odoo: Preferences > Account Security)
    ODOO_TIMEOUT    seconds, default 10

Two Odoo APIs, chosen automatically:

- **JSON-2** (``POST /json/2/<model>/<method>``, bearer API key): Odoo 19 and
  later. It is tried first.
- **XML-RPC** (``/xmlrpc/2/...``): Odoo 18 and earlier. Odoo 20 no longer
  serves it (checked on 10 November 2026: a 20.0 demo database answers 404 on
  ``/xmlrpc/2/common`` and ``/jsonrpc``), so it is only the fallback when the
  server has no ``/json/2`` route.

Status: the JSON-2 route and its bearer-key authentication were confirmed
against a public Odoo 20.0 demo server (it answers 401 "Invalid apikey" to a
wrong key). No call was made with a valid key, so the four operations below
are covered by tests with simulated servers only and have NOT run against a
real Odoo database. Try them on a CCIFJ test database before production.
"""

import html
import json
import logging
import os
import urllib.error
import urllib.request
import xmlrpc.client
from typing import Any, Optional

logger = logging.getLogger(__name__)

_REQUIRED = ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")
MODEL = "crm.lead"
LEAD_FIELDS = ["id", "name", "contact_name", "email_from", "description", "active", "stage_id", "create_date"]


class OdooError(RuntimeError):
    """Odoo refused or could not complete a call."""


class _NoJson2(Exception):
    """The server has no /json/2 route (Odoo 18 or earlier)."""


def _settings() -> Optional[dict]:
    values = {name: os.getenv(name, "").strip() for name in _REQUIRED}
    return values if all(values.values()) else None


def enabled() -> bool:
    return _settings() is not None


def _timeout() -> float:
    try:
        return float(os.getenv("ODOO_TIMEOUT", "10") or 10)
    except ValueError:
        return 10.0


# --- JSON-2 (Odoo 19+) -------------------------------------------------------

def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors="replace")
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, raw[:300]


def _json2(settings: dict, method: str, payload: dict) -> Any:
    status, body = _post_json(
        f"{settings['ODOO_URL'].rstrip('/')}/json/2/{MODEL}/{method}", payload,
        {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"bearer {settings['ODOO_API_KEY']}",
            "X-Odoo-Database": settings["ODOO_DB"],
        },
        _timeout(),
    )
    if status == 404 and not (isinstance(body, dict) and body.get("name")):
        # An HTML "not found" page: the route itself does not exist on this server.
        raise _NoJson2()
    if status != 200:
        message = body.get("message") if isinstance(body, dict) else body
        raise OdooError(f"Odoo answered HTTP {status}: {message}")
    return body


# --- XML-RPC (Odoo 18 and earlier) -------------------------------------------

class _TimeoutTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: float):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


def _proxy(url: str, path: str, timeout: float) -> xmlrpc.client.ServerProxy:
    transport = _TimeoutTransport(timeout) if url.startswith("https://") else None
    return xmlrpc.client.ServerProxy(f"{url.rstrip('/')}{path}", transport=transport, allow_none=True)


def _xmlrpc(settings: dict, method: str, args: list, kwargs: Optional[dict] = None) -> Any:
    timeout = _timeout()
    try:
        common = _proxy(settings["ODOO_URL"], "/xmlrpc/2/common", timeout)
        uid = common.authenticate(settings["ODOO_DB"], settings["ODOO_USER"], settings["ODOO_API_KEY"], {})
        if not uid:
            raise OdooError(f"Odoo refused the login for {settings['ODOO_USER']}.")
        models = _proxy(settings["ODOO_URL"], "/xmlrpc/2/object", timeout)
        return models.execute_kw(settings["ODOO_DB"], uid, settings["ODOO_API_KEY"], MODEL, method, args, kwargs or {})
    except xmlrpc.client.Fault as fault:
        raise OdooError(f"Odoo refused the call: {fault.faultString[:300]}") from fault
    except xmlrpc.client.ProtocolError as error:
        raise OdooError(f"Odoo answered HTTP {error.errcode} on its XML-RPC route (Odoo 20 removed it).") from error


def _call(method: str, *, json2: dict, xmlrpc_args: list, xmlrpc_kwargs: Optional[dict] = None) -> Any:
    """One ORM call, through JSON-2 when the server has it, else XML-RPC. The two
    APIs take the same methods; JSON-2 names every argument, XML-RPC takes them in order."""
    settings = _settings()
    if settings is None:
        raise OdooError("Odoo is not configured.")
    try:
        return _json2(settings, method, json2)
    except _NoJson2:
        return _xmlrpc(settings, method, xmlrpc_args, xmlrpc_kwargs)


# --- The four operations -----------------------------------------------------

def _marker(reference: str) -> str:
    return f"[Concierge {reference}]"


def lead_values(request: dict) -> dict:
    """The Odoo lead for one staff request. Only standard ``crm.lead`` fields are used.

    ``description`` is an HTML field in Odoo 15 and later, so line breaks are
    sent as ``<br>`` and the text is escaped.
    """
    recipients = ", ".join(request.get("recipient_emails") or []) or "CCIFJ team"
    lines = [
        *str(request["message"]).splitlines(), "", "---",
        f"Received through the CCIFJ website concierge. Reference: {request['reference']}",
        f"Type: {request['request_type']} | Category: {request['category']} | Addressed to: {recipients}",
    ]
    return {
        "name": f"{_marker(request['reference'])} {request['subject']}"[:200],
        "contact_name": request["sender_name"],
        "email_from": request["sender_email"],
        "description": "<br>".join(html.escape(line) for line in lines),
    }


def create_lead(request: dict) -> int:
    """Insert: create the lead and return its Odoo id."""
    values = lead_values(request)
    created = _call("create", json2={"vals_list": [values]}, xmlrpc_args=[[values]])
    lead_id = created[0] if isinstance(created, list) else created
    return int(lead_id)


def find_lead(reference: str, include_archived: bool = True) -> Optional[dict]:
    """Retrieve: the lead created for a concierge reference, or None."""
    domain: list = [["name", "like", _marker(reference)]]
    if include_archived:
        domain.append(["active", "in", [True, False]])
    found = _call(
        "search_read",
        json2={"domain": domain, "fields": LEAD_FIELDS, "limit": 1},
        xmlrpc_args=[domain], xmlrpc_kwargs={"fields": LEAD_FIELDS, "limit": 1},
    )
    return found[0] if found else None


def update_lead(reference: str, values: dict) -> bool:
    """Update: write ``values`` (standard ``crm.lead`` fields) on the lead of a reference."""
    lead = find_lead(reference)
    if lead is None:
        return False
    return bool(_call("write", json2={"ids": [lead["id"]], "vals": values}, xmlrpc_args=[[lead["id"]], values]))


def delete_lead(reference: str, permanent: bool = False) -> bool:
    """Delete: archive the lead (the default, reversible in Odoo) or remove it for good."""
    lead = find_lead(reference)
    if lead is None:
        return False
    if not permanent:
        return bool(_call("write", json2={"ids": [lead["id"]], "vals": {"active": False}},
                          xmlrpc_args=[[lead["id"]], {"active": False}]))
    return bool(_call("unlink", json2={"ids": [lead["id"]]}, xmlrpc_args=[[lead["id"]]]))


def push_staff_request(request: dict) -> Optional[int]:
    """Create the lead for an approved staff request. Returns its Odoo id, or None when
    Odoo is off or the call fails: the chat never fails because of the CRM."""
    if not enabled():
        return None
    try:
        lead_id = create_lead(request)
        logger.info("Staff request %s created in Odoo as lead %s", request.get("reference"), lead_id)
        return lead_id
    except Exception:
        logger.exception("Could not send staff request %s to Odoo; it stays in the concierge database",
                         request.get("reference"))
        return None
