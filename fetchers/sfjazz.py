"""
SFJAZZ — React SPA; needs Playwright render.
"""
from __future__ import annotations
import re
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from .base import Event, PACIFIC, assume_pacific
from ._spa import render_and_extract

KEY = "sfjazz"
NAME = "SFJAZZ"
LOCATION = "SFJAZZ Center, 201 Franklin St, San Francisco, CA 94102"
URL = "https://www.sfjazz.org/tickets/calendar/"


def _dom_scrape(html: str) -> list[Event]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Event] = []
    seen = set()

    # SFJAZZ uses URL patterns like /tickets/productions/<slug>/ or /shows/<slug>/
    for link in soup.select('a[href*="/tickets/productions/"], a[href*="/shows/"]'):
        href = link.get("href", "")
        if not href or href in seen:
            continue
        title = link.get_text(" ", strip=True)
        if not title or len(title) < 3:
            continue

        container = link.find_parent(["article", "div", "li"]) or link
        time_el = container.select_one("time[datetime]")
        if not time_el:
            continue
        try:
            start = dtparser.isoparse(time_el["datetime"])
        except Exception:
            continue
        start = start.astimezone(PACIFIC) if start.tzinfo else assume_pacific(start)

        seen.add(href)
        full_url = href if href.startswith("http") else f"https://www.sfjazz.org{href}"
        slug_match = re.search(r"/(?:productions|shows)/([^/?#]+)", href)
        uid = slug_match.group(1) if slug_match else None

        out.append(Event(
            venue_key=KEY, venue_name=NAME, title=title, start=start,
            url=full_url, location=LOCATION, source_uid=uid,
        ))
    return out


def fetch(browser_ctx=None) -> list[Event]:
    return render_and_extract(
        browser_ctx,
        key=KEY, name=NAME, location=LOCATION, url=URL,
        wait_for='a[href*="/productions/"], a[href*="/shows/"], time[datetime]',
        settle_ms=2500,
        scroll=True,
        dom_scraper=_dom_scrape,
    )
