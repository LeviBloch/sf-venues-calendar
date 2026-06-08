"""
Shared helper for venues whose primary signal is schema.org JSON-LD Event
objects embedded in their calendar/listing pages.

Reliable when the venue has decent SEO (most ticketed arts orgs do).
Fails cleanly when the page is SPA-rendered with no SSR — the orchestrator
falls back to cache + stale warning.
"""
from __future__ import annotations
from datetime import datetime
from dateutil import parser as dtparser
from .base import Event, http_get, PACIFIC, assume_pacific, extract_jsonld_events


def fetch_jsonld_venue(
    *,
    key: str,
    name: str,
    location: str,
    listing_urls: list[str],
) -> list[Event]:
    """Fetch + parse JSON-LD Event objects from one or more listing pages."""
    seen_uids: set[str] = set()
    events: list[Event] = []

    for url in listing_urls:
        try:
            r = http_get(url)
        except Exception:
            continue  # try next listing URL

        raw_events = extract_jsonld_events(r.text)
        for ev in raw_events:
            start_raw = ev.get("startDate")
            if not start_raw:
                continue
            try:
                start = dtparser.isoparse(start_raw)
            except (ValueError, TypeError):
                try:
                    start = dtparser.parse(start_raw)
                except (ValueError, TypeError):
                    continue
            start = start.astimezone(PACIFIC) if start.tzinfo else assume_pacific(start)

            end = None
            end_raw = ev.get("endDate")
            if end_raw:
                try:
                    end = dtparser.isoparse(end_raw)
                    end = end.astimezone(PACIFIC) if end.tzinfo else assume_pacific(end)
                except (ValueError, TypeError):
                    pass

            title = (ev.get("name") or "").strip()
            if not title:
                continue

            event_url = ev.get("url")
            if isinstance(event_url, list):
                event_url = event_url[0] if event_url else None

            # JSON-LD `@id` is often a stable canonical URL → great UID
            source_uid = ev.get("@id") or event_url

            desc = ev.get("description") or ""
            if isinstance(desc, dict):
                desc = desc.get("@value", "")

            uid_key = source_uid or f"{title}|{start.isoformat()}"
            if uid_key in seen_uids:
                continue
            seen_uids.add(uid_key)

            events.append(Event(
                venue_key=key,
                venue_name=name,
                title=title,
                start=start,
                end=end,
                url=event_url,
                description=str(desc).strip(),
                location=location,
                source_uid=source_uid,
            ))

    if not events:
        raise RuntimeError(
            f"No JSON-LD events found at {listing_urls!r}. "
            "Site may have changed structure or gone SPA-only."
        )
    return events
