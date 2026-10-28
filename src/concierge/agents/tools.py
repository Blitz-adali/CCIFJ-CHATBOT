from collections import Counter
from datetime import datetime, time, timezone
from typing import Literal, Optional

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel
from rag import search, EMBED_MODEL_NAME
from sentence_transformers import SentenceTransformer
import bm25s

# Adjust "concierge" to your actual package name.
from DB.relationaldb import crud
from DB.relationaldb.models import SubscriptionType
from DB.relationaldb.session import AsyncSessionLocal

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
        _retriever = bm25s.BM25.load("index/", load_corpus=True)
    return _retriever

#################################################
#
# Helpers (database-backed tools)
#
#################################################

PAGE_SIZE = 10


def _now() -> datetime:
    # SQLite stores naive datetimes, so compare against naive UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None)


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

@tool
async def list_available_events(
    name: str = "",
    category: str = "",
    location: str = "",
    date_from: str = "",
    date_to: str = "",
) -> str:
    """
    List upcoming events, optionally filtered by name, category, location,
    or date range.

    Use this tool when:
    - The user asks what events are available.
    - The user wants to browse upcoming events.
    - The user knows an event's name but not its date or location.
    - The user wants to find events matching specific criteria.

    Args:
        name: Optional full or partial event name. Case-insensitive.
        category: Optional event category, such as technology, energy, healthcare, or networking.
        location: Optional city or venue.
        date_from: Optional earliest event date, formatted YYYY-MM-DD.
        date_to: Optional latest event date, formatted YYYY-MM-DD.

    Returns:
        A list of matching events with event IDs, names, dates, locations,
        categories, and prices. Ticket availability is not tracked.
    """
    try:
        start = _parse_date(date_from) if date_from else _now()
        end = _parse_date(date_to, end_of_day=True) if date_to else None
    except ValueError:
        return "Invalid date. Dates must be formatted YYYY-MM-DD."
    if end and end < start:
        return "Invalid date range: date_to must not be earlier than date_from."

    async with AsyncSessionLocal() as session:
        events = await crud.search_events(
            session,
            name=name or None,
            category=category or None,
            location=location or None,
            date_from=start,
            date_to=end,
            limit=PAGE_SIZE + 1,  # one extra row tells us whether more exist
        )

    if not events:
        return "No matching events were found."

    text = "\n".join(_event_line(e) for e in events[:PAGE_SIZE])
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

        wanted = _norm(space_name)
        space = next(
            (s for s in spaces if _norm(s.name) in wanted or wanted in _norm(s.name)),
            None,
        )
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
    - The user asks for companies in a particular region (e.g., Tokyo, Kansai).
    - The user wants companies matching both sector and region.

    Args:
        industry: Optional industry or business sector, such as IT, Healthcare, or Retail.
        region: Optional region, prefecture, or city, such as Tokyo or Kansai.

    Returns:
        Matching company records including company names, sectors, regions,
        and directory descriptions.
    """
    if not industry and not region:
        return (
            "Please provide an industry and/or a region to search. "
            "Use list_available_industries_and_regions to see the valid values."
        )

    async with AsyncSessionLocal() as session:
        members = await crud.search_members(
            session,
            sector=industry or None,
            region=region or None,
            limit=PAGE_SIZE + 1,
        )

    if not members:
        return (
            "No member companies matched those filters. "
            "Use list_available_industries_and_regions to see the valid values."
        )

    blocks = []
    for m in members[:PAGE_SIZE]:
        place = ", ".join(p for p in (m.region, m.country) if p) or "region unknown"
        block = f"{m.name} | Industry: {m.sector or 'unknown'} | {place}"
        if m.description:
            desc = m.description if len(m.description) <= 200 else m.description[:200] + "..."
            block += f"\n  {desc}"
        blocks.append(block)

    text = "\n\n".join(blocks)
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
        regions = [r for r in await crud.list_regions(session) if r]

    return (
        f"Industries: {', '.join(sectors) or 'none found'}\n"
        f"Regions: {', '.join(regions) or 'none found'}"
    )


#################################################
#
# 3. General & Business Tools
#
#################################################

@tool
def get_ccifj_contact(
    topic: Literal[
        "become_speaker",
        "become_sponsor",
        "corporate_news",
        "japanese_market_support",
        "recruitment_and_hr",
    ],
) -> str:
    """
    Retrieve direct contact person details (name, title, email, phone number) 
    for specific CCIFJ inquiries.

    Use this tool when the user wants to:
    - Inquire about becoming a speaker at CCIFJ events or conferences.
    - Sponsor CCIFJ events, summits, or business forums.
    - Share corporate news or press releases with CCIFJ media.
    - Ask questions about entering or developing in the Japanese market.
    - Inquire about recruitment, hiring, posting job offers, or HR services.

    Args:
        topic: The specific subject of inquiry. Must be one of:
            - 'become_speaker': Inquire about speaking at events (Elena SUZUKI).
            - 'become_sponsor': Inquire about sponsorship opportunities (Marie OZAKI & Elena SUZUKI).
            - 'corporate_news': Submit or share corporate news with CCIFJ (Communication Dept).
            - 'japanese_market_support': Advisory & support for French companies entering the Japanese market.
            - 'recruitment_and_hr': Inquiries regarding job postings, recruitment, and HR support (Nicolas CHAUVET).

    Returns:
        Verified contact details including the officer name, title, email address,
        and telephone number where available.
    """
    contacts = {
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
            "Contact for Sharing Corporate News:\n"
            "- Department: Communication Department\n"
            "- Email: communication@ccifj.or.jp"
        ),
        "japanese_market_support": (
            "Contact for Japanese Market Entry & Business Support:\n"
            "- Department: Business Support / Appui aux Entreprises\n"
            "- Email: appui.entreprises@ccifj.or.jp\n"
            "- Note: For commercial inquiries, users can also submit the Business Development Form."
        ),
        "recruitment_and_hr": (
            "Contact for Recruitment & Job Offers:\n"
            "- Name: Nicolas CHAUVET\n"
            "- Department: Employment & Training Department\n"
            "- Email: n.chauvet@ccifj.or.jp\n"
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
