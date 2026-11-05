import re
from collections import Counter
from datetime import datetime, time, timedelta
from typing import Literal, Optional

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel
from ..rag import search, get_collection, EMBED_MODEL_NAME
from ..rag.src.config import INDEX_PATH
from sentence_transformers import SentenceTransformer
import bm25s

from ..db.relationaldb import crud
from ..db.relationaldb.models import (
    Booking,
    BookingStatus,
    ReservationStatus,
    SpaceReservation,
    SubscriptionType,
)
from ..db.relationaldb.session import AsyncSessionLocal
from ..db.relationaldb.datetimes import japan_now

# Cached embedding model singleton to avoid reloading weights on every invocation
_embed_model = None

def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer(EMBED_MODEL_NAME)
    return _embed_model

# Cached bm25s retriever singleton to avoid reloading weights on every invocation
_retriever = None

def _get_retriever():
    global _retriever
    if _retriever is None:
        _retriever = bm25s.BM25.load(str(INDEX_PATH), load_corpus=True)
    return _retriever


def warm_up() -> None:
    """Load the search models now so the first knowledge-base question does not wait."""
    _get_embed_model()
    _get_retriever()
    get_collection()

#################################################
#
# Helpers (database-backed tools)
#
#################################################

PAGE_SIZE = 10


def _now() -> datetime:
    # Event and reservation schedules are stored in Japan time (UTC+9).
    return japan_now()


def _parse_date(text: str, end_of_day: bool = False) -> datetime:
    """Parse YYYY-MM-DD. Raises ValueError on a bad format."""
    day = datetime.strptime(text.strip(), "%Y-%m-%d").date()
    return datetime.combine(day, time.max if end_of_day else time.min)


def _parse_datetime(text: str, end_of_day: bool = False) -> datetime:
    """Parse 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'. Raises ValueError on a bad format."""
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return _parse_date(text, end_of_day=end_of_day)


def _to_int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _get_user_id(runtime) -> Optional[int]:
    """The logged-in user's ID from the graph state, or None for guests."""
    if runtime is None:
        return None
    return _to_int(runtime.state.get("user_id"))


def _money(amount, currency: Optional[str]) -> str:
    if currency is None:
        return f"{amount:,.2f} (currency not specified)"
    if currency.upper() == "JPY":
        return f"¥{int(amount):,}"
    return f"{amount:,.2f} {currency.upper()}"


def _price_text(event) -> str:
    labelled = [
        ("standard", event.price),
        ("members", event.member_price),
        ("non-members", event.non_member_price),
    ]
    present = [(label, value) for label, value in labelled if value is not None]
    if not present:
        return "unknown"

    def fmt(value) -> str:
        return "Free" if value == 0 else _money(value, event.currency)

    if len(present) == 1 and present[0][0] == "standard":
        return fmt(present[0][1])
    return ", ".join(f"{label} {fmt(value)}" for label, value in present)


def _when(dt: Optional[datetime], missing: str = "unknown") -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else missing


_TODAY_FETCH_MARGIN = 10


def _has_finished(event, now: datetime) -> bool:
    """An event is over once it ends; without an end time, once it starts."""
    if event.start_at is None:
        return False
    finish = event.end_at or event.start_at
    return finish <= now


def _event_line(event, now: datetime | None = None) -> str:
    status = ""
    if now is not None and event.start_at is not None and event.start_at <= now:
        status = " | IN PROGRESS NOW (already started; cannot be booked)"
    return (
        f"[ID {event.event_id}] {event.title} | {_when(event.start_at, 'date unknown')} "
        f"| {event.location or 'location unknown'} | Category: {event.category} "
        f"| Price: {_price_text(event)}{status}"
    )


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _find_space(spaces, space_name: str):
    wanted = _norm(space_name)
    return next(
        (s for s in spaces if _norm(s.name) in wanted or wanted in _norm(s.name)),
        None,
    )


def _booking_ref(booking_id: int) -> str:
    return f"CCIFJ-EVT-{booking_id:05d}"


def _reservation_ref(reservation_id: int) -> str:
    return f"CCIFJ-SPC-{reservation_id:05d}"


def _chat_session_id(runtime) -> Optional[int]:
    """The API chat session from the graph thread ID ('api:session:<id>:turn:<n>')."""
    config = getattr(runtime, "config", None) or {}
    thread_id = str((config.get("configurable") or {}).get("thread_id") or "")
    match = re.match(r"api:session:(\d+):", thread_id)
    return int(match.group(1)) if match else None


def _ref_id(reference: str) -> Optional[int]:
    """Accept 'CCIFJ-EVT-00012', '#12' or '12' and return 12."""
    digits = re.findall(r"\d+", str(reference))
    return int(digits[-1]) if digits else None


def _event_window(event) -> tuple[Optional[datetime], Optional[datetime]]:
    """An event's time range; an unknown end is treated as the start time."""
    if event.start_at is None:
        return None, None
    return event.start_at, event.end_at or event.start_at


def _overlaps(start_a, end_a, start_b, end_b) -> bool:
    # A zero-length range (unknown end) still counts as one minute.
    end_a = max(end_a, start_a + timedelta(minutes=1))
    end_b = max(end_b, start_b + timedelta(minutes=1))
    return start_a < end_b and start_b < end_a


def _registration_problem(event) -> Optional[str]:
    """Why the event can't be booked right now, or None. Mirrors crud.book_event."""
    now = _now()
    if event.start_at is not None and event.start_at <= now:
        return "the event has already started or taken place"
    closing_at = event.reg_deadline if event.reg_deadline is not None else event.end_at
    if closing_at is not None and now >= closing_at:
        return f"registration closed on {_when(closing_at)}"
    return None


async def _restore_status(model, row_id: int, status) -> bool:
    """Put a cancelled booking or reservation back after a failed change."""
    try:
        async with AsyncSessionLocal() as session:
            row = await session.get(model, row_id)
            if row is None:
                return False
            row.status = status
            session.add(row)
            await session.commit()
            return True
    except Exception:
        return False


class AttendeeInfo(BaseModel):
    """One person attending (one ticket)."""
    name: str
    email: str
    telephone: Optional[str] = None


#################################################
#
# 1. Events Tools
#
#################################################

# Event locations are stored as street addresses, so a region a user names
# ("Kansai") is searched as the cities in it.
_REGION_CITIES = {
    "kanto": ("Tokyo", "Yokohama", "Kawasaki", "Chiba", "Saitama", "Maebashi", "Takasaki"),
    "kansai": ("Osaka", "Kyoto", "Kobe", "Nara"),
    "chubu": ("Nagoya", "Shizuoka", "Fukui", "Kanazawa"),
    "kyushu": ("Fukuoka", "Kitakyushu", "Kumamoto"),
    "hokkaido": ("Sapporo", "Hokkaido"),
}


_KEYWORD_FETCH_LIMIT = 200


def _split_terms(text: str) -> list[str]:
    return list(dict.fromkeys(term.strip() for term in text.split(",") if term.strip()))


def _location_terms(location: str) -> list[str]:
    places = []
    for place in _split_terms(location):
        places.extend(_REGION_CITIES.get(place.casefold(), (place,)))
    return list(dict.fromkeys(places))


