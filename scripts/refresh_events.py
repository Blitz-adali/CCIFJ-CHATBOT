"""Check every event against its page on ccifj.or.jp and fill in or correct what the page states.

    uv run python scripts/refresh_events.py                 # report only, nothing is written
    uv run python scripts/refresh_events.py --apply         # write the changes
    uv run python scripts/refresh_events.py --apply --add-new   # also add upcoming events missing from the database

Each event's description holds its "Event details" link. The script downloads that page
(politely, one at a time, cached in --cache for re-runs) and reads the fields the site
states in a fixed layout: title, date and time, venue and address, event languages,
registration deadline, the price box (member / non-member / single price, currency) and
the contact e-mail. It then compares them with the database and prints every difference.

Rules it follows:
- A value on the page replaces a different or missing value in the database. A value the
  page does not state is left as it is (never blanked).
- Prices are only stored when the page labels them unambiguously (member, non-member, or a
  single price). Tiered prices (seats, passes, sponsor packages) stay in the description
  under "Prices stated on the CCIFJ event page", where the agent quotes them as written.
- The site's "1 January 1970" deadline means "none set" and is ignored.
- Short descriptions get the page's own introduction appended, so search and matching have
  something to work with.

With --add-new it also reads the site's "upcoming events" list and creates the events that
are not in the database yet. Nothing is deleted: an event that left the site keeps its row.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SITE = "https://www.ccifj.or.jp"
LISTING = SITE + "/en/events/upcoming-events{page}.html"
USER_AGENT = "Mozilla/5.0 (CCIFJ-Concierge event refresh)"
DETAILS_LINK = re.compile(r"(https://www\.ccifj\.or\.jp/[^\s\"']*event-details/event/[^\s\"'?#]+\.html)")
MONTHS = {name: number for number, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
DAY = r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day"
TIME = r"(\d{1,2}):(\d{2})\s*([AP]M)"
STOP_LINES = ("event language", "deadline for registration", "price:", "registration closed", "event ended",
              "event closed", "log in", "register", "faq - events", "to register for this event", "open on map",
              "add to my calendar", "(utc")
LANGUAGE_CODES = {"english": "en", "anglais": "en", "french": "fr", "français": "fr", "francais": "fr",
                  "japanese": "ja", "japonais": "ja", "日本語": "ja"}
PRICES_HEADING = "Prices stated on the CCIFJ event page:"


# ---------- Fetching ----------

def fetch(url: str, cache: Path | None, name: str, delay: float = 0.6) -> str | None:
    target = cache / name if cache else None
    if target and target.exists() and target.stat().st_size > 5000:
        return target.read_text(encoding="utf-8", errors="replace")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read()
    except Exception as exc:
        print(f"  could not download {url}: {type(exc).__name__} {getattr(exc, 'code', '')}")
        return None
    time.sleep(delay)
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return raw.decode("utf-8", errors="replace")


# ---------- Parsing ----------

def to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|select|form)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>|</tr>|</header>|</section>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = html.unescape(raw).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line and "UTC-11" not in line)


def _clock(hour: str, minute: str, half: str) -> tuple[int, int]:
    value = int(hour) % 12 + (12 if half.upper() == "PM" else 0)
    return value, int(minute)


def parse_when(text: str) -> tuple[datetime | None, datetime | None]:
    """Start and end from the site's three date layouts (single day, multi-day, all day)."""
    single = re.search(DAY + r"\s+(\d{1,2})\s+(\w+)\s+(\d{4})\s*-\s*" + TIME + r"\s*to\s*" + TIME, text)
    if single and single.group(2).lower() in MONTHS:
        day, month, year = int(single.group(1)), MONTHS[single.group(2).lower()], int(single.group(3))
        start = datetime(year, month, day, *_clock(*single.group(4, 5, 6)))
        end = datetime(year, month, day, *_clock(*single.group(7, 8, 9)))
        return start, end if end > start else None
    multi = re.search(r"(\w+),\s*(\d{1,2})\s*\(\s*" + TIME + r"\s*\)\s*~\s*" + DAY
                      + r"\s+(\d{1,2})\s+(\w+)\s+(\d{4})\s*\(\s*" + TIME + r"\s*\)", text)
    if multi and multi.group(1).lower() in MONTHS and multi.group(7).lower() in MONTHS:
        end_month, year = MONTHS[multi.group(7).lower()], int(multi.group(8))
        start_month = MONTHS[multi.group(1).lower()]
        start_year = year - 1 if start_month > end_month else year
        start = datetime(start_year, start_month, int(multi.group(2)), *_clock(*multi.group(3, 4, 5)))
        end = datetime(year, end_month, int(multi.group(6)), *_clock(*multi.group(9, 10, 11)))
        return start, end
    all_day = re.search(DAY + r"\s+(\d{1,2})\s+(\w+)\s+(\d{4})\s+to\s+12:00\s*AM", text)
    if all_day and all_day.group(2).lower() in MONTHS:
        return datetime(int(all_day.group(3)), MONTHS[all_day.group(2).lower()], int(all_day.group(1))), None
    return None, None


