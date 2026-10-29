import asyncio
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy import func, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from .datetimes import japan_now, japan_naive

from .models import (
    Booking,
    BookingStatus,
    ChatSession,
    Event,
    EventLanguage,
    Member,
    MemberCategory,
    Message,
    MessageRole,
    ReservationStatus,
    Space,
    SpaceReservation,
    Speaker,
    SpeaksAt,
    Subscription,
    SubscriptionType,
    Ticket,
    User,
    UserAuth,
)


def _now() -> datetime:
    return japan_now()


def _naive(dt: datetime) -> datetime:
    """Normalize scheduling values to the database's naive Japan time."""
    return japan_naive(dt)


# ---------- Events ----------

async def search_events(
    session: AsyncSession,
    *,
    keyword: Optional[str] = None,
    name: Optional[str] = None,
    category: Optional[str] = None,
    location: Optional[str] = None,
    language: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    max_price: Optional[float] = None,
    currency: Optional[str] = None,
    limit: int = 10,
) -> Sequence[Event]:
    """Every filter is optional; only the ones given are applied.

    Prices in different currencies aren't comparable, so `max_price` is always
    matched against one currency: `currency` if given, otherwise JPY.
    `currency` on its own (without max_price) just filters by that currency.
    """
    stmt = select(Event)

    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            col(Event.title).ilike(like) | col(Event.description).ilike(like)
        )
    if category:
        stmt = stmt.where(col(Event.category).ilike(category))
    if name:
        stmt = stmt.where(col(Event.title).ilike(f"%{name}%"))
    if location:
        stmt = stmt.where(col(Event.location).ilike(f"%{location}%"))
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
    if max_price is not None:
        price_currency = (currency or "JPY").upper()
        stmt = stmt.where(
            (
                (Event.price <= max_price)
                | (Event.member_price <= max_price)
                | (Event.non_member_price <= max_price)
            ),
            Event.currency == price_currency,
        )
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
    closing_at = event.reg_deadline if event.reg_deadline is not None else event.end_at
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
