"""One place that decides what an account may see: guest, user, member or staff.

- guest:  no login.
- user:   signed in, not linked to a member company.
- member: signed in and linked to a member company (a `belongs` row).
- staff:  `user.role == "staff"`, or listed in CCIFJ_STAFF_USER_IDS (to bootstrap the first staff account).

Staff is checked first, so a staff account that also belongs to a member company is staff.
"""

from enum import Enum
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.config import get_settings
from concierge.db.relationaldb.models import Belongs, User, UserRole
from .dependencies import CurrentUser


class AccessLevel(str, Enum):
    guest = "guest"
    user = "user"
    member = "member"
    staff = "staff"


def is_staff(user: User | None) -> bool:
    if user is None:
        return False
    return user.role == UserRole.staff or user.user_id in get_settings().staff_user_ids


async def is_member(session: AsyncSession, user: User | None) -> bool:
    if user is None or user.user_id is None:
        return False
    row = (await session.exec(select(Belongs.member_id).where(Belongs.user_id == user.user_id).limit(1))).first()
    return row is not None


async def access_level(session: AsyncSession, user: User | None) -> AccessLevel:
    if user is None:
        return AccessLevel.guest
    if is_staff(user):
        return AccessLevel.staff
    if await is_member(session, user):
        return AccessLevel.member
    return AccessLevel.user


async def require_staff(user: CurrentUser) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to access the staff dashboard.",
                            headers={"WWW-Authenticate": "Bearer"})
    if not is_staff(user):
        raise HTTPException(status_code=403, detail="Staff access required.")
    return user


StaffUser = Annotated[User, Depends(require_staff)]
