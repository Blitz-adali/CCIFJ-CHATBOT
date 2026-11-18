"""Incoming WhatsApp (Meta Cloud API) and Slack (Events API) webhooks.

Every request is authenticated by the platform's signature over the raw body, then
acknowledged at once; the answer is sent from a background task (see concierge.channels).
A channel that is not configured answers 404.
"""

import hmac
import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from concierge import channels
from concierge.config import get_settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
MAX_BODY_BYTES = 1_000_000


async def _signed_body(request: Request) -> bytes:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Webhook body is too large.")
    return body


def _json(body: bytes) -> dict:
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON.")
    return payload


@router.get("/whatsapp", response_class=PlainTextResponse)
async def verify_whatsapp(
    mode: Annotated[str, Query(alias="hub.mode")] = "",
    token: Annotated[str, Query(alias="hub.verify_token")] = "",
    challenge: Annotated[str, Query(alias="hub.challenge")] = "",
):
    """Meta calls this once when the webhook URL is saved in the app settings."""
    settings = get_settings()
    if not settings.whatsapp_enabled:
        raise HTTPException(status_code=404, detail="WhatsApp is not configured.")
    if mode != "subscribe" or not hmac.compare_digest(token.encode(), settings.whatsapp_verify_token.encode()):
        raise HTTPException(status_code=403, detail="Verification failed.")
    return challenge


@router.post("/whatsapp")
async def whatsapp(request: Request, tasks: BackgroundTasks):
    if not get_settings().whatsapp_enabled:
        raise HTTPException(status_code=404, detail="WhatsApp is not configured.")
    body = await _signed_body(request)
    if not channels.valid_whatsapp_signature(body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=403, detail="Invalid signature.")
    for incoming in channels.whatsapp_messages(_json(body)):
        tasks.add_task(channels.handle, incoming)
    return {"status": "ok"}


@router.post("/slack")
async def slack(request: Request, tasks: BackgroundTasks):
    if not get_settings().slack_enabled:
        raise HTTPException(status_code=404, detail="Slack is not configured.")
    body = await _signed_body(request)
    if not channels.valid_slack_signature(
        body, request.headers.get("x-slack-request-timestamp"), request.headers.get("x-slack-signature"),
    ):
        raise HTTPException(status_code=403, detail="Invalid signature.")
    payload = _json(body)
    if payload.get("type") == "url_verification":
        # Slack checks the URL once when Event Subscriptions is turned on.
        return {"challenge": payload.get("challenge")}
    incoming = channels.slack_message(payload)
    if incoming is not None:
        tasks.add_task(channels.handle, incoming)
    return {"status": "ok"}
