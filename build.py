"""
SF Venues Calendar — orchestrator.

Pipeline:
    for each venue fetcher:
        try fresh fetch
            on success: write events to cache/<venue>.json
            on failure: load events from cache + inject warning VEVENT
        apply filters from config.yaml
        clip to horizon_days
        emit docs/<venue>.ics
    emit docs/all.ics (combined)
    emit docs/index.html (status dashboard)
    emit docs/status.json (machine-readable status)

Designed for GitHub Actions: run via `python build.py`. Exits 0 even on
per-venue failures (graceful degradation per user requirements).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from icalendar import Calendar, Event as ICalEvent
from icalendar import vText

from fetchers import REGISTRY
from fetchers.base import Event, PACIFIC

ROOT = Path(__file__).parent
CACHE = ROOT / "cache"
DOCS = ROOT / "docs"
CONFIG = ROOT / "config.yaml"

CACHE.mkdir(exist_ok=True)
DOCS.mkdir(exist_ok=True)


# ---------- cache ----------

def atomic_write(p: Path, data: bytes | str) -> None:
    """Write atomically via temp + rename. Required because some sandboxed
    macOS environments (and good engineering practice) reject in-place
    overwrites of existing files."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

def cache_path(key: str) -> Path:
    return CACHE / f"{key}.json"

def meta_path(key: str) -> Path:
    return CACHE / f"{key}.meta.json"

def save_cache(key: str, events: list[Event]) -> None:
    atomic_write(cache_path(key), json.dumps(
        [e.to_dict() for e in events], indent=2, ensure_ascii=False,
    ))

def load_cache(key: str) -> list[Event]:
    p = cache_path(key)
    if not p.exists():
        return []
    try:
        return [Event.from_dict(d) for d in json.loads(p.read_text())]
    except Exception:
        return []

def save_meta(key: str, **fields) -> None:
    p = meta_path(key)
    existing = {}
    if p.exists():
        try:
            existing = json.loads(p.read_text())
        except Exception:
            pass
    existing.update(fields)
    atomic_write(p, json.dumps(existing, indent=2))

def load_meta(key: str) -> dict:
    p = meta_path(key)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


# ---------- filtering ----------

def compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns or []]

def apply_filters(events: list[Event], cfg: dict, horizon_days: int) -> list[Event]:
    excludes = compile_patterns(cfg.get("exclude_keywords", []))
    includes = compile_patterns(cfg.get("include_keywords", []))
    cutoff = datetime.now(PACIFIC) + timedelta(days=horizon_days)
    now = datetime.now(PACIFIC) - timedelta(hours=2)  # keep events that started recently

    kept = []
    seen_uids: set[str] = set()  # defensive dedup — some sites duplicate cards (mobile/desktop variants)
    for e in events:
        if e.start > cutoff or e.start < now:
            continue
        text = f"{e.title}\n{e.description}"
        if includes and not any(p.search(text) for p in includes):
            continue
        if any(p.search(text) for p in excludes):
            continue
        u = e.uid()
        if u in seen_uids:
            continue
        seen_uids.add(u)
        kept.append(e)
    kept.sort(key=lambda e: e.start)
    return kept


# ---------- ICS emission ----------

def event_to_vevent(e: Event) -> ICalEvent:
    v = ICalEvent()
    v.add("uid", e.uid())
    v.add("summary", e.title)
    v.add("dtstart", e.start)
    if e.end:
        v.add("dtend", e.end)
    else:
        # Default duration: assume 2.5h for arts perfs / club nights
        v.add("dtend", e.start + timedelta(hours=2, minutes=30))
    v.add("dtstamp", datetime.now(PACIFIC))
    if e.url:
        v.add("url", e.url)
    if e.location:
        v["location"] = vText(e.location)
    desc_parts = []
    if e.description:
        desc_parts.append(e.description)
    if e.url:
        desc_parts.append(e.url)
    desc_parts.append(f"[{e.venue_name}]")
    v.add("description", "\n\n".join(desc_parts))
    v.add("categories", [e.venue_name])
    return v

