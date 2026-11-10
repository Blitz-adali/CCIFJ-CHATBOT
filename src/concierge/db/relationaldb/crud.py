import asyncio
import re
from datetime import datetime
from typing import Optional, Sequence
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy import delete, func, or_, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from .datetimes import japan_now, japan_naive

import logging

from .models import (
    Booking,
    BookingStatus,
    ChatSession,
    Belongs,
    Event,
    EventLanguage,
    Member,
    MemberCategory,
    MemberContact,
    Message,
    MessageRole,
    ReservationStatus,
    Space,
    SpaceReservation,
    Speaker,
    StaffRequest,
    StaffRequestRecipient,
    StaffRequestType,
    SpeaksAt,
    ContactUs,
    Subscription,
    SubscriptionType,
    Ticket,
    User,
    UserAuth,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return japan_now()


_QUOTES = re.compile(r"[\"'`‘’‚‛“”„]")


def _naive(dt: datetime) -> datetime:
    """Normalize scheduling values to the database's naive Japan time."""
    return japan_naive(dt)


# ---------- Events ----------

async def list_event_categories(session: AsyncSession) -> list[str]:
    """The event categories in use, for validating the agent's category filter."""
    result = await session.exec(
        select(Event.category).where(col(Event.category).is_not(None)).distinct().order_by(Event.category)
    )
    return [category for category in result.all() if category]


async def search_events(
    session: AsyncSession,
    *,
    keyword: Optional[str] = None,
    keywords: Sequence[str] = (),
    name: Optional[str] = None,
    category: Optional[str] = None,
    location: Optional[str] = None,
    locations: Sequence[str] = (),
    language: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    max_price: Optional[float] = None,
    currency: Optional[str] = None,
    price_audience: Optional[str] = None,
    registration_open_only: bool = False,
    limit: int = 10,
) -> Sequence[Event]:
    """Every filter is optional; only the ones given are applied.

    Prices in different currencies aren't comparable, so `max_price` is always
    matched against one currency: `currency` if given, otherwise JPY.
    `currency` on its own (without max_price) just filters by that currency.
    `keywords` and `locations` match when any one of their terms matches.
    """
    stmt = select(Event).where(col(Event.cancelled).is_(False))  # staff cancelled it: never offered

    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            col(Event.title).ilike(like) | col(Event.description).ilike(like)
        )
    if keywords:
        stmt = stmt.where(or_(*(
            col(Event.title).ilike(f"%{term}%") | col(Event.description).ilike(f"%{term}%")
            for term in keywords
        )))
    if category:
        stmt = stmt.where(col(Event.category).ilike(category))
    if name:
        # Titles mix straight and curly quotes ("Korea's" vs "Korea’s"); let any quote match any other.
        stmt = stmt.where(col(Event.title).ilike(f"%{_QUOTES.sub('_', name)}%"))
    if location:
        stmt = stmt.where(col(Event.location).ilike(f"%{location}%"))
    if locations:
        stmt = stmt.where(or_(*(col(Event.location).ilike(f"%{place}%") for place in locations)))
    if language:
        # Subquery instead of a join, so an event isn't returned once per language.
        has_language = select(EventLanguage.event_id).where(
            col(EventLanguage.language).ilike(language)
        )
        stmt = stmt.where(col(Event.event_id).in_(has_language))
    if date_from:
        stmt = stmt.where(Event.start_at >= _naive(date_from))
    if date_to:
        stmt = stmt.where(Event.start_at <= _naive(date_to))
    if registration_open_only:
        stmt = stmt.where(Event.start_at > _now(), (col(Event.reg_deadline).is_(None)) | (Event.reg_deadline >= _now()))
    if max_price is not None:
        price_currency = (currency or "JPY").upper()
        if price_audience in {"member", "non_member"}:
            specific = Event.member_price if price_audience == "member" else Event.non_member_price
            stmt = stmt.where(func.coalesce(specific, Event.price) <= max_price, Event.currency == price_currency)
        else:
            stmt = stmt.where(((Event.price <= max_price) | (Event.member_price <= max_price) | (Event.non_member_price <= max_price)), Event.currency == price_currency)
    elif currency:
        stmt = stmt.where(Event.currency == currency.upper())

    stmt = stmt.order_by(Event.start_at).limit(limit)
    result = await session.exec(stmt)
    return result.all()