def parse_deadline(text: str) -> datetime | None:
    match = re.search(r"Deadline for registration:\s*" + DAY + r"\s+(\d{1,2})\s+(\w+)\s+(\d{4})\s*-\s*" + TIME, text)
    if not match or match.group(2).lower() not in MONTHS or int(match.group(3)) < 2000:
        return None
    return datetime(int(match.group(3)), MONTHS[match.group(2).lower()], int(match.group(1)), *_clock(*match.group(4, 5, 6)))


_AMOUNT = re.compile(r"(?<![\w.,])(\d{1,3}(?:[ ,.]\d{3})+|\d{3,7})(?![\w]*\d)")


def amounts(line: str) -> list[Decimal]:
    """Money amounts in a line: 45.000, 20 000, 3,000, 4600JPY and ¥7,000 are all thousands."""
    return [Decimal(re.sub(r"[ ,.]", "", found)) for found in _AMOUNT.findall(line.replace(" ", " "))]


def price_lines(text: str) -> list[str]:
    """The lines of the page's price box, without the notices that follow it."""
    match = re.search(r"\nPrice:\s*\n(.*)", text, re.S)
    if not match:
        return []
    lines = []
    for line in match.group(1).split("\n"):
        low = line.lower()
        if low.startswith(STOP_LINES[3:]) or low.startswith(("share this page", "faq")):
            break
        if len(lines) >= 12:
            break
        lines.append(line)
    return lines


def parse_prices(lines: list[str]) -> dict:
    """member_price / non_member_price / price / currency, only when the labels are unambiguous."""
    # "Members: ¥10,000 / Non-Members: ¥12,000" on one line is two prices.
    lines = [part.strip() for line in lines for part in re.split(r"\s+/\s+|\s*\|\s*", line) if part.strip()]
    joined = " ".join(lines)
    result: dict = {}
    if re.search(r"€|\beur\b|euros?\b", joined, re.I):
        result["currency"] = "EUR"
    elif re.search(r"¥|￥|\byens?\b|jpy|円", joined, re.I):
        result["currency"] = "JPY"
    member = non_member = None
    priced = []           # (line, amount) for lines that carry a price
    for index, line in enumerate(lines):
        low = line.lower()
        if re.match(r"^\W*(any|all|for|a \d|nb|cancell|please|after|if|\*)", low) and not re.search(r"member", low):
            continue  # cancellation and payment notices
        found = amounts(line)
        if not found and re.search(r"member", low) and index + 1 < len(lines):
            found = amounts(lines[index + 1])[:1]  # "Member Rate" on one line, the amount on the next
        if not found:
            continue
        is_non = bool(re.search(r"non[\s-]*members?|non[\s-]*membres?", low))
        is_member = not is_non and bool(re.search(r"\bmembers?\b|\bmembres?\b", low))
        if is_non and non_member is None:
            non_member = found[0]
        elif is_member and member is None:
            member = found[0]
        priced.append((line, found[0]))
    if member is not None:
        result["member_price"] = member
    if non_member is not None:
        result["non_member_price"] = non_member
    if member is None and non_member is None:
        if re.search(r"^\W*free\b", joined.strip(), re.I) and not priced:
            result["price"] = Decimal(0)
            result.setdefault("currency", "JPY")
        elif len(priced) == 1:
            result["price"] = priced[0][1]
    if any(key in result for key in ("price", "member_price", "non_member_price")):
        result.setdefault("currency", "JPY")
    else:
        result.pop("currency", None)
    return result


