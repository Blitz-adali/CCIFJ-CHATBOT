"""Database-backed event cards shared by guided paths and agent responses."""
from concierge.db.relationaldb import crud
from concierge.db.relationaldb.datetimes import japan_now


def event_card(event) -> dict:
    def schedule(value):
        # Event schedules in this database are naive Japan-local time.
        return value.isoformat() + "+09:00" if value else None

    from concierge.source_context import effective_deadline, matching_event
    snapshot = matching_event(event)
    deadline = effective_deadline(event)
    now = japan_now()
    open_registration = (False if not event.start_at or event.start_at <= now
                         or (deadline is not None and deadline <= now)
                         else None if event.reg_deadline is None else True)
    return {
        "event_id": event.event_id, "title": event.title,
        "category": event.category, "location": event.location or (snapshot or {}).get("location"),
        "source_url": (snapshot or {}).get("url"),
        "start_at": schedule(event.start_at), "end_at": schedule(event.end_at),
        "reg_deadline": schedule(deadline),
        "price": str(event.price) if event.price is not None else None,
        "member_price": str(event.member_price) if event.member_price is not None else None,
        "non_member_price": str(event.non_member_price) if event.non_member_price is not None else None,
        "currency": event.currency, "registration_open": open_registration,
    }


async def cards_for_ids(session, ids) -> list[dict]:
    cards = []
    for event_id in list(dict.fromkeys(ids))[:10]:
        event = await crud.get_event(session, int(event_id))
        if event is not None:
            cards.append(event_card(event))
    return cards
