from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import Column, Index, JSON, UniqueConstraint, text
from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    # SQLite stores timestamps without offsets; use naive UTC consistently.
    return datetime.now(timezone.utc).replace(tzinfo=None)

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


class StaffRequestType(str, Enum):
    staff_email = "staff_email"  # addressed to known CCIFJ contacts
    team_ticket = "team_ticket"  # no contact exists; the CCIFJ team reviews it


class StaffRequestStatus(str, Enum):
    new = "new"
    in_review = "in_review"
    closed = "closed"


class UserRole(str, Enum):
    user = "user"  # signed-in account; "member" comes from a Belongs link, not from this field
    staff = "staff"  # can open the admin dashboard


class IntroStatus(str, Enum):
    new = "new"              # a member asked; staff have not acted yet
    proposed = "proposed"    # staff wrote to the other company
    introduced = "introduced"  # both sides agreed and were put in touch
    declined = "declined"
    closed = "closed"


class OutreachStatus(str, Enum):
    pending = "pending"    # waiting for staff approval
    sent = "sent"          # delivered (in-app) or written to the mock email/LINE outbox
    skipped = "skipped"    # staff decided not to send


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
    role: UserRole = Field(default=UserRole.user, sa_column_kwargs={"server_default": "user"})
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


class UserAuth(SQLModel, table=True):
    __tablename__ = "user_auth"

    user_id: int = Field(foreign_key="user.user_id", primary_key=True)
    password_hash: str = Field(max_length=255)
    updated_at: NaiveDatetime = updated_at_field()


# ---------- Chat ----------
# Named ChatSession so it doesn't clash with sqlmodel's Session.

class ChatSession(SQLModel, table=True):
    __tablename__ = "session"

    session_id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.user_id")  # None = anonymous
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


class Message(SQLModel, table=True):
    __tablename__ = "message"

    message_id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.session_id", index=True)
    role: MessageRole
    content: str
    tool_calls: Optional[list] = Field(default=None, sa_column=Column(JSON))
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


# ---------- Events ----------

class Event(SQLModel, table=True):
    __tablename__ = "event"

    event_id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(max_length=255)
    description: str
    category: str = Field(max_length=50, index=True)
    location: Optional[str] = Field(default=None, max_length=255)
    contact: Optional[str] = Field(default=None, max_length=255)
    start_at: Optional[NaiveDatetime] = Field(default=None, index=True)
    end_at: Optional[NaiveDatetime] = Field(default=None)
    price: Optional[Decimal] = Field(default=None, max_digits=10, decimal_places=2)
    member_price: Optional[Decimal] = Field(default=None, max_digits=10, decimal_places=2)
    non_member_price: Optional[Decimal] = Field(default=None, max_digits=10, decimal_places=2)
    currency: Optional[str] = Field(default=None, max_length=3)  # ISO code: "JPY", "EUR"; None when unknown
    reg_deadline: Optional[NaiveDatetime] = Field(default=None)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


class EventLanguage(SQLModel, table=True):
    __tablename__ = "event_language"

    event_id: int = Field(foreign_key="event.event_id", primary_key=True)
    language: str = Field(max_length=30, primary_key=True)


class Booking(SQLModel, table=True):
    __tablename__ = "booking"
    __table_args__ = (
        Index(
            "uq_booking_confirmed_user_event",
            "user_id",
            "event_id",
            unique=True,
            sqlite_where=text("status = 'confirmed'"),
        ),
    )

    booking_id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.user_id")
    event_id: int = Field(foreign_key="event.event_id", index=True)
    status: BookingStatus = Field(default=BookingStatus.confirmed)
    registered_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


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
    category_id: Optional[int] = Field(default=None, foreign_key="member_category.category_id", index=True)
    continent: Optional[str] = Field(default=None, max_length=50, index=True)
    country: Optional[str] = Field(default=None, max_length=100, index=True)
    region: Optional[str] = Field(default=None, max_length=255, index=True)
    sector: Optional[str] = Field(default=None, max_length=100, index=True)
    manpower: Optional[int] = Field(default=None)
    description: Optional[str] = Field(default=None)
    contact: Optional[str] = Field(default=None, max_length=255)
    established_at: Optional[date] = Field(default=None)
    # Directory export fields: other sectors the company works in (comma-separated), the
    # language the company prefers to be contacted in, and whether it is shown in the directory.
    secondary_sectors: Optional[str] = Field(default=None, max_length=255)
    language: Optional[str] = Field(default=None, max_length=30)
    directory_visible: bool = Field(default=True, sa_column_kwargs={"server_default": "1"})
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


