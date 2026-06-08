"""
SF Symphony — needs Playwright because the calendar sits behind Queue-it
(corporate anti-bot waiting room) and the actual performance grid is
Tessitura's TNEW SmartCalendar widget that renders client-side.

Strategy (in _spa.py): rendered JSON-LD → XHR JSON → DOM fallback.

Tessitura's widget classically emits cards with class `.tn-prod-list-item`
or similar; we try several variants in the DOM fallback.
"""
from __future__ import annotations
import re
from datetime import datetime
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from .base import Event, PACIFIC, assume_pacific
from ._spa import render_and_extract

KEY = "symphony"
NAME = "SF Symphony"
LOCATION = "Davies Symphony Hall, 201 Van Ness Ave, San Francisco, CA 94102"
URL = "https://www.sfsymphony.org/Buy-Tickets/Calendar"

# Selectors Tessitura/SF Symphony might use, tried in order
EVENT_CARD_SELECTORS = [
    ".tn-prod-list-item",
    ".tn-events-list__item",
    ".perf-card",
    ".performance-card",
    ".event-listing",
    "[data-perf-no]",
]

# Within a card, where to look for things
TITLE_SELECTORS = [".tn-prod-list-item__title", ".perf-title", "h2 a", "h3 a", "h4 a", "a[href*='/Buy-Tickets/']"]
DATE_SELECTORS  = ["time[datetime]", ".tn-prod-list-item__perf-time", ".perf-date", ".date"]


def _dom_scrape(html: str) -> list[Event]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Event] = []
    cards = []
    for sel in EVENT_CARD_SELECTORS:
        cards = soup.select(sel)
        if cards:
            break
    if not cards:
        return []

    for card in cards:
        # Title
        title = None
        for sel in TITLE_SELECTORS:
            el = card.select_one(sel)
            if el:
                title = el.get_text(" ", strip=True)
                if title:
                    break
        if not title:
            continue
        # Date — prefer machine-readable <time datetime="…">
        start = None
        time_el = card.select_one("time[datetime]")
        if time_el:
            try:
                start = dtparser.isoparse(time_el["datetime"])
            except Exception:
                pass
        if start is None:
            for sel in DATE_SELECTORS:
                el = card.select_one(sel)
                if el:
                    try:
                        start = dtparser.parse(el.get_text(" ", strip=True), fuzzy=True)
                        break
                    except Exception:
                        continue
        if start is None:
            continue
        start = start.astimezone(PACIFIC) if start.tzinfo else assume_pacific(start)

        link = card.find("a", href=True)
        href = link["href"] if link else None
        if href and href.startswith("/"):
            href = f"https://www.sfsymphony.org{href}"

        # Stable UID from URL slug + date
        uid = None
        if href:
            m = re.search(r"/Buy-Tickets/[^?]+", href)
            if m:
                uid = m.group(0)

        out.append(Event(
            venue_key=KEY, venue_name=NAME, title=title, start=start,
            url=href, location=LOCATION, source_uid=uid,
        ))
    return out


def fetch(browser_ctx=None) -> list[Event]:
    return render_and_extract(
        browser_ctx,
        key=KEY, name=NAME, location=LOCATION, url=URL,
        wait_for="a[href*='/Buy-Tickets/']",  # any concert link
        settle_ms=3000,  # extra time for Queue-it + widget
        scroll=True,
        dom_scraper=_dom_scrape,
    )
