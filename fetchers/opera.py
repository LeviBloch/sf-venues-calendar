"""
SF Opera — Tessitura Smart Calendar widget; needs Playwright render.
Same general approach as Symphony.
"""
from __future__ import annotations
import re
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from .base import Event, PACIFIC, assume_pacific
from ._spa import render_and_extract

KEY = "opera"
NAME = "SF Opera"
LOCATION = "War Memorial Opera House, 301 Van Ness Ave, San Francisco, CA 94102"
URL = "https://www.sfopera.com/calendar/"


def _dom_scrape(html: str) -> list[Event]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Event] = []

    # Opera's Tessitura widget tends to render performance items as
    # links to /operas/<slug>/ with a sibling date element.
    seen = set()
    for link in soup.select('a[href*="/operas/"], a[href*="/concerts/"], a[href*="/recitals/"]'):
        href = link.get("href", "")
        if not href or href in seen:
            continue
        title = link.get_text(" ", strip=True)
        if not title or len(title) < 3:
            continue

        # Look for a nearby <time> element in the same card/container
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
        full_url = href if href.startswith("http") else f"https://www.sfopera.com{href}"
        slug_match = re.search(r"/(?:operas|concerts|recitals)/([^/?#]+)", href)
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
        wait_for='a[href*="/operas/"], time[datetime]',
        settle_ms=2500,
        scroll=True,
        dom_scraper=_dom_scrape,
    )