@tool
async def list_available_events(
    keyword: str = "",
    name: str = "",
    category: str = "",
    location: str = "",
    date_from: str = "",
    date_to: str = "",
) -> str:
    """
    List upcoming events, optionally filtered by topic keywords, name,
    category, location, or date range.

    Use this tool when:
    - The user asks what events are available.
    - The user wants to browse upcoming events.
    - The user looks for events about a topic or industry (food, HR, startups...).
    - The user knows an event's name but not its date or location.

    Args:
        keyword: Optional topic words, comma-separated synonyms, searched in
            each event's title and description; an event matches if any word
            matches. Use this for topics and industries, e.g.
            "F&B, food, wine, gastronomy" or "HR, recruitment, career".
        name: Optional full or partial event title. Case-insensitive.
        category: Optional event FORMAT, not a topic. One of: Business event,
            Conference, Networking, Training. Leave empty for topic searches.
        location: Optional city, venue, or region (Kanto, Kansai, Chubu,
            Kyushu, Hokkaido); comma-separate several.
        date_from: Optional earliest event date, formatted YYYY-MM-DD.
        date_to: Optional latest event date, formatted YYYY-MM-DD.

    Returns:
        A list of matching events with event IDs, names, dates, locations,
        categories, and prices. Without `date_from`, events taking place right
        now are included and marked IN PROGRESS NOW. Ticket availability is
        not tracked.
    """
    now = _now()
    try:
        # Without a date, search from the start of today so events in progress
        # are still found; finished ones are dropped below.
        start = _parse_date(date_from) if date_from else datetime.combine(now.date(), time.min)
        end = _parse_date(date_to, end_of_day=True) if date_to else None
    except ValueError:
        return "Invalid date. Dates must be formatted YYYY-MM-DD."
    if end and end < start:
        return "Invalid date range: date_to must not be earlier than date_from."

    async with AsyncSessionLocal() as session:
        if category:
            # The categories are formats; read them from the data so the list never goes stale.
            categories = await crud.list_event_categories(session)
            if category.casefold() not in {known.casefold() for known in categories}:
                return (
                    f"'{category}' is not an event category. Categories are event formats: "
                    f"{', '.join(categories)}. For a topic or industry, search with `keyword` instead."
                )
        keywords = _split_terms(keyword)
        events = await crud.search_events(
            session,
            keywords=keywords,
            name=name or None,
            category=category or None,
            locations=_location_terms(location),
            date_from=start,
            date_to=end,
            # Keyword hits are re-checked below, so fetch enough to fill a page after that.
            # One extra row tells us whether more exist; a few more cover today's finished events.
            limit=_KEYWORD_FETCH_LIMIT if keywords else PAGE_SIZE + 1 + _TODAY_FETCH_MARGIN,
        )
    if not date_from:
        events = [e for e in events if not _has_finished(e, now)]
    if not keywords:
        events = events[:PAGE_SIZE + 1]
    if keywords:
        # SQL matches substrings ("HR" in "through"); keep whole-word matches only.
        patterns = [re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE) for term in keywords]
        events = [
            event for event in events
            if any(p.search(f"{event.title}\n{event.description or ''}") for p in patterns)
        ][:PAGE_SIZE + 1]

    if not events:
        if keyword:
            return (
                "No upcoming events match these keywords. Try other synonyms, or list "
                "upcoming events without `keyword` and pick the relevant ones."
            )
        return "No matching events were found."

    text = "\n".join(_event_line(e, now) for e in events[:PAGE_SIZE])
    if len(events) > PAGE_SIZE:
        text += (
            f"\n\nShowing the first {PAGE_SIZE} results; more events match. "
            "Narrow the search (category, location or dates) to see others."
        )
    else:
        text += f"\n\n{min(len(events), PAGE_SIZE)} matching event(s)."
    return text


@tool
async def get_event_details(
    event_id: str,
) -> str:
    """
    Retrieve the complete details of a specific event using its event ID.

    Use this tool when the user asks for more information about an event,
    including its schedule, venue, description, ticket price, or availability.

    Args:
        event_id: Unique identifier of the event.

    Returns:
        The event's available details, including its name, date, location,
        description, category, ticket price, languages and speakers.
        Ticket availability is not tracked and is reported as unknown.
    """
    eid = _to_int(event_id)
    if eid is None:
        return "Invalid event ID."

    async with AsyncSessionLocal() as session:
        event = await crud.get_event(session, eid)
        if event is None:
            return f"No event was found with ID {event_id}."
        languages = await crud.get_event_languages(session, eid)
        speakers = await crud.get_event_speakers(session, eid)

    lines = [
        f"[ID {event.event_id}] {event.title}",
        f"Category: {event.category}",
        f"Location: {event.location or 'unknown'}",
        f"Starts: {_when(event.start_at)}",
        f"Ends: {_when(event.end_at)}",
        f"Registration deadline: {_when(event.reg_deadline, 'none stated')}",
        f"Price: {_price_text(event)}",
        f"Languages: {', '.join(languages) if languages else 'unknown'}",
        f"Contact: {event.contact or 'unknown'}",
        "Ticket availability: unknown (not tracked)",
        f"Description: {event.description}",
    ]
    if speakers:
        lines.append("Speakers:")
        for s in speakers:
            lines.append(f"- {s.name}" + (f" ({s.role})" if s.role else ""))
    return "\n".join(lines)


@tool
async def book_event_ticket(
    event_id: str,
    attendees: list[AttendeeInfo],
    runtime: ToolRuntime = None,
) -> str:
    """
    Reserve tickets for an upcoming CCIFJ event.
    The user_id is automatically accessed from the runtime state (runtime.state['user_id']).
    The user must be logged in.

    Use this tool only when:
    - The user has clearly selected a specific event (by event_id).
    - The user explicitly confirms their intention to book tickets.
    - You have collected the name and email (and ideally the phone number)
      of EVERY attendee. One attendee = one ticket.

    Args:
        event_id: Unique identifier of the event to reserve.
        attendees: One entry per ticket, each with name, email and optional telephone.
            The number of tickets is the number of attendees.
        runtime: Injected ToolRuntime providing access to the current graph state.

    Returns:
        A confirmed booking reference and summary if the transaction succeeds,
        or a clear error explanation (e.g., registration closed, invalid event ID,
        already registered).

    Behavior & Database Expectations:
        - Accesses user_id via runtime.state['user_id'].
        - A user can hold only one confirmed booking per event.
        - Records the booking and one ticket per attendee in the database.
        - Returns a booking reference code (CCIFJ-EVT-XXXXX) and reservation summary.
        - This is a registration only; no payment is taken.
    """
    user_id = _get_user_id(runtime)
    if user_id is None:
        return "Booking failed: you must be logged in to register for events."
    eid = _to_int(event_id)
    if eid is None:
        return "Booking failed: invalid event ID."
    if not attendees:
        return (
            "Booking failed: please provide the name and email of each attendee "
            "(one entry per ticket)."
        )

    people = [
        a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in attendees
    ]

    async with AsyncSessionLocal() as session:
        try:
            booking = await crud.book_event(session, user_id, eid, people)
        except ValueError as exc:
            return f"Booking failed: {exc}"

    # Confirm the committed booking and ticket rows from a fresh session before
    # reporting success to the agent.
    try:
        async with AsyncSessionLocal() as verify_session:
            saved_booking = await crud.get_booking(verify_session, booking.booking_id)
            saved_tickets = await crud.get_booking_tickets(
                verify_session, user_id, booking.booking_id
            )
            event = await crud.get_event(verify_session, eid)
    except Exception:
        return (
            "Booking may have been saved, but its database state could not be "
            "verified. Do not retry automatically; check the booking before trying again."
        )

    expected_tickets = Counter(
        (
            (person.get("name") or "").strip(),
            (person.get("email") or "").strip(),
            (person.get("telephone") or "").strip() or None,
        )
        for person in people
    )
    actual_tickets = Counter(
        (ticket.name, ticket.email, ticket.telephone) for ticket in saved_tickets
    )
    if (
        saved_booking is None
        or saved_booking.user_id != user_id
        or saved_booking.event_id != eid
        or saved_booking.status.value != "confirmed"
        or actual_tickets != expected_tickets
        or event is None
    ):
        return (
            "Booking may have been saved, but the stored booking or attendee details "
            "did not match the request. Do not retry automatically; check the booking."
        )

    title = event.title
    where = event.location or "location unknown"
    names = ", ".join(ticket.name for ticket in saved_tickets)
    return (
        "Booking confirmed.\n"
        f"Reference: CCIFJ-EVT-{saved_booking.booking_id:05d}\n"
        f"Event: {title} ({_when(event.start_at)}, {where})\n"
        f"Tickets: {len(saved_tickets)} ({names})\n"
        f"Price per ticket: {_price_text(event)}\n"
        "This is a registration only; no payment was taken."
    )