async def get_event(session: AsyncSession, event_id: int) -> Optional[Event]:
    return await session.get(Event, event_id)


async def get_event_languages(session: AsyncSession, event_id: int) -> list[str]:
    result = await session.exec(
        select(EventLanguage.language).where(EventLanguage.event_id == event_id)
    )
    return list(result.all())


async def get_event_speakers(session: AsyncSession, event_id: int) -> Sequence[Speaker]:
    stmt = (
        select(Speaker)
        .join(SpeaksAt, col(SpeaksAt.speaker_id) == col(Speaker.speaker_id))
        .where(SpeaksAt.event_id == event_id)
        .order_by(Speaker.name)
    )
    result = await session.exec(stmt)
    return result.all()


# ---------- Speakers ----------

async def search_speakers(
    session: AsyncSession,
    *,
    keyword: Optional[str] = None,
    limit: int = 10,
) -> Sequence[Speaker]:
    """Keyword matches name, role or biography."""
    stmt = select(Speaker)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            col(Speaker.name).ilike(like)
            | col(Speaker.role).ilike(like)
            | col(Speaker.biography).ilike(like)
        )
    result = await session.exec(stmt.order_by(Speaker.name).limit(limit))
    return result.all()


async def get_speaker_events(session: AsyncSession, speaker_id: int) -> Sequence[Event]:
    """Events a speaker appears at, soonest first."""
    stmt = (
        select(Event)
        .join(SpeaksAt, col(SpeaksAt.event_id) == col(Event.event_id))
        .where(SpeaksAt.speaker_id == speaker_id)
        .order_by(Event.start_at)
    )
    result = await session.exec(stmt)
    return result.all()


# ---------- Members ----------

async def search_members(
    session: AsyncSession,
    *,
    keyword: Optional[str] = None,
    category: Optional[str] = None,
    country: Optional[str] = None,
    continent: Optional[str] = None,
    region: Optional[str] = None,
    sector: Optional[str] = None,
    limit: int = 10,
) -> Sequence[Member]:
    """`category` matches the membership category name (partial, case-insensitive).
    `region` matches the member's region column (e.g. Tokyo, Kansai)."""
    stmt = select(Member)

    if category:
        stmt = stmt.join(
            MemberCategory, col(Member.category_id) == col(MemberCategory.category_id)
        ).where(col(MemberCategory.category_name).ilike(f"%{category}%"))
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            col(Member.name).ilike(like) | col(Member.description).ilike(like)
        )
    if country:
        stmt = stmt.where(col(Member.country).ilike(country))
    if continent:
        stmt = stmt.where(col(Member.continent).ilike(continent))
    if region:
        stmt = stmt.where(col(Member.region).ilike(f"%{region}%"))
    if sector:
        stmt = stmt.where(col(Member.sector).ilike(f"%{sector}%"))

    result = await session.exec(stmt.order_by(Member.name).limit(limit))
    return result.all()


async def get_member(session: AsyncSession, member_id: int) -> Optional[Member]:
    return await session.get(Member, member_id)


async def member_contacts(session: AsyncSession, member_ids: Sequence[int], include_hidden: bool = False) -> dict[int, list[MemberContact]]:
    """Contact people of the given member companies, grouped by company."""
    if not member_ids:
        return {}
    rows = (await session.exec(select(MemberContact).where(col(MemberContact.member_id).in_(list(member_ids)))
                               .order_by(col(MemberContact.contact_id)))).all()
    grouped: dict[int, list[MemberContact]] = {}
    for contact in rows:
        if include_hidden or contact.directory_visible is not False:
            grouped.setdefault(contact.member_id, []).append(contact)
    return grouped


async def get_events_by_ids(session: AsyncSession, event_ids: Sequence[int]) -> Sequence[Event]:
    if not event_ids:
        return []
    return (await session.exec(select(Event).where(col(Event.event_id).in_(list(event_ids))))).all()


async def list_sectors(session: AsyncSession) -> list[str]:
    """Every distinct industry (sector) that at least one member belongs to."""
    result = await session.exec(
        select(Member.sector).distinct().order_by(Member.sector)
    )
    return list(result.all())


