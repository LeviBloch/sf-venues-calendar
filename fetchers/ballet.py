"""
SF Ballet — parses the Elementor `production_calendar` widget HTML.

The /calendar/ page is fully server-side rendered (1MB+) with the entire
fiscal year laid out. Structure:

    <div class="production-calendar-container">
      <div class="month month-June-2026 block">       ← class encodes year+month
        <div class="calendar-day day-in-current-month">
          <h6>15</h6>                                  ← day number
          <div class="day-events-column">
            <div class="event-card">
              <a href="/productions/<slug>/">…</a>     ← title + URL
            </div>
          </div>
        </div>
      </div>
      <div class="month month-July-2026 hidden">…</div>
      ...
    </div>

Notes:
- Hidden months are still in the DOM — CSS toggles visibility per month
  filter. We want them all.
- The calendar shows day-level resolution only; no showtimes. We default
  to 19:30 Pacific (typical curtain) and include the production URL in
  the description so the user can click through for exact times.
- We deliberately INCLUDE ballet classes (which show up here) so the user
  has full visibility — they can filter via config.yaml:
      exclude_keywords: ["ballet classes", "pre-ballet"]
"""
from __future__ import annotations
import re
from datetime import datetime
from bs4 import BeautifulSoup
from .base import Event, http_get, PACIFIC

KEY = "ballet"
NAME = "SF Ballet"
LOCATION = "War Memorial Opera House, 301 Van Ness Ave, San Francisco, CA 94102"
URL = "https://www.sfballet.org/calendar/"

MONTH_CLASS_RE = re.compile(r"month-([A-Z][a-z]+)-(\d{4})")
DAY_NUM_RE = re.compile(r"^\s*(\d{1,2})\s*$")

DEFAULT_HOUR = 19
DEFAULT_MIN = 30

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11,
    "December": 12,
}


def _find_day_number(card) -> int | None:
    """The day number lives in the parent calendar-day cell, e.g. <h6>15</h6>.
    We walk up to the calendar-day ancestor and find its day-number element."""
    cell = card.find_parent(class_="calendar-day")
    if cell is None:
        return None
    # Prefer .day-number explicitly
    dn = cell.select_one(".day-number h6, .day-number")
    if dn:
        m = DAY_NUM_RE.match(dn.get_text(strip=True))
        if m:
            return int(m.group(1))
    # Fall back to any small heading containing just a number
    for h in cell.find_all(["h5", "h6"]):
        m = DAY_NUM_RE.match(h.get_text(strip=True))
        if m:
            return int(m.group(1))
    return None


def fetch() -> list[Event]:
    r = http_get(URL)
    soup = BeautifulSoup(r.text, "lxml")

    container = soup.select_one(".production-calendar-container")
    if container is None:
        raise RuntimeError("production-calendar-container not found — page structure changed")

    events: list[Event] = []
    seen: set[tuple[str, str]] = set()  # (date_iso, production_slug) dedup

    for month_div in container.select(".month"):
        classes = month_div.get("class") or []
        month_name = year = None
        for c in classes:
            m = MONTH_CLASS_RE.match(c)
            if m:
                month_name, year = m.group(1), int(m.group(2))
                break
        if not month_name or month_name not in MONTHS:
            continue
        month_num = MONTHS[month_name]

        for card in month_div.select(".event-card"):
            # Must be in a "day-in-current-month" cell to belong to THIS month
            # (calendar grids spill into previous/next month cells).
            cell = card.find_parent(class_="calendar-day")
            if cell is None or "day-in-current-month" not in (cell.get("class") or []):
                continue

            link = card.select_one('a[href*="/productions/"]')
            if not link:
                continue
            title = link.get_text(" ", strip=True)
            href = link["href"]
            if not title:
                continue

            day = _find_day_number(card)
            if day is None:
                continue

            try:
                start = datetime(year, month_num, day, DEFAULT_HOUR, DEFAULT_MIN,
                                 tzinfo=PACIFIC)
            except ValueError:
                continue

            # Dedup: same production on same day appears in BOTH mobile and
            # desktop variants of the cell
            slug_match = re.match(r"/productions/([^/]+)/", href)
            slug = slug_match.group(1) if slug_match else title
            dedup_key = (start.date().isoformat(), slug)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Absolute URL
            url = href if href.startswith("http") else f"https://www.sfballet.org{href}"

            # Subtitle inside event-details (e.g. "San Francisco Ballet School")
            sub_el = card.select_one(".event-details .hover-text")
            subtitle = sub_el.get_text(" ", strip=True) if sub_el else ""

            desc = "Default curtain: 7:30 PM — click through for exact showtimes."
            if subtitle and subtitle.lower() != title.lower():
                desc = f"{subtitle}\n{desc}"

            events.append(Event(
                venue_key=KEY,
                venue_name=NAME,
                title=title,
                start=start,
                url=url,
                description=desc,
                location=LOCATION,
                source_uid=f"{slug}|{start.date().isoformat()}",
            ))

    if not events:
        raise RuntimeError("Parsed Ballet calendar but found 0 events — selectors may have changed")
    return events
