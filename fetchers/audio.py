"""
Audio SF — homepage links events out to Eventbrite. Eventbrite event pages
ARE server-rendered with proper schema.org Event JSON-LD, but Eventbrite
aggressively rejects raw `requests` traffic from cloud IPs (GH Actions).

Solution: use the SAME Playwright browser to navigate to each Eventbrite
URL. Real browser fingerprint + cookies → no anti-bot rejection.
"""
from __future__ import annotations
import re
from datetime import datetime
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from .base import Event, PACIFIC, assume_pacific
from ._spa import render_and_extract, events_from_jsonld
from ._browser import render

KEY = "audio"
NAME = "Audio"
LOCATION = "Audio, 316 11th St, San Francisco, CA 94103"
URL = "https://audiosf.com/"

EB_EVENT_RE = re.compile(r"eventbrite\.com/e/(?P<slug>[a-z0-9-]+?)-tickets-(?P<id>\d+)")


def _make_dom_scraper(browser_ctx):
    """Returns a closure that uses the shared browser to enrich Eventbrite links."""
    def _scrape(html: str) -> list[Event]:
        soup = BeautifulSoup(html, "lxml")
        seen_ids: set[str] = set()
        out: list[Event] = []

        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = EB_EVENT_RE.search(href)
            if not m:
                continue
            eb_id = m.group("id")
            if eb_id in seen_ids:
                continue
            seen_ids.add(eb_id)

            eb_url = f"https://www.eventbrite.com/e/{m.group('slug')}-tickets-{eb_id}"

            # Render the Eventbrite page in the shared browser — handles
            # their anti-bot and gives us clean JSON-LD.
            try:
                eb_page = render(
                    browser_ctx, eb_url,
                    wait_for="script[type='application/ld+json']",
                    settle_ms=1500,
                )
                ev_list = events_from_jsonld(
                    eb_page.html, key=KEY, name=NAME, location=LOCATION,
                )
                if ev_list:
                    # Eventbrite often has multiple events on a page; the FIRST is
                    # typically the canonical one for that URL
                    ev = ev_list[0]
                    # Override fields specific to this venue context
                    ev.url = eb_url
                    ev.source_uid = eb_id
                    ev.location = LOCATION
                    out.append(ev)
            except Exception as ex:
                # If enrichment fails, fall back to a title-from-slug + skip
                # (we can't add to calendar without a real date)
                continue
        return out
    return _scrape


def fetch(browser_ctx=None) -> list[Event]:
    return render_and_extract(
        browser_ctx,
        key=KEY, name=NAME, location=LOCATION, url=URL,
        wait_for='a[href*="eventbrite.com"]',
        settle_ms=3000,
        scroll=True,
        dom_scraper=_make_dom_scraper(browser_ctx),
    )
