import re
from collections import Counter
import logging
import threading

from datetime import datetime, time, timedelta
from typing import Literal, Optional

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel
from ..rag import search, get_collection, EMBED_MODEL_NAME
from ..rag.src.config import INDEX_PATH
from ..rag.src.rag_search import search_passages, get_tokenizer, get_collection
from concierge.evidence import record_passages, record_events, record_resource, record_database_source, record_warning
from concierge.resources import membership_form, BUSINESS_INQUIRY, CV_REGISTRATION, MEMBERSHIP_FEE_SOURCE
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
from ..integrations import odoo

logger = logging.getLogger(__name__)

# Keep the small search encoder on CPU. Automatic accelerator selection raised
# AcceleratorError during BERT inference on the demo host (see retrieval diagnostics).
# The conversation model/provider is independent and keeps its configured device.
# Cached singleton avoids reloading weights on every invocation.
_embed_model = None
_embed_model_lock = threading.Lock()

def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        with _embed_model_lock:
            if _embed_model is None:
                try:
                    _embed_model = SentenceTransformer(EMBED_MODEL_NAME, device="cpu", local_files_only=True)
                except OSError:
                    _embed_model = SentenceTransformer(EMBED_MODEL_NAME, device="cpu")
    return _embed_model

# Cached bm25s retriever singleton to avoid reloading weights on every invocation
_retriever = None
_retriever_lock = threading.Lock()

def _get_retriever():
    global _retriever
    if _retriever is None:
        with _retriever_lock:
            if _retriever is None:
                # Staff uploads rebuild a combined index (website + uploads) under var/knowledge.
                from concierge.knowledge import bm25_dir
                combined = bm25_dir()
                path = combined if (combined / "params.index.json").exists() else INDEX_PATH
                _retriever = bm25s.BM25.load(str(path), load_corpus=True)
    return _retriever


def warm_up() -> None:
    """Warm independent search backends; one usable branch keeps chat available."""
    from concierge.rag.src.diagnostics import record
    loaded = {}
    for stage, loader in (('embedding', _get_embed_model), ('lexical', _get_retriever)):
        try:
            loader()
            record(stage)
            loaded[stage] = True
        except Exception as exc:
            loaded[stage] = False
            record(stage, exc)
            logger.exception('Search warm-up failed stage=%s', stage)
    # A tokenizer/Chroma issue must not prevent the keyword index warming up.
    for stage, loader in (('tokenizer', get_tokenizer), ('semantic', get_collection)):
        try:
            loader()
            loaded[stage] = True
            record(stage)
        except Exception as exc:
            loaded[stage] = False
            record(stage, exc)
            logger.exception('Optional search warm-up failed stage=%s', stage)
    if not loaded.get('lexical') and not (loaded.get('embedding') and loaded.get('semantic')):
        raise RuntimeError('No search backend could be warmed')

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


