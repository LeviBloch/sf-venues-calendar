"""
Shared logic for Playwright-based fetchers.

Each SPA venue uses the same fetch pattern:
    1. Render the page with headless Chromium
    2. Try to find JSON-LD Event objects in the rendered HTML (most reliable
       because many venues add SEO markup that's only injected after JS runs)
    3. Walk any JSON XHR responses captured during render for event-shaped
       payloads — this is the holy grail when it works (true API data)
    4. Fall back to venue-specific DOM scraping (callback)

If all 4 strategies fail, raise so the orchestrator emits the stale warning.
"""
from __future__ import annotations
from datetime import datetime
from typing import Callable, Optional
from dateutil import parser as dtparser
from .base import Event, PACIFIC, assume_pacific, extract_jsonld_events


def _parse_dt(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = dtparser.isoparse(raw)
    except (ValueError, TypeError):
        try:
            dt = dtparser.parse(raw)
        except (ValueError, TypeError):
            return None
    return dt.astimezone(PACIFIC) if dt.tzinfo else assume_pacific(dt)


def events_from_jsonld(
    html: str, *, key: str, name: str, location: str,
) -> list[Event]:
    """Pull Event objects from JSON-LD in (rendered) HTML."""
    out: list[Event] = []
    for ev in extract_jsonld_events(html):
        start = _parse_dt(ev.get("startDate"))
        if not start:
            continue
        title = (ev.get("name") or "").strip()
        if not title:
            continue
        end = _parse_dt(ev.get("endDate"))
        url = ev.get("url")
        if isinstance(url, list):
            url = url[0] if url else None
        desc = ev.get("description") or ""
        if isinstance(desc, dict):
            desc = desc.get("@value", "")
        out.append(Event(
            venue_key=key, venue_name=name, title=title, start=start, end=end,
            url=url, description=str(desc).strip(), location=location,
            source_uid=ev.get("@id") or url,
        ))
    return out


def events_from_xhr(
    json_responses: list[tuple[str, dict | list]],
    *, key: str, name: str, location: str,
) -> list[Event]:
    """Walk every captured JSON response, looking for objects that have the
    shape of an event: { name|title, startDate|start|date, ... }.

    Deliberately tolerant — different venues' APIs use different field names.
    """
    out: list[Event] = []
    seen_keys: set[str] = set()

    NAME_KEYS  = ("name", "title", "headline", "performanceName", "eventName")
    START_KEYS = ("startDate", "start", "date", "startTime", "performanceDateTime",
                  "performance_datetime", "datetime", "dateTime")
    END_KEYS   = ("endDate", "end", "endTime")
    URL_KEYS   = ("url", "link", "permalink", "ticketUrl", "eventUrl")
    UID_KEYS   = ("id", "@id", "performanceId", "eventId", "uid", "perfNo")

    def first(d: dict, keys) -> Optional[str]:
        for k in keys:
            v = d.get(k)
            if v:
                return v if not isinstance(v, dict) else v.get("@value")
        return None

    def looks_like_event(d: dict) -> bool:
        return isinstance(d, dict) and first(d, NAME_KEYS) and first(d, START_KEYS)

    def walk(node):
        if isinstance(node, dict):
            if looks_like_event(node):
                title = str(first(node, NAME_KEYS) or "").strip()
                start_raw = first(node, START_KEYS)
                start = _parse_dt(str(start_raw)) if start_raw else None
                if title and start:
                    end_raw = first(node, END_KEYS)
                    end = _parse_dt(str(end_raw)) if end_raw else None
                    url = first(node, URL_KEYS)
                    uid = first(node, UID_KEYS)
                    dedup_key = f"{title}|{start.isoformat()}"
                    if dedup_key not in seen_keys:
                        seen_keys.add(dedup_key)
                        out.append(Event(
                            venue_key=key, venue_name=name, title=title,
                            start=start, end=end, url=url if isinstance(url, str) else None,
                            location=location, source_uid=str(uid) if uid else None,
                        ))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for _url, body in json_responses:
        try:
            walk(body)
        except Exception:
            continue
    return out


def render_and_extract(
    browser_ctx,
    *,
    key: str,
    name: str,
    location: str,
    url: str,
    wait_for: Optional[str] = None,
    dom_scraper: Optional[Callable[[str], list[Event]]] = None,
    settle_ms: int = 2000,
    scroll: bool = False,
) -> list[Event]:
    """Full fetch pipeline. Tries JSON-LD → XHR JSON → dom_scraper in order."""
    if browser_ctx is None:
        raise RuntimeError(
            f"{name} needs Playwright but no browser context was provided "
            "(Playwright not installed or browser failed to start)"
        )

    from ._browser import render
    page = render(browser_ctx, url, wait_for=wait_for, settle_ms=settle_ms, scroll=scroll)

    # 1) JSON-LD in rendered HTML
    events = events_from_jsonld(page.html, key=key, name=name, location=location)
    if events:
        return events

    # 2) Event-shaped JSON in XHR responses
    events = events_from_xhr(page.json_responses, key=key, name=name, location=location)
    if events:
        return events

    # 3) Venue-specific DOM scraping (last resort)
    if dom_scraper:
        events = dom_scraper(page.html)
        if events:
            return events

    raise RuntimeError(
        f"All extraction strategies failed for {name} at {page.url}. "
        f"Rendered HTML was {len(page.html)} bytes; "
        f"captured {len(page.json_responses)} JSON XHR responses."
    )
