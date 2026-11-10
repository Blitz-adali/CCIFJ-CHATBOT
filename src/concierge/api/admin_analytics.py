"""Staff analytics: how the assistant is used and what it achieves (staff only)."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from concierge import analytics
from concierge.auth.access import require_staff
from concierge.auth.dependencies import DatabaseSession

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_staff)])


@router.get("/analytics")
async def usage(session: DatabaseSession, response: Response, days: Annotated[int, Query(ge=1, le=90)] = 14):
    response.headers["Cache-Control"] = "no-store"
    return await analytics.summary(session, days)


@router.get("/analytics/langsmith")
async def langsmith(response: Response, days: Annotated[int, Query(ge=1, le=30)] = 7):
    response.headers["Cache-Control"] = "no-store"
    # The SDK is synchronous and may call the network.
    return await asyncio.to_thread(analytics.langsmith_summary, days)