@tool
async def book_space(
    start_date: str,
    end_date: str,
    title: str,
    space_name: str = "L'Espace@CCIFJ",
    expected_attendees: Optional[int] = None,
    is_public: bool = False,
    notes: str = "",
    runtime: ToolRuntime = None
) -> str:
    """
    Reserve CCIFJ's event and conference facilities (such as L'Espace).
    The user_id is automatically accessed from the runtime state (runtime.state['user_id']).
    The user must be logged in.

    About L'Espace @ CCIFJ:
    L'Espace is CCIFJ's modular event and conference room located in Nihonbashi, Tokyo.
    It accommodates seminars, corporate presentations, cocktail receptions, and board meetings.

    Use this tool when:
    - The user wants to rent or book L'Espace or meeting facilities for a specific date and time.
    - The user provides the requested reservation dates and a title or purpose.

    Args:
        start_date: Reservation start date and time (formatted as YYYY-MM-DD or YYYY-MM-DD HH:MM).
            A date without a time means the start of that day.
        end_date: Reservation end date and time (formatted as YYYY-MM-DD or YYYY-MM-DD HH:MM).
            A date without a time means the end of that day.
        title: Short title or purpose of the reservation (e.g. "Board meeting").
        space_name: Name of the facility to reserve. Defaults to "L'Espace@CCIFJ".
        expected_attendees: Optional number of people expected.
        is_public: True if the reserved session is a public event, False if private.
        notes: Optional extra details or requests from the user.

    Returns:
        A reservation receipt containing:
        - Space booking reference ID (CCIFJ-SPC-XXXXX)
        - Reserved space name, scheduled dates, and duration
        - Booking status (reservations are confirmed immediately if the slot is free)
        Or a clear error (e.g. the time slot is already taken, or exceeds capacity).

    Behavior & Database Expectations:
        - Accesses user_id via runtime.state['user_id'].
        - Checks for date/time scheduling conflicts against existing space reservations.
        - Inserts the reservation record linked to user_id in the facility bookings table.
        - Equipment or catering requests are not handled automatically.
    """
    user_id = _get_user_id(runtime)
    if user_id is None:
        return "Reservation failed: you must be logged in to reserve a space."

    try:
        start = _parse_datetime(start_date)
        end = _parse_datetime(end_date, end_of_day=True)
    except ValueError:
        return "Invalid date. Use YYYY-MM-DD or YYYY-MM-DD HH:MM."

    async with AsyncSessionLocal() as session:
        spaces = await crud.list_spaces(session)
        if not spaces:
            return "Reservation failed: no spaces are available for reservation yet."

        space = _find_space(spaces, space_name)
        if space is None:
            available = ", ".join(s.name for s in spaces)
            return f"Reservation failed: unknown space '{space_name}'. Available spaces: {available}."

        try:
            reservation = await crud.create_reservation(
                session,
                user_id,
                space.space_id,
                title,
                start,
                end,
                is_public=is_public,
                expected_attendees=expected_attendees,
                notes=notes or None,
            )
        except ValueError as exc:
            return f"Reservation failed: {exc}"

    # Reload the committed reservation and space before returning a confirmation.
    try:
        async with AsyncSessionLocal() as verify_session:
            saved_reservation = await crud.get_reservation(
                verify_session, reservation.reservation_id
            )
            saved_space = await crud.get_space(verify_session, space.space_id)
    except Exception:
        return (
            "The reservation may have been saved, but its database state could not "
            "be verified. Do not retry automatically; check the reservation first."
        )

    if (
        saved_reservation is None
        or saved_space is None
        or saved_reservation.user_id != user_id
        or saved_reservation.space_id != space.space_id
        or saved_reservation.event_id is not None
        or saved_reservation.title != title.strip()
        or saved_reservation.start_at != start
        or saved_reservation.end_at != end
        or saved_reservation.is_public != is_public
        or saved_reservation.expected_attendees != expected_attendees
        or saved_reservation.notes != (notes or None)
        or saved_reservation.status.value != "confirmed"
    ):
        return (
            "The reservation may have been saved, but its stored details did not "
            "match the request. Do not retry automatically; check the reservation first."
        )

    minutes = int(
        (saved_reservation.end_at - saved_reservation.start_at).total_seconds() // 60
    )
    return (
        "Space reservation confirmed.\n"
        f"Reference: CCIFJ-SPC-{saved_reservation.reservation_id:05d}\n"
        f"Space: {saved_space.name}\n"
        f"From: {_when(saved_reservation.start_at)}\n"
        f"To: {_when(saved_reservation.end_at)} (duration {minutes // 60} h {minutes % 60:02d} min)\n"
        f"Title: {saved_reservation.title} ({'public' if saved_reservation.is_public else 'private'})\n"
        f"Status: {saved_reservation.status.value.upper()}"
    )


#################################################
#
# 1b. The user's bookings and reservations
#
#################################################

@tool
async def get_my_bookings(
    include_past: bool = False,
    include_cancelled: bool = False,
    runtime: ToolRuntime = None,
) -> str:
    """
    List the logged-in user's event bookings with their references and attendees.

    Use this tool when the user asks what they have booked or registered for,
    wants a booking reference, or before cancelling or changing a booking.

    Args:
        include_past: Also list bookings for events that have already started.
        include_cancelled: Also list cancelled bookings.
        runtime: Injected ToolRuntime providing access to the current graph state.

    Returns:
        One block per booking: reference, status, event, date, venue, and attendees.
    """
    user_id = _get_user_id(runtime)
    if user_id is None:
        return "You must be logged in to see your bookings."

    now = _now()
    async with AsyncSessionLocal() as session:
        rows = await crud.list_user_bookings(session, user_id, include_cancelled=include_cancelled)
        rows = [
            (booking, event) for booking, event in rows
            if include_past or event.start_at is None or event.start_at > now
        ]
        blocks = []
        for booking, event in rows:
            tickets = await crud.get_booking_tickets(session, user_id, booking.booking_id)
            people = ", ".join(f"{t.name} <{t.email}>" for t in tickets) or "none"
            blocks.append(
                f"{_booking_ref(booking.booking_id)} | {booking.status.value.upper()}\n"
                f"  Event: {_event_line(event)}\n"
                f"  Attendees ({len(tickets)}): {people}"
            )
    if not blocks:
        return "No bookings found for this account with these filters."
    return "\n".join(blocks)


@tool
async def get_my_space_reservations(
    include_past: bool = False,
    runtime: ToolRuntime = None,
) -> str:
    """
    List the logged-in user's confirmed space reservations (such as L'Espace@CCIFJ).

    Use this tool when the user asks whether or when they reserved a room, wants
    a reservation reference, or before cancelling or changing a reservation.

    Args:
        include_past: Also list reservations that have already ended.
        runtime: Injected ToolRuntime providing access to the current graph state.

    Returns:
        One line per reservation: reference, space, dates, title, and attendees.
    """
    user_id = _get_user_id(runtime)
    if user_id is None:
        return "You must be logged in to see your space reservations."

    async with AsyncSessionLocal() as session:
        rows = await crud.list_user_reservations(session, user_id, upcoming_only=not include_past)
    if not rows:
        return "No confirmed space reservations found for this account."
    return "\n".join(
        f"{_reservation_ref(r.reservation_id)} | {space.name} | {_when(r.start_at)} -> {_when(r.end_at)} "
        f"| {r.title} ({'public' if r.is_public else 'private'})"
        f"{f' | {r.expected_attendees} attendees' if r.expected_attendees else ''}"
        for r, space in rows
    )