async def list_regions(session: AsyncSession) -> list[str]:
    result = await session.exec(
        select(Member.region).distinct().order_by(Member.region)
    )
    return list(result.all())


async def list_countries(session: AsyncSession) -> list[str]:
    result = await session.exec(
        select(Member.country).distinct().order_by(Member.country)
    )
    return list(result.all())


async def list_continents(session: AsyncSession) -> list[str]:
    result = await session.exec(
        select(Member.continent).distinct().order_by(Member.continent)
    )
    return list(result.all())


# ---------- Member categories ----------

async def list_member_categories(session: AsyncSession) -> Sequence[MemberCategory]:
    """All categories with prices. Check `currency` before comparing prices."""
    result = await session.exec(
        select(MemberCategory).order_by(MemberCategory.category_name)
    )
    return result.all()


async def get_member_category(
    session: AsyncSession, name: str
) -> Optional[MemberCategory]:
    result = await session.exec(
        select(MemberCategory).where(
            col(MemberCategory.category_name).ilike(f"%{name}%")
        )
    )
    return result.first()


# ---------- Chat sessions and messages ----------

async def get_user_by_email(session: AsyncSession, email: str) -> Optional[User]:
    result = await session.exec(
        select(User).where(func.lower(User.email) == email.strip().casefold())
    )
    return result.first()


async def get_user_credentials(
    session: AsyncSession, email: str
) -> Optional[tuple[User, UserAuth]]:
    result = await session.exec(
        select(User, UserAuth)
        .join(UserAuth, UserAuth.user_id == User.user_id)
        .where(func.lower(User.email) == email.strip().casefold())
    )
    return result.first()


async def register_user(
    session: AsyncSession, name: str, email: str, password_hash: str
) -> User:
    email = email.strip().casefold()
    if await get_user_by_email(session, email) is not None:
        raise ValueError("An account with this email already exists.")
    user = User(name=name.strip(), email=email)
    try:
        session.add(user)
        await session.flush()
        session.add(UserAuth(user_id=user.user_id, password_hash=password_hash))
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if await get_user_by_email(session, email) is not None:
            raise ValueError("An account with this email already exists.") from exc
        raise
    await session.refresh(user)
    return user


async def get_chat_session(session: AsyncSession, session_id: int) -> Optional[ChatSession]:
    return await session.get(ChatSession, session_id)


async def claim_chat_session(session: AsyncSession, session_id: int, user_id: int) -> ChatSession:
    """Atomically bind a guest conversation to the first authenticated owner."""
    await session.exec(
        update(ChatSession).where(
            ChatSession.session_id == session_id, ChatSession.user_id.is_(None)
        ).values(user_id=user_id)
    )
    await session.commit()
    chat = await session.get(ChatSession, session_id, populate_existing=True)
    if chat is None or chat.user_id != user_id:
        raise ValueError("This session is not available for your account.")
    return chat


async def _delete_guest_sessions(session: AsyncSession, guest_sessions) -> int:
    """Delete guest sessions (a subquery of ids) with their messages; owned sessions are never matched.

    A guest conversation that produced a staff request is kept: the visitor approved sending
    that request to CCIFJ, and staff read the conversation behind it to follow up.
    """
    with_request = select(StaffRequest.session_id).where(col(StaffRequest.session_id).is_not(None))
    ids = select(ChatSession.session_id).where(
        col(ChatSession.session_id).in_(guest_sessions), ChatSession.user_id.is_(None),
        col(ChatSession.session_id).not_in(with_request),
    )
    await session.exec(delete(Message).where(col(Message.session_id).in_(ids)))
    result = await session.exec(
        delete(ChatSession).where(
            col(ChatSession.session_id).in_(guest_sessions), ChatSession.user_id.is_(None),
            col(ChatSession.session_id).not_in(with_request),
        )
    )
    await session.commit()
    return result.rowcount or 0


async def delete_guest_session(session: AsyncSession, session_id: int) -> bool:
    """Delete one anonymous conversation; a session that has an owner is left untouched."""
    return await _delete_guest_sessions(
        session, select(ChatSession.session_id).where(ChatSession.session_id == session_id)
    ) > 0


