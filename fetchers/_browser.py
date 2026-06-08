"""
Shared headless-browser helper for SPA-driven venue sites.

Why Playwright instead of just requests:
  - SF Symphony sits behind Queue-it (anti-bot interstitial) — needs a real
    browser to clear the queue and load the actual page.
  - SF Opera / SFJAZZ / Audio render event data client-side via XHR — the
    initial HTML is mostly empty.

Strategy:
  We open the page in headless Chromium, optionally wait for a known content
  selector to appear (signals the JS has populated the DOM), then return
  the fully rendered HTML. Each per-venue fetcher then runs BeautifulSoup
  against the rendered HTML using normal scraping techniques.

  We also capture all JSON XHR responses during the render — venue fetchers
  that find a clean JSON API can use those directly (more robust than DOM
  scraping).

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


@contextmanager
def browser_session():
    """Yield a Playwright browser context. Use as:
        with browser_session() as ctx:
            page = render(ctx, "https://…", wait_for=".perf-card")
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1400, "height": 900})
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
    wait_timeout_ms: int = 20_000,
    settle_ms: int = 1500,
    scroll: bool = False,
) -> RenderedPage:
    """Open `url` in a new tab, optionally wait for `wait_for` CSS selector,
    return rendered HTML + any JSON XHR payloads we saw."""
    import json as _json

    page = ctx.new_page()
    json_responses: list[tuple[str, dict | list]] = []

    def on_response(resp):
        try:
            ct = resp.headers.get("content-type", "")
            if resp.status == 200 and "json" in ct.lower():
                # Cap body size to avoid OOM on huge feeds
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
        # Queue-it interstitials redirect — give them time to resolve
        page.wait_for_load_state("networkidle", timeout=wait_timeout_ms)
        if wait_for:
            try:
                page.wait_for_selector(wait_for, timeout=wait_timeout_ms, state="attached")
            except Exception:
                pass  # not fatal; we still grab whatever rendered
        if scroll:
            # Some sites lazy-load on scroll
            for _ in range(3):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(500)
        page.wait_for_timeout(settle_ms)
        html = page.content()
        final_url = page.url
    finally:
        page.close()

    return RenderedPage(url=final_url, html=html, json_responses=json_responses)