@tool
async def check_space_availability(
    start_date: str,
    end_date: str,
    space_name: str = "L'Espace@CCIFJ",
    runtime: ToolRuntime = None,
) -> str:
    """
    Check whether a CCIFJ space is free for a time range, and suggest free slots.

    Use this tool before collecting reservation details, and whenever the user
    asks if or when a space is free. Guests may use it.

    Args:
        start_date: Range start (YYYY-MM-DD or YYYY-MM-DD HH:MM). A date alone means 00:00.
        end_date: Range end (YYYY-MM-DD or YYYY-MM-DD HH:MM). A date alone means the end of that day.
        space_name: The space to check. Defaults to "L'Espace@CCIFJ".
        runtime: Injected ToolRuntime providing access to the current graph state.

    Returns:
        FREE or TAKEN for the range, the reserved times inside it, the free gaps,
        and the next free slot of the same length when the range is taken.
    """
    try:
        start = _parse_datetime(start_date)
        end = _parse_datetime(end_date, end_of_day=True)
    except ValueError:
        return "Invalid date. Use YYYY-MM-DD or YYYY-MM-DD HH:MM."
    if end <= start:
        return "The end must be after the start."
    user_id = _get_user_id(runtime)

    async with AsyncSessionLocal() as session:
        spaces = await crud.list_spaces(session)
        space = _find_space(spaces, space_name) if spaces else None
        if space is None:
            available = ", ".join(s.name for s in spaces) or "none"
            return f"Unknown space '{space_name}'. Available spaces: {available}."
        taken = list(await crud.list_space_reservations(session, space.space_id, start, end))
        # Look ahead two weeks for the next free slot of the same length.
        horizon = list(await crud.list_space_reservations(
            session, space.space_id, start, end + timedelta(days=14)
        ))

    capacity = f" (capacity {space.capacity})" if space.capacity else ""
    past_note = " Note: part of this range is in the past." if start <= _now() else ""
    if not taken:
        return f"FREE: {space.name}{capacity} has no reservations from {_when(start)} to {_when(end)}.{past_note}"

    def label(r) -> str:
        if user_id is not None and r.user_id == user_id:
            return f"your reservation {_reservation_ref(r.reservation_id)} '{r.title}'"
        # Never reveal the titles of other people's private reservations.
        return f"public event '{r.title}'" if r.is_public else "reserved"

    lines = [f"TAKEN: {space.name}{capacity} is not free for the whole range {_when(start)} to {_when(end)}.{past_note}",
             "Reserved times in this range:"]
    lines += [f"- {_when(r.start_at)} -> {_when(r.end_at)} ({label(r)})" for r in taken]

    gaps, cursor = [], start
    for r in sorted(taken, key=lambda r: r.start_at):
        if r.start_at > cursor:
            gaps.append((cursor, r.start_at))
        cursor = max(cursor, r.end_at)
    if cursor < end:
        gaps.append((cursor, end))
    if gaps:
        lines.append("Free gaps in this range:")
        lines += [f"- {_when(a)} -> {_when(b)}" for a, b in gaps]

    length, cursor = end - start, start
    for r in sorted(horizon, key=lambda r: r.start_at):
        if r.start_at - cursor >= length:
            break
        cursor = max(cursor, r.end_at)
    lines.append(f"Next free slot of the same length: {_when(cursor)} -> {_when(cursor + length)}.")
    return "\n".join(lines)


@tool
async def check_event_booking(
    event_id: str,
    runtime: ToolRuntime = None,
) -> str:
    """
    Check whether the user can book an event before collecting attendee details.

    Checks that the event exists, has not started, and that registration is still
    open. For a logged-in user, also checks for an existing booking of this event
    and for time clashes with their other bookings and space reservations.
    Guests may use it (login and clash checks are then skipped).

    Args:
        event_id: The event to check.
        runtime: Injected ToolRuntime providing access to the current graph state.

    Returns:
        CAN BOOK or CANNOT BOOK with the reason, plus any clash warnings.
    """
    eid = _to_int(event_id)
    if eid is None:
        return "CANNOT BOOK: invalid event ID."
    user_id = _get_user_id(runtime)

    async with AsyncSessionLocal() as session:
        event = await crud.get_event(session, eid)
        if event is None:
            return f"CANNOT BOOK: no event with ID {eid} exists."
        bookings = await crud.list_user_bookings(session, user_id) if user_id is not None else []
        reservations = await crud.list_user_reservations(session, user_id) if user_id is not None else []

    summary = f"Event: {_event_line(event)} | Registration deadline: {_when(event.reg_deadline, 'none stated')}"
    problem = _registration_problem(event)
    if problem:
        return f"CANNOT BOOK: {problem}.\n{summary}"
    existing = next((b for b, e in bookings if e.event_id == eid), None)
    if existing is not None:
        return (
            f"CANNOT BOOK: the user already has a confirmed booking for this event "
            f"({_booking_ref(existing.booking_id)}). To change its attendees, use change_event_booking.\n{summary}"
        )

    warnings = []
    start, end = _event_window(event)
    if start is not None:
        for booking, other in bookings:
            o_start, o_end = _event_window(other)
            if o_start is not None and _overlaps(start, end, o_start, o_end):
                warnings.append(f"- Overlaps the user's booking {_booking_ref(booking.booking_id)}: {other.title} ({_when(o_start)})")
        for r, space in reservations:
            if _overlaps(start, end, r.start_at, r.end_at):
                warnings.append(f"- Overlaps the user's reservation {_reservation_ref(r.reservation_id)} at {space.name} ({_when(r.start_at)} -> {_when(r.end_at)})")

    login_note = "" if user_id is not None else "\nThe user is not logged in: booking requires login."
    clash_note = ("\nSchedule clashes (tell the user; they may still book):\n" + "\n".join(warnings)) if warnings else ""
    return f"CAN BOOK.\n{summary}{login_note}{clash_note}"


@tool
async def cancel_event_booking(
    booking_reference: str,
    runtime: ToolRuntime = None,
) -> str:
    """
    Cancel one of the user's event bookings (the whole booking, all its tickets).

    Allowed until the event starts. Use get_my_bookings first to find the
    reference, and call this only after the user explicitly confirms.

    Args:
        booking_reference: The booking reference, e.g. 'CCIFJ-EVT-00012' or '12'.
        runtime: Injected ToolRuntime providing access to the current graph state.

    Returns:
        Confirmation of the cancellation, or why it could not be cancelled.
    """
    user_id = _get_user_id(runtime)
    if user_id is None:
        return "Cancellation failed: you must be logged in."
    booking_id = _ref_id(booking_reference)
    if booking_id is None:
        return "Cancellation failed: invalid booking reference."

    async with AsyncSessionLocal() as session:
        try:
            booking = await crud.cancel_booking(session, user_id, booking_id)
        except ValueError as exc:
            return f"Cancellation failed: {exc}"
        event = await crud.get_event(session, booking.event_id)

    try:
        async with AsyncSessionLocal() as verify_session:
            saved = await crud.get_booking(verify_session, booking_id)
    except Exception:
        return "The booking may have been cancelled, but this could not be verified. Do not retry; check the booking first."
    if saved is None or saved.status != BookingStatus.cancelled:
        return "The cancellation could not be verified. Do not retry; check the booking first."
    return (
        f"Booking cancelled: {_booking_ref(booking_id)}\n"
        f"Event: {event.title if event else 'unknown'} ({_when(event.start_at) if event else 'date unknown'})\n"
        "All tickets on this booking are cancelled. No payment was involved."
    )


@tool
async def cancel_space_reservation(
    reservation_reference: str,
    runtime: ToolRuntime = None,
) -> str:
    """
    Cancel one of the user's space reservations.

    Allowed until the reservation starts. Use get_my_space_reservations first to
    find the reference, and call this only after the user explicitly confirms.

    Args:
        reservation_reference: The reservation reference, e.g. 'CCIFJ-SPC-00003' or '3'.
        runtime: Injected ToolRuntime providing access to the current graph state.

    Returns:
        Confirmation of the cancellation, or why it could not be cancelled.
    """
    user_id = _get_user_id(runtime)
    if user_id is None:
        return "Cancellation failed: you must be logged in."
    reservation_id = _ref_id(reservation_reference)
    if reservation_id is None:
        return "Cancellation failed: invalid reservation reference."

    async with AsyncSessionLocal() as session:
        try:
            reservation = await crud.cancel_reservation(session, user_id, reservation_id)
        except ValueError as exc:
            return f"Cancellation failed: {exc}"
        space = await crud.get_space(session, reservation.space_id)

    try:
        async with AsyncSessionLocal() as verify_session:
            saved = await crud.get_reservation(verify_session, reservation_id)
    except Exception:
        return "The reservation may have been cancelled, but this could not be verified. Do not retry; check it first."
    if saved is None or saved.status != ReservationStatus.cancelled:
        return "The cancellation could not be verified. Do not retry; check the reservation first."
    return (
        f"Space reservation cancelled: {_reservation_ref(reservation_id)}\n"
        f"Space: {space.name if space else 'unknown'} | {_when(saved.start_at)} -> {_when(saved.end_at)} | {saved.title}"
    )