async def delete_guest_sessions_created_before(session: AsyncSession, cutoff: datetime) -> int:
    """Delete anonymous conversations created before ``cutoff`` (their tokens have expired)."""
    return await _delete_guest_sessions(
        session, select(ChatSession.session_id).where(ChatSession.created_at < cutoff)
    )


async def list_session_messages(
    session: AsyncSession, session_id: int, *, after_id: int = 0, limit: int = 100
) -> Sequence[Message]:
    result = await session.exec(
        select(Message).where(
            Message.session_id == session_id, Message.message_id > after_id
        ).order_by(Message.message_id).limit(limit)
    )
    return result.all()


async def get_latest_message(session: AsyncSession, session_id: int) -> Optional[Message]:
    result = await session.exec(
        select(Message).where(Message.session_id == session_id)
        .order_by(col(Message.message_id).desc()).limit(1)
    )
    return result.first()


async def update_message_metadata(
    session: AsyncSession, message: Message, metadata: Optional[list]
) -> None:
    message.tool_calls = metadata
    session.add(message)
    await session.commit()


async def create_chat_session(
    session: AsyncSession, user_id: Optional[int] = None
) -> ChatSession:
    chat = ChatSession(user_id=user_id)
    session.add(chat)
    await session.commit()
    await session.refresh(chat)
    return chat


async def add_message(
    session: AsyncSession,
    session_id: int,
    role: MessageRole,
    content: str,
    tool_calls: Optional[list] = None,
) -> Message:
    msg = Message(
        session_id=session_id, role=role, content=content, tool_calls=tool_calls
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


async def list_message_metadata(session: AsyncSession, session_id: int) -> list[list]:
    """Metadata of every message in a conversation that has any, oldest first."""
    result = await session.exec(
        select(Message.tool_calls).where(
            Message.session_id == session_id, col(Message.tool_calls).is_not(None)
        ).order_by(Message.message_id)
    )
    return [item for item in result.all() if isinstance(item, list)]


async def get_history(
    session: AsyncSession, session_id: int, limit: int = 20
) -> list[Message]:
    """Last `limit` messages, returned oldest first (the order the LLM expects)."""
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(col(Message.message_id).desc())
        .limit(limit)
    )
    result = await session.exec(stmt)
    return list(reversed(result.all()))


# ---------- Event bookings ----------
# A Booking is one registration; each person on it is a Ticket row
# (name, email, telephone). The number of tickets is the number of rows.

async def book_event(
    session: AsyncSession,
    user_id: int,
    event_id: int,
    attendees: Sequence[dict],
) -> Booking:
    """Create one confirmed booking per user/event and its attendee tickets.

    `attendees` is a list of dicts: {"name": ..., "email": ..., "telephone": ...}
    (telephone is optional). A repeated request returns a clear error and does not
    create another booking. A partial unique index is the final guard against races.
    """
    if await session.get(User, user_id) is None:
        raise ValueError("No registered user was found for this account.")
    event = await session.get(Event, event_id)
    if event is None:
        raise ValueError("That event doesn't exist.")
    if getattr(event, "cancelled", False):
        raise ValueError("This event has been cancelled.")
    from concierge.source_context import effective_deadline
    deadline = effective_deadline(event)
    closing_at = deadline if deadline is not None else event.end_at
    if closing_at is not None and _now() >= _naive(closing_at):
        raise ValueError("Registration for this event has closed.")
    if not attendees:
        raise ValueError("At least one attendee (name and email) is required.")

    async def existing_confirmed_booking() -> Optional[Booking]:
        result = await session.exec(
            select(Booking)
            .where(
                Booking.user_id == user_id,
                Booking.event_id == event_id,
                Booking.status == BookingStatus.confirmed,
            )
            .order_by(Booking.booking_id)
        )
        return result.first()

    existing = await existing_confirmed_booking()
    if existing:
        raise ValueError(
            f"You already have a confirmed registration for this event "
            f"(booking #{existing.booking_id}). No second booking was created."
        )

    cleaned = []
    for i, person in enumerate(attendees, start=1):
        name = (person.get("name") or "").strip()
        email = (person.get("email") or "").strip()
        telephone = (person.get("telephone") or "").strip() or None
        if not name or not email or "@" not in email:
            raise ValueError(f"Attendee {i} needs a name and a valid email address.")
        cleaned.append((name, email, telephone))

    booking = Booking(user_id=user_id, event_id=event_id)
    try:
        session.add(booking)
        await session.flush()  # assigns booking.booking_id before tickets use it
        for name, email, telephone in cleaned:
            session.add(
                Ticket(
                    booking_id=booking.booking_id,
                    name=name,
                    email=email,
                    telephone=telephone,
                )
            )
        await session.commit()
    except (IntegrityError, OperationalError) as exc:
        await session.rollback()
        existing = await existing_confirmed_booking()
        if existing:
            raise ValueError(
                f"You already have a confirmed registration for this event "
                f"(booking #{existing.booking_id}). No second booking was created."
            ) from exc
        raise
    await session.refresh(booking)
    return booking


