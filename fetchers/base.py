"""
Base types and helpers for venue fetchers.

Each venue module exposes:
    KEY: str            — short slug ("dnalounge")
    NAME: str           — display name ("DNA Lounge")
    LOCATION: str       — venue address
    URL: str            — venue homepage / source URL
    def fetch() -> list[Event]: raise on hard failure

The orchestrator (build.py) catches fetcher exceptions and falls back to cache.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from hashlib import sha1
from typing import Optional
from zoneinfo import ZoneInfo
import requests

PACIFIC = ZoneInfo("America/Los_Angeles")

# Browser-like UA — many venue sites 403 the default requests UA
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 20


@dataclass
class Event:
    venue_key: str
    venue_name: str
    title: str
    start: datetime                  # MUST be timezone-aware
    end: Optional[datetime] = None
    url: Optional[str] = None
    description: str = ""
    location: str = ""
    source_uid: Optional[str] = None  # if the source provides a stable ID, use it

    def uid(self) -> str:
        """Stable UID for ICS dedup. Apple Calendar uses this to update events
        in place rather than creating duplicates on every refresh.

        We always include the start date — some venues (e.g., SeeTickets) use
        ONE ticket ID for multiple performance dates (NBA Finals across 4
        games), so source_uid alone collapses them. Combining with date
        keeps them distinct while remaining stable across refreshes."""
        date_key = self.start.date().isoformat()
        if self.source_uid:
            base = f"{self.venue_key}:{self.source_uid}:{date_key}"
        else:
            base = f"{self.venue_key}|{self.title}|{self.start.isoformat()}"
        return f"{sha1(base.encode()).hexdigest()}@sf-venues"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat() if self.end else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        d = dict(d)
        d["start"] = datetime.fromisoformat(d["start"])
        d["end"] = datetime.fromisoformat(d["end"]) if d.get("end") else None
        return cls(**d)


def http_get(url: str, **kwargs) -> requests.Response:
    """Centralized GET with sane defaults + raises on 4xx/5xx."""
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
    r.raise_for_status()
    return r


def assume_pacific(dt: datetime) -> datetime:
    """Tag a naive datetime as Pacific. No conversion."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=PACIFIC)
    return dt.astimezone(PACIFIC)


def extract_jsonld_events(html: str) -> list[dict]:
    """
    Walk every <script type="application/ld+json"> block and yield any
    objects whose @type is (or includes) 'Event', 'MusicEvent',
    'TheaterEvent', 'DanceEvent', 'Festival', etc.

    Handles three common shapes:
      1. Single Event object
      2. List of Event objects
      3. @graph wrapping a list (used by Yoast SEO etc.)
      4. ItemList with itemListElement -> Event
    """
    import json
    import re
    from bs4 import BeautifulSoup

    EVENT_TYPES = {
        "Event", "MusicEvent", "TheaterEvent", "DanceEvent",
        "Festival", "ScreeningEvent", "ComedyEvent", "ConcertSeries",
    }

    def is_event(node: dict) -> bool:
        t = node.get("@type")
        if isinstance(t, str):
            return t in EVENT_TYPES
        if isinstance(t, list):
            return any(x in EVENT_TYPES for x in t)
        return False

    def walk(node, out):
        if isinstance(node, dict):
            if is_event(node):
                out.append(node)
            for v in node.values():
                walk(v, out)
        elif isinstance(node, list):
            for v in node:
                walk(v, out)

    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        # Some sites concatenate multiple JSON objects, or include trailing
        # JS — try strict parse first, then a leniency fallback.
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract balanced JSON objects via brace counting
            for m in re.finditer(r"\{.*?\}(?=\s*(?:,|\]|$))", raw, re.S):
                try:
                    data = json.loads(m.group(0))
                    walk(data, out)
                except json.JSONDecodeError:
                    pass
            continue
        walk(data, out)
    return out
