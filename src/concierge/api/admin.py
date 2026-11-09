"""Admin dashboard API. Every route needs a staff account (see concierge.auth.access)."""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_
from sqlmodel import col, select

from concierge.auth.access import StaffUser, access_level, require_staff
from concierge.auth.dependencies import DatabaseSession
from concierge.db.relationaldb.models import (
    Belongs, ContactUs, Event, IntroRequest, IntroStatus, Member, StaffRequest, StaffRequestStatus, User, UserRole,
    utcnow,
)
from concierge.db.relationaldb.datetimes import japan_now
from concierge.staff_directory import TOPICS, split_topics

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_staff)])


@router.get("/overview")
async def overview(session: DatabaseSession, response: Response):
    async def count(query) -> int:
        return (await session.exec(query)).one()

    open_statuses = [StaffRequestStatus.new, StaffRequestStatus.in_review]
    contacts = (await session.exec(select(ContactUs).where(ContactUs.is_active == True))).all()  # noqa: E712
    covered = {topic for contact in contacts for topic in split_topics(contact.topics)}
    response.headers["Cache-Control"] = "no-store"
    return {
        "users": await count(select(func.count()).select_from(User)),
        "members": await count(select(func.count(func.distinct(Belongs.user_id)))),
        "staff": await count(select(func.count()).select_from(User).where(User.role == UserRole.staff)),
        "member_companies": await count(select(func.count()).select_from(Member)),
        "open_requests": await count(select(func.count()).select_from(StaffRequest)
                                     .where(col(StaffRequest.status).in_(open_statuses))),
        "new_requests": await count(select(func.count()).select_from(StaffRequest)
                                    .where(StaffRequest.status == StaffRequestStatus.new)),
        # Event times are stored in Japan time.
        "upcoming_events": await count(select(func.count()).select_from(Event).where(Event.start_at >= japan_now())),
        "new_intro_requests": await count(select(func.count()).select_from(IntroRequest)
                                          .where(IntroRequest.status == IntroStatus.new)),
        "active_contacts": len(contacts),
        "has_triage_contact": any(contact.is_triage for contact in contacts),
        "uncovered_topics": [key for key in TOPICS if key != "general_or_unsure" and key not in covered],
    }


# ---------- Accounts and roles ----------

class RoleUpdate(BaseModel):
    role: UserRole


@router.get("/users")
async def list_users(
    session: DatabaseSession, response: Response,
    q: Annotated[str, Query(max_length=100)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    query = select(User)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(User.name).like(like), func.lower(User.email).like(like)))
    rows = list((await session.exec(query.order_by(col(User.user_id)).offset(offset).limit(limit + 1))).all())
    visible = rows[:limit]
    companies: dict[int, list[str]] = {}
    if visible:
        links = await session.exec(
            select(Belongs.user_id, Member.name).join(Member, Member.member_id == Belongs.member_id)
            .where(col(Belongs.user_id).in_([user.user_id for user in visible]))
        )
        for user_id, company in links.all():
            companies.setdefault(user_id, []).append(company)
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [{
            "user_id": user.user_id, "name": user.name, "email": user.email, "role": user.role,
            "access_level": (await access_level(session, user)).value,
            "companies": sorted(companies.get(user.user_id, [])),
            "created_at": user.created_at.isoformat() + "Z",
        } for user in visible],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


@router.patch("/users/{user_id}")
async def update_user_role(user_id: int, body: RoleUpdate, session: DatabaseSession, staff: StaffUser):
    if user_id == staff.user_id and body.role != UserRole.staff:
        raise HTTPException(status_code=400, detail="You cannot remove your own staff access.")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    user.role = body.role
    session.add(user)
    await session.commit()
    return {"user_id": user_id, "role": user.role, "access_level": (await access_level(session, user)).value}


# ---------- Staff directory (who the agent routes requests to) ----------