def _event_line(event) -> str:
    return (
        f"[ID {event.event_id}] {event.title} | {_when(event.start_at, 'date unknown')} "
        f"| {event.location or 'location unknown'} | Category: {event.category} "
        f"| Price: {_price_text(event)}"
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
    if getattr(event, "cancelled", False):
        return "the event has been cancelled by CCIFJ"
    now = _now()
    if event.start_at is not None and event.start_at <= now:
        return "the event has already started or taken place"
    from concierge.source_context import effective_deadline
    deadline = effective_deadline(event)
    closing_at = deadline if deadline is not None else event.end_at
    if closing_at is not None and now >= closing_at:
        return f"registration closed on {_when(closing_at)}"
    return None


def _registration_summary(event):
    problem = _registration_problem(event)
    if problem:
        return 'closed: ' + problem
    if event.reg_deadline is None:
        return ('unconfirmed (database deadline missing; an indexed source may state a deadline, '
                'but database booking eligibility does not verify official registration status)')
    return 'open according to known database deadline (current ticket capacity unverified)'


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
    max_price: Optional[float] = None,
    currency: str = "JPY",
    limit: int = 3,
    registration_open_only: bool = False,
    runtime: ToolRuntime = None,
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
        max_price: Optional per-attendee budget. Unknown prices do not satisfy it.
        currency: Original price currency; defaults to JPY. Never convert without a dated rate.
        limit: Maximum events to return, from 1 to 10; defaults to 3.
        registration_open_only: True to exclude events with closed registration.
        runtime: Injected authentication context; not a user-supplied membership claim.

    Returns:
        A list of matching events with event IDs, names, dates, locations,
        categories, and prices. Ticket availability is not tracked.
    """
    try:
        start = max(_parse_date(date_from), _now()) if date_from else _now()
        end = _parse_date(date_to, end_of_day=True) if date_to else None
    except ValueError:
        return "Invalid date. Dates must be formatted YYYY-MM-DD."
    if end and end < start:
        return "Invalid date range: date_to must not be earlier than date_from."

    if not 1 <= limit <= PAGE_SIZE or max_price is not None and max_price < 0:
        return "Invalid search constraints: limit must be 1–10 and max_price nonnegative."
    audience = "non_member"
    async with AsyncSessionLocal() as session:
        user_id = _get_user_id(runtime)
        if user_id is not None:
            profile = await crud.load_user_context(session, user_id)
            if profile and profile.get('membership'):
                audience = 'member'
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
            max_price=max_price, currency=currency if max_price is not None else None,
            price_audience=audience, registration_open_only=registration_open_only,
            limit=_KEYWORD_FETCH_LIMIT if keywords else limit + 1,  # one extra row tells us whether more exist
        )
    if keywords:
        # SQL matches substrings ("HR" in "through"); keep whole-word matches only.
        patterns = [re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE) for term in keywords]
        events = [
            event for event in events
            if any(p.search(f"{event.title}\n{event.description or ''}") for p in patterns)
        ][:PAGE_SIZE + 1]

    if registration_open_only:
        from concierge.source_context import effective_deadline
        events = [e for e in events if not _registration_problem(e) and e.reg_deadline is not None]
    by_topic = False
    if not events and keyword and not name:
        events, by_topic = await _events_by_topic(keyword, start, end, category, location, limit), True
    if not events:
        if keyword:
            return (
                "No upcoming events match these keywords. Try other synonyms, or list "
                "upcoming events without `keyword` and pick the relevant ones."
            )
        return "No matching events were found."

    record_events(e.event_id for e in events[:limit])
    text = "\n".join(_event_line(e) + f" | Registration: {_registration_summary(e)}" for e in events[:limit])
    if by_topic:
        text = ("No upcoming event uses these exact words; these are the closest by topic. "
                "Say they are related events, not exact matches.\n" + text)
    if len(events) > limit:
        text += (
            f"\n\nShowing the first {limit} results; more events match. "
            "Narrow the search (category, location or dates) to see others."
        )
    else:
        text += f"\n\n{min(len(events), limit)} matching event(s)."
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

    record_events([event.event_id])
    from concierge.source_context import matching_event, page_passage
    snapshot = matching_event(event)
    if snapshot:
        passage = page_passage(snapshot["url"])
        if passage:
            record_passages([passage])
    lines = [
        f"[ID {event.event_id}] {event.title}",
        f"Category: {event.category}",
        f"Location: {event.location or 'unknown'}",
        f"Starts: {_when(event.start_at)}",
        f"Ends: {_when(event.end_at)}",
        f"Registration deadline: {_when(event.reg_deadline, 'none stated')}",
        f"Registration status: {_registration_summary(event)}",
        f"Price: {_price_text(event)}",
        f"Languages: {', '.join(languages) if languages else 'unknown'}",
        f"Contact: {event.contact or 'unknown'}",
        "Ticket availability: unknown (not tracked)",
        f"Description: {event.description}",
    ]
    if snapshot:
        from concierge.source_context import event_facts
        lines.append("Website page (background only; the fields above are the certain values and win over "
                     "anything below, without mentioning any difference): " + snapshot["url"] + event_facts(snapshot["url"]))
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

    from concierge.evidence import record_space_check
    record_space_check(space, start, end, not taken, _now())
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

    summary = f"Event: {_event_line(event)} | Registration deadline: {_when(event.reg_deadline, 'none stated')} | Registration status: {_registration_summary(event)}"
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
    record_resource(MEMBERSHIP_FEE_SOURCE)
    record_database_source("Membership annual fees", MEMBERSHIP_FEE_SOURCE, "member_category")
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
                return describe(category) + "\nSource: " + MEMBERSHIP_FEE_SOURCE + "\nDatabase snapshot; confirm current eligibility, taxes and rates before applying."
        categories = await crud.list_member_categories(session)

    if not categories:
        return "No membership category information is available."

    prefix = ""
    if tier_or_category_name:
        prefix = f"No category matched '{tier_or_category_name}'. Available categories:\n\n"
    return prefix + "\n\n".join(describe(c) for c in categories) + "\nSource: " + MEMBERSHIP_FEE_SOURCE + "\nDatabase snapshot; confirm current eligibility, taxes and rates before applying."


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


# Legal forms carry no meaning for "which company": "Acme K.K." and "ACME Japan Co., Ltd." are the
# same search. Removed only as whole words (or as the Japanese prefix/suffix).
_LEGAL_FORMS = re.compile(
    r"\b(?:kabushiki\s+kaisha|godo\s+kaisha|k\.?\s?k\.?|g\.?\s?k\.?|co\.?|corp\.?|corporation|company\s+limited|"
    r"ltd\.?|limited|inc\.?|incorporated|llc|llp|plc|s\.?a\.?s\.?|s\.?a\.?r\.?l\.?|s\.?a\.?|gmbh|ag|bv|nv)(?=\W|$)",
    re.IGNORECASE)
_JA_LEGAL_FORMS = re.compile(r"株式会社|合同会社|有限会社|\(株\)|（株）|㈱")


def _name_key(value: str) -> str:
    """A company or person name reduced to what identifies it: case, width, punctuation,
    legal forms and leading zeros ("COMPANY 01" == "company 1") do not matter."""
    import unicodedata
    text = unicodedata.normalize("NFKC", value or "")
    # Accents are often dropped when typing ("Dupre" for "Dupré"); kana voicing marks are kept.
    text = "".join(ch for ch in unicodedata.normalize("NFD", text)
                   if not (unicodedata.combining(ch) and ord(ch) < 0x0370))
    text = unicodedata.normalize("NFKC", text)
    text = _JA_LEGAL_FORMS.sub(" ", text)
    stripped = _LEGAL_FORMS.sub(" ", text)
    if re.search(r"\w", stripped):  # never strip a name down to nothing ("SA" alone stays)
        text = stripped
    text = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", text)  # "company11" -> "company 11"
    words = re.findall(r"\w+", text.casefold())
    return " ".join(str(int(w)) if w.isdigit() else w for w in words)


def _name_score(query: str, name: str) -> float:
    """1.0 same name, 0.9 the query is a whole-word part of the name, else spelling similarity."""
    from difflib import SequenceMatcher
    q, n = _name_key(query), _name_key(name)
    if not q or not n:
        return 0.0
    if q == n or q.replace(" ", "") == n.replace(" ", ""):
        return 1.0
    numbers = re.findall(r"\d+", q)
    if numbers and set(numbers) - set(re.findall(r"\d+", n)):
        return 0.0  # a different number is a different company: "COMPANY 11" is not "COMPANY 01"
    if len(q) >= 3 and re.search(r"(?<!\w)" + re.escape(q) + r"(?!\w)", n):
        return 0.9
    if " " not in q and len(q) >= 4 and q in n.replace(" ", ""):
        return 0.85  # "company11", "lvmh" inside "lvmh moet hennessy"
    return SequenceMatcher(None, q.replace(" ", ""), n.replace(" ", "")).ratio()


_NAME_MATCH = 0.82  # tolerates a typo or two, not a different company


def _members_by_name(members: list, contacts: dict, query: str) -> list[tuple[object, str]]:
    """Members whose company name, or one of whose contact people, matches `query`.

    Returns (member, why) pairs, best first; `why` names the matched contact person if any.
    """
    scored = []
    for m in members:
        best, why = _name_score(query, m.name or ""), ""
        for contact in contacts.get(getattr(m, "member_id", None), []):
            score = _name_score(query, getattr(contact, "name", "") or "")
            if score > best:
                best, why = score, f"contact person {contact.name}"
        if best >= _NAME_MATCH:
            scored.append((best, m, why))
    if not scored:
        return []
    top = max(score for score, _, _ in scored)
    # An exact name hides near-misses: asking for "COMPANY 1" must not also list COMPANY 11.
    floor = 1.0 if top == 1.0 else _NAME_MATCH
    scored.sort(key=lambda item: -item[0])
    return [(m, why) for score, m, why in scored if score >= floor]


def _contacts_line(contacts: list) -> str:
    """Contact people of a member company, with the role and working language of each."""
    people = []
    for contact in contacts[:6]:
        get = contact.get if isinstance(contact, dict) else lambda key, c=contact: getattr(c, key, None)
        details = ", ".join(part for part in (get("job_title"), f"works in {get('language')}" if get("language") else "") if part)
        name = " ".join(part for part in (get("salutation"), get("name")) if part)
        people.append(name + (f" ({details})" if details else "") + (f" <{get('email')}>" if get("email") else ""))
    return f"\n  Contact people: {'; '.join(people)}" if people else ""


async def _events_by_topic(topic: str, start, end, category: Optional[str], location: Optional[str], limit: int) -> list:
    """Upcoming events closest in meaning to a topic, for when no event contains the words themselves.

    Only when the matching index is ready; the date, format and place filters still apply.
    """
    from concierge import matching
    if not matching.ready():
        return []
    places = _location_terms(location)
    async with AsyncSessionLocal() as session:
        try:
            matches = await matching.match_events(session, topic, limit=limit + 10)
        except Exception:
            logger.exception("Topic search for events unavailable")
            return []
        # Keep what is clearly about the topic: relevant on its own, and close to the best match.
        best = max((m.score for m in matches), default=0.0)
        ids = [m.id for m in matches
               if matching.is_relevant(m.score, m.fields.get("wording", 0.0)) and m.score >= best - 0.03]
        events = {e.event_id: e for e in await crud.get_events_by_ids(session, ids)} if ids else {}
    kept = []
    for event_id in ids:
        event = events.get(event_id)
        if event is None or (start and event.start_at < start) or (end and event.start_at > end):
            continue
        if category and (event.category or "").casefold() != category.casefold():
            continue
        if places and not any(place.casefold() in (event.location or "").casefold() for place in places):
            continue
        kept.append(event)
    return kept[:limit]


@tool
async def search_member_directory(
    industry: Optional[str] = None,
    region: Optional[str] = None,
    company_name: Optional[str] = None,
    runtime: ToolRuntime = None,
) -> str:
    """
    Search the CCIFJ member directory for companies by name, industry and/or region.

    Use this tool when:
    - The user names a company or a person ("is Acme a member?", "contact for COMPANY 11",
      "how do I reach Marie Dupont?"): pass company_name, alone.
    - The user wants to find member companies in the CCIFJ network.
    - The user asks for companies in a particular sector (e.g., IT, Energy, Luxury, Aerospace).
    - The user asks for companies in a particular place.

    Args:
        company_name: Optional company name, or a contact person's name, as the user wrote
            it. Case, spacing, legal forms (K.K., Co., Ltd., SAS) and small typos do not
            matter. When set, industry and region are ignored.
        industry: Optional directory sector, e.g. "Wine and Spirits", "Food & Beverage",
            "Distribution", "IT & Computer". Use list_available_industries_and_regions
            for the exact list.
        region: Optional. Only set it when the user asked for a place. Accepts a region
            (Tokyo, Kanto, Kansai, Chubu, Kyushu, Hokkaido, Chugoku, Shikoku) or a city
            (Osaka, Yokohama, Paris...); comma-separate several. "Japan" means no filter.

    Returns:
        Matching company records including company names, sectors, regions,
        and directory descriptions. Names are shown to members and staff only;
        other users get how many companies match.
    """
    company_name = (company_name or "").strip()[:200]
    if company_name:
        return await _directory_by_name(company_name, runtime)
    pattern = _member_region_pattern(region or "")
    if not industry and pattern is None:
        return (
            "Please provide a company name, an industry and/or a place in Japan or abroad to search. "
            "Use list_available_industries_and_regions to see the industries."
        )

    from concierge import matching
    contacts: dict[int, list] = {}
    async with AsyncSessionLocal() as session:
        # The sector is understood loosely ("F&B", "food", "agroalimentaire" -> Food & Beverage) and
        # also matches a company's secondary sectors; nothing has to be typed exactly.
        everyone = [m for m in await crud.search_members(session, limit=_MEMBER_FETCH_LIMIT)
                    if getattr(m, "directory_visible", True) is not False]
        sectors = matching.resolve_sectors(industry, matching.sectors_of(everyone)) if industry else []
        members = [m for m in everyone if not industry or (sectors and matching.in_sectors(m, sectors))]
        if industry and not sectors:
            # No directory sector is called that: rank companies by what they do instead.
            found = await matching.match_members(session, industry, limit=PAGE_SIZE + 1, region=region or None)
            ids = [m.id for m in found if matching.is_relevant(m.score, m.fields.get("wording", 0.0))]
            members = [m for m in everyone if m.member_id in ids]
        member_ids = [m.member_id for m in members if getattr(m, "member_id", None) is not None]
        contacts = await crud.member_contacts(session, member_ids) if member_ids else {}

    note = ""
    if pattern is not None:
        in_place = [m for m in members if pattern.search(m.region or "")]
        if not in_place and members and industry:
            # Better to show the sector elsewhere than nothing at all.
            note = f"No {industry} members are listed in {region}; these {industry} members are elsewhere.\n\n"
        else:
            members = in_place
    members = list(members)[:PAGE_SIZE + 1]

    if members:
        level = await _access_level(runtime)
        if level in ("guest", "user"):
            more = "or more " if len(members) > PAGE_SIZE else ""
            return note + f"{min(len(members), PAGE_SIZE)} {more}member companies match these filters. " + MEMBERS_ONLY_NOTE

    if not members:
        return (
            "No member companies matched those filters. "
            "Use list_available_industries_and_regions to see the industries."
        )

    blocks = []
    for m in members[:PAGE_SIZE]:
        place = ", ".join(p for p in (m.region, m.country) if p) or "region unknown"
        block = f"{m.name} | Industry: {m.sector or 'unknown'} | {place}"
        if getattr(m, "secondary_sectors", None):
            block += f" | Also: {m.secondary_sectors}"
        if m.description:
            desc = m.description[:2000]
            block += f"\n  {desc}"
        block += _contacts_line(contacts.get(getattr(m, "member_id", None), []))
        blocks.append(block)

    text = (_names_allowed_note(level) + "Commercial role: unverified. These are sector/location matches, not a verified buyer, "
            "importer or distributor shortlist. Recommend a commercial role only when the description "
            "explicitly supports it; a sector or website alone does not.\n\n" + note + "\n\n".join(blocks))
    if len(members) > PAGE_SIZE:
        text += f"\n\nShowing the first {PAGE_SIZE} results; more companies match. Add another filter to narrow it down."
    return text


async def _directory_by_name(query: str, runtime) -> str:
    """Directory records for a company (or contact person) the user named."""
    async with AsyncSessionLocal() as session:
        everyone = [m for m in await crud.search_members(session, limit=_MEMBER_FETCH_LIMIT)
                    if getattr(m, "directory_visible", True) is not False]
        ids = [m.member_id for m in everyone if getattr(m, "member_id", None) is not None]
        contacts = await crud.member_contacts(session, ids) if ids else {}
    found = _members_by_name(everyone, contacts, query)
    level = await _access_level(runtime)
    if level in ("guest", "user"):
        # Confirming or denying that a named company is a member is itself directory information.
        return (f"The user asked about \"{query}\" in the member directory. " + MEMBERS_ONLY_NOTE
                + " Do not say whether this company is or is not a member.")
    if not found:
        return (f"No member company or contact person named \"{query}\" is in the CCIFJ directory "
                "(checked ignoring case, spacing, legal forms and small typos). Say so plainly; it may "
                "not be a member, or be listed under another name. Offer a search by sector, or a "
                "message to CCIFJ staff.")
    blocks = []
    for m, why in found[:PAGE_SIZE]:
        place = ", ".join(p for p in (m.region, m.country) if p) or "region unknown"
        block = f"{m.name} | Industry: {m.sector or 'unknown'} | {place}"
        if why:
            block += f" | matched on {why}"
        if getattr(m, "secondary_sectors", None):
            block += f" | Also: {m.secondary_sectors}"
        if m.description:
            block += f"\n  {m.description[:2000]}"
        block += _contacts_line(contacts.get(getattr(m, "member_id", None), []))
        blocks.append(block)
    head = (f"Directory records for \"{query}\" (exact name matches first). If several are listed, "
            "the user's spelling was close to more than one: show them and ask which one is meant.\n\n")
    return _names_allowed_note(level) + head + "\n\n".join(blocks)


#################################################
#
# Matching (members and events) and introductions
#
#################################################

def _names_allowed_note(level: str) -> str:
    # The answer writer only sees tool text and the profile (which may show no company for
    # staff); say explicitly that this viewer may see the names.
    who = "CCIFJ staff" if level == "staff" else "a CCIFJ member"
    return f"Directory access: the signed-in user is {who}, so these company names may be shown.\n"


MEMBERS_ONLY_NOTE = (
    "Company names and contacts in the CCIFJ directory are visible to CCIFJ members only. "
    "Do not name or guess companies. Explain that members see the full matches and can ask CCIFJ "
    "for an introduction; offer the membership information, or a message to CCIFJ staff "
    "(submit_staff_request, category 'membership' or 'business_support') asking for an introduction."
)


async def _access_level(runtime) -> str:
    """guest | user | member | staff for the signed-in user (see concierge.auth.access)."""
    user_id = _get_user_id(runtime)
    if user_id is None:
        return "guest"
    from concierge.auth.access import access_level
    from ..db.relationaldb.models import User
    async with AsyncSessionLocal() as session:
        return (await access_level(session, await session.get(User, user_id))).value


def _sector_summary(sectors: dict[str, int]) -> str:
    return ", ".join(f"{name} ({count})" for name, count in list(sectors.items())[:6])


@tool
async def find_matching_members(
    need: str,
    industry: Optional[str] = None,
    region: Optional[str] = None,
    runtime: ToolRuntime = None,
) -> str:
    """
    Find CCIFJ member companies that fit a need described in plain language, e.g.
    "a French agrifood company that knows distribution in Japan", "a law firm to set up
    a subsidiary", "an HR consultant in Osaka". Use it for partner, supplier, distributor,
    service-provider or peer searches; use search_member_directory for a plain list of a sector.

    What the user sees depends on their account: members and staff get company names,
    sectors, places and why each fits; signed-in non-members get how many members match and
    their sectors; guests get only how many members match.

    Args:
        need: What the user is looking for, in English, with the key details (activity,
            products, market, place).
        industry: Optional exact directory sector to restrict to (see
            list_available_industries_and_regions).
        region: Optional place, only when the user asked for one (Tokyo, Kansai, Osaka...).
    """
    from concierge import matching
    need = (need or "").strip()[:1000]
    if not need:
        return "Describe what the user is looking for before searching."
    level = await _access_level(runtime)
    async with AsyncSessionLocal() as session:
        if level in ("guest", "user"):
            count, sectors = await matching.count_relevant(session, need, sector=industry or None)
            if not count:
                return ("No member company clearly matches this need. Say so, and offer a message to CCIFJ "
                        "staff, who know the network beyond the directory.")
            if level == "guest":
                return (f"About {count} CCIFJ member companies look relevant to this need. " + MEMBERS_ONLY_NOTE
                        + " Mention that signing in and joining CCIFJ gives access to the directory.")
            return (f"About {count} CCIFJ member companies look relevant. Their sectors: {_sector_summary(sectors)}. "
                    + MEMBERS_ONLY_NOTE)
        matches = await matching.match_members(session, need, limit=5, sector=industry or None, region=region or None)
    if not matches:
        return "No member company matched. Try a broader need or no region, or offer a message to CCIFJ staff."
    blocks = []
    for match in matches:
        place = ", ".join(p for p in (match.fields.get("region"), match.fields.get("country")) if p) or "place unknown"
        wording = match.fields.get("wording", 0.0)
        strength = ("strong" if match.score >= 0.84 or (wording >= 0.6 and match.score >= matching.RELEVANT_SCORE)
                    else "good" if matching.is_relevant(match.score, wording) or match.fields.get("in_sector") else "weak")
        sector = match.fields.get("sector") or "unknown"
        if match.fields.get("secondary_sectors"):
            sector += f" (also {', '.join(match.fields['secondary_sectors'])})"
        block = f"{match.name} | Sector: {sector} | {place} | match strength: {strength}"
        if match.why:
            block += f"\n  Why: {match.why}"
        if match.fields.get("matched_terms"):
            block += f"\n  Matches the request on: {', '.join(match.fields['matched_terms'][:6])}"
        if match.fields.get("contact"):
            block += f"\n  Public contact: {match.fields['contact']}"
        block += _contacts_line(match.fields.get("contacts", []))
        blocks.append(block)
    advice = ("Explain in one line per company why it fits, using only the sector, place and text above; "
              "a sector match is not proof of a commercial role. When a company lists contact people, suggest the "
              "one whose role fits the request and who works in the user's language. ")
    advice = _names_allowed_note(level) + advice
    if level == "member":
        advice += ("Offer to ask CCIFJ to introduce the user to one or more of them (request_introduction, "
                   "with the exact company names above).")
    return advice + "\n\n" + "\n\n".join(blocks)


@tool
async def recommend_events(interests: str, runtime: ToolRuntime = None) -> str:
    """
    Recommend upcoming CCIFJ events that fit the user's interests, sector or goals, e.g.
    "food and wine networking", "HR and recruitment", "meet investors in Kansai".
    Returns up to 5 upcoming events with why each fits; use get_event_details for prices
    and registration.

    Args:
        interests: The user's interests, sector and goals, in English.
    """
    from concierge import matching
    interests = (interests or "").strip()[:1000]
    if not interests:
        return "Ask what the user is interested in first."
    async with AsyncSessionLocal() as session:
        matches = await matching.match_events(session, interests, limit=5)
    good = [m for m in matches if m.score >= 0.79] or matches[:2]
    if not good:
        return "No upcoming events are listed. Offer the newsletter or the events team contact."
    record_events([m.id for m in good])
    lines = []
    for m in good:
        when = _when(datetime.fromisoformat(m.fields["start_at"]), "date unknown")
        line = f"[ID {m.id}] {m.name} | {m.fields['category']} | {when} | {m.fields.get('location') or 'location to be confirmed'}"
        if m.why:
            line += f"\n  Why: {m.why}"
        lines.append(line)
    return "Upcoming events ranked by fit. Say briefly why each fits; never show the IDs.\n\n" + "\n".join(lines)


@tool
async def request_introduction(
    company_names: list[str],
    message: str,
    runtime: ToolRuntime = None,
) -> str:
    """
    Ask CCIFJ staff to introduce the signed-in member to one or more member companies
    found with find_matching_members. Staff contact the other company first; nothing is sent
    to it automatically. Members only: for other users, offer submit_staff_request instead.

    Use only after the user chose the companies and agreed to the message.

    Args:
        company_names: Exact company names from find_matching_members (1 to 5).
        message: What the user wants from the introduction and why, written with the user,
            in the user's words where possible.
    """
    from uuid import uuid4
    from sqlalchemy import func as sa_func
    from sqlmodel import select as sql_select
    from concierge.auth.access import access_level
    from ..db.relationaldb.models import Belongs, IntroRequest, Member, User
    user_id = _get_user_id(runtime)
    if user_id is None:
        return "Introductions need a member account: ask the user to sign in."
    names = [name.strip() for name in (company_names or []) if name and name.strip()][:5]
    message = (message or "").strip()
    if not names or not message:
        return "Choose at least one company and write the introduction message with the user first."
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        level = (await access_level(session, user)).value
        if level not in ("member", "staff"):
            return ("Only CCIFJ members can request introductions through the directory. Offer a message to "
                    "CCIFJ staff (submit_staff_request) instead, and the membership information.")
        own = (await session.exec(sql_select(Belongs.member_id).where(Belongs.user_id == user_id))).first()
        # A company staff hid from the directory cannot be asked for by name either.
        everyone = [m for m in (await session.exec(sql_select(Member))).all() if m.directory_visible is not False]
        created, unknown = [], []
        for name in names:
            target = (await session.exec(sql_select(Member).where(sa_func.lower(Member.name) == name.lower()))).first()
            if target is None:
                # "company 11", "Acme KK": accept the name only when it points to one company.
                close = _members_by_name(everyone, {}, name)
                target = close[0][0] if len(close) == 1 else None
            if target is None or target.member_id == own or target.directory_visible is False:
                unknown.append(name)
                continue
            intro = IntroRequest(reference=f"pending-{uuid4().hex[:24]}", requester_user_id=user_id,
                                 requester_member_id=own, target_member_id=target.member_id, message=message[:4000])
            session.add(intro)
            await session.flush()
            intro.reference = f"IR-{japan_now().year}-{intro.intro_id:06d}"
            created.append((target.name, intro.reference))
        await session.commit()
    if not created:
        return f"No introduction was stored: these names are not member companies: {', '.join(unknown)}."
    text = "Introduction requests stored for CCIFJ staff (they contact the company first; no email was sent):\n"
    text += "\n".join(f"- {name}: reference {reference}" for name, reference in created)
    if unknown:
        text += f"\nNot found in the directory, so not requested: {', '.join(unknown)}."
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
    from concierge import matching
    async with AsyncSessionLocal() as session:
        sectors = await matching.directory_sectors(session)

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
async def get_ccifj_contact() -> str:
    """
    Return the CCIFJ staff directory: every active contact with their department, what they
    handle, email, phone and languages, maintained by CCIFJ staff in the admin dashboard.

    Use this tool when the user wants to reach someone at CCIFJ or needs a recipient for a
    staff email: event questions, speaking or sponsoring, membership, corporate news, market
    entry, recruitment, training, communication, venue rental, billing or anything else.

    Pick the contact whose department and "Handles" line match the request. When none fits,
    use the entry marked "(triage)", who routes the request. Never invent a name or email that
    is not in the output; if the directory is empty, send a 'team_ticket' instead.
    """
    from ..staff_directory import describe_directory

    async with AsyncSessionLocal() as session:
        return await describe_directory(session)


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
        url = membership_form(lang)
        record_resource(url)
        return (
            f"CCIFJ Membership Application Form ({lang.upper()}):\n"
            f"URL: {url}\n"
            f"Please complete the online application to start your membership onboarding."
        )

    elif form_type == "cv_database_registration":
        url = CV_REGISTRATION
        record_resource(url)
        return (
            "Candidate CV Database Registration Form:\n"
            f"URL: {url}\n"
            "Job seekers can register their CV in the official CCIFJ talent pool for French-Japanese companies."
        )

    elif form_type == "business_development_inquiry":
        url = BUSINESS_INQUIRY
        record_resource(url)
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

    # Optional: also create it in CCIFJ's Odoo CRM as a lead (off until ODOO_* is configured).
    # The request is already stored; Odoo being slow or down never fails the answer.
    if odoo.enabled():
        import asyncio
        await asyncio.to_thread(odoo.push_staff_request, {
            "reference": reference, "request_type": request_type, "category": category,
            "subject": subject.strip(), "message": message.strip(), "sender_name": sender_name.strip(),
            "sender_email": sender_email, "recipient_emails": recipients,
        })

    recipient = ", ".join(recipients) or "the CCIFJ team"
    return (
        f"Staff request stored. Reference: {reference}. "
        f"Type: {request_type}; category: {category}; addressed to {recipient}. "
        f"CCIFJ staff will reply to {sender_email}. "
        "No email has been sent yet; staff review stored requests."
    )


@tool
def search_vectordb(query: str, original_query: str = "") -> str:
    """
    Search the vector database for information relevant to the user's request.

    Use this tool whenever you need to retrieve business-related or general information
    from the CCIFJ knowledge base, such as:
    - Business support services (market studies, company setup / implantation, domiciliation, VAT recovery).
    - General CCIFJ chamber information (history, mission, office location, committees).
    - Member benefits, services overview, publications, and bilateral France-Japan business context.

    Args:
        query: A clear, specific, self-contained English search query describing
               exactly what information you need. The website index is in English;
               preserve exact names, dates, identifiers, and key terms.
        original_query: The user's own wording of the question in their language
               (French, Japanese...), when it is not English. It is searched as well,
               so documents staff uploaded in that language can match. Leave empty
               for English questions.

    Returns:
        Relevant content chunks retrieved and reranked from the knowledge database.
    """
    model, retriever = None, None
    try:
        model = _get_embed_model()
    except Exception as exc:
        from concierge.rag.src.diagnostics import record
        record('embedding', exc)
        logger.exception('Embedding model unavailable for search')
        record_warning('retrieval_semantic_unavailable')
    try:
        retriever = _get_retriever()
    except Exception:
        logger.exception('Keyword index unavailable for search')
        record_warning('retrieval_lexical_unavailable')
    if not query.strip() or len(query)>4000:
        raise ValueError('Search query must be nonempty and at most 4000 characters')
    from concierge.rag.src.search_cache import public_search_cache, cache_key
    from concierge.evidence import turn_evidence
    original_query = (original_query or '')[:4000]
    key = cache_key(query + ('\n' + original_query.strip() if original_query.strip() else ''),retriever,model) \
        if retriever is not None and model is not None else None
    passages = public_search_cache.get(key) if key is not None else None
    if passages is None:
        passages = search_passages(retriever, model, query, 20, 10, 5, extra_queries=(original_query,))
        evidence = turn_evidence.get()
        # Never retain partial/fallback/error answers or cache when diagnostics
        # are unavailable. SQL availability, user data and writes never use this cache.
        if key is not None and evidence is not None and not any(w.startswith('retrieval_') for w in evidence.warnings):
            public_search_cache.put(key,passages)
    else:
        logger.info('retrieval_cache hit=true')
    record_passages(passages)
    if not passages:
        return "No relevant source passages found. Say that the knowledge base does not contain enough evidence; offer CCIFJ staff help."
    return ('\n' + "=" * 80).join(
        f"Source: {p['url']}\nChunk: {p['id']}\n{p['text']}" for p in passages
    )


@tool
def search_indexed_events(query: str = '', location: str = '', limit: int = 5) -> str:
    """Find future events in the local website crawl when SQL has no suitable record.

    Uses structured event titles, dates and locations, without semantic search.
    These are crawl candidates, not live bookable matches. Prices, membership
    eligibility and ticket capacity may be unknown. Never assume they meet a budget.
    """
    from concierge.source_context import find_events, event_facts, page_passage
    urls = find_events(query, _now(), location=location, limit=max(1, min(limit, 5)))
    if not urls:
        return 'No matching future events in the indexed website snapshot. This is not a live website check.'
    results = []
    for url in urls:
        passage = page_passage(url)
        # Keep the full page available through get_source_details, but return
        # compact structured facts for browsing instead of every page's prose.
        passage = {**passage, 'text': event_facts(url)}
        record_passages([passage])
        results.append(f"Source: {url}\n{passage['text']}")
    return ('Indexed website candidates only. Verify price and eligibility; unknown prices do not meet a strict budget.\n'
            + '\n'.join(results))


@tool
def get_source_details(url: str) -> str:
    """Read a complete known indexed page when a search chunk omits event or service details.

    Only URLs already in the local CCIFJ corpus are accepted. This does not fetch
    the live website. Use before claiming a cited page lacks venue, price or deadline.
    """
    from concierge.source_context import page_passage
    passage = page_passage(url)
    if passage is None:
        return 'This URL is not in the indexed corpus. Its contents are unverified; do not fetch arbitrary URLs or infer missing facts.'
    record_passages([passage])
    return f"Source: {url}\n{passage['text']}"


@tool
def return_to_main_menu() -> str:
    """
    Call this tool when the user's inquiry in the current topic is completed,
    or when the user asks a question about another department or topic.
    This safely returns the conversation to the digital receptionist.
    """
    return "Returning to the CCIFJ digital receptionist."
