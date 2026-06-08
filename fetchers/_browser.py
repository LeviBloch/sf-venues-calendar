"""
Shared headless-browser helper for SPA-driven venue sites.

Why Playwright instead of just requests:
  - SF Symphony sits behind Queue-it (anti-bot interstitial) — needs a real
    browser to clear the queue and load the actual page.
  - SF Opera / SFJAZZ / Audio render event data client-side via XHR — the
    initial HTML is mostly empty.

Wait strategy (matters — got burned by this on the first deploy):
  Many venue sites stream analytics/telemetry continuously (Tessitura,
  React/Redux apps), so `networkidle` NEVER fires within any reasonable
  timeout. We use a layered approach:
    1. Wait for `domcontentloaded` (initial HTML parsed)
    2. Wait for the venue-specific content selector (signals SPA populated)
    3. Optionally scroll to trigger lazy-loading
    4. A short settle delay
  We do NOT block on `networkidle` — it caused 20s timeouts on Opera/SFJAZZ.

XHR capture:
  We record all JSON XHR responses + a list of every XHR URL during render.
  The JSON responses feed the SPA event-extractor; the URL list is dumped
  to the debug artifact when fetchers fail (so we can iterate on selectors
  by inspecting which APIs the page actually called).

Browser is launched once per build for efficiency (see `browser_session`).
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


@dataclass
class RenderedPage:
    url: str
    html: str
    json_responses: list[tuple[str, dict | list]] = field(default_factory=list)
    xhr_urls: list[str] = field(default_factory=list)


@contextmanager
def browser_session():
    """Yield a Playwright browser context."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1400, "height": 900},
            locale="en-US",
            timezone_id="America/Los_Angeles",
        )
        try:
            yield ctx
        finally:
            ctx.close()
            browser.close()


def render(
    ctx,
    url: str,
    *,
    wait_for: Optional[str] = None,
    wait_timeout_ms: int = 15_000,
    settle_ms: int = 2500,
    scroll: bool = False,
) -> RenderedPage:
    """Open `url` in a new tab. Returns rendered HTML + XHR data.

    Never raises on wait timeouts — we always return whatever rendered.
    Downstream extraction decides whether that's enough.
    """
    import json as _json

    page = ctx.new_page()
    json_responses: list[tuple[str, dict | list]] = []
    xhr_urls: list[str] = []

    def on_response(resp):
        try:
            xhr_urls.append(f"{resp.status} {resp.request.method} {resp.url}")
            ct = resp.headers.get("content-type", "")
            if resp.status == 200 and "json" in ct.lower():
                body = resp.body()
                if len(body) < 2_000_000:
                    try:
                        json_responses.append((resp.url, _json.loads(body)))
                    except Exception:
                        pass
        except Exception:
            pass

    page.on("response", on_response)

    try:
        page.goto(url, timeout=30_000, wait_until="domcontentloaded")

        if wait_for:
            try:
                page.wait_for_selector(wait_for, timeout=wait_timeout_ms, state="attached")
            except Exception:
                pass

        if scroll:
            for _ in range(4):
                try:
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(400)
                except Exception:
                    break

        page.wait_for_timeout(settle_ms)
        html = page.content()
        final_url = page.url
    finally:
        page.close()

    return RenderedPage(url=final_url, html=html,
                        json_responses=json_responses, xhr_urls=xhr_urls)