class ContactFields(BaseModel):
    name: Optional[str] = Field(default=None, max_length=100)
    role: Optional[str] = Field(default=None, max_length=50)
    mobile: Optional[str] = Field(default=None, max_length=30)
    category: Optional[str] = Field(default=None, max_length=100)
    sub_category: Optional[str] = Field(default=None, max_length=255)
    topics: Optional[list[str]] = None
    languages: Optional[str] = Field(default=None, max_length=50)
    is_triage: Optional[bool] = None
    is_active: Optional[bool] = None


def _check_email(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip().casefold()
    local, _, domain = value.partition("@")
    if not local or "." not in domain or "@" in domain or any(ch.isspace() for ch in value):
        raise ValueError("Invalid email address.")
    return value


class ContactCreate(ContactFields):
    email: str = Field(min_length=3, max_length=255)
    category: str = Field(min_length=1, max_length=100)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return _check_email(value)


class ContactUpdate(ContactFields):
    email: Optional[str] = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: Optional[str]) -> Optional[str]:
        return _check_email(value)


def contact_json(contact: ContactUs) -> dict:
    return {
        "contact_id": contact.contact_id, "name": contact.name, "role": contact.role, "mobile": contact.mobile,
        "email": contact.email, "category": contact.category, "sub_category": contact.sub_category,
        "topics": split_topics(contact.topics), "languages": contact.languages,
        "is_triage": contact.is_triage, "is_active": contact.is_active,
    }


def apply_contact(contact: ContactUs, body: ContactFields) -> None:
    values = body.model_dump(exclude_unset=True)
    if "topics" in values:
        unknown = [topic for topic in values["topics"] or [] if topic not in TOPICS]
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown topics: {', '.join(unknown)}")
        values["topics"] = ",".join(dict.fromkeys(values["topics"] or [])) or None
    for field in ("name", "role", "mobile", "languages", "sub_category"):
        if field in values and isinstance(values[field], str):
            values[field] = values[field].strip() or None
    for field, value in values.items():
        if field in ("email", "category", "sub_category", "is_triage", "is_active") and value is None:
            continue  # required columns: "not sent" and "null" both mean "keep"
        setattr(contact, field, str(value) if field == "email" else value)
    contact.updated_at = utcnow()


@router.get("/topics")
async def topics():
    return [{"key": key, "label": label, "description": description} for key, (label, description) in TOPICS.items()]


@router.get("/staff-contacts")
async def list_contacts(session: DatabaseSession, response: Response):
    rows = (await session.exec(select(ContactUs).order_by(col(ContactUs.contact_id)))).all()
    response.headers["Cache-Control"] = "no-store"
    return [contact_json(row) for row in rows]


async def _email_taken(session, email: str, contact_id: int | None = None) -> bool:
    query = select(ContactUs.contact_id).where(func.lower(ContactUs.email) == email.lower())
    if contact_id is not None:
        query = query.where(ContactUs.contact_id != contact_id)
    return (await session.exec(query)).first() is not None


@router.post("/staff-contacts", status_code=201)
async def create_contact(body: ContactCreate, session: DatabaseSession):
    if await _email_taken(session, str(body.email)):
        raise HTTPException(status_code=409, detail="A contact with this email already exists.")
    contact = ContactUs(email=str(body.email), category=body.category.strip(), sub_category="")
    apply_contact(contact, body)
    session.add(contact)
    await session.commit()
    await session.refresh(contact)
    return contact_json(contact)


@router.patch("/staff-contacts/{contact_id}")
async def update_contact(contact_id: int, body: ContactUpdate, session: DatabaseSession):
    contact = await session.get(ContactUs, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found.")
    if body.email is not None and await _email_taken(session, str(body.email), contact_id):
        raise HTTPException(status_code=409, detail="A contact with this email already exists.")
    apply_contact(contact, body)
    session.add(contact)
    await session.commit()
    await session.refresh(contact)
    return contact_json(contact)
