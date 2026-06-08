# SF Venues Calendar

A self-updating iCalendar feed aggregating upcoming events from 8 SF venues
into a single calendar subscription. Designed to be deployed once and forgotten.

**Venues:** DNA Lounge · The Chapel · SF Symphony · SF Opera · SF Ballet ·
SFJAZZ · Audio · Mr Tipple's

## How it works

```
GitHub Actions cron (every 12h)
        │
        ▼
   python build.py
        │
        ├── Light HTTP fetchers     ── DNA Lounge (iCal), Chapel, Ballet, Mr Tipple's
        ├── Playwright/Chromium     ── SF Symphony, SF Opera, SFJAZZ, Audio
        │     ├── render page (handles Queue-it / SPAs / Tessitura widgets)
        │     ├── try JSON-LD in rendered HTML
        │     ├── try JSON XHR responses captured during render
        │     └── fall back to venue-specific DOM scraping
        │
        ├── each venue: success → cache. failure → use cache + emit ⚠ warning event
        │
        ▼
   docs/{dnalounge,chapel,…,all}.ics  +  docs/index.html
        │
        ▼
   Published to GitHub Pages → subscribe in Apple Calendar via webcal://
```

If a parser breaks, you don't lose the calendar — you get a single warning
event in your calendar telling you which venue's parser needs fixing.

## How each venue is fetched

| Venue | Method | Notes |
|---|---|---|
| **DNA Lounge** | Direct `.ics` passthrough | jwz publishes a clean machine-readable feed |
| **The Chapel** | SeeTickets widget HTML parse | Stable CSS classes |
| **SF Ballet** | Calendar grid HTML parse | Full 10-month season is SSR'd; classes are filtered by default config |
| **Mr Tipple's** | Weekly "check schedule" reminders | Small venue, no calendar feed |
| **SF Symphony** | **Playwright** + Tessitura DOM scrape | Page sits behind Queue-it anti-bot |
| **SF Opera** | **Playwright** + Tessitura DOM scrape | Smart Calendar widget renders client-side |
| **SFJAZZ** | **Playwright** + DOM scrape | React SPA |
| **Audio** | **Playwright** + Eventbrite enrichment | Homepage links to EB events; we hit each EB page for accurate dates |

## One-time setup (~5 minutes)

### 1. Push to a new GitHub repo

```bash
cd sf-venues-calendar
git init -b main
git add .
git commit -m "initial commit"
gh repo create sf-venues-calendar --public --source=. --push
```

(Or create the repo in the GH web UI and push manually.)

### 2. Enable GitHub Pages

In the repo on github.com:
1. **Settings → Pages**
2. **Source:** *GitHub Actions*  (NOT "Deploy from a branch")
3. Save.

### 3. Enable workflow permissions

**Settings → Actions → General → Workflow permissions:**
- Select **Read and write permissions**
- Check **Allow GitHub Actions to create and approve pull requests**
- Save.

### 4. Trigger the first build

**Actions tab → "Build SF Venues Calendar" → Run workflow → Run workflow**

Wait ~2–3 minutes (browser install adds ~30s the first time). When it finishes:
- Check the **Actions logs** for the per-venue summary
- Visit `https://<your-username>.github.io/sf-venues-calendar/` for the status dashboard

### 5. Subscribe in Apple Calendar

Open the URL from step 4 to find the `.ics` links. Then in Calendar.app:

**File → New Calendar Subscription** and paste one of:

| Calendar | URL |
|---|---|
| **Everything combined** | `webcal://<you>.github.io/sf-venues-calendar/all.ics` |
| DNA Lounge | `webcal://<you>.github.io/sf-venues-calendar/dnalounge.ics` |
| The Chapel | `…/chapel.ics` |
| SF Symphony | `…/symphony.ics` |
| SF Opera | `…/opera.ics` |
| SF Ballet | `…/ballet.ics` |
| SFJAZZ | `…/sfjazz.ics` |
| Audio | `…/audio.ics` |
| Mr Tipple's | `…/tipples.ics` |

In the subscription dialog:
- **Auto-refresh:** Every hour
- **Remove:** Alerts ✓, Attachments ✓ (recommended unless you want them)

**Tip:** Subscribe to each venue separately rather than `all.ics` — that way
each venue gets its own color and on/off toggle in the Calendar sidebar.

## Filtering

