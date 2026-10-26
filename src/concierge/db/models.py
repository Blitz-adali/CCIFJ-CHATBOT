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

class SubscriptionType(str, Enum):
    newsletter = "newsletter"
    studio_newsletter = "studio_newsletter"
    training = "training"


class ReservationStatus(str, Enum):
    confirmed = "confirmed"
    cancelled = "cancelled"


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
    mobile: Optional[str] = Field(default=None, max_length=30)
    address: Optional[str] = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


class UserAuth(SQLModel, table=True):
    __tablename__ = "user_auth"

    user_id: int = Field(foreign_key="user.user_id", primary_key=True)
    password_hash: str = Field(max_length=255)
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
    tool_calls: Optional[list] = Field(default=None, sa_column=Column(JSON))
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
    currency: str = Field(default="JPY", max_length=3)  # ISO code: "JPY", "EUR"
    reg_deadline: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


class EventLanguage(SQLModel, table=True):
    __tablename__ = "event_language"

    event_id: int = Field(foreign_key="event.event_id", primary_key=True)
    language: str = Field(max_length=30, primary_key=True)


class Booking(SQLModel, table=True):
    __tablename__ = "booking"

    booking_id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.user_id")
    event_id: int = Field(foreign_key="event.event_id", index=True)
    status: BookingStatus = Field(default=BookingStatus.confirmed)
    registered_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


class Ticket(SQLModel, table=True):
    __tablename__ = "ticket"

    ticket_id:  Optional[int] = Field(default=None, primary_key=True)
    booking_id: int = Field(foreign_key="booking.booking_id")
    name: str = Field(max_length=100)
    email: str = Field(max_length=255)
    telephone: Optional[str] = Field(default=None, max_length=30)


# ---------- Members ----------

class Member(SQLModel, table=True):
    __tablename__ = "member"

    member_id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=255)
    category_id: int = Field(foreign_key="member_category.category_id", index=True)
    continent: str = Field(max_length=50, index=True)
    country: str = Field(max_length=100, index=True)
    region: str = Field(max_length=255, index=True)
    sector: str = Field(max_length=100, index=True)
    manpower: Optional[int] = Field(default=None)
    description: str
    contact: Optional[str] = Field(default=None, max_length=255)
    established_at: Optional[date] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


class MemberCategory(SQLModel, table=True):
    __tablename__ = "member_category"

    category_id: Optional[int] = Field(default=None, primary_key=True)
    category_name: str = Field(max_length=255, unique=True)
    category_description: str
    annual_price: Decimal = Field(max_digits=10, decimal_places=2)
    currency: str = Field(default="JPY", max_length=3)  # ISO code: "JPY", "EUR"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


class Belongs(SQLModel, table=True):
    __tablename__ = "belongs"

    user_id: int = Field(foreign_key="user.user_id", primary_key=True)
    member_id: int = Field(foreign_key="member.member_id", primary_key=True, index=True)


class Speaker(SQLModel, table=True):
    __tablename__ = "speaker"

    speaker_id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=100)
    role: str = Field(max_length=255)
    biography: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


class SpeaksAt(SQLModel, table=True):
    __tablename__ = "speaks_at"

    speaker_id: int = Field(foreign_key="speaker.speaker_id", primary_key=True)
    event_id: int = Field(foreign_key="event.event_id", primary_key=True, index=True)


# ---------- Subscriptions & Contact Us ----------


class Subscription(SQLModel, table=True):
    __tablename__ = "subscription"

    user_email: str = Field(max_length=255, primary_key=True, index=True)
    sub_type: SubscriptionType = Field(primary_key=True, index=True)


class ContactUs(SQLModel, table=True):
    __tablename__ = "contact_us"

    contact_id: Optional[int] = Field(default=None, primary_key=True)
    name: Optional[str] = Field(default=None, max_length=100)
    role: Optional[str] = Field(default=None, max_length=50)
    mobile: Optional[str] = Field(default=None, max_length=30)
    email: str = Field(max_length=255)
    category: str = Field(max_length=100)
    sub_category: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


# ---------- Spaces ----------


class Space(SQLModel, table=True):
    __tablename__ = "space"

    space_id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=100, unique=True)
    description: Optional[str] = Field(default=None)
    capacity: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()


class SpaceReservation(SQLModel, table=True):
    __tablename__ = "space_reservation"

    reservation_id: Optional[int] = Field(default=None, primary_key=True)
    space_id: int = Field(foreign_key="space.space_id", index=True)
    user_id: int = Field(foreign_key="user.user_id", index=True)
    event_id: Optional[int] = Field(default=None, foreign_key="event.event_id")
    title: str = Field(max_length=255)
    is_public: bool = Field(default=False)
    expected_attendees: Optional[int] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    start_at: datetime = Field(index=True)
    end_at: datetime
    status: ReservationStatus = Field(default=ReservationStatus.confirmed)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = updated_at_field()
