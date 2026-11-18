"""Website side of account linking (see concierge.account_linking).

The website opens with ?link=<token>, the user logs in as usual, sees which chat account
the link belongs to, and confirms. Every route needs the normal login, so the link token
alone can do nothing.
"""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import Field

from concierge import account_linking as linking
from concierge import channels
from concierge.auth.dependencies import CurrentUser, DatabaseSession
from concierge.db.relationaldb.models import ChannelIdentity
from .schemas import RequestModel

router = APIRouter(prefix="/auth", tags=["auth"])


class LinkRequest(RequestModel):
    token: str = Field(min_length=20, max_length=200)
    locale: Literal["en", "fr", "ja"] = "en"


def _signed_in(user):
    if user is None:
        raise HTTPException(status_code=401, detail="Log in to link your account.", headers={"WWW-Authenticate": "Bearer"})
    return user


def _link_error(error: linking.LinkError) -> HTTPException:
    status = 409 if error.code == "already_linked" else 410
    return HTTPException(status_code=status, detail={"message": str(error), "error_code": error.code})


def _summary(identity: ChannelIdentity) -> dict:
    return {
        "link_id": identity.identity_id, "platform": identity.platform,
        "account_hint": linking.account_hint(identity), "linked_at": identity.linked_at,
    }


@router.post("/link/preview")
async def preview(body: LinkRequest, session: DatabaseSession, user: CurrentUser, response: Response):
    """Which chat account this link would connect. Does not use up the link."""
    _signed_in(user)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await linking.preview_link(session, body.token)
    except linking.LinkError as error:
        raise _link_error(error) from error


@router.post("/link/complete")
async def complete(
    body: LinkRequest, session: DatabaseSession, user: CurrentUser, response: Response, tasks: BackgroundTasks,
):
    """Use the link once: attach its chat account to the signed-in user, then tell the chat."""
    user = _signed_in(user)
    response.headers["Cache-Control"] = "no-store"
    try:
        identity = await linking.complete_link(session, body.token, user.user_id)
    except linking.LinkError as error:
        raise _link_error(error) from error
    tasks.add_task(channels.notify_linked, identity, user.name, body.locale)
    return _summary(identity)


@router.get("/links")
async def links(session: DatabaseSession, user: CurrentUser, response: Response):
    """Chat accounts linked to the signed-in user."""
    user = _signed_in(user)
    response.headers["Cache-Control"] = "no-store"
    return {"items": [_summary(identity) for identity in await linking.list_links(session, user.user_id)]}


@router.delete("/links/{link_id}", status_code=204)
async def remove_link(link_id: int, session: DatabaseSession, user: CurrentUser):
    """Disconnect a chat account, for example after losing the phone."""
    user = _signed_in(user)
    identity = await session.get(ChannelIdentity, link_id)
    if identity is None or identity.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="Link not found.")
    await linking.unlink(session, identity)
    return Response(status_code=204)
