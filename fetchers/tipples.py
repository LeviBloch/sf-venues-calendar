"""
Mr Tipple's Recording Studio — small jazz club.

Their site doesn't expose a structured calendar feed. We attempt JSON-LD
discovery; if it returns nothing, the orchestrator emits a stale warning
event. As a deliberate baseline, we ALSO synthesize a single weekly
'Check schedule' reminder so the venue is never silently absent — small
jazz clubs often book a week or two out.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from .base import Event, PACIFIC, extract_jsonld_events, http_get
from dateutil import parser as dtparser

KEY = "tipples"
NAME = "Mr Tipple's"
LOCATION = "Mr Tipple's, 39 Fell St, San Francisco, CA 94102"
URL = "https://www.mrtipples.com/"


def _weekly_reminders() -> list[Event]:
    """Baseline: a Thursday-evening reminder for the next ~12 weeks."""
    now = datetime.now(PACIFIC).replace(hour=18, minute=0, second=0, microsecond=0)
    # snap forward to next Thursday (weekday 3)
    days_ahead = (3 - now.weekday()) % 7
    nxt = now + timedelta(days=days_ahead)
    out: list[Event] = []
    for i in range(12):
        d = nxt + timedelta(weeks=i)
        out.append(Event(
            venue_key=KEY,
            venue_name=NAME,
            title="🎷 Check Mr Tipple's schedule",
            start=d,
            end=d + timedelta(minutes=15),
            url=URL,
            description="Weekly reminder — Mr Tipple's books week-to-week. "
                        "Visit the site for this week's lineup.",
            location=LOCATION,
            source_uid=f"reminder-{d.date().isoformat()}",
        ))
    return out


def fetch() -> list[Event]:
    events: list[Event] = []
    try:
        r = http_get(URL)
        raw = extract_jsonld_events(r.text)
        for ev in raw:
            start_raw = ev.get("startDate")
            if not start_raw:
                continue
            try:
                start = dtparser.isoparse(start_raw)
            except Exception:
                continue
            start = start.astimezone(PACIFIC) if start.tzinfo else start.replace(tzinfo=PACIFIC)
            title = (ev.get("name") or "").strip()
            if not title:
                continue
            events.append(Event(
                venue_key=KEY, venue_name=NAME, title=title, start=start,
                url=ev.get("url"), location=LOCATION,
                source_uid=ev.get("@id") or ev.get("url"),
            ))
    except Exception:
        # network/parse failure — still return reminders, don't raise
        pass

    # Always add reminders so venue isn't silently missing
    events.extend(_weekly_reminders())
    return events