Edit `config.yaml` and push. The default config already filters ballet classes
(keeping only performances). Examples:

```yaml
venues:
  dnalounge:
    exclude_keywords:
      - "hubba hubba"
      - "industry night"
      - "bootie sf"

  symphony:
    include_keywords:
      - "mahler|beethoven|shostakovich"  # ONLY these composers

  ballet:
    exclude_keywords:
      - "ballet classes"
      - "pre-ballet"
```

Patterns are case-insensitive regex matched against title + description.

## Reliability map

| Venue | Day-1 reliability | Failure mode |
|---|---|---|
| **DNA Lounge** | Very high — direct iCal feed | Their server goes down |
| **The Chapel** | High — stable SeeTickets DOM | SeeTickets renames CSS classes |
| **SF Ballet** | High — calendar grid is server-rendered | Elementor widget HTML changes |
| **Mr Tipple's** | Always works (reminders are synthesized) | — |
| **SF Symphony** | Medium — depends on Tessitura DOM stability + Queue-it | Tessitura DOM changes, or Queue-it tightens detection |
| **SF Opera** | Medium — Tessitura DOM | Same as Symphony |
| **SFJAZZ** | Medium — React SPA with `/productions/` URLs | DOM restructure |
| **Audio** | Medium — relies on Eventbrite links present in homepage | Audio removes EB integration |

When a venue fails, the build still succeeds. The previously-cached events
stay published, and a single ⚠️ warning event appears in your calendar the
next day at noon — that's your "needs maintenance" signal.

## Tuning a broken Playwright fetcher

If e.g. SFJAZZ starts failing, look at the GH Actions logs. The error message
includes how much HTML was rendered and how many JSON XHRs were captured —
those numbers tell you what to do next:

- **HTML > 100KB, 0 JSON XHRs** → the page didn't make API calls. Probably
  needs a different `wait_for` selector or longer settle time. Check
  `fetchers/<venue>.py` and try selectors that match actual rendered cards.
- **0 JSON XHRs, normal HTML size** → DOM scraping is the path. Inspect
  rendered HTML structure in browser devtools, update the `_dom_scrape()`
  selectors.
- **JSON XHRs present but no events extracted** → the API uses unusual
  field names. Look at `_spa.py`'s `events_from_xhr()` — extend the
  `NAME_KEYS`/`START_KEYS`/etc. tuples.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

python build.py
open docs/index.html
```

## Directory layout

```
sf-venues-calendar/
├── .github/workflows/build.yml   # cron + deploy
├── fetchers/
│   ├── base.py                   # Event dataclass + JSON-LD helper
│   ├── _jsonld.py                # simple JSON-LD scraper (currently unused)
│   ├── _spa.py                   # Playwright pipeline: JSON-LD → XHR → DOM
│   ├── _browser.py               # Playwright context manager + render()
│   ├── dnalounge.py              # iCal passthrough
│   ├── chapel.py                 # SeeTickets HTML parse
│   ├── ballet.py                 # Elementor calendar grid HTML parse
│   ├── symphony.py / opera.py / sfjazz.py / audio.py   # Playwright-based
│   └── tipples.py                # weekly reminders
├── cache/                        # last-good events per venue (auto-committed)
├── docs/                         # GH Pages output (generated each build)
├── config.yaml                   # per-venue filters
├── build.py                      # orchestrator (shared browser session)
└── requirements.txt
```

## Stable UIDs (why duplicates don't happen)

Apple Calendar uses the iCal `UID` field to identify events across refreshes.
If a UID changes, you get a duplicate; if it stays the same, the event is
updated in place. UIDs here are derived from:

1. The source's stable ID where available (DNA Lounge URL, SeeTickets event
   ID, JSON-LD `@id`, Eventbrite event ID, ballet production slug)
2. **Always combined with the start date** — caught a real-world bug:
   SeeTickets uses ONE ticket ID for multi-night runs (NBA Finals across 4
   games). Source UID alone would collapse all 4 into one event.

If a venue rewrites all its titles or URLs, you'll see one cycle of
duplicates, then it self-corrects.

## Cost (GitHub Actions)

- Build time: ~1–2 min per run (Chromium install is cached after first run)
- Schedule: every 12h = 60 runs/month
- Free tier quota: 2,000 min/month for public repos → using <5%
- Storage: ~5MB for docs/ artifact per build, ~50MB for browser cache