class MemberContact(SQLModel, table=True):
    """A person at a member company. A company can list several, each with their own role
    and working language, so an introduction can go to the right person in the right language."""
    __tablename__ = "member_contact"

    contact_id: Optional[int] = Field(default=None, primary_key=True)
    member_id: int = Field(foreign_key="member.member_id", index=True)
    name: str = Field(max_length=150)
    salutation: Optional[str] = Field(default=None, max_length=20)
    job_title: Optional[str] = Field(default=None, max_length=150)
    email: Optional[str] = Field(default=None, max_length=255)
    language: Optional[str] = Field(default=None, max_length=30)  # e.g. "English", "Japanese", "French"
    directory_visible: bool = Field(default=True, sa_column_kwargs={"server_default": "1"})
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


class MemberCategory(SQLModel, table=True):
    __tablename__ = "member_category"

    category_id: Optional[int] = Field(default=None, primary_key=True)
    category_name: str = Field(max_length=255, unique=True)
    category_description: str
    annual_price: Decimal = Field(max_digits=10, decimal_places=2)
    currency: str = Field(default="JPY", max_length=3)  # ISO code: "JPY", "EUR"
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


class Belongs(SQLModel, table=True):
    __tablename__ = "belongs"

    user_id: int = Field(foreign_key="user.user_id", primary_key=True)
    member_id: int = Field(foreign_key="member.member_id", primary_key=True, index=True)


class Speaker(SQLModel, table=True):
    __tablename__ = "speaker"

    speaker_id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=100)
    role: Optional[str] = Field(default=None, max_length=255)
    biography: Optional[str] = Field(default=None)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


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
    # Staff directory fields: which agent topics this contact handles (comma-separated keys),
    # the languages they answer in, and whether they triage requests nobody else fits.
    topics: Optional[str] = Field(default=None, max_length=255)
    languages: Optional[str] = Field(default=None, max_length=50)
    is_triage: bool = Field(default=False, sa_column_kwargs={"server_default": "0"})
    is_active: bool = Field(default=True, sa_column_kwargs={"server_default": "1"})
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


# ---------- Spaces ----------


class Space(SQLModel, table=True):
    __tablename__ = "space"

    space_id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=100, unique=True)
    description: Optional[str] = Field(default=None)
    capacity: Optional[int] = Field(default=None)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


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
    start_at: NaiveDatetime = Field(index=True)
    end_at: NaiveDatetime
    status: ReservationStatus = Field(default=ReservationStatus.confirmed)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


# ---------- Staff requests ----------
# Stored for CCIFJ staff to read; nothing is emailed automatically.


class StaffRequest(SQLModel, table=True):
    __tablename__ = "staff_request"

    request_id: Optional[int] = Field(default=None, primary_key=True)
    reference: str = Field(max_length=32, unique=True)  # e.g. "SR-2026-000123", shown to the user
    request_type: StaffRequestType
    category: str = Field(max_length=50)
    subject: str = Field(max_length=255)
    message: str
    sender_name: str = Field(max_length=100)
    sender_email: str = Field(max_length=255)
    user_id: Optional[int] = Field(default=None, foreign_key="user.user_id", index=True)  # None = guest
    session_id: Optional[int] = Field(default=None, foreign_key="session.session_id", index=True)
    status: StaffRequestStatus = Field(default=StaffRequestStatus.new)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


class IntroRequest(SQLModel, table=True):
    """A member asking CCIFJ to introduce them to another member company.

    Staff handle it from the dashboard; nothing is sent to the other company automatically.
    """
    __tablename__ = "intro_request"

    intro_id: Optional[int] = Field(default=None, primary_key=True)
    reference: str = Field(max_length=32, unique=True)  # e.g. "IR-2026-000012", shown to the member
    requester_user_id: int = Field(foreign_key="user.user_id", index=True)
    requester_member_id: Optional[int] = Field(default=None, foreign_key="member.member_id")
    target_member_id: int = Field(foreign_key="member.member_id", index=True)
    message: str
    status: IntroStatus = Field(default=IntroStatus.new)
    staff_note: Optional[str] = Field(default=None)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


class MemberProfile(SQLModel, table=True):
    """Onboarding data for a member account: when it joined and what it wants from CCIFJ."""
    __tablename__ = "member_profile"

    user_id: int = Field(foreign_key="user.user_id", primary_key=True)
    joined_at: Optional[NaiveDatetime] = Field(default=None)  # None: linked before onboarding existed
    goals: Optional[str] = Field(default=None)        # comma-separated keys, e.g. "find_clients,network"
    interests: Optional[str] = Field(default=None)    # free text: sectors, topics, products
    preferred_language: str = Field(default="en", max_length=2)
    preferred_channel: str = Field(default="in_app", max_length=10)  # in_app | email | line
    profile_completed_at: Optional[NaiveDatetime] = Field(default=None)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


class ActivityEvent(SQLModel, table=True):
    """Logins and chats; bookings, reservations and introductions are read from their own tables."""
    __tablename__ = "activity_event"

    activity_id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.user_id", index=True)
    kind: str = Field(max_length=20)
    created_at: NaiveDatetime = Field(default_factory=utcnow, index=True)