@tool
async def change_event_booking(
    booking_reference: str,
    new_event_id: Optional[str] = None,
    attendees: Optional[list[AttendeeInfo]] = None,
    runtime: ToolRuntime = None,
) -> str:
    """
    Change one of the user's event bookings in a single step: move it to another
    event and/or replace its attendee list (add or remove people).

    Behind the scenes the old booking is cancelled and a new one is created; if
    the new booking fails, the old booking is restored. Present the result to the
    user as a change. Call only after the user explicitly confirms the change.

    Args:
        booking_reference: The booking to change, e.g. 'CCIFJ-EVT-00012' or '12'.
        new_event_id: The event to move the booking to. Omit to keep the same event.
        attendees: The complete new attendee list (name, email, optional telephone).
            Omit to keep the current attendees.
        runtime: Injected ToolRuntime providing access to the current graph state.

    Returns:
        The new booking reference and summary, or why the change was not made.
    """
    user_id = _get_user_id(runtime)
    if user_id is None:
        return "Change failed: you must be logged in."
    booking_id = _ref_id(booking_reference)
    if booking_id is None:
        return "Change failed: invalid booking reference."

    async with AsyncSessionLocal() as session:
        booking = await crud.get_booking(session, booking_id)
        if booking is None or booking.user_id != user_id:
            return "Change failed: no booking with that reference was found for your account."
        if booking.status != BookingStatus.confirmed:
            return "Change failed: this booking is already cancelled. Book again instead."
        old_event = await crud.get_event(session, booking.event_id)
        target_id = _to_int(new_event_id) if new_event_id else booking.event_id
        if target_id is None:
            return "Change failed: invalid new event ID."
        target = await crud.get_event(session, target_id)
        if target is None:
            return f"Change failed: no event with ID {target_id} exists."
        if old_event is not None and old_event.start_at is not None and old_event.start_at <= _now():
            return "Change failed: the booked event has already started."
        problem = _registration_problem(target)
        if problem:
            return f"Change failed: {problem} for '{target.title}'. The original booking is unchanged."
        if target_id != booking.event_id:
            other = next((b for b, e in await crud.list_user_bookings(session, user_id) if e.event_id == target_id), None)
            if other is not None:
                return f"Change failed: the user already has a booking for that event ({_booking_ref(other.booking_id)})."
        if attendees:
            people = [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in attendees]
        else:
            tickets = await crud.get_booking_tickets(session, user_id, booking_id)
            people = [{"name": t.name, "email": t.email, "telephone": t.telephone} for t in tickets]
        if not people:
            return "Change failed: the booking needs at least one attendee. To drop everyone, cancel the booking."
        if target_id == booking.event_id and not attendees:
            return "Nothing to change: give a new event or a new attendee list."

        try:
            await crud.cancel_booking(session, user_id, booking_id)
        except ValueError as exc:
            return f"Change failed: {exc}"

    try:
        async with AsyncSessionLocal() as session:
            new_booking = await crud.book_event(session, user_id, target_id, people)
    except Exception as exc:
        restored = await _restore_status(Booking, booking_id, BookingStatus.confirmed)
        reason = str(exc).rstrip(".") if isinstance(exc, ValueError) else "the new booking could not be saved"
        if restored:
            return f"Change failed: {reason}. The original booking {_booking_ref(booking_id)} is still active."
        return (
            f"Change failed: {reason}, and the original booking {_booking_ref(booking_id)} could not be "
            "restored automatically. Do not retry; tell the user to contact CCIFJ events staff."
        )

    names = ", ".join(person["name"] for person in people)
    return (
        "Booking changed.\n"
        f"New reference: {_booking_ref(new_booking.booking_id)} (replaces {_booking_ref(booking_id)})\n"
        f"Event: {target.title} ({_when(target.start_at)}, {target.location or 'location unknown'})\n"
        f"Tickets: {len(people)} ({names})\n"
        "This is a registration only; no payment was taken."
    )


@tool
async def change_space_reservation(
    reservation_reference: str,
    new_start_date: Optional[str] = None,
    new_end_date: Optional[str] = None,
    new_title: Optional[str] = None,
    new_expected_attendees: Optional[int] = None,
    runtime: ToolRuntime = None,
) -> str:
    """
    Change one of the user's space reservations in a single step (new dates,
    title, or number of attendees).

    Behind the scenes the old reservation is cancelled and a new one is created;
    if the new one fails, the old reservation is restored. Check the new dates
    with check_space_availability first. Present the result to the user as a
    change. Call only after the user explicitly confirms the change.

    Args:
        reservation_reference: The reservation to change, e.g. 'CCIFJ-SPC-00003' or '3'.
        new_start_date: New start (YYYY-MM-DD or YYYY-MM-DD HH:MM). Omit to keep it.
        new_end_date: New end (YYYY-MM-DD or YYYY-MM-DD HH:MM). Omit to keep it.
        new_title: New title or purpose. Omit to keep it.
        new_expected_attendees: New number of attendees. Omit to keep it.
        runtime: Injected ToolRuntime providing access to the current graph state.

    Returns:
        The new reservation reference and summary, or why the change was not made.
    """
    user_id = _get_user_id(runtime)
    if user_id is None:
        return "Change failed: you must be logged in."
    reservation_id = _ref_id(reservation_reference)
    if reservation_id is None:
        return "Change failed: invalid reservation reference."

    async with AsyncSessionLocal() as session:
        old = await crud.get_reservation(session, reservation_id)
        if old is None or old.user_id != user_id:
            return "Change failed: no reservation with that reference was found for your account."
        if old.status != ReservationStatus.confirmed:
            return "Change failed: this reservation is already cancelled. Reserve again instead."
        if old.start_at <= _now():
            return "Change failed: this reservation has already started."
        try:
            start = _parse_datetime(new_start_date) if new_start_date else old.start_at
            end = _parse_datetime(new_end_date, end_of_day=True) if new_end_date else old.end_at
        except ValueError:
            return "Change failed: invalid date. Use YYYY-MM-DD or YYYY-MM-DD HH:MM."
        title = (new_title or old.title).strip()
        expected = new_expected_attendees if new_expected_attendees is not None else old.expected_attendees
        if (start, end, title, expected) == (old.start_at, old.end_at, old.title, old.expected_attendees):
            return "Nothing to change: give new dates, a new title, or a new number of attendees."
        if end <= start or start <= _now():
            return "Change failed: the new time must be in the future and end after it starts."
        conflicts = [
            r for r in await crud.find_space_conflicts(session, old.space_id, start, end)
            if r.reservation_id != reservation_id
        ]
        if conflicts:
            return "Change failed: the new time overlaps another reservation. Use check_space_availability to find a free slot. The original reservation is unchanged."
        space = await crud.get_space(session, old.space_id)
        try:
            await crud.cancel_reservation(session, user_id, reservation_id)
        except ValueError as exc:
            return f"Change failed: {exc}"

    try:
        async with AsyncSessionLocal() as session:
            new = await crud.create_reservation(
                session, user_id, old.space_id, title, start, end,
                is_public=old.is_public, expected_attendees=expected,
                notes=old.notes, event_id=old.event_id,
            )
    except Exception as exc:
        restored = await _restore_status(SpaceReservation, reservation_id, ReservationStatus.confirmed)
        reason = str(exc).rstrip(".") if isinstance(exc, ValueError) else "the new reservation could not be saved"
        if restored:
            return f"Change failed: {reason}. The original reservation {_reservation_ref(reservation_id)} is still active."
        return (
            f"Change failed: {reason}, and the original reservation {_reservation_ref(reservation_id)} could not be "
            "restored automatically. Do not retry; tell the user to contact CCIFJ staff."
        )

    return (
        "Space reservation changed.\n"
        f"New reference: {_reservation_ref(new.reservation_id)} (replaces {_reservation_ref(reservation_id)})\n"
        f"Space: {space.name if space else 'unknown'}\n"
        f"From: {_when(new.start_at)}\nTo: {_when(new.end_at)}\n"
        f"Title: {new.title}{f' | {new.expected_attendees} attendees' if new.expected_attendees else ''}"
    )


