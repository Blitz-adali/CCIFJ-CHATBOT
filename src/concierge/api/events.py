"""Database-backed event cards shared by guided paths and agent responses."""
import re
import unicodedata

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


async def sources_for_ids(session, ids, answer, limit=5) -> list[dict]:
    """The CCIFJ page of each looked-up event that the answer names, as a source.

    Event facts come from the events table, so no page is "retrieved" for them; the page
    the record was taken from is still where a visitor can check the date, place and price.
    Open, closed and unconfirmed events all count: this is provenance, not a recommendation.
    """
    from concierge.source_context import matching_event
    named = _title_key(answer or '')
    sources = []
    for event_id in list(dict.fromkeys(ids or []))[:20]:
        event = await crud.get_event(session, int(event_id))
        title = _title_key(event.title) if event is not None and event.title else ''
        url = ((matching_event(event) or {}).get('url') or '') if title else ''
        if title and title in named and url.startswith('https://') and all(item['url'] != url for item in sources):
            sources.append({'title': event.title, 'url': url, 'record_type': 'event', 'provenance': 'database'})
            if len(sources) == limit:
                break
    return sources


def _title_key(text):
    return re.sub(r'[^\w]+', ' ', unicodedata.normalize('NFKC', text).casefold()).strip()


def registration_preferences(request, *, browsing=False):
    """Presentation can narrow, never broaden an explicit open-only request."""
    strict_open = bool(re.search(
        r'(?:only|just|uniquement|seulement).{0,50}(?:open|confirmed|ouvert)|'
        r'(?:open|confirmed|ouvert).{0,50}(?:only|uniquement|seulement)|受付中のみ', request, re.I))
    excludes_closed = bool(re.search(
        r'(?:no|exclude|without|hide|do not show|don.t show|sans|exclure).{0,25}(?:closed|expired|clos)|終了.*(?:除く|表示しない)', request, re.I))
    history = bool(re.search(
        r'past events?|my bookings?|mes r[ée]servations|[ée]v[ée]nements? pass[ée]s|過去|キャンセル|'
        r'(?:show|list|why|which|voir|pourquoi).{0,50}(?:closed|expired|clos)', request, re.I))
    return history and not (strict_open or excludes_closed), browsing and not strict_open


async def cards_for_ids(session, ids, *, answer=None, include_closed=False, include_unconfirmed=False) -> list[dict]:
    """Only decorate events actually discussed, never every research candidate.

    Explicit detail/history requests may show closed events. Browsing may show
    unknown status explicitly, but ordinary advice recommends confirmed-open only.
    """
    cards = []
    for event_id in list(dict.fromkeys(ids))[:10]:
        event = await crud.get_event(session, int(event_id))
        if event is not None:
            title = _title_key(event.title)
            if answer is not None and (not title or title not in _title_key(answer)):
                continue
            card = event_card(event)
            if card['registration_open'] is False and not include_closed:
                continue
            if card['registration_open'] is None and not include_unconfirmed:
                continue
            cards.append(card)
            if len(cards) == 3:
                break
    return cards