async def list_user_bookings(
    session: AsyncSession, user_id: int, include_cancelled: bool = False
) -> list[tuple[Booking, Event]]:
    """The user's bookings paired with their events, soonest event first."""
    stmt = (
        select(Booking, Event)
        .join(Event, col(Booking.event_id) == col(Event.event_id))
        .where(Booking.user_id == user_id)
    )
    if not include_cancelled:
        stmt = stmt.where(Booking.status == BookingStatus.confirmed)
    result = await session.exec(stmt.order_by(col(Event.start_at)))
    return list(result.all())


async def get_booking_tickets(
    session: AsyncSession, user_id: int, booking_id: int
) -> Sequence[Ticket]:
    """The attendees on a booking. Only returns rows if the booking is the user's."""
    stmt = (
        select(Ticket)
        .join(Booking, col(Ticket.booking_id) == col(Booking.booking_id))
        .where(Ticket.booking_id == booking_id, Booking.user_id == user_id)
        .order_by(Ticket.ticket_id)
    )
    result = await session.exec(stmt)
    return result.all()


async def get_booking(
    session: AsyncSession, booking_id: int
) -> Optional[Booking]:
    """Reload a booking by its database ID for post-write verification."""
    return await session.get(Booking, booking_id)


async def cancel_booking(
    session: AsyncSession, user_id: int, booking_id: int
) -> Booking:
    """Cancels the whole booking (all its tickets). Raises ValueError if it can't."""
    booking = await session.get(Booking, booking_id)
    if booking is None or booking.user_id != user_id:
        raise ValueError("No booking with that ID was found for your account.")
    if booking.status == BookingStatus.cancelled:
        raise ValueError("This booking is already cancelled.")
    event = await session.get(Event, booking.event_id)
    if event is not None and event.start_at is not None and _naive(event.start_at) <= _now():
        raise ValueError("This event has already started, so it can't be cancelled.")

    booking.status = BookingStatus.cancelled  # updated_at refreshes via onupdate
    session.add(booking)
    await session.commit()
    await session.refresh(booking)
    return booking


# ---------- Spaces and reservations ----------

# Avoid redundant attempts within this process. Database triggers installed by
# migrations enforce the same rule across processes and direct SQL writes.
_reservation_lock = asyncio.Lock()


async def list_spaces(session: AsyncSession) -> Sequence[Space]:
    result = await session.exec(select(Space).order_by(Space.name))
    return result.all()


async def get_space(session: AsyncSession, space_id: int) -> Optional[Space]:
    return await session.get(Space, space_id)


async def get_reservation(
    session: AsyncSession, reservation_id: int
) -> Optional[SpaceReservation]:
    """Reload a reservation by its database ID for post-write verification."""
    return await session.get(SpaceReservation, reservation_id)


async def find_space_conflicts(
    session: AsyncSession,
    space_id: int,
    start_at: datetime,
    end_at: datetime,
) -> Sequence[SpaceReservation]:
    """Confirmed reservations that overlap [start_at, end_at) in this space.

    Two time ranges overlap when each starts before the other ends, so
    back-to-back reservations (one ends exactly when the next starts) are fine.
    """
    stmt = select(SpaceReservation).where(
        SpaceReservation.space_id == space_id,
        SpaceReservation.status == ReservationStatus.confirmed,
        SpaceReservation.start_at < _naive(end_at),
        SpaceReservation.end_at > _naive(start_at),
    )
    result = await session.exec(stmt.order_by(SpaceReservation.start_at))
    return result.all()