#################################################
#
# 2. Membership Tools
#
#################################################

@tool
async def get_membership_tier_info(
    tier_or_category_name: Optional[str] = None,
) -> str:
    """
    Retrieve official CCIFJ membership category information, fees, and benefits.

    Use this tool when:
    - The user asks about membership prices or annual dues.
    - The user wants to compare membership categories (Benefactor, Active, Startup, Non-Resident).
    - The user asks about a specific category's eligibility or benefits.
    - The user does not know which membership tier to choose.

    Args:
        tier_or_category_name: Optional membership category name or alias
            (e.g., 'startup', 'active', 'benefactor', 'non-resident').
            If omitted, return an overview of all available categories.

    Returns:
        The requested category's official annual fee (with its currency),
        description of eligibility and benefits, or a comparative overview of all tiers.
    """
    def describe(c) -> str:
        return (
            f"{c.category_name}\n"
            f"  Fee: {_money(c.annual_price, c.currency)} per year\n"
            f"  Details: {c.category_description}"
        )

    async with AsyncSessionLocal() as session:
        if tier_or_category_name:
            category = await crud.get_member_category(session, tier_or_category_name)
            if category is not None:
                return describe(category)
        categories = await crud.list_member_categories(session)

    if not categories:
        return "No membership category information is available."

    prefix = ""
    if tier_or_category_name:
        prefix = f"No category matched '{tier_or_category_name}'. Available categories:\n\n"
    return prefix + "\n\n".join(describe(c) for c in categories)


# Member regions are stored as written in each company's address: Tokyo wards
# ("Minato-ku", "CHIYODA-KU"), cities ("OSAKA", "Yokohama-shi"), or towns in
# France. A region the user names is searched as the places inside it.
_TOKYO_PLACES = (
    "Tokyo", "Chiyoda", "Chuo", "Minato", "Shinjuku", "Bunkyo", "Taito", "Taitou", "Sumida",
    "Koto", "Shinagawa", "Meguro", "Ota", "Setagaya", "Shibuya", "Nakano", "Suginami",
    "Toshima", "Kita", "Arakawa", "Itabashi", "Nerima", "Adachi", "Katsushika", "Edogawa",
    "Minamiazabu", "Chofu", "Mitaka", "Machida", "町田", "西東京",
)
_MEMBER_REGION_PLACES = {
    "tokyo": _TOKYO_PLACES,
    "kanto": _TOKYO_PLACES + (
        "Yokohama", "Kawasaki", "Sagamihara", "Ebina", "Zushi", "Ashigara", "Chiba", "Narashino",
        "Minamiboso", "Saitama", "Sayama", "Higashimatsuyama", "Maebashi", "Tatebayashi",
        "Annaka", "Ashikaga", "Utsunomiya", "Mito", "Tsukuba",
    ),
    "kansai": (
        "Osaka", "Osakasayama", "Kyoto", "Nakagyo", "Kobe", "Nara", "Nishinomiya", "Amagasaki",
        "Ashiya", "Suita", "Sakai", "Hirakata", "Izumisano", "Himeji", "Ibaraki", "Arida",
        "Hikone", "Yamatotakada",
    ),
    "chubu": (
        "Nagoya", "Kariya", "Okazaki", "Ogaki", "Nakatugawa", "Chino", "Nagano", "Shizuoka",
        "Iwata", "Shimada", "Fukui", "Kanazawa", "Komatsu", "Nanao", "Kofu", "Fuefuki",
        "Suzuka", "Yokkaichi", "Tokoname", "Nagaizumi", "Tateyama",
    ),
    "kyushu": ("Fukuoka", "Nagasaki"),
    "hokkaido": ("Sapporo", "Hokkaido"),
    "chugoku": ("Onomichi", "Iwakuni", "Matsue", "Izumo", "Tsuyama"),
    "shikoku": ("Tosa",),
}
# Asking for "Japan" (or anywhere) is no filter at all.
_NO_REGION_FILTER = {"japan", "japon", "日本", "all", "any", "anywhere", "everywhere"}
_MEMBER_FETCH_LIMIT = 1000


def _member_region_pattern(region: str):
    """A whole-word pattern for the places in `region`, or None for no filter."""
    places = []
    for part in _split_terms(region):
        key = part.casefold()
        if key in _NO_REGION_FILTER:
            return None
        places.extend(_MEMBER_REGION_PLACES.get(key, (part,)))
    if not places:
        return None
    places = list(dict.fromkeys(places))
    # Whole words only: "Nara" must not match "Narashino", "Kita" not "Kitaazumi".
    # Japanese has no spaces between words ("町田市"), so its names match anywhere.
    latin = [re.escape(p) for p in places if p.isascii()]
    other = [re.escape(p) for p in places if not p.isascii()]
    parts = ([r"(?<![^\W\d_])(?:" + "|".join(latin) + r")(?![^\W\d_])"] if latin else []) + other
    return re.compile("|".join(parts), re.IGNORECASE)


@tool
async def search_member_directory(
    industry: Optional[str] = None,
    region: Optional[str] = None,
) -> str:
    """
    Search the CCIFJ member directory for companies matching the provided industry and/or region.

    Use this tool when:
    - The user wants to find member companies in the CCIFJ network.
    - The user asks for companies in a particular sector (e.g., IT, Energy, Luxury, Aerospace).
    - The user asks for companies in a particular place.

    Args:
        industry: Optional directory sector, e.g. "Wine and Spirits", "Food & Beverage",
            "Distribution", "IT & Computer". Use list_available_industries_and_regions
            for the exact list.
        region: Optional. Only set it when the user asked for a place. Accepts a region
            (Tokyo, Kanto, Kansai, Chubu, Kyushu, Hokkaido, Chugoku, Shikoku) or a city
            (Osaka, Yokohama, Paris...); comma-separate several. "Japan" means no filter.

    Returns:
        Matching company records including company names, sectors, regions,
        and directory descriptions.
    """
    pattern = _member_region_pattern(region or "")
    if not industry and pattern is None:
        return (
            "Please provide an industry and/or a place in Japan or abroad to search. "
            "Use list_available_industries_and_regions to see the industries."
        )

    async with AsyncSessionLocal() as session:
        members = await crud.search_members(
            session,
            sector=industry or None,
            # Places are matched below, so fetch the whole sector first.
            limit=_MEMBER_FETCH_LIMIT if pattern else PAGE_SIZE + 1,
        )

    note = ""
    if pattern is not None:
        in_place = [m for m in members if pattern.search(m.region or "")]
        if not in_place and members and industry:
            # Better to show the sector elsewhere than nothing at all.
            note = f"No {industry} members are listed in {region}; these {industry} members are elsewhere.\n\n"
        else:
            members = in_place
    members = list(members)[:PAGE_SIZE + 1]

    if not members:
        return (
            "No member companies matched those filters. "
            "Use list_available_industries_and_regions to see the industries."
        )

    blocks = []
    for m in members[:PAGE_SIZE]:
        place = ", ".join(p for p in (m.region, m.country) if p) or "region unknown"
        block = f"{m.name} | Industry: {m.sector or 'unknown'} | {place}"
        if m.description:
            desc = m.description if len(m.description) <= 200 else m.description[:200] + "..."
            block += f"\n  {desc}"
        blocks.append(block)

    text = note + "\n\n".join(blocks)
    if len(members) > PAGE_SIZE:
        text += f"\n\nShowing the first {PAGE_SIZE} results; more companies match. Add another filter to narrow it down."
    return text


