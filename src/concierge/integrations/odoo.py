"""Send approved staff requests to Odoo CRM as leads (optional, off by default).

CCIFJ uses Odoo as its CRM. When the four variables below are set, every staff
request a visitor approves in the chat is also created in Odoo as a ``crm.lead``
through Odoo's standard external API (XML-RPC), so the team follows it up where
it already works. The request is always stored in the concierge database first;
Odoo being slow or down never loses a request or fails the user's answer.

    ODOO_URL        e.g. https://ccifj.odoo.com
    ODOO_DB         database name
    ODOO_USER       login of a technical user allowed to create leads
    ODOO_API_KEY    that user's API key (Odoo: Preferences > Account Security)
    ODOO_TIMEOUT    seconds, default 10

Status: written against Odoo's documented API and covered by a test with a
simulated server. It has not been run against CCIFJ's Odoo instance yet.
"""

import logging
import os
import xmlrpc.client
from typing import Optional

logger = logging.getLogger(__name__)

_REQUIRED = ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")


def _settings() -> Optional[dict]:
    values = {name: os.getenv(name, "").strip() for name in _REQUIRED}
    return values if all(values.values()) else None


def enabled() -> bool:
    return _settings() is not None


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


def lead_values(request: dict) -> dict:
    """The Odoo lead for one staff request. Only standard ``crm.lead`` fields are used."""
    recipients = ", ".join(request.get("recipient_emails") or []) or "CCIFJ team"
    description = (
        f"{request['message']}\n\n"
        "---\n"
        f"Received through the CCIFJ website concierge. Reference: {request['reference']}\n"
        f"Type: {request['request_type']} | Category: {request['category']} | Addressed to: {recipients}"
    )
    return {
        "name": f"[Concierge {request['reference']}] {request['subject']}"[:200],
        "contact_name": request["sender_name"],
        "email_from": request["sender_email"],
        "description": description,
    }


def push_staff_request(request: dict) -> Optional[int]:
    """Create the lead and return its Odoo id, or None when Odoo is off or the call fails."""
    settings = _settings()
    if settings is None:
        return None
    try:
        timeout = float(os.getenv("ODOO_TIMEOUT", "10") or 10)
        common = _proxy(settings["ODOO_URL"], "/xmlrpc/2/common", timeout)
        uid = common.authenticate(settings["ODOO_DB"], settings["ODOO_USER"], settings["ODOO_API_KEY"], {})
        if not uid:
            logger.error("Odoo refused the login for %s; staff request %s stays in the concierge database only",
                         settings["ODOO_USER"], request.get("reference"))
            return None
        models = _proxy(settings["ODOO_URL"], "/xmlrpc/2/object", timeout)
        lead_id = models.execute_kw(
            settings["ODOO_DB"], uid, settings["ODOO_API_KEY"], "crm.lead", "create", [lead_values(request)]
        )
        logger.info("Staff request %s created in Odoo as lead %s", request.get("reference"), lead_id)
        return int(lead_id)
    except Exception:
        # Never lose the request or fail the answer because of the CRM.
        logger.exception("Could not send staff request %s to Odoo; it stays in the concierge database",
                         request.get("reference"))
        return None
