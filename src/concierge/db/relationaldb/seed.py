"""Demo accounts for trying personalized answers. Safe to run more than once.

Creates two member accounts (linked to member companies through ``belongs``), one
non-member account and one staff account for the admin dashboard. Existing users,
links, bookings, and subscriptions are left as they are.

These accounts share a published password: they are for demos and reviewers only.
Delete them (dashboard: Accounts & roles) before the agent is opened to the public.
"""

import asyncio

from sqlmodel import col, select

from concierge.auth.security import hash_password
from . import crud
from .models import Belongs, Booking, BookingStatus, Event, Member, SubscriptionType, UserRole
from .session import AsyncSessionLocal, create_db_and_tables

DEMO_PASSWORD = "ccifj-demo-2026"

# (name, email, member tier or None for a non-member)
DEMO_USERS = [
    ("Claire Dubois", "claire.member@demo.ccifj.test", "Active"),
    ("Kenji Sato", "kenji.member@demo.ccifj.test", "Startup"),
    ("Lucas Bernard", "lucas.guest@demo.ccifj.test", None),
]
# Opens the staff dashboard: requests, conversations, content, matching, automation.
DEMO_STAFF = ("Demo Staff", "staff@demo.ccifj.test")


async def _demo_user(session, name, email):
    user = await crud.get_user_by_email(session, email)
    if user is None:
        user = await crud.register_user(session, name, email, hash_password(DEMO_PASSWORD))
    return user


async def _link_member(session, user, tier):
    if (await session.exec(select(Belongs).where(Belongs.user_id == user.user_id))).first():
        return
    category = await crud.get_member_category(session, tier)
    # Prefer a company already in this tier; otherwise give an untiered company the tier.
    member = (await session.exec(
        select(Member).where(Member.category_id == category.category_id).order_by(col(Member.member_id))
    )).first()
    if member is None:
        member = (await session.exec(
            select(Member).where(col(Member.category_id).is_(None)).order_by(col(Member.member_id))
        )).first()
        if member is not None:
            member.category_id = category.category_id
            session.add(member)
        else:
            # Every company already has another tier (e.g. the organisers' directory has no
            # Startup company): link to a company no demo user has yet and keep its own tier.
            linked = select(Belongs.member_id)
            member = (await session.exec(
                select(Member).where(col(Member.member_id).not_in(linked)).order_by(col(Member.member_id))
            )).first()
            if member is None:
                raise RuntimeError("No member companies are available to link demo users to.")
    session.add(Belongs(user_id=user.user_id, member_id=member.member_id))
    await session.commit()


async def _book_next_event(session, user):
    booked = (await session.exec(
        select(Booking).where(Booking.user_id == user.user_id, Booking.status == BookingStatus.confirmed)
    )).first()
    if booked:
        return
    upcoming = await session.exec(
        select(Event).where(col(Event.start_at) >= crud._now(), col(Event.cancelled).is_(False)).order_by(col(Event.start_at))
    )
    for event in upcoming.all():
        try:
            await crud.book_event(session, user.user_id, event.event_id, [{"name": user.name, "email": user.email}])
            return
        except ValueError:
            continue  # registration closed; try the next event


async def seed_demo() -> list[tuple[str, str]]:
    await create_db_and_tables()
    created = []
    async with AsyncSessionLocal() as session:
        for index, (name, email, tier) in enumerate(DEMO_USERS):
            user = await _demo_user(session, name, email)
            if tier is not None:
                await _link_member(session, user, tier)
                if index == 0:  # the first member also has a booking and the newsletter
                    await _book_next_event(session, user)
                    await crud.add_subscription(session, user.email, SubscriptionType.newsletter)
            created.append((email, "member" if tier else "non-member"))
        staff = await _demo_user(session, *DEMO_STAFF)
        if staff.role != UserRole.staff:
            staff.role = UserRole.staff
            session.add(staff)
            await session.commit()
        created.append((DEMO_STAFF[1], "staff"))
    return created


def main() -> None:
    for email, role in asyncio.run(seed_demo()):
        print(f"{email} ({role})")
    print(f"Password for all demo accounts: {DEMO_PASSWORD}")