@tool
async def list_available_industries_and_regions() -> str:
    """
    Retrieve the distinct industry and region values supported by the CCIFJ member directory.

    Use this tool when:
    - The user asks which industries or sectors are represented in CCIFJ.
    - The user asks which regions can be searched.
    - The agent is unsure how an industry or region is categorized in the directory.

    Returns:
        The available industry and region values supported by the directory.
    """
    async with AsyncSessionLocal() as session:
        sectors = [s for s in await crud.list_sectors(session) if s]

    # Stored regions are raw address parts (wards, towns, typos); offer the
    # names search_member_directory understands instead.
    regions = ", ".join(name.capitalize() for name in _MEMBER_REGION_PLACES)
    return (
        f"Industries: {', '.join(sectors) or 'none found'}\n"
        f"Regions: {regions}, or any city name (e.g. Osaka, Yokohama, Paris). "
        "Leave the region empty to search all of Japan and abroad."
    )


#################################################
#
# 3. General & Business Tools
#
#################################################

@tool
def get_ccifj_contact(
    topic: Literal[
        "events",
        "become_speaker",
        "become_sponsor",
        "bonjour_france",
        "membership",
        "corporate_news",
        "japanese_market_support",
        "recruitment_and_hr",
    ],
) -> str:
    """
    Retrieve direct contact person details (name, title, email, phone number)
    for specific CCIFJ inquiries.

    Use this tool when the user wants to:
    - Ask about a CCIFJ event, registration problems, or late registration.
    - Inquire about becoming a speaker at CCIFJ events or conferences.
    - Sponsor CCIFJ events, summits, or business forums.
    - Learn about BONJOUR FRANCE, the starting point for companies that want to work in Japan.
    - Ask about CCIFJ membership, joining, dues, or member services.
    - Share corporate news or press releases with CCIFJ media.
    - Ask questions about entering or developing in the Japanese market.
    - Inquire about recruitment, hiring, posting job offers, or HR services.

    Args:
        topic: The specific subject of inquiry. Must be one of:
            - 'events': General event questions, registration issues, late registration (Elena SUZUKI).
            - 'become_speaker': Inquire about speaking at events (Elena SUZUKI).
            - 'become_sponsor': Inquire about sponsorship opportunities (Marie OZAKI & Elena SUZUKI).
            - 'bonjour_france': BONJOUR FRANCE, the starting point for companies that want to work in Japan (Mayu HANDA).
            - 'membership': Membership, joining, dues, and member services (Tomoyo FUNABASHI & Marianne BARGES).
            - 'corporate_news': Submit or share corporate news with CCIFJ (Communication Dept & Marianne BARGES).
            - 'japanese_market_support': Advisory & support for French companies entering the Japanese market.
            - 'recruitment_and_hr': Inquiries regarding job postings, recruitment, and HR support (Nicolas CHAUVET & Haruka KOBAYASHI).

    Returns:
        Verified contact details including the officer name, title, email address,
        and telephone number where available.
    """
    contacts = {
        "events": (
            "Contact for CCIFJ Events (registration and event questions):\n"
            "- Name: Elena SUZUKI\n"
            "- Role: Events Officer\n"
            "- Phone: 03-4500-6606\n"
            "- Email: e.suzuki@ccifj.or.jp"
        ),
        "bonjour_france": (
            "Contact for BONJOUR FRANCE (starting point for companies that want to work in Japan):\n"
            "- Name: Mayu HANDA\n"
            "- Role: Director, BONJOUR FRANCE\n"
            "- Phone: 03-4500-6530\n"
            "- Email: m.handa@ccifj.or.jp"
        ),
        "membership": (
            "Contacts for CCIFJ Membership:\n"
            "1. Tomoyo FUNABASHI\n"
            "   - Role: Membership Manager\n"
            "   - Phone: 03-4500-6644\n"
            "   - Email: t.funabashi@ccifj.or.jp\n"
            "2. Marianne BARGES\n"
            "   - Role: Membership Associate Manager & Communication Coordinator\n"
            "   - Phone: 03-4500-6522\n"
            "   - Email: m.barges@ccifj.or.jp"
        ),
        "become_speaker": (
            "Contact for Event Speakers:\n"
            "- Name: Elena SUZUKI\n"
            "- Role: Events Officer\n"
            "- Phone: 03-4500-6606\n"
            "- Email: e.suzuki@ccifj.or.jp"
        ),
        "become_sponsor": (
            "Contacts for Event Sponsorship:\n"
            "1. Marie OZAKI\n"
            "   - Role: Managing Director, Head of Events\n"
            "   - Phone: 03-4500-6531\n"
            "   - Email: m.ozaki@ccifj.or.jp\n"
            "2. Elena SUZUKI\n"
            "   - Role: Events Officer\n"
            "   - Phone: 03-4500-6606\n"
            "   - Email: e.suzuki@ccifj.or.jp"
        ),
        "corporate_news": (
            "Contacts for Sharing Corporate News:\n"
            "1. Communication Department\n"
            "   - Email: communication@ccifj.or.jp\n"
            "2. Marianne BARGES\n"
            "   - Role: Membership Associate Manager & Communication Coordinator\n"
            "   - Phone: 03-4500-6522\n"
            "   - Email: m.barges@ccifj.or.jp"
        ),
        "japanese_market_support": (
            "Contact for Japanese Market Entry & Business Support:\n"
            "- Department: Business Support / Appui aux Entreprises\n"
            "- Email: appui.entreprises@ccifj.or.jp\n"
            "- Note: For commercial inquiries, users can also submit the Business Development Form."
        ),
        "recruitment_and_hr": (
            "Contacts for Recruitment & Job Offers (Employment & Training Department):\n"
            "1. Nicolas CHAUVET\n"
            "   - Role: Responsable Recrutement (Recruitment Manager)\n"
            "   - Phone: 03-4500-6521\n"
            "   - Email: n.chauvet@ccifj.or.jp\n"
            "2. Haruka KOBAYASHI\n"
            "   - Role: Recruitment Officer\n"
            "   - Phone: 03-4500-6529\n"
            "   - Email: h.kobayashi@ccifj.or.jp\n"
            "- Note: Job seekers can also register their resume via the CV Database form."
        ),
    }

    return contacts.get(topic, "Contact details not found for this topic.")


@tool
def get_official_form(
    form_type: Literal[
        "membership_application",
        "cv_database_registration",
        "business_development_inquiry",
    ],
    lang: Literal["en", "fr", "jp"] = "en",
) -> str:
    """
    Retrieve direct links to official CCIFJ registration and inquiry forms.

    Use this tool when the user needs:
    - The CCIFJ membership application form.
    - The CV / Resume submission form for candidates seeking employment in Japan.
    - The commercial support inquiry form for business development and market entry.

    Args:
        form_type: The type of form requested:
            - 'membership_application': Official application to join CCIFJ.
            - 'cv_database_registration': Form for candidates to register their resume in the CCIFJ CV database.
            - 'business_development_inquiry': Contact form for questions regarding CCIFJ commercial and business development services.
        lang: Language preference for forms supporting multilingual URLs ('en', 'fr', 'jp'). Defaults to 'en'.

    Returns:
        The official URL and guidance for completing the submission.
    """
    if form_type == "membership_application":
        membership_urls = {
            "en": "https://survey.ccifi.net/232729?lang=en",
            "fr": "https://survey.ccifi.net/232729?lang=fr",
            "jp": "https://survey.ccifi.net/232729?lang=jp",
        }
        url = membership_urls.get(lang, membership_urls["en"])
        return (
            f"CCIFJ Membership Application Form ({lang.upper()}):\n"
            f"URL: {url}\n"
            f"Please complete the online application to start your membership onboarding."
        )

    elif form_type == "cv_database_registration":
        url = (
            "https://ccifj-emploi.mycv.tech/index.php?"
            "langue=en&module=candidat&action=infosGeneralesCandidatVide&typepage=formulaire&idpage=a94966106b492"
        )
        return (
            "Candidate CV Database Registration Form:\n"
            f"URL: {url}\n"
            "Job seekers can register their CV in the official CCIFJ talent pool for French-Japanese companies."
        )

    elif form_type == "business_development_inquiry":
        url = "https://www.ccifj.or.jp/en/commercial-support/contact-us.html"
        return (
            "Business Development & Commercial Support Inquiry Form:\n"
            f"URL: {url}\n"
            "Submit your inquiry regarding market studies, company implantation, or domiciliation in Japan."
        )

    return "Form not found for the specified type."


