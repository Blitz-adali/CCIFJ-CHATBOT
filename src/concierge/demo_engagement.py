"""Demo data for onboarding and engagement: seven fake members with different stories.

    uv run python -m concierge.demo_engagement            # create or refresh the personas
    uv run python -m concierge.demo_engagement --reset    # remove every demo row

All personas use @demo.ccifj.test emails and "[Demo]" companies, so they are easy to spot
and to remove. Dates are relative to today, so the story is the same whenever you run it.
Logins (one shared generated password) are written to var/demo-accounts.txt (gitignored).
"""

import argparse
import asyncio
import secrets
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func
from sqlmodel import col, select

from concierge.auth.security import hash_password
from concierge.db.relationaldb.datetimes import japan_now
from concierge.db.relationaldb.models import (
    ActivityEvent, Belongs, ChatSession, IntroRequest, Member, MemberProfile, Message, Outreach, StaffRequest,
    StaffRequestRecipient, User, UserAuth,
)
from concierge.db.relationaldb.session import AsyncSessionLocal, create_db_and_tables

DOMAIN = "demo.ccifj.test"
PREFIX = "[Demo] "
ACCOUNTS_FILE = Path(__file__).resolve().parents[2] / "var" / "demo-accounts.txt"

# name, company, sector, description, joined days ago (None: long-standing), last activity days ago,
# goals, interests, profile completed
PERSONAS = (
    ("Aiko Tanaka", "Sakura Patisserie KK", "Food & Beverage",
     "Tokyo pastry maker looking for French ingredient suppliers and retail partners.", 0, None, None, None, False),
    ("Pierre Martin", "Vins de Loire Japan", "Wine and Spirits",
     "Imports Loire Valley wines and is looking for restaurant and retail distributors in Japan.",
     7, 1, "find_clients,network", "wine import, distribution to restaurants and retailers", True),
    ("Sato Hiroshi", "Kansai Robotics", "Manufacturing",
     "Industrial robotics integrator in Osaka working with French automotive suppliers.",
     14, 3, "network,find_clients", "industrial automation, automotive, Kansai network", True),
    ("Claire Dubois", "Atelier Lumière", "Luxury",
     "French lighting design studio with a Tokyo showroom.", None, 6, "visibility", "design, luxury retail", True),
    ("Kenji Mori", "Mori Logistics", "Logistics, Shipping, Freight Forwarding & Import/Export",
     "Freight forwarder for French food and beverage imports.", None, 65, None, None, False),
    ("Marc Lefèvre", "Lefèvre Conseil", "Consulting",
     "Strategy consultancy helping French SMEs enter the Japanese market.", None, 95, "find_clients", "market entry consulting", True),
    ("Yuki Ishida", "Talent Bridge Japan", "Human Resources & Recruitment",
     "Bilingual recruitment agency placing French-speaking staff in Japan.",
     None, 4, "recruit,network", "HR, recruitment, salaries and hiring trends", True),
)


async def reset(session) -> int:
    users = list((await session.exec(select(User.user_id).where(col(User.email).like(f"%@{DOMAIN}")))).all())
    members = list((await session.exec(select(Member.member_id).where(col(Member.name).like(f"{PREFIX}%")))).all())
    if users:
        sessions = list((await session.exec(select(ChatSession.session_id).where(col(ChatSession.user_id).in_(users)))).all())
        if sessions:
            await session.exec(delete(Message).where(col(Message.session_id).in_(sessions)))
            await session.exec(delete(ChatSession).where(col(ChatSession.session_id).in_(sessions)))
        # Staff tasks the automation created for demo members (e.g. "Re-engagement call").
        tasks = [t for t in (await session.exec(select(Outreach.staff_request_id).where(
            col(Outreach.user_id).in_(users), col(Outreach.staff_request_id).is_not(None)))).all()]
        await session.exec(delete(Outreach).where(col(Outreach.user_id).in_(users)))
        if tasks:
            await session.exec(delete(StaffRequestRecipient).where(col(StaffRequestRecipient.request_id).in_(tasks)))
            await session.exec(delete(StaffRequest).where(col(StaffRequest.request_id).in_(tasks)))
        for model, column in ((ActivityEvent, ActivityEvent.user_id),
                              (MemberProfile, MemberProfile.user_id), (IntroRequest, IntroRequest.requester_user_id),
                              (Belongs, Belongs.user_id), (UserAuth, UserAuth.user_id)):
            await session.exec(delete(model).where(col(column).in_(users)))
        await session.exec(delete(User).where(col(User.user_id).in_(users)))
    if members:
        await session.exec(delete(IntroRequest).where(col(IntroRequest.target_member_id).in_(members)))
        await session.exec(delete(Belongs).where(col(Belongs.member_id).in_(members)))
        await session.exec(delete(Member).where(col(Member.member_id).in_(members)))
    await session.commit()
    return len(users)


async def seed() -> None:
    await create_db_and_tables()
    password = secrets.token_urlsafe(9)
    password_hash = await asyncio.to_thread(hash_password, password)
    now = japan_now().replace(microsecond=0)
    lines = ["Demo member accounts (fake data, created by concierge.demo_engagement)", f"password for all: {password}", ""]
    async with AsyncSessionLocal() as session:
        await reset(session)
        for name, company, sector, description, joined, active, goals, interests, completed in PERSONAS:
            email = name.lower().replace(" ", ".").replace("è", "e") + f"@{DOMAIN}"
            # Long-standing members were created a year ago; the newest ones on their join day.
            created = now - timedelta(days=joined if joined is not None else 400)
            user = User(name=name, email=email, created_at=created)
            member = Member(name=PREFIX + company, sector=sector, country="Japan", region="Tokyo", description=description)
            session.add_all([user, member])
            await session.flush()
            session.add(UserAuth(user_id=user.user_id, password_hash=password_hash))
            session.add(Belongs(user_id=user.user_id, member_id=member.member_id))
            session.add(MemberProfile(
                user_id=user.user_id, goals=goals, interests=interests,
                joined_at=now - timedelta(days=joined) if joined is not None else None,
                # New members completed it the day after joining; long-standing ones a year ago, so it
                # never counts as recent activity.
                profile_completed_at=(now - timedelta(days=max(joined - 1, 0) if joined is not None else 380))
                if completed else None))
            if active is not None:
                session.add(ActivityEvent(user_id=user.user_id, kind="chat", created_at=now - timedelta(days=active)))
            story = (f"joined {joined} day(s) ago" if joined is not None else "long-standing member") + (
                f", last active {active} day(s) ago" if active is not None else ", no activity yet")
            lines.append(f"{email:40} {company:28} {story}")
        await session.commit()
        total = (await session.exec(select(func.count()).select_from(User).where(col(User.email).like(f"%@{DOMAIN}")))).one()
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Created {total} demo members. Logins: {ACCOUNTS_FILE}")


async def remove() -> None:
    async with AsyncSessionLocal() as session:
        print(f"Removed {await reset(session)} demo members.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="remove the demo members and everything they created")
    asyncio.run(remove() if parser.parse_args().reset else seed())


if __name__ == "__main__":
    main()
