from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def updated_at_field():
    return Field(default_factory=utcnow, sa_column_kwargs={"onupdate": utcnow})


# ---------- Fixed-value fields ----------

class TicketStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"


class BookingStatus(str, Enum):
    confirmed = "confirmed"
    cancelled = "cancelled"


class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"
    tool = "tool"


# ---------- Users ----------

class User(SQLModel, table=True):
    __tablename__ = "user"

    user_id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=100)
    email: str = Field(max_length=255, unique=True, index=True)
    mobile: str = Field(max_length=30)
    address: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


class UserAuth(SQLModel, table=True):
    __tablename__ = "user_auth"

    user_id: int = Field(foreign_key="user.user_id", primary_key=True)
    password_hash: str = Field(max_length=255)
    updated_at: datetime = updated_at_field()


class Staff(SQLModel, table=True):
    __tablename__ = "staff"

    staff_id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=100)
    role: str = Field(max_length=50)
    mobile: str = Field(max_length=30)
    email: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


# ---------- Chat ----------
# Named ChatSession so it doesn't clash with sqlmodel's Session.

class ChatSession(SQLModel, table=True):
    __tablename__ = "session"

    session_id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.user_id")  # None = anonymous
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


class Message(SQLModel, table=True):
    __tablename__ = "message"

    message_id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.session_id", index=True)
    role: MessageRole
    content: str
    tool_calls: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


class Ticket(SQLModel, table=True):
    __tablename__ = "ticket"

    ticket_id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.session_id")
    staff_id: Optional[int] = Field(default=None, foreign_key="staff.staff_id")
    status: TicketStatus = Field(default=TicketStatus.open)
    subject: str = Field(max_length=255)
    description: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


# ---------- Events ----------

class Event(SQLModel, table=True):
    __tablename__ = "event"

    event_id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(max_length=255)
    description: str
    category: str = Field(max_length=50, index=True)
    location: str = Field(max_length=255)
    contact: str = Field(max_length=255)
    start_at: datetime = Field(index=True)
    end_at: datetime
    price: Decimal = Field(default=Decimal("0.00"), max_digits=10, decimal_places=2)
    reg_deadline: datetime
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


class EventLanguage(SQLModel, table=True):
    __tablename__ = "event_language"

    event_id: int = Field(foreign_key="event.event_id", primary_key=True)
    language: str = Field(max_length=30, primary_key=True)


class Booking(SQLModel, table=True):
    __tablename__ = "booking"

    user_id: int = Field(foreign_key="user.user_id", primary_key=True)
    event_id: int = Field(foreign_key="event.event_id", primary_key=True)
    status: BookingStatus = Field(default=BookingStatus.confirmed)
    registered_at: datetime = Field(default_factory=utcnow)


# ---------- Members ----------

class Member(SQLModel, table=True):
    __tablename__ = "member"

    member_id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=255)
    continent: str = Field(max_length=50, index=True)
    country: str = Field(max_length=100, index=True)
    sector: str = Field(max_length=100, index=True)
    manpower: int
    description: str
    contact: str = Field(max_length=255)
    established_at: date
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


class Belongs(SQLModel, table=True):
    __tablename__ = "belongs"

    user_id: int = Field(foreign_key="user.user_id", primary_key=True)
    member_id: int = Field(foreign_key="member.member_id", primary_key=True)