async def is_space_available(
    session: AsyncSession,
    space_id: int,
    start_at: datetime,
    end_at: datetime,
) -> bool:
    return not await find_space_conflicts(session, space_id, start_at, end_at)


async def list_space_reservations(
    session: AsyncSession,
    space_id: int,
    date_from: datetime,
    date_to: datetime,
) -> Sequence[SpaceReservation]:
    """Confirmed reservations in a window, to show which times are taken.
    Callers should not expose the title or details of private reservations."""
    return await find_space_conflicts(session, space_id, date_from, date_to)


async def create_reservation(
    session: AsyncSession,
    user_id: int,
    space_id: int,
    title: str,
    start_at: datetime,
    end_at: datetime,
    *,
    is_public: bool = False,
    expected_attendees: Optional[int] = None,
    notes: Optional[str] = None,
    event_id: Optional[int] = None,
) -> SpaceReservation:
    """Reserve a space. Confirmed immediately if the slot is free.
    Raises ValueError with a message the agent can relay to the user."""
    start_at, end_at = _naive(start_at), _naive(end_at)
    if end_at <= start_at:
        raise ValueError("The end time must be after the start time.")
    if start_at <= _now():
        raise ValueError("Reservations must start in the future.")
    if not title.strip():
        raise ValueError("A title or purpose for the reservation is required.")
    if expected_attendees is not None and expected_attendees <= 0:
        raise ValueError("Expected attendees must be greater than zero.")

    if await session.get(User, user_id) is None:
        raise ValueError("No registered user was found for this account.")
    space = await session.get(Space, space_id)
    if space is None:
        raise ValueError("That space doesn't exist.")
    if event_id is not None and await session.get(Event, event_id) is None:
        raise ValueError("The event linked to this reservation doesn't exist.")
    if (
        expected_attendees is not None
        and space.capacity is not None
        and expected_attendees > space.capacity
    ):
        raise ValueError(f"{space.name} holds at most {space.capacity} people.")

    async with _reservation_lock:
        if await find_space_conflicts(session, space_id, start_at, end_at):
            raise ValueError("That time slot is already reserved for this space.")
        reservation = SpaceReservation(
            space_id=space_id,
            user_id=user_id,
            event_id=event_id,
            title=title.strip(),
            is_public=is_public,
            expected_attendees=expected_attendees,
            notes=notes,
            start_at=start_at,
            end_at=end_at,
        )
        session.add(reservation)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            if "space_reservation_overlap" in str(exc.orig):
                raise ValueError("That time slot is already reserved for this space.") from exc
            raise
        await session.refresh(reservation)
    return reservation


async def list_user_reservations(
    session: AsyncSession, user_id: int, upcoming_only: bool = True
) -> list[tuple[SpaceReservation, Space]]:
    """The user's confirmed reservations paired with their space, soonest first."""
    stmt = (
        select(SpaceReservation, Space)
        .join(Space, col(SpaceReservation.space_id) == col(Space.space_id))
        .where(
            SpaceReservation.user_id == user_id,
            SpaceReservation.status == ReservationStatus.confirmed,
        )
    )
    if upcoming_only:
        stmt = stmt.where(SpaceReservation.end_at > _now())
    result = await session.exec(stmt.order_by(col(SpaceReservation.start_at)))
    return list(result.all())


async def cancel_reservation(
    session: AsyncSession, user_id: int, reservation_id: int
) -> SpaceReservation:
    """Cancel one of the user's reservations. Raises ValueError if it can't."""
    reservation = await session.get(SpaceReservation, reservation_id)
    if reservation is None or reservation.user_id != user_id:
        raise ValueError("No reservation with that ID was found for your account.")
    if reservation.status == ReservationStatus.cancelled:
        raise ValueError("This reservation is already cancelled.")
    if reservation.start_at <= _now():
        raise ValueError("This reservation has already started, so it can't be cancelled.")

    reservation.status = ReservationStatus.cancelled  # updated_at refreshes via onupdate
    session.add(reservation)
    await session.commit()
    await session.refresh(reservation)
    return reservation


