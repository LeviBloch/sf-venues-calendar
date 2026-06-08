"""
Shared logic for Playwright-based fetchers.

Each SPA venue uses the same fetch pattern:
    1. Render the page with headless Chromium
    2. Try to find JSON-LD Event objects in the rendered HTML
    3. Walk any JSON XHR responses captured during render for event-shaped
       payloads
    4. Fall back to venue-specific DOM scraping (callback)

If all 4 strategies fail, we SAVE the rendered HTML and XHR URL list to
docs/debug/<venue>.html and docs/debug/<venue>-xhrs.txt before raising,
so the failure auto-publishes diagnostic artifacts to GH Pages. Iterating
on a broken parser then takes one round trip: look at debug HTML → fix
selectors → push.
"""
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from dateutil import parser as dtparser
from .base import Event, PACIFIC, assume_pacific, extract_jsonld_events

# docs/debug is published via GH Pages — debug artifacts are publicly
# accessible at https://<user>.github.io/sf-venues-calendar/debug/<venue>.html
DEBUG_DIR = Path(__file__).resolve().parent.parent / "docs" / "debug"


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


def events_from_jsonld(html: str, *, key: str, name: str, location: str) -> list[Event]:
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
    """Walk every JSON response looking for objects with shape
    { name|title, startDate|start|date, … }."""
    out: list[Event] = []
    seen_keys: set[str] = set()

    NAME_KEYS  = ("name", "title", "headline", "performanceName", "eventName", "production_name")
    START_KEYS = ("startDate", "start", "date", "startTime", "performanceDateTime",
                  "performance_datetime", "datetime", "dateTime", "performance_date",
                  "start_date", "start_time", "scheduled_start")
    END_KEYS   = ("endDate", "end", "endTime", "end_date", "end_time")
    URL_KEYS   = ("url", "link", "permalink", "ticketUrl", "eventUrl", "href")
    UID_KEYS   = ("id", "@id", "performanceId", "eventId", "uid", "perfNo", "performance_id")

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


def _dump_debug(key: str, page) -> str:
    """Write rendered HTML + XHR list. Returns a public-accessible URL hint."""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        # Atomic-ish write — same trick as build.atomic_write
        import tempfile, os as _os
        for name, data in [
            (f"{key}.html", page.html),
            (f"{key}-xhrs.txt",
             f"# Final URL: {page.url}\n"
             f"# Captured XHRs: {len(page.xhr_urls)}\n"
             f"# JSON responses parsed: {len(page.json_responses)}\n"
             f"# Generated: {datetime.utcnow().isoformat()}Z\n\n"
             + "\n".join(page.xhr_urls)),
        ]:
            target = DEBUG_DIR / name
            fd, tmp = tempfile.mkstemp(dir=str(DEBUG_DIR), prefix=f".{name}.", suffix=".tmp")
            with _os.fdopen(fd, "wb") as f:
                f.write(data.encode("utf-8") if isinstance(data, str) else data)
            _os.replace(tmp, target)
        return f"debug/{key}.html"
    except Exception as ex:
        return f"(debug dump failed: {ex})"


def render_and_extract(
    browser_ctx,
    *,
    key: str,
    name: str,
    location: str,
    url: str,
    wait_for: Optional[str] = None,
    dom_scraper: Optional[Callable[[str], list[Event]]] = None,
    settle_ms: int = 2500,
    scroll: bool = False,
) -> list[Event]:
    """Render → JSON-LD → XHR JSON → dom_scraper. On failure, dumps debug
    artifacts to docs/debug/ and raises with a pointer to them."""
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

    # 4) Failure — dump debug artifacts so we can iterate
    debug_url = _dump_debug(key, page)
    raise RuntimeError(
        f"All extraction strategies failed for {name} at {page.url}. "
        f"HTML={len(page.html)} bytes, JSON XHRs={len(page.json_responses)}, "
        f"all XHRs={len(page.xhr_urls)}. "
        f"Debug artifact: {debug_url}"
    )