def parse_page(raw: str) -> dict:
    """Everything the event page states, as a dict of the fields that could be read."""
    found: dict = {}
    title = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", raw)
    if title:
        found["title"] = to_text(title.group(1)).replace("\n", " ").strip()[:255]
    start = raw.find("<h1")
    text = to_text(raw[start if start > 0 else 0:])
    head = text[:6000]
    found["start_at"], found["end_at"] = parse_when(head)
    deadline = parse_deadline(head)
    if deadline:
        found["reg_deadline"] = deadline

    # Venue: the lines between the calendar links and the next labelled block, plus the map address.
    venue = ""
    block = re.search(r"outlook\.com calendar\n(.*?)\n(?:Event language|Deadline for registration|Price:|Log in|"
                      r"Register|Registration closed|Event closed|FAQ - EVENTS|To register)", head, re.S)
    if block:
        lines = [line for line in block.group(1).split("\n")
                 if not line.lower().startswith(STOP_LINES) and "on map" not in line.lower() and len(line) < 160]
        venue = lines[0].strip(" ,;") if lines else ""
        if venue.lower() in LANGUAGE_CODES or venue.lower().startswith(("to be confirmed", "tbc", "online")):
            venue = ""  # an online or unconfirmed event has no venue line
    address = ""
    maps = re.search(r'maps\.google\.com/\?q=([^"]+)"', raw)
    if maps:
        address = urllib.parse.unquote(maps.group(1)).replace("+", " ").strip()
        if re.fullmatch(r"[-\d.]+,\s*[-\d.]+", address):
            address = ""  # coordinates only
    if venue and address and venue.lower() not in address.lower():
        found["location"] = f"{venue}, {address}"[:255]
    elif address or venue:
        found["location"] = (address or venue)[:255]

    languages = re.search(r"Event language\(s\)\n([^\n]+)", head) or re.search(r"Languages?\s*:\s*([^\n]+)", head)
    if languages:
        codes = [code for word, code in LANGUAGE_CODES.items() if word in languages.group(1).lower()]
        if codes:
            found["languages"] = list(dict.fromkeys(codes))

    lines = price_lines(head)
    found["price_lines"] = lines
    found.update(parse_prices(lines))

    labels = to_text(raw[max(0, start - 1200): start]) if start > 0 else ""
    found["labels"] = [line for line in labels.split("\n")[-4:] if len(line) < 40 and line != "•"]

    # The page's own introduction, for descriptions that say almost nothing.
    lead = re.search(r'(?is)<p class="lead[^"]*">(.*?)</p>', raw)
    found["lead"] = to_text(lead.group(1)).replace("\n", " ").strip() if lead else ""
    body_start = lead.end() if lead else raw.find("</header>", start)
    body = to_text(raw[body_start: body_start + 20000]) if body_start > 0 else ""
    for marker in ("\nSpeaker(s)", "\nEvent Location", "\nSponsors", "\nPartners", "\nAny Questions?",
                   "\nShare this page", "\nRegistration\n", "\nContact"):
        cut = body.find(marker)
        if cut > 0:
            body = body[:cut]
    found["about"] = "\n".join(line for line in body.split("\n") if len(line) > 25)[:1500].strip()

    emails = re.findall(r"[\w.+-]+\s*\(@\)\s*ccifj\.or\.jp|[\w.+-]+@ccifj\.or\.jp", to_text(raw[start: raw.find('<footer', start)]))
    if emails:
        found["contact"] = re.sub(r"\s*\(@\)\s*", "@", emails[-1]).lower()[:255]
    return {key: value for key, value in found.items() if value not in (None, "", [])}


def category_for(labels: list[str], title: str) -> str:
    text = " ".join(labels + [title]).lower()
    if "training" in text or "certification" in text or "formation" in text:
        return "Training"
    if re.search(r"networking|gala|dinner|party|soir[ée]e|cocktail|exceptional", text):
        return "Networking"
    if re.search(r"conference|seminar|webinar|forum|summit", text):
        return "Conference"
    return "Business event"


def describe(current: str, page: dict, url: str) -> str:
    """The stored description with the page's price lines refreshed and a thin text enriched."""
    text = current or ""
    head, _, _ = text.partition(PRICES_HEADING)
    head = head.rstrip()
    intro = head.split("\nRegistration:")[0].split("\nEvent details:")[0].strip()
    about = page.get("about", "")
    if len(intro) < 300 and about and about[:80] not in head:
        links = head[len(intro):].strip()
        head = "\n\n".join(part for part in (intro or page.get("lead", ""), about, links) if part)
    if "Event details:" not in head:
        head += f"\n\nEvent details: {url}"
    lines = [line for line in page.get("price_lines", []) if line.strip()][:8]
    if lines:
        head += f"\n\n{PRICES_HEADING}\n" + "\n".join(f"- {line.lstrip('-–• ').strip()}" for line in lines)
    return head.strip()


# ---------- Database ----------

FIELDS = ("title", "start_at", "end_at", "location", "reg_deadline", "price", "member_price", "non_member_price",
          "currency", "contact")


def _same(old, new) -> bool:
    if isinstance(new, Decimal) and old is not None:
        return Decimal(str(old)) == new
    if isinstance(new, str) and isinstance(old, str):
        return " ".join(old.split()).lower() == " ".join(new.split()).lower()
    return old == new