def warning_vevent(venue_name: str, since: str) -> ICalEvent:
    """A single all-day reminder that this venue's parser is stale."""
    v = ICalEvent()
    # Stable UID so it doesn't duplicate across rebuilds
    v.add("uid", f"warning-{venue_name.lower().replace(' ', '-')}@sf-venues")
    v.add("summary", f"⚠️ {venue_name} feed stale")
    # Schedule the warning for tomorrow at noon so it surfaces in Calendar
    when = datetime.now(PACIFIC).replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
    v.add("dtstart", when)
    v.add("dtend", when + timedelta(minutes=30))
    v.add("dtstamp", datetime.now(PACIFIC))
    v.add("description",
          f"The fetcher for {venue_name} has been failing since {since}. "
          "The calendar is showing cached (possibly outdated) events. "
          "See the project repo for parser maintenance.")
    v.add("categories", ["sf-venues warnings"])
    return v

def build_calendar(name: str, events: list[Event], warnings: list[ICalEvent] = None) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//sf-venues-calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-timezone", "America/Los_Angeles")
    cal.add("method", "PUBLISH")
    for e in events:
        cal.add_component(event_to_vevent(e))
    for w in (warnings or []):
        cal.add_component(w)
    return cal.to_ical()


# ---------- per-venue run ----------

