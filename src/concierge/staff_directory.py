"""The staff directory the agent routes requests to (the `contact_us` table).

Staff edit it in the admin dashboard. Each contact lists the topics it handles; the agent's
`get_ccifj_contact` tool asks for a topic and gets the active contacts for it. When nobody
handles a topic, the request goes to the triage contacts, who reassign it.
"""

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.db.relationaldb.models import ContactUs

GENERAL_TOPIC = "general_or_unsure"

# Topic key -> (label used in the tool output, short description for the dashboard).
TOPICS: dict[str, tuple[str, str]] = {
    "events": ("CCIFJ Events (registration and event questions)", "Event questions, registration, late registration"),
    "become_speaker": ("Event Speakers", "Speaking at CCIFJ events"),
    "become_sponsor": ("Event Sponsorship", "Sponsoring events, summits and forums"),
    "bonjour_france": ("BONJOUR FRANCE (starting point for companies that want to work in Japan)", "BONJOUR FRANCE"),
    "membership": ("CCIFJ Membership", "Joining, dues and member services"),
    "corporate_news": ("Sharing Corporate News", "Corporate news and press releases"),
    "japanese_market_support": ("Japanese Market Entry & Business Support", "Market entry and business support"),
    "recruitment_and_hr": ("Recruitment & Job Offers (Employment & Training Department)", "Recruitment, job offers, HR"),
    GENERAL_TOPIC: ("General enquiries (the CCIFJ team will route your request)", "Triage: anything no other contact covers"),
}
TOPIC_NOTES = {
    "japanese_market_support": "For commercial inquiries, users can also submit the Business Development Form.",
    "recruitment_and_hr": "Job seekers can also register their resume via the CV Database form.",
}
NO_CONTACT = (
    "No direct contact is on file for this topic. Offer to send the request to the CCIFJ team "
    "as a 'team_ticket' with submit_staff_request."
)


def split_topics(value: str | None) -> list[str]:
    return [topic.strip() for topic in (value or "").split(",") if topic.strip()]


def _entry(contact: ContactUs, numbered: int | None) -> list[str]:
    fields = [("Role", contact.role if contact.name else None), ("Phone", contact.mobile), ("Email", contact.email)]
    if contact.name is None:
        fields.insert(0, ("Department", contact.role or contact.category))
    if contact.languages:
        fields.append(("Languages", contact.languages))
    lines = [f"{label}: {value}" for label, value in fields if value]
    if numbered is None:
        head = [f"- Name: {contact.name}"] if contact.name else []
        return head + [f"- {line}" for line in lines]
    title = contact.name or contact.role or contact.category
    return [f"{numbered}. {title}"] + [f"   - {line}" for line in lines if not line.startswith("Department:")]


def format_contacts(topic: str, contacts: list[ContactUs]) -> str:
    label = TOPICS.get(topic, (topic, ""))[0]
    lines = [f"{'Contacts' if len(contacts) > 1 else 'Contact'} for {label}:"]
    for index, contact in enumerate(contacts, start=1):
        lines += _entry(contact, index if len(contacts) > 1 else None)
    if topic in TOPIC_NOTES:
        lines.append(f"- Note: {TOPIC_NOTES[topic]}")
    return "\n".join(lines)


async def contacts_for_topic(session: AsyncSession, topic: str) -> list[ContactUs]:
    rows = (await session.exec(
        select(ContactUs).where(ContactUs.is_active == True).order_by(col(ContactUs.contact_id))  # noqa: E712
    )).all()
    if topic == GENERAL_TOPIC:
        return [row for row in rows if row.is_triage]
    return [row for row in rows if topic in split_topics(row.topics)]


async def describe_directory(session: AsyncSession) -> str:
    """Tool output: the whole active directory, so the model picks the department itself.

    Staff maintain the directory in the admin dashboard; a contact added there is returned on the
    next call with no code change. Each entry carries its department and what it handles.
    """
    rows = (await session.exec(
        select(ContactUs).where(ContactUs.is_active == True).order_by(col(ContactUs.contact_id))  # noqa: E712
    )).all()
    if not rows:
        return NO_CONTACT
    lines = ["CCIFJ staff directory. Choose the contact whose department matches the request; "
             "when none fits, use the triage contact, who routes requests. Use only these names and emails."]
    for index, contact in enumerate(rows, start=1):
        title = contact.name or contact.role or contact.category
        lines.append(f"{index}. {title}" + (" (triage)" if contact.is_triage else ""))
        fields = [("Department", contact.category), ("Handles", contact.sub_category),
                  ("Role", contact.role if contact.name else None), ("Phone", contact.mobile),
                  ("Email", contact.email), ("Languages", contact.languages)]
        lines += [f"   - {label}: {value}" for label, value in fields if value]
    lines.append("- Note: " + TOPIC_NOTES["japanese_market_support"])
    lines.append("- Note: " + TOPIC_NOTES["recruitment_and_hr"])
    return "\n".join(lines)


async def describe_contacts(session: AsyncSession, topic: str) -> str:
    """Tool output: the contacts for a topic, else the triage contacts, else a team-ticket hint."""
    contacts = await contacts_for_topic(session, topic)
    if contacts:
        return format_contacts(topic, contacts)
    triage = await contacts_for_topic(session, GENERAL_TOPIC)
    if triage:
        return "No specialist is on file for this topic, so the CCIFJ triage contact will route it.\n" + \
            format_contacts(GENERAL_TOPIC, triage)
    return NO_CONTACT