class AutomationRule(SQLModel, table=True):
    """Staff settings for one engagement rule (the rule logic itself lives in concierge.engagement)."""
    __tablename__ = "automation_rule"

    rule_key: str = Field(max_length=30, primary_key=True)
    enabled: bool = Field(default=True)
    requires_approval: bool = Field(default=False)
    updated_at: NaiveDatetime = updated_at_field()


class AutomationRun(SQLModel, table=True):
    __tablename__ = "automation_run"

    run_id: Optional[int] = Field(default=None, primary_key=True)
    as_of: NaiveDatetime
    triggered_by: str = Field(max_length=20)  # schedule | staff | membership_approval
    created_count: int = 0
    duplicate_count: int = 0
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Outreach(SQLModel, table=True):
    """One message the engagement rules decided to send. `dedupe_key` is unique, so a rule
    never fires twice for the same member and situation, however often the job runs."""
    __tablename__ = "outreach"

    outreach_id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.user_id", index=True)
    rule_key: str = Field(max_length=30, index=True)
    dedupe_key: str = Field(max_length=120, unique=True)
    channel: str = Field(max_length=10)  # in_app | email | line | staff_task
    subject: str = Field(max_length=255)
    body: str
    reason: str  # the facts that made the rule fire, shown to staff
    status: OutreachStatus = Field(default=OutreachStatus.pending, index=True)
    run_id: Optional[int] = Field(default=None, foreign_key="automation_run.run_id")
    staff_request_id: Optional[int] = Field(default=None, foreign_key="staff_request.request_id")
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    sent_at: Optional[NaiveDatetime] = Field(default=None)
    read_at: Optional[NaiveDatetime] = Field(default=None)


class KnowledgeDocument(SQLModel, table=True):
    """A file staff uploaded as an agent source. Its chunks live in the upload search
    index (see concierge.knowledge), not in this table."""
    __tablename__ = "knowledge_document"

    doc_id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(max_length=255)
    filename: str = Field(max_length=200)
    mime_type: str = Field(max_length=100)
    source_url: Optional[str] = Field(default=None, max_length=500)  # public page/Calameo link, if any
    sha256: str = Field(max_length=64, unique=True)
    char_count: int = 0
    chunk_count: int = 0
    uploaded_by: Optional[int] = Field(default=None, foreign_key="user.user_id")
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    # Linked sources (see concierge.linked_sources): set when staff linked a URL instead of
    # uploading a file; the document is fetched again on a schedule and re-indexed when it changes.
    sync_url: Optional[str] = Field(default=None, max_length=500)
    download_url: Optional[str] = Field(default=None, max_length=500)  # full-text PDF for a Calameo link
    version: Optional[str] = Field(default=None, max_length=300)
    checked_at: Optional[NaiveDatetime] = None
    changed_at: Optional[NaiveDatetime] = None
    sync_error: Optional[str] = Field(default=None, max_length=500)


class ChannelIdentity(SQLModel, table=True):
    """A person writing from WhatsApp or Slack (see concierge.channels).

    `user_id` stays empty until they prove who they are by logging in on the website through
    a one-time link (see concierge.account_linking). A Slack user ID is only unique inside
    its workspace, so `team_id` is part of the key; it is "" for WhatsApp.
    """
    __tablename__ = "channel_identity"
    __table_args__ = (
        UniqueConstraint("platform", "team_id", "platform_user_id", name="uq_channel_identity"),
    )

    identity_id: Optional[int] = Field(default=None, primary_key=True)
    platform: str = Field(max_length=20)  # whatsapp | slack
    platform_user_id: str = Field(max_length=64)  # WhatsApp number (digits) or Slack user ID
    team_id: str = Field(default="", max_length=64)
    reply_to: Optional[str] = Field(default=None, max_length=64)  # Slack DM channel; unused for WhatsApp
    user_id: Optional[int] = Field(default=None, foreign_key="user.user_id", index=True)
    # Current conversation. Not a foreign key: guest conversations are swept after their
    # token lifetime, and a missing one simply starts a new conversation.
    session_id: Optional[int] = Field(default=None)
    linked_at: Optional[NaiveDatetime] = Field(default=None)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = updated_at_field()


class LinkToken(SQLModel, table=True):
    """A short-lived, single-use account-linking token. Only its SHA-256 is stored, and the
    row is deleted the moment it is used, so a leaked database or a replayed link is useless."""
    __tablename__ = "link_token"

    token_hash: str = Field(max_length=64, primary_key=True)
    identity_id: int = Field(foreign_key="channel_identity.identity_id", index=True)
    expires_at: NaiveDatetime
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class StaffRequestRecipient(SQLModel, table=True):
    __tablename__ = "staff_request_recipient"

    request_id: int = Field(foreign_key="staff_request.request_id", primary_key=True)
    email: str = Field(max_length=255, primary_key=True)
    # Set when the address matches a contact_us row; contacts are also hard-coded in the agent.
    contact_id: Optional[int] = Field(default=None, foreign_key="contact_us.contact_id")
