"""
DNA Lounge — direct passthrough of the venue's published .ics feed.

DNA Lounge (jwz) publishes a high-quality machine-readable calendar with
stable UIDs, geolocation, and proper VTIMEZONE blocks. This is the gold path:
parse their ICS, convert each VEVENT into our Event objects, let the
orchestrator re-emit. We do NOT just copy their file — we want the same
deduplication + filtering pipeline as every other venue.
"""
from __future__ import annotations
from datetime import datetime, date
from icalendar import Calendar
from .base import Event, http_get, PACIFIC, assume_pacific

KEY = "dnalounge"
NAME = "DNA Lounge"
LOCATION = "DNA Lounge, 375 11th St, San Francisco, CA 94103"
URL = "https://www.dnalounge.com/calendar/"
FEED_URL = "https://www.dnalounge.com/calendar/dnalounge.ics"


def fetch() -> list[Event]:
    r = http_get(FEED_URL)
    cal = Calendar.from_ical(r.content)

    events: list[Event] = []
    for comp in cal.walk("VEVENT"):
        dtstart = comp.get("DTSTART")
        if dtstart is None:
            continue
        start = dtstart.dt
        # icalendar gives datetime (with tz) or date for all-day. Normalize.
        if isinstance(start, datetime):
            start = start.astimezone(PACIFIC) if start.tzinfo else assume_pacific(start)
        elif isinstance(start, date):
            start = datetime(start.year, start.month, start.day, 20, 0, tzinfo=PACIFIC)
        else:
            continue

        dtend = comp.get("DTEND")
        end = None
        if dtend is not None:
            end_v = dtend.dt
            if isinstance(end_v, datetime):
                end = end_v.astimezone(PACIFIC) if end_v.tzinfo else assume_pacific(end_v)

        events.append(Event(
            venue_key=KEY,
            venue_name=NAME,
            title=str(comp.get("SUMMARY", "Event")).strip(),
            start=start,
            end=end,
            url=str(comp.get("URL", "")) or None,
            description=str(comp.get("DESCRIPTION", "")).strip(),
            location=LOCATION,
            source_uid=str(comp.get("UID", "")) or None,
        ))
    return events