# ---------- Subscriptions ----------

def _normalize_subscription_email(email: str) -> str:
    return email.strip().casefold()


async def get_subscription(
    session: AsyncSession, email: str, sub_type: SubscriptionType
) -> Optional[Subscription]:
    """Read a subscription by its composite primary key."""
    normalized_email = _normalize_subscription_email(email)
    return await session.get(Subscription, (normalized_email, sub_type))


async def add_subscription(
    session: AsyncSession, email: str, sub_type: SubscriptionType
) -> bool:
    """Create a subscription once; return False if it already exists."""
    normalized_email = _normalize_subscription_email(email)
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("A valid email address is required.")

    existing = await get_subscription(session, normalized_email, sub_type)
    if existing is not None:
        return False

    session.add(Subscription(user_email=normalized_email, sub_type=sub_type))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # A concurrent request may have inserted the same composite key.
        if await get_subscription(session, normalized_email, sub_type) is not None:
            return False
        raise
    return True


# ---------- User context (agent personalization) ----------

def _schedule_text(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def _amount_text(amount) -> str:
    return str(int(amount)) if amount == amount.to_integral_value() else str(amount)


async def load_user_context(session: AsyncSession, user_id: Optional[int]) -> Optional[dict]:
    """The signed-in user's profile in the shape the agent reads, or None for guests.

    Membership uses the user's first linked company. Bookings and reservations are
    upcoming confirmed rows in Japan time, soonest first. Interests are the event
    categories the user books most; recommended events are open, unbooked events
    in those categories (or simply the next open events without booking history).
    """
    if user_id is None:
        return None
    user = await session.get(User, user_id)
    if user is None:
        return None
    now = _now()

    result = await session.exec(
        select(Member, MemberCategory)
        .join(Belongs, col(Belongs.member_id) == col(Member.member_id))
        .join(MemberCategory, col(Member.category_id) == col(MemberCategory.category_id), isouter=True)
        .where(Belongs.user_id == user_id)
        .order_by(col(Member.member_id))
    )
    row = result.first()
    membership = None
    if row is not None:
        member, category = row
        membership = {
            "company": member.name,
            # Lets the concierge tailor advice without asking the user again.
            "industry": member.sector,
            "country": member.country,
            "region": member.region,
            "tier": category.category_name if category else None,
            "annual_price": _amount_text(category.annual_price) if category else None,
            "currency": category.currency if category else None,
        }

    result = await session.exec(
        select(Event)
        .join(Booking, col(Booking.event_id) == col(Event.event_id))
        .where(
            Booking.user_id == user_id,
            Booking.status == BookingStatus.confirmed,
            col(Event.start_at) >= now,
        )
        .order_by(col(Event.start_at))
        .limit(5)
    )
    bookings = [
        {
            "event_id": event.event_id,
            "title": event.title,
            "start_at": _schedule_text(event.start_at),
            "category": event.category,
            "location": event.location,
            "days_until": (event.start_at.date() - now.date()).days,
        }
        for event in result.all()
    ]

    # Past and upcoming confirmed bookings; ties go to the most recently attended category.
    result = await session.exec(
        select(Event.category)
        .join(Booking, col(Booking.event_id) == col(Event.event_id))
        .where(Booking.user_id == user_id, Booking.status == BookingStatus.confirmed)
        .group_by(col(Event.category))
        .order_by(func.count().desc(), func.max(Event.start_at).desc())
        .limit(3)
    )
    interests = list(result.all())

    booked = (
        select(Booking.event_id)
        .where(Booking.user_id == user_id, Booking.status == BookingStatus.confirmed)
    )
    stmt = select(Event).where(
        col(Event.cancelled).is_(False),
        col(Event.start_at) > now,
        (col(Event.reg_deadline).is_(None)) | (col(Event.reg_deadline) > now),
        col(Event.event_id).not_in(booked),
    )
    if interests:
        stmt = stmt.where(col(Event.category).in_(interests))
    # Prefer events that fit what the user does and has shown interest in (company sector and
    # description, onboarding answers, past bookings); fall back to their usual event formats.
    picked = None
    try:
        from concierge import matching
        booked_ids = set((await session.exec(booked)).all())
        picked = await matching.events_for_user(session, user_id, booked_ids, limit=2)
    except Exception:
        logger.exception("Could not match events to the user's interests; using event formats")
    if picked is None:
        picked = (await session.exec(stmt.order_by(col(Event.start_at)).limit(2))).all()
    recommended = [
        {
            "event_id": event.event_id,
            "title": event.title,
            "category": event.category,
            "start_at": _schedule_text(event.start_at),
            "reg_deadline": _schedule_text(event.reg_deadline) if event.reg_deadline else None,
            "location": event.location,
        }
        for event in picked
    ]

    result = await session.exec(
        select(SpaceReservation)
        .where(
            SpaceReservation.user_id == user_id,
            SpaceReservation.status == ReservationStatus.confirmed,
            col(SpaceReservation.end_at) >= now,
        )
        .order_by(col(SpaceReservation.start_at))
        .limit(3)
    )
    reservations = [
        {
            "reservation_id": item.reservation_id,
            "title": item.title,
            "start_at": _schedule_text(item.start_at),
            "end_at": _schedule_text(item.end_at),
        }
        for item in result.all()
    ]

    result = await session.exec(
        select(Subscription.user_email)
        .where(Subscription.user_email == _normalize_subscription_email(user.email))
        .limit(1)
    )
    context = {
        "name": user.name,
        "email": user.email,
        "membership": membership,
        "upcoming_bookings": bookings,
        "upcoming_space_reservations": reservations,
        "newsletter_subscribed": result.first() is not None,
        "interests": interests,
        "recommended_events": recommended,
    }
    if user.mobile:
        # Lets a draft to staff carry the user's phone number instead of a placeholder.
        context["phone"] = user.mobile
    return context


# ---------- Staff requests ----------

async def add_staff_request(
    session: AsyncSession,
    *,
    request_type: StaffRequestType,
    category: str,
    subject: str,
    message: str,
    sender_name: str,
    sender_email: str,
    recipient_emails: Sequence[str] = (),
    user_id: Optional[int] = None,
    session_id: Optional[int] = None,
) -> StaffRequest:
    """Store a request for CCIFJ staff with one recipient row per address. Sends nothing."""
    request_type = StaffRequestType(request_type)
    recipients = list(dict.fromkeys(email.strip().casefold() for email in recipient_emails if email.strip()))
    if request_type == StaffRequestType.staff_email and not recipients:
        raise ValueError("A staff email needs at least one recipient.")
    if request_type == StaffRequestType.team_ticket and recipients:
        raise ValueError("A team ticket has no recipients.")

    request = StaffRequest(
        # Unique placeholder; the readable reference needs the generated ID.
        reference=f"pending-{uuid4().hex[:24]}",
        request_type=request_type,
        category=category,
        subject=subject.strip(),
        message=message,
        sender_name=sender_name.strip(),
        sender_email=sender_email.strip(),
        user_id=user_id,
        session_id=session_id,
    )
    try:
        session.add(request)
        await session.flush()
        request.reference = f"SR-{japan_now().year}-{request.request_id:06d}"
        contacts = {}
        if recipients:
            result = await session.exec(
                select(ContactUs).where(func.lower(ContactUs.email).in_(recipients)).order_by(col(ContactUs.contact_id))
            )
            for contact in result.all():
                contacts.setdefault(contact.email.casefold(), contact.contact_id)
        for email in recipients:
            session.add(StaffRequestRecipient(request_id=request.request_id, email=email, contact_id=contacts.get(email)))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await session.refresh(request)
    return request


async def get_staff_request(session: AsyncSession, reference: str) -> Optional[StaffRequest]:
    result = await session.exec(select(StaffRequest).where(StaffRequest.reference == reference.strip()))
    return result.first()


async def list_staff_request_recipients(session: AsyncSession, request_id: int) -> Sequence[StaffRequestRecipient]:
    result = await session.exec(
        select(StaffRequestRecipient).where(StaffRequestRecipient.request_id == request_id).order_by(StaffRequestRecipient.email)
    )
    return result.all()