def run_venue(mod, cfg: dict, horizon: int, browser_ctx=None) -> tuple[list[Event], dict]:
    """Returns (events, status_dict). browser_ctx is a Playwright context
    shared across all venues; fetchers that need it accept it via kwarg,
    fetchers that don't simply ignore the kwarg."""
    key, name = mod.KEY, mod.NAME
    status = {"key": key, "name": name, "url": mod.URL}
    venue_cfg = cfg.get("venues", {}).get(key, {})

    if not venue_cfg.get("enabled", True):
        status.update(state="disabled", count=0)
        return [], status

    warnings: list[ICalEvent] = []
    try:
        # Pass browser_ctx if the fetcher accepts it (introspect via signature)
        import inspect
        sig = inspect.signature(mod.fetch)
        if "browser_ctx" in sig.parameters:
            fresh = mod.fetch(browser_ctx=browser_ctx)
        else:
            fresh = mod.fetch()
        save_cache(key, fresh)
        save_meta(key, last_success=datetime.now(PACIFIC).isoformat(),
                  last_error=None, error_count=0)
        events = fresh
        status["state"] = "ok"
        status["last_success"] = datetime.now(PACIFIC).isoformat()
    except Exception as ex:
        # Log + degrade to cache
        meta = load_meta(key)
        err_count = meta.get("error_count", 0) + 1
        save_meta(key, last_error=str(ex), error_count=err_count,
                  last_attempt=datetime.now(PACIFIC).isoformat())
        events = load_cache(key)
        last_success = meta.get("last_success", "never")
        warnings.append(warning_vevent(name, last_success))
        status["state"] = "stale"
        status["error"] = str(ex)[:300]
        status["last_success"] = last_success
        status["error_count"] = err_count
        print(f"[{key}] FAIL: {ex}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    filtered = apply_filters(events, venue_cfg, horizon)
    status["count"] = len(filtered)

    # Write per-venue ICS
    ics_bytes = build_calendar(f"SF · {name}", filtered, warnings)
    atomic_write(DOCS / f"{key}.ics", ics_bytes)

    # Return events tagged with warnings flag for combined emission
    return filtered, status


# ---------- main ----------

def main() -> int:
    cfg = yaml.safe_load(CONFIG.read_text())
    horizon = int(cfg.get("horizon_days", 90))

    all_events: list[Event] = []
    all_warnings: list[ICalEvent] = []
    statuses: list[dict] = []

    # Lazy-import + spin up a single browser session that's shared across
    # all venues that need JS rendering. If Playwright isn't installed,
    # browser_ctx stays None and Playwright-based fetchers will fail
    # gracefully (degraded to cache + warning).
    browser_ctx = None
    browser_mgr = None
    try:
        from fetchers._browser import browser_session
        browser_mgr = browser_session()
        browser_ctx = browser_mgr.__enter__()
        print("Browser session started (Playwright/Chromium)")
    except ImportError:
        print("Playwright not installed — SPA venues will use cache + warning fallback",
              file=sys.stderr)
    except Exception as ex:
        print(f"Browser session failed to start: {ex} — continuing without it",
              file=sys.stderr)

    try:
        for mod in REGISTRY:
            events, status = run_venue(mod, cfg, horizon, browser_ctx=browser_ctx)
            all_events.extend(events)
            if status.get("state") == "stale":
                all_warnings.append(warning_vevent(mod.NAME, status.get("last_success", "never")))
            statuses.append(status)
    finally:
        if browser_mgr is not None:
            try:
                browser_mgr.__exit__(None, None, None)
            except Exception:
                pass

    # Combined ICS
    all_events.sort(key=lambda e: e.start)
    atomic_write(DOCS / "all.ics", build_calendar("SF Venues (all)", all_events, all_warnings))

    # status.json
    summary = {
        "generated_at": datetime.now(PACIFIC).isoformat(),
        "horizon_days": horizon,
        "total_events": len(all_events),
        "venues": statuses,
    }
    atomic_write(DOCS / "status.json", json.dumps(summary, indent=2))

    # index.html dashboard
    write_index_html(summary)

    # Print summary to stdout (visible in GH Actions logs)
    print(f"Generated {len(all_events)} events across {len(statuses)} venues")
    for s in statuses:
        print(f"  [{s['state']:>8}] {s['name']:>14}  {s.get('count', 0):>4} events"
              + (f"  ({s.get('error', '')})" if s['state'] == 'stale' else ''))
    return 0


def write_index_html(summary: dict) -> None:
    rows = []
    for s in summary["venues"]:
        badge = {
            "ok": '<span style="color:#0a0">●</span>',
            "stale": '<span style="color:#c80">⚠</span>',
            "disabled": '<span style="color:#888">○</span>',
        }.get(s["state"], "?")
        rows.append(
            f"<tr><td>{badge}</td><td><a href='{s['url']}'>{s['name']}</a></td>"
            f"<td>{s['count']}</td>"
            f"<td><a href='{s['key']}.ics'>{s['key']}.ics</a></td>"
            f"<td>{s.get('last_success', '—')}</td>"
            f"<td>{s.get('error', '')}</td></tr>"
        )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>SF Venues Calendar</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 860px;
          margin: 2em auto; padding: 0 1em; color: #222; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: .4em .8em; border-bottom: 1px solid #ddd;
            font-size: 14px; }}
  th {{ background: #f5f5f5; }}
  code {{ background: #eee; padding: 2px 6px; border-radius: 4px; }}
  .meta {{ color: #666; font-size: 13px; }}
</style></head>
<body>
<h1>SF Venues Calendar</h1>
<p class="meta">Last build: {summary['generated_at']} · Horizon: {summary['horizon_days']} days ·
   Total events: {summary['total_events']}</p>

<h2>Subscribe</h2>
<p>Combined: <code>webcal://YOUR-GH-PAGES-URL/all.ics</code></p>
<p>Or subscribe per venue (lets Apple Calendar color them independently).</p>

<h2>Status</h2>
<table>
<tr><th></th><th>Venue</th><th>Events</th><th>Feed</th><th>Last good fetch</th><th>Error</th></tr>
{''.join(rows)}
</table>
<p class="meta">Source: <a href="https://github.com/">github repo</a> · Rebuilds every 12h</p>
</body></html>"""
    (DOCS / "index.html").parent.mkdir(parents=True, exist_ok=True)
    atomic_write(DOCS / "index.html", html)


if __name__ == "__main__":
    sys.exit(main())