async def refresh(apply: bool, add_new: bool, cache: Path | None, only: set[int]) -> None:
    from sqlmodel import delete, select

    from concierge.db.relationaldb.models import Event, EventLanguage, utcnow
    from concierge.db.relationaldb.session import AsyncSessionLocal

    changed = unchanged = failed = added = 0
    async with AsyncSessionLocal() as session:
        events = (await session.exec(select(Event).order_by(Event.event_id))).all()
        known = set()
        for event in events:
            link = DETAILS_LINK.search(event.description or "")
            if link:
                known.add(link.group(1).rsplit("/", 1)[-1])
            if only and event.event_id not in only:
                continue
            if not link:
                print(f"#{event.event_id} {event.title[:50]}: no event page link in the description, skipped")
                failed += 1
                continue
            raw = fetch(link.group(1), cache, f"{event.event_id}.html")
            if raw is None or "<h1" not in raw:
                failed += 1
                print(f"#{event.event_id} {event.title[:50]}: page unavailable, left as is")
                continue
            page = parse_page(raw)
            updates = {}
            for name in FIELDS:
                if name in page and not _same(getattr(event, name), page[name]):
                    updates[name] = page[name]
            # A page that now shows member/non-member prices replaces a stored single price.
            if ("member_price" in page or "non_member_price" in page) and "price" not in page and event.price is not None:
                updates["price"] = None
            description = describe(event.description, page, link.group(1))
            if description != (event.description or "").strip():
                updates["description"] = description
            stored_languages = set((await session.exec(
                select(EventLanguage.language).where(EventLanguage.event_id == event.event_id))).all())
            new_languages = set(page.get("languages", []))
            language_change = bool(new_languages) and new_languages != stored_languages
            if not updates and not language_change:
                unchanged += 1
                continue
            changed += 1
            print(f"#{event.event_id} {event.title[:60]}")
            for name, value in updates.items():
                old = getattr(event, name)
                if name == "description":
                    print(f"    description: {len(old or '')} -> {len(value)} characters")
                else:
                    print(f"    {name}: {old!s:.70} -> {value!s:.90}")
            if language_change:
                print(f"    languages: {sorted(stored_languages)} -> {sorted(new_languages)}")
            if apply:
                for name, value in updates.items():
                    setattr(event, name, value)
                event.updated_at = utcnow()
                session.add(event)
                if language_change:
                    await session.exec(delete(EventLanguage).where(EventLanguage.event_id == event.event_id))
                    for code in sorted(new_languages):
                        session.add(EventLanguage(event_id=event.event_id, language=code))

        if add_new and not only:
            links: list[str] = []
            for number in range(1, 8):
                suffix = "" if number == 1 else f"/event-page/{number}"
                listing = fetch(LISTING.format(page=suffix), None, "")
                found = re.findall(r'href="(/en/events/upcoming-events/event-details/event/[^"#?]+\.html)', listing or "")
                new = [link for link in dict.fromkeys(found) if link not in links]
                if not new:
                    break
                links += new
            for path in links:
                slug = path.rsplit("/", 1)[-1]
                if slug in known:
                    continue
                url = SITE + path
                raw = fetch(url, cache, f"new-{slug}")
                if raw is None:
                    continue
                page = parse_page(raw)
                if not page.get("title") or not page.get("start_at"):
                    print(f"NEW {slug}: could not read a title and a date, skipped")
                    continue
                added += 1
                print(f"NEW {page['title'][:70]} | {page['start_at']} | {page.get('location', 'no venue')[:50]}")
                if apply:
                    event = Event(
                        title=page["title"], category=category_for(page.get("labels", []), page["title"]),
                        description=describe(page.get("lead", ""), page, url),
                        **{name: page[name] for name in FIELDS if name in page and name != "title"})
                    session.add(event)
                    await session.flush()
                    for code in page.get("languages", []):
                        session.add(EventLanguage(event_id=event.event_id, language=code))
        if apply:
            await session.commit()
    verb = "updated" if apply else "would update"
    print(f"\n{changed} events {verb}, {unchanged} already match their page, {failed} could not be checked"
          + (f", {added} new events {'added' if apply else 'would be added'}" if add_new else "") + ".")
    if not apply:
        print("Report only. Re-run with --apply to write these changes.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: report only)")
    parser.add_argument("--add-new", action="store_true", help="also add upcoming events missing from the database")
    parser.add_argument("--cache", type=Path, help="folder to keep downloaded pages in, for faster re-runs")
    parser.add_argument("--only", type=int, nargs="*", default=[], help="event IDs to check (default: all)")
    args = parser.parse_args()
    asyncio.run(refresh(args.apply, args.add_new, args.cache, set(args.only)))


if __name__ == "__main__":
    main()