SUBSCRIPTION_TYPES = {
    "general_news": SubscriptionType.newsletter,
    "training_announcements": SubscriptionType.training,
    "podcast_studio_ccifj": SubscriptionType.studio_newsletter,
}


@tool
async def subscribe_newsletter(
    email: str,
    newsletter_type: Literal[
        "general_news",
        "training_announcements",
        "podcast_studio_ccifj",
    ] = "general_news",
) -> str:
    """
    Subscribe a user's email address to CCIFJ newsletters or announcement lists.

    Use this tool when a user explicitly asks to subscribe to CCIFJ communications,
    such as general news, professional training announcements, or the @StudioCCIFJ podcast.

    Args:
        email: The user's valid email address.
        newsletter_type: Type of subscription:
            - 'general_news': CCIFJ corporate news and chamber updates.
            - 'training_announcements': Updates on upcoming professional workshops and training programs.
            - 'podcast_studio_ccifj': @StudioCCIFJ newsletter to receive new podcast episodes in their inbox.

    Returns:
        Confirmation of subscription status for the requested newsletter.
    """
    type_descriptions = {
        "general_news": "CCIFJ General Corporate News",
        "training_announcements": "CCIFJ Training & Workshops Announcements",
        "podcast_studio_ccifj": "@StudioCCIFJ Podcast Newsletter",
    }
    desc = type_descriptions.get(newsletter_type, newsletter_type)

    async with AsyncSessionLocal() as session:
        try:
            created = await crud.add_subscription(
                session, email, SUBSCRIPTION_TYPES[newsletter_type]
            )
        except ValueError as exc:
            return f"Subscription failed: {exc}"

    try:
        async with AsyncSessionLocal() as verify_session:
            saved_subscription = await crud.get_subscription(
                verify_session, email, SUBSCRIPTION_TYPES[newsletter_type]
            )
    except Exception:
        return (
            "The subscription may have been saved, but its database state could not "
            "be verified. Do not retry automatically; check the subscription first."
        )

    if saved_subscription is None:
        return (
            "The subscription was not found after saving, so it could not be "
            "verified. Do not retry automatically; check the subscription first."
        )

    if not created:
        return f"'{email.strip()}' is already subscribed to {desc}."
    return (
        f"Successfully subscribed '{email.strip()}' to {desc}. "
        f"The user will receive future updates directly at this email address."
    )


_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@tool
async def submit_staff_request(
    request_type: Literal["staff_email", "team_ticket"],
    category: Literal[
        "events",
        "membership",
        "business_support",
        "recruitment_and_hr",
        "media_and_news",
        "booking_change_or_refund",
        "other",
    ],
    subject: str,
    message: str,
    sender_name: str,
    sender_email: str,
    recipient_emails: Optional[list[str]] = None,
    runtime: ToolRuntime = None,
) -> str:
    """
    Store a message for CCIFJ staff, written together with the user.

    Use this tool only after the user has reviewed the final draft (subject and
    message) and explicitly agreed to send it. Guests may use it; a login is
    not required.

    Use it when:
    - No contact was found for the user's request: store a 'team_ticket' that
      the CCIFJ team will study (leave recipient_emails empty).
    - A CCIFJ contact exists (from get_ccifj_contact): store a 'staff_email'
      addressed to that contact (set recipient_emails, with every contact
      in the draft's To line).
    - The concierge cannot complete a reasonable request itself (for example,
      registration closed before the event, booking changes, refunds).

    Args:
        request_type: 'staff_email' when addressed to a known CCIFJ contact,
            'team_ticket' when no contact exists and the team must review it.
        category: The subject area of the request.
        subject: One-line subject agreed with the user.
        message: The full message body agreed with the user.
        sender_name: The user's name.
        sender_email: The user's email address, used by staff to reply.
        recipient_emails: Every CCIFJ contact email in the draft's To line,
            for a 'staff_email'.
        runtime: Injected ToolRuntime providing access to the current graph state.

    Returns:
        The stored request reference, or what is missing.
    """
    sender_email = sender_email.strip()
    recipients = list(dict.fromkeys(email.strip() for email in recipient_emails or [] if email.strip()))
    missing = [
        label
        for label, value in (("subject", subject), ("message", message), ("sender name", sender_name))
        if not value.strip()
    ]
    if missing:
        return f"Staff request not stored: missing {', '.join(missing)}. Ask the user for it."
    if not _EMAIL_PATTERN.match(sender_email):
        return "Staff request not stored: the sender email is invalid. Ask the user for a valid email."
    if request_type == "staff_email" and not recipients:
        return "Staff request not stored: a staff_email needs the CCIFJ contact emails as recipient_emails."
    invalid = [email for email in recipients if not _EMAIL_PATTERN.match(email)]
    if invalid:
        return f"Staff request not stored: invalid recipient email(s): {', '.join(invalid)}."

    if request_type == "team_ticket":
        recipients = []

    try:
        async with AsyncSessionLocal() as session:
            stored = await crud.add_staff_request(
                session,
                request_type=request_type,
                category=category,
                subject=subject,
                message=message,
                sender_name=sender_name,
                sender_email=sender_email,
                recipient_emails=recipients,
                user_id=_get_user_id(runtime),
                session_id=_chat_session_id(runtime),
            )
            reference = stored.reference
    except ValueError as exc:
        return f"Staff request not stored: {exc}"
    except Exception:
        return (
            "The staff request may have been stored, but saving could not be confirmed. "
            "Do not retry automatically; tell the user to contact CCIFJ if they get no reply."
        )

    try:
        async with AsyncSessionLocal() as verify_session:
            saved = await crud.get_staff_request(verify_session, reference)
    except Exception:
        saved = None
    if saved is None:
        return (
            f"The staff request may have been stored as {reference}, but it could not be verified. "
            "Do not retry automatically; tell the user to contact CCIFJ if they get no reply."
        )

    recipient = ", ".join(recipients) or "the CCIFJ team"
    return (
        f"Staff request stored. Reference: {reference}. "
        f"Type: {request_type}; category: {category}; addressed to {recipient}. "
        f"CCIFJ staff will reply to {sender_email}. "
        "No email has been sent yet; staff review stored requests."
    )


@tool
def search_vectordb(query: str) -> str:
    """
    Search the vector database for information relevant to the user's request.

    Use this tool whenever you need to retrieve business-related or general information
    from the CCIFJ knowledge base, such as:
    - Business support services (market studies, company setup / implantation, domiciliation, VAT recovery).
    - General CCIFJ chamber information (history, mission, office location, committees).
    - Member benefits, services overview, publications, and bilateral France-Japan business context.

    Args:
        query: A clear, specific, self-contained English search query describing
               exactly what information you need. The knowledge base is indexed
               in English; preserve exact names, dates, identifiers, and key terms.

    Returns:
        Relevant content chunks retrieved and reranked from the knowledge database.
    """
    model = _get_embed_model()
    retriever = _get_retriever()
    chunks = search(retriever, model, query, 20, 10, 5)
    return ('\n' + "=" * 80).join(chunks)


@tool
def return_to_main_menu() -> str:
    """
    Call this tool when the user's inquiry in the current topic is completed,
    or when the user asks a question about another department or topic.
    This safely returns the conversation to the digital receptionist.
    """
    return "Returning to the CCIFJ digital receptionist."
