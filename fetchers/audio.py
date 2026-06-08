"""
Audio SF — homepage carousels events that mostly link to Eventbrite. We
render the homepage and also probe an Eventbrite organizer page if one is
discoverable.

Note: Audio's static HTML only references one Eventbrite event; after JS
runs the events list populates. If parsing the homepage yields nothing,
the DOM scraper looks for all eventbrite.com/e/<slug>-<id> links and
extracts dates from each event slug's surrounding context.
"""
from __future__ import annotations
import re
from datetime import datetime
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from .base import Event, http_get, PACIFIC, assume_pacific
from ._spa import render_and_extract

KEY = "audio"
NAME = "Audio"
LOCATION = "Audio, 316 11th St, San Francisco, CA 94103"
URL = "https://audiosf.com/"

EB_EVENT_RE = re.compile(r"eventbrite\.com/e/(?P<slug>[a-z0-9-]+?)-tickets-(?P<id>\d+)")


def _title_from_slug(slug: str) -> str:
    """`juanita-more-mighty-real-pride-afters` -> `Juanita More Mighty Real Pride Afters`."""
    return " ".join(w.capitalize() for w in slug.split("-"))


def _enrich_from_eventbrite(eb_url: str) -> tuple[str | None, datetime | None]:
    """Fetch an Eventbrite event page (server-rendered) and pull title + start.
    Eventbrite pages always have schema.org Event JSON-LD."""
    try:
        r = http_get(eb_url)
    except Exception:
        return None, None
    import json
    soup = BeautifulSoup(r.text, "lxml")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        # data may be dict or list
        items = data if isinstance(data, list) else [data]
        for d in items:
            if isinstance(d, dict) and d.get("@type") in ("Event", "MusicEvent", "Festival"):
                title = (d.get("name") or "").strip() or None
                start_raw = d.get("startDate")
                start = None
                if start_raw:
                    try:
                        start = dtparser.isoparse(start_raw)
                        start = start.astimezone(PACIFIC) if start.tzinfo else assume_pacific(start)
                    except Exception:
                        pass
                return title, start
    return None, None


def _dom_scrape(html: str) -> list[Event]:
    """Find all Eventbrite event links, then enrich each via Eventbrite's
    own JSON-LD (which is reliable)."""
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

        # Clean URL (strip tracking params)
        eb_url = f"https://www.eventbrite.com/e/{m.group('slug')}-tickets-{eb_id}"
        title, start = _enrich_from_eventbrite(eb_url)
        if not start:
            # Couldn't enrich — skip rather than guess a date
            continue
        if not title:
            title = _title_from_slug(m.group("slug"))

        out.append(Event(
            venue_key=KEY, venue_name=NAME, title=title, start=start,
            url=eb_url, location=LOCATION, source_uid=eb_id,
        ))
    return out


def fetch(browser_ctx=None) -> list[Event]:
    return render_and_extract(
        browser_ctx,
        key=KEY, name=NAME, location=LOCATION, url=URL,
        wait_for='a[href*="eventbrite.com"]',
        settle_ms=3000,
        scroll=True,
        dom_scraper=_dom_scrape,
    )
