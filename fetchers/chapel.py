"""
The Chapel — SeeTickets widget embedded on /music/.

Confirmed page structure (verified June 2026):
    <div class="seetickets-list-event-container">
      <div class="event-info-block">
        <p class="title"><a href="https://wl.seetickets.us/event/<slug>/<numeric_id>">…</a></p>
        <p class="date">Tue Jun 16</p>
        <p class="doortime-showtime">
          Doors at <span class="see-doortime">5:30PM</span>
          / Show at <span class="see-showtime">5:30PM</span>
        </p>
        <p class="venue">at The Chapel Main Bar</p>
        <p class="genre">…</p>
      </div>
    </div>

SeeTickets URL pattern: /event/<slug>/<numeric_id> — the numeric ID is our
stable source_uid.
"""
from __future__ import annotations
import re
from datetime import datetime
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from .base import Event, http_get, PACIFIC, assume_pacific

KEY = "chapel"
NAME = "The Chapel"
LOCATION = "The Chapel, 777 Valencia St, San Francisco, CA 94110"
URL = "https://thechapelsf.com/music/"

# Matches numeric ID at end of SeeTickets event URL path
SEETICKETS_ID_RE = re.compile(r"/event/[^/]+/(\d+)")


def _parse_chapel_dt(date_text: str, time_text: str) -> datetime | None:
    """date_text like 'Tue Jun 16'; time_text like '5:30PM' or '8:00 PM'."""
    text = f"{date_text.strip()} {time_text.strip()}"
    try:
        dt = dtparser.parse(text, fuzzy=True)
    except (ValueError, OverflowError):
        return None

    # dateutil defaults to current year — handle Dec→Jan rollover
    now = datetime.now()
    if dt.month < now.month and (now.month - dt.month) > 6:
        dt = dt.replace(year=now.year + 1)
    return assume_pacific(dt)


def _first_text(parent, *selectors) -> str:
    for sel in selectors:
        el = parent.select_one(sel)
        if el:
            txt = el.get_text(" ", strip=True)
            if txt:
                return txt
    return ""


def fetch() -> list[Event]:
    r = http_get(URL)
    soup = BeautifulSoup(r.text, "lxml")

    events: list[Event] = []
    for card in soup.select(".seetickets-list-event-container"):
        info = card.select_one(".event-info-block") or card

        # Title — inside <p class="title"><a>...
        title_link = info.select_one("p.title a, .title a")
        if not title_link:
            continue
        title = title_link.get_text(strip=True)
        href = title_link.get("href")
        if not title or not href:
            continue

        # Date text — "Tue Jun 16"
        date_text = _first_text(info, "p.date", ".date")
        if not date_text:
            continue

        # Show time — prefer .see-showtime, fallback to .see-doortime, then default
        time_text = _first_text(info, ".see-showtime", ".see-doortime")
        if not time_text:
            time_text = "8:00 PM"

        start = _parse_chapel_dt(date_text, time_text)
        if start is None:
            continue

        source_uid = None
        m = SEETICKETS_ID_RE.search(href)
        if m:
            source_uid = m.group(1)

        # Additional context for the description
        genre = _first_text(info, ".genre")
        sub_venue = _first_text(info, ".venue")  # "at The Chapel Main Bar"
        headliners = _first_text(info, ".headliners")
        supporting = _first_text(info, ".supporting-talent")

        desc_lines = []
        if headliners and headliners != title:
            desc_lines.append(headliners)
        if supporting:
            desc_lines.append(f"with {supporting}")
        if sub_venue:
            desc_lines.append(sub_venue)
        if genre:
            desc_lines.append(f"Genre: {genre}")

        events.append(Event(
            venue_key=KEY,
            venue_name=NAME,
            title=title,
            start=start,
            url=href,
            description="\n".join(desc_lines),
            location=LOCATION,
            source_uid=source_uid,
        ))
    return events
