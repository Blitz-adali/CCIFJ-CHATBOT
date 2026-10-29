from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.db.relationaldb.models import User
from concierge.db.relationaldb.session import get_session
from .security import read_token

DatabaseSession = Annotated[AsyncSession, Depends(get_session)]
bearer = HTTPBearer(auto_error=False)


async def optional_user(
    request: Request,
    session: DatabaseSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User | None:
    if credentials is None and "authorization" not in request.headers:
        return None
    try:
        if credentials is None:
            raise ValueError("Invalid authorization header")
        user_id = read_token(credentials.credentials, "access")
        user = await session.get(User, user_id)
        if user is None:
            raise ValueError("Unknown account")
        return user
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail="Invalid or expired bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUser = Annotated[User | None, Depends(optional_user)]
