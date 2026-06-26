#!/usr/bin/env python3
"""
Update San Diego art listings.

Pipeline:
  1. Read sources.yaml.
  2. Fetch each allowed page (robots.txt respected, polite UA, rate-limited).
  3. Ask Claude to extract a structured list of events from each page.
  4. Normalize, de-duplicate, and keep events inside the rolling window.
  5. Write data/events.json and re-render the marked regions of
     index.html and onview.html.

Run modes:
  python scripts/update_listings.py                 # full run (needs ANTHROPIC_API_KEY)
  python scripts/update_listings.py --no-fetch       # just re-render from existing events.json
  python scripts/update_listings.py --horizon 14     # change the window length (days)
  REFERENCE_DATE=2026-06-06 python scripts/...        # pretend "today" is this date (testing)
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, html, datetime as dt
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "events.json")
SOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.yaml")

MODEL = "claude-haiku-4-5-20251001"   # cheap, fast extraction
HORIZON_DEFAULT = 12
MAX_TEXT_CHARS = 12000
REQUEST_TIMEOUT = 20
RATE_LIMIT_SECONDS = 1.5
RENDER_MIN_CHARS = 800   # below this, retry with a headless browser (if --render-js)
USER_AGENT = "SeesJournalBot/1.0 (+https://seesjournal.com; San Diego art listings, contact hello@seesjournal.com)"

CODES = {
    "balboa park":"BP","la jolla":"LJ","barrio logan":"BL","logan heights":"BL",
    "liberty station":"LS","point loma":"LS","north park":"NP","south park":"NP",
    "downtown":"DT","east village":"DT","gaslamp":"DT","little italy":"DT",
    "hillcrest":"HC","bankers hill":"HC","oceanside":"OC","escondido":"ES",
    "carlsbad":"CB","encinitas":"EN","chula vista":"CV","del mar":"DM","cardiff":"CD",
}

def today() -> dt.date:
    r = os.environ.get("REFERENCE_DATE")
    return dt.date.fromisoformat(r) if r else dt.date.today()

# ---------------------------------------------------------------- fetching
def robots_ok(url: str) -> bool:
    try:
        p = urlparse(url)
        rp = RobotFileParser()
        rp.set_url(f"{p.scheme}://{p.netloc}/robots.txt")
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True  # if robots can't be read, proceed politely

def _og_image(soup, base_url: str):
    """The page's official share image (og:image / twitter:image), absolute URL."""
    from urllib.parse import urljoin
    for attrs in ({"property": "og:image"}, {"name": "twitter:image"},
                  {"property": "twitter:image"}, {"name": "og:image"}):
        t = soup.find("meta", attrs=attrs)
        if t and t.get("content"):
            return urljoin(base_url, t["content"].strip())
    return None

def _extract_text(html: str, url: str):
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, "html.parser")
    image = _og_image(soup, url)
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    # Append each link's absolute URL inline (e.g. "Show Title [https://...]") so the
    # model can return the real per-event link instead of falling back to the homepage.
    for a in soup.find_all("a", href=True):
        href = urljoin(url, (a.get("href") or "").strip())
        if href.startswith("http"):
            a.append(f" [{href}] ")
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n"))[:MAX_TEXT_CHARS]
    return (text if text.strip() else None), image

def _fetch_static(url: str):
    import requests
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        print(f"  [warn] fetch failed {url}: {e}")
        return None, "error", None
    text, image = _extract_text(r.text, url)
    return (text, "ok", image) if text else (None, "empty", image)

def _fetch_rendered(url: str):
    """Render a JS-heavy page in a headless browser. Requires playwright; if it isn't
    installed this returns an error so the caller can fall back to the static result."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None, "error", None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(user_agent=USER_AGENT)
            page.set_default_timeout(30000)
            page.goto(url, wait_until="load")
            page.wait_for_timeout(2500)  # let client-side JS render the listings
            html = page.content()
            browser.close()
    except Exception as e:
        print(f"  [warn] render failed {url}: {e}")
        return None, "error", None
    text, image = _extract_text(html, url)
    return (text, "ok", image) if text else (None, "empty", image)

def fetch_text(url: str, ignore_robots: bool = False, render: bool = False):
    """Returns (text, status, image). Polite static GET first; if that yields thin or
    empty text and rendering is enabled, retries with a headless browser."""
    if not ignore_robots and not robots_ok(url):
        print(f"  [skip] robots.txt disallows {url}")
        return None, "robots", None
    text, status, image = _fetch_static(url)
    thin = status != "ok" or (text is not None and len(text) < RENDER_MIN_CHARS)
    if render and thin:
        rtext, rstatus, rimage = _fetch_rendered(url)
        if rstatus == "ok" and rtext and len(rtext) > len(text or ""):
            print(f"  [js] rendered {url} ({len(rtext)} chars)")
            return rtext, "ok", (rimage or image)
    return text, status, image

# ---------------------------------------------------------------- extraction
EXTRACT_PROMPT = """You are extracting art exhibitions and events from the text of a San Diego art webpage.

Return ONLY a JSON array (no prose, no markdown fences). Each element:
{{
  "title": "exhibition or event title",
  "venue": "gallery or museum name",
  "neighborhood": "San Diego neighborhood or city (e.g. Balboa Park, La Jolla, Barrio Logan, Oceanside)",
  "start_date": "YYYY-MM-DD or empty string if unknown",
  "end_date": "YYYY-MM-DD or empty string if unknown",
  "time": "reception/opening time if given, else empty string",
  "type": "one of: opening, reception, closing, art_walk, talk, market, exhibition",
  "url": "direct link to THIS specific event/exhibition page if present in the text; empty string if only a homepage is available or you are unsure",
  "description": "one short sentence, <= 20 words",
  "notable": true/false
}}

Rules:
- Extract EVERY visual-art exhibition or event shown on the page: exhibitions currently ON VIEW (ongoing), upcoming openings, receptions, closing/last-chance shows, art walks, artist talks, and art markets.
- Include a show even if only its title and a date range are listed (e.g. "Artist Name, through August 9"). If a current exhibition has no explicit end date, set end_date to "".
- Do NOT skip a show just because it opened before today — if it is still on view, include it.
- Ignore site navigation, ads, newsletter signups, store/shop product pages, and unrelated news articles.
- type: use "closing" for final-day/last-chance shows; "opening"/"reception" for new shows or receptions; "art_walk" for art walks/crawls; "talk" for lectures/tours; "market" for art markets/fairs; otherwise "exhibition".
- Set "notable" true for museum shows, closings happening soon, and art walks.
- Links in the page text appear in square brackets, e.g. "Show Title [https://gallery.com/exhibitions/show]". For "url", copy the bracketed link that belongs to THAT specific exhibition/event. Never use the site homepage or a generic listing root — use "" if no specific link is shown for it.
- Today's date is {today}. Include current and upcoming items; you may include ongoing exhibitions that are on view now even if they run past {horizon} days.
- If there are genuinely no visual-art exhibitions or events in the text, return [].

PAGE TEXT:
{body}
"""

def extract_events(text: str, source_url: str, horizon: int, client) -> list[dict]:
    prompt = EXTRACT_PROMPT.format(today=today().isoformat(), horizon=horizon, body=text)
    try:
        msg = client.messages.create(
            model=MODEL, max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        print(f"  [warn] extraction failed for {source_url}: {e}")
        return []
    # tolerate stray text around the JSON array
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not it.get("title"):
            continue
        it["source"] = source_url
        if not _is_event_url(it.get("url", "")):
            it["url"] = ""
        out.append(it)
    return out

# ---------------------------------------------------------------- normalize
def code_for(neighborhood: str) -> str:
    n = (neighborhood or "").strip().lower()
    for key, code in CODES.items():
        if key in n:
            return code
    return "".join(w[0] for w in n.split()[:2]).upper() if n else "SD"

def norm_key(ev: dict) -> str:
    t = re.sub(r"[^a-z0-9]+", "", (ev.get("title") or "").lower())
    v = re.sub(r"[^a-z0-9]+", "", (ev.get("venue") or "").lower())[:10]
    return f"{t}|{v}"

def merge(existing: list[dict], scraped: list[dict]) -> list[dict]:
    seen_first = {norm_key(e): e.get("first_seen") for e in existing}
    by_key: dict[str, dict] = {}
    for ev in scraped:
        k = norm_key(ev)
        ev["code"] = code_for(ev.get("neighborhood", ""))
        ev["is_pick"] = bool(ev.get("notable")) or ev.get("type") in {"opening", "closing", "reception", "art_walk"}
        ev["first_seen"] = seen_first.get(k, today().isoformat())
        ev.setdefault("confidence", "medium")
        if k in by_key:  # merge duplicate across sources: widen dates, fill blanks
            cur = by_key[k]
            for f in ("time", "url", "description", "image"):
                if not cur.get(f) and ev.get(f):
                    cur[f] = ev[f]
            if ev.get("start_date") and (not cur.get("start_date") or ev["start_date"] < cur["start_date"]):
                cur["start_date"] = ev["start_date"]
            if ev.get("end_date") and ev["end_date"] > cur.get("end_date", ""):
                cur["end_date"] = ev["end_date"]
        else:
            by_key[k] = ev
    return list(by_key.values())

def in_window(ev: dict, horizon: int) -> bool:
    t0, t1 = today(), today() + dt.timedelta(days=horizon)
    sd = ev.get("start_date") or ""
    ed = ev.get("end_date") or ""
    try:
        s = dt.date.fromisoformat(sd) if sd else t0
        # no end date -> treat as ongoing/open-ended (still on view through the window)
        e = dt.date.fromisoformat(ed) if ed else max(s, t1)
    except ValueError:
        return True  # keep undated events rather than silently drop
    return s <= t1 and e >= t0

# ---------------------------------------------------------------- rendering
PANEL = {"opening":"is-blue","reception":"is-blue","art_walk":"is-blue",
         "market":"is-blue","talk":"is-grey","exhibition":"is-grey","closing":"is-brown"}
KICK = {"opening":"Opening","reception":"Reception","closing":"Final Day",
        "art_walk":"Art Walk","talk":"Talk","market":"Market","exhibition":"On View"}

def esc(s: str) -> str:
    return html.escape(s or "", quote=True)

def _is_event_url(u: str) -> bool:
    """True only for a usable event link: an internal *.html page, or an absolute
    URL with a path beyond the domain root (i.e. not a bare homepage)."""
    if not u or u.startswith("#"):
        return False
    if u.endswith(".html"):
        return True
    from urllib.parse import urlparse
    p = urlparse(u)
    return p.scheme in ("http", "https") and bool(p.path.strip("/"))

def event_link(ev: dict) -> str:
    """The event's own URL if it's a real deep link; otherwise the source listing
    page it was scraped from; otherwise '#'."""
    u = ev.get("url", "")
    if _is_event_url(u):
        return u
    src = ev.get("source", "")
    if isinstance(src, str) and src.startswith("http"):
        return src
    return "#"

def card_date(ev: dict) -> str:
    t = ev.get("type")
    d = (ev.get("end_date") if t == "closing" else ev.get("start_date")) or ev.get("start_date") or ""
    try:
        return dt.date.fromisoformat(d).strftime("%b %-d")
    except ValueError:
        return d

def card(ev: dict) -> str:
    panel = "is-plain"
    kick = KICK.get(ev.get("type"), "On View")
    slug = ev.get("type") or "exhibition"
    date = card_date(ev)
    when = ev.get("time") or ""
    url = event_link(ev)
    ext = "" if url.endswith(".html") or url.startswith("#") else ' target="_blank" rel="noopener"'
    place = esc(ev.get("venue", ""))
    if ev.get("neighborhood"):
        place += " &middot; " + esc(ev.get("neighborhood", ""))
    img = ev.get("image")
    has = " has-photo" if img else ""
    photo = (f'<img class="card__photo" src="{esc(img)}" alt="" loading="lazy" '
             f"onerror=\"this.closest('.card__panel').classList.remove('has-photo');this.remove()\">"
             if img else "")
    return (f'<a class="card" href="{esc(url)}"{ext}>'
            f'<div class="card__panel {panel}{has}">{photo}'
            f'<span class="card__kick kick--{slug}">{esc(kick)}</span>'
            f'<span class="card__date">{esc(date)}</span>'
            f'<span class="card__when">{esc(when)}</span></div>'
            f'<div class="card__body"><div class="card__title">{esc(ev.get("title",""))}</div>'
            f'<div class="card__venue">{place}</div>'
            f'<span class="card__more">More Info</span></div></a>')

def grid(events: list[dict]) -> str:
    return '<div class="grid">\n' + "\n".join(card(e) for e in events) + "\n</div>"

def calendar(events: list[dict], horizon: int) -> str:
    by_day: dict[str, list[dict]] = {}
    t0 = today()
    for ev in events:
        sd = ev.get("start_date") or ""
        try:
            d = dt.date.fromisoformat(sd)
        except ValueError:
            continue
        if t0 <= d <= t0 + dt.timedelta(days=horizon):
            by_day.setdefault(sd, []).append(ev)
    if not by_day:
        return ('<p class="note">No dated openings or events in the next %d days. '
                'The picks above are on view now &mdash; check back for new dates.</p>' % horizon)
    featured_types = {"opening", "reception", "closing", "art_walk"}
    blocks = []
    for day in sorted(by_day):
        d = dt.date.fromisoformat(day)
        feats, rows = [], []
        for ev in by_day[day]:
            typ = ev.get("type", "")
            slug = typ or "exhibition"
            url = event_link(ev)
            if typ in featured_types:  # promote to a mini-card
                href = url if (url and url != "#") else "onview.html"
                ext = "" if href.endswith(".html") else ' target="_blank" rel="noopener"'
                label = KICK.get(typ, "On View")
                img = ev.get("image")
                thumb = (f'<img class="mcard__img" src="{esc(img)}" alt="" loading="lazy" '
                         f'onerror="this.remove()">' if img else "")
                place = esc(ev.get("venue", ""))
                if ev.get("neighborhood"):
                    place += " &middot; " + esc(ev.get("neighborhood", ""))
                feats.append(
                    f'<a class="mcard" href="{esc(href)}"{ext}>'
                    f'<div class="mcard__thumb thumb--{slug}">{thumb}</div>'
                    f'<div class="mcard__main"><span class="card__kick kick--{slug}">{esc(label)}</span>'
                    f'<div class="mcard__title">{esc(ev.get("title",""))}</div>'
                    f'<div class="mcard__venue">{place}</div></div>'
                    f'<div class="mcard__when">{esc(ev.get("time",""))}</div></a>')
            else:  # routine entry stays a compact row, with a colored type dot
                more = ""
                if url and url != "#":
                    ext = "" if url.endswith(".html") else ' target="_blank" rel="noopener"'
                    more = f'<a class="row__more" href="{esc(url)}"{ext}>Info</a>'
                rows.append(
                    f'<div class="row"><span class="row__title">'
                    f'<span class="dot dot--{slug}"></span>{esc(ev.get("title",""))}</span>'
                    f'<span class="row__venue">{esc(ev.get("venue",""))}</span>'
                    f'<span class="row__code">{esc(ev.get("code","SD"))}</span>'
                    f'<span class="row__time">{esc(ev.get("time",""))}</span>{more}</div>')
        items = "".join(feats) + "".join(rows)
        blocks.append(f'<div class="dayblock"><div class="dayblock__date">'
                      f'<div class="dd">{d.strftime("%d")}</div>'
                      f'<div class="dw">{d.strftime("%A")}</div></div>'
                      f'<div class="dayblock__items">{items}</div></div>')
    return "\n".join(blocks)

def replace_between(text: str, start: str, end: str, inner: str) -> str:
    pat = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pat.search(text):
        return text
    return pat.sub(lambda m: start + "\n" + inner + "\n" + end, text)

def daterange_str(horizon: int) -> str:
    t0, t1 = today(), today() + dt.timedelta(days=horizon)
    if t0.year == t1.year:
        return f"{t0.strftime('%B %-d')} &ndash; {t1.strftime('%B %-d, %Y')}"
    return f"{t0.strftime('%B %-d, %Y')} &ndash; {t1.strftime('%B %-d, %Y')}"

def hero_event(events: list[dict]):
    """The single event chosen for the homepage hero (an art walk in the window,
    otherwise the soonest pick, otherwise the soonest event)."""
    walks = [e for e in events if e.get("type") == "art_walk"]
    pool = walks or [e for e in events if e.get("is_pick")] or events
    return sorted(pool, key=lambda e: (e.get("start_date") or "9999"))[0] if pool else None

def hero_inner(events: list[dict]) -> str | None:
    ev = hero_event(events)
    if not ev:
        return None
    d = ev.get("start_date") or ""
    try:
        dd = dt.date.fromisoformat(d).strftime("%B %-d, %Y")
    except ValueError:
        dd = d
    place = esc(ev.get("neighborhood", "")) or esc(ev.get("code", "SD"))
    meta = f"{esc(dd)} &nbsp;|&nbsp; {place}" if dd else place
    cat = esc(ev.get("description") or KICK.get(ev.get("type"), "On View"))
    url = event_link(ev)
    if url == "#":
        url = "onview.html"
    ext = "" if url.endswith(".html") else ' target="_blank" rel="noopener"'
    return (f'<h3>{esc(ev.get("title",""))}</h3>'
            f'<p class="hero__meta">{meta}</p>'
            f'<p class="hero__cat">{cat}</p>'
            f'<a class="pill" href="{esc(url)}"{ext}>More Info</a>')

def onview_stats(events: list[dict]) -> str | None:
    """A live summary line for the On View header, e.g. '8 shows on view · 3 closing soon'."""
    total = len(events)
    if total == 0:
        return None
    closings = sum(1 for e in events if e.get("type") == "closing")
    openings = sum(1 for e in events if e.get("type") in {"opening", "reception"})
    walks = sum(1 for e in events if e.get("type") == "art_walk")
    parts = [f"{total} show{'s' if total != 1 else ''} on view"]
    if closings:
        parts.append(f"{closings} closing soon")
    if openings:
        parts.append(f"{openings} opening")
    if walks:
        parts.append(f"{walks} art walk{'s' if walks != 1 else ''}")
    return " &middot; ".join(parts)

def opening_header(picks: list[dict]) -> tuple[str, str, str]:
    """Adapt the homepage section tag/heading/subtitle to the current picks."""
    openings = sum(1 for e in picks if e.get("type") in {"opening", "reception"})
    closings = sum(1 for e in picks if e.get("type") == "closing")
    if closings > openings:
        tag, head = "Last Chance", "Closing Soon"
    elif openings:
        tag, head = "This Week", "This Week&rsquo;s Openings"
    else:
        tag, head = "On View", "On View This Week"
    shown, total = len(picks[:6]), len(picks)
    if total > shown:
        sub = f"{shown} of {total} picks &mdash; see them all on On View"
    else:
        sub = f"{total} pick{'s' if total != 1 else ''} this week &mdash; full calendar on On View"
    return tag, head, sub

def tips(events: list[dict], horizon: int) -> str:
    """Schedule-driven 'This Week' bullets built from the current in-window events."""
    from collections import Counter
    def fmt(ev): return esc(card_date(ev))
    walks    = sorted([e for e in events if e.get("type") == "art_walk"],            key=lambda e: (e.get("start_date") or "9999"))
    closings = sorted([e for e in events if e.get("type") == "closing"],             key=lambda e: (e.get("end_date") or "9999"))
    openings = sorted([e for e in events if e.get("type") in {"opening","reception"}], key=lambda e: (e.get("start_date") or "9999"))
    items = []
    if walks:
        joined = "; ".join(f"{esc(w.get('title') or w.get('venue',''))} ({fmt(w)})" for w in walks[:3])
        items.append(f"<li><b>Art walks this window:</b> {joined}.</li>")
    if closings:
        joined = "; ".join(f"{esc(c.get('title',''))} at {esc(c.get('venue',''))} ({fmt(c)})" for c in closings[:3])
        items.append(f"<li><b>Catch before it closes:</b> {joined}.</li>")
    if openings:
        joined = "; ".join(f"{esc(o.get('title',''))} ({fmt(o)})" for o in openings[:3])
        items.append(f"<li><b>Just opened / opening:</b> {joined}.</li>")
    days = Counter(e.get("start_date") for e in events if e.get("start_date"))
    if days:
        best, n = days.most_common(1)[0]
        try:
            label = dt.date.fromisoformat(best).strftime("%A, %b %-d")
            if n >= 2:
                items.append(f"<li><b>Busiest day:</b> {label} &mdash; {n} things on.</li>")
        except ValueError:
            pass
    if not items:
        items.append(f"<li>No dated openings or art walks in the next {horizon} days "
                     f"&mdash; the ongoing shows above are your best bet.</li>")
    return "\n        ".join(items)

def render(events: list[dict], horizon: int) -> None:
    picks = sorted([e for e in events if e.get("is_pick")],
                   key=lambda e: (e.get("start_date") or "9999"))[:8]
    stamp = today().strftime("%B %-d, %Y")

    # onview.html — picks grid, day-by-day calendar, "updated" stamp
    p = os.path.join(ROOT, "onview.html"); s = open(p).read()
    stats = onview_stats(events) or "Listings updating &mdash; new shows are being gathered."
    s = replace_between(s, "<!-- AUTO:ONVIEWSTATS:START -->", "<!-- AUTO:ONVIEWSTATS:END -->", stats)
    # Also On View: ongoing exhibitions already open (deep links via event_link, never stale)
    also = [e for e in events if e.get("type") == "exhibition"
            and (not e.get("start_date") or e["start_date"] < today().isoformat())]
    also.sort(key=lambda e: (e.get("end_date") or "9999"))
    also_html = grid(also[:9]) if also else \
        '<p class="note">No ongoing exhibitions listed right now &mdash; see the day-by-day calendar below.</p>'
    s = replace_between(s, "<!-- AUTO:ALSO:START -->", "<!-- AUTO:ALSO:END -->", also_html)
    cal = calendar(events, horizon)
    s = replace_between(s, "<!-- AUTO:CALENDAR:START -->", "<!-- AUTO:CALENDAR:END -->", cal)
    s = replace_between(s, "<!-- AUTO:UPDATED:START -->", "<!-- AUTO:UPDATED:END -->", f"Updated {stamp}")
    s = replace_between(s, "<!-- AUTO:TIPS:START -->", "<!-- AUTO:TIPS:END -->", tips(events, horizon))
    open(p, "w").write(s)

    # index.html — date range, hero standout, and highlight cards
    p = os.path.join(ROOT, "index.html"); s = open(p).read()
    s = replace_between(s, "<!-- AUTO:DATERANGE:START -->", "<!-- AUTO:DATERANGE:END -->", daterange_str(horizon))
    hev = hero_event(events)
    hero = hero_inner(events) or (
        '<h3>Listings updating</h3>'
        '<p class="hero__meta">New shows are being gathered</p>'
        '<p class="hero__cat">Openings, closings, and art walks will appear here shortly.</p>'
        '<a class="pill" href="onview.html">Browse all</a>')
    s = replace_between(s, "<!-- AUTO:HERO:START -->", "<!-- AUTO:HERO:END -->", hero)
    ordered = picks + [e for e in events if not e.get("is_pick")]
    home_picks = [e for e in ordered if e is not hev][:6]
    if home_picks:
        tag, head, sub = opening_header(home_picks)
        highlights = grid(home_picks)
    else:
        tag, head, sub = ("This Week", "What&rsquo;s On",
                          "Listings updating &mdash; check back shortly.")
        highlights = '<p class="note">New listings are being gathered &mdash; check back shortly.</p>'
    s = replace_between(s, "<!-- AUTO:OPENTAG:START -->", "<!-- AUTO:OPENTAG:END -->", tag)
    s = replace_between(s, "<!-- AUTO:OPENHEAD:START -->", "<!-- AUTO:OPENHEAD:END -->", head)
    s = replace_between(s, "<!-- AUTO:OPENSUB:START -->", "<!-- AUTO:OPENSUB:END -->", sub)
    s = replace_between(s, "<!-- AUTO:HIGHLIGHTS:START -->", "<!-- AUTO:HIGHLIGHTS:END -->", highlights)
    open(p, "w").write(s)

    print(f"  homepage: hero + {len(home_picks)} curated picks; onview: full board (calendar + ongoing)")

# ---------------------------------------------------------------- main
def _emit_summary(md: str) -> None:
    """Write to the GitHub Actions run summary if available, else to stdout."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(md)
    else:
        print("\n" + md)

def write_summary(results: list[dict], kept: int, horizon: int) -> None:
    if not results:  # --no-fetch or no API key: nothing was scraped
        msg = f"Re-rendered existing data &mdash; **{kept}** event(s) in the next {horizon} days."
        _emit_summary(f"## Listings update &mdash; {today().isoformat()}\n\n{msg}\n")
        print(msg.replace("**", "").replace("&mdash;", "-"))
        return
    icon = {"ok": "\u2705", "no events": "\u26aa", "empty": "\u26aa",
            "robots": "\u26d4", "error": "\u274c"}
    total = sum(r["events"] for r in results)
    n_ok = sum(1 for r in results if r["status"] == "ok")
    rows = sorted(results, key=lambda r: (-r["events"], r["name"]))
    md = [f"## Listings update &mdash; {today().isoformat()}", "",
          f"**{kept}** events in the next {horizon} days &middot; **{total}** extracted "
          f"from **{n_ok}/{len(results)}** sources that returned events", "",
          "| Source | Status | Events |", "|---|---|---:|"]
    for r in rows:
        ev = str(r["events"]) if r["status"] in ("ok", "no events") else "&ndash;"
        md.append(f"| {r['name']} | {icon.get(r['status'], '')} {r['status']} | {ev} |")
    dead = [r["name"] for r in results if r["status"] in ("robots", "error", "empty", "no events")]
    if dead:
        md += ["", f"**Returned nothing ({len(dead)}) &mdash; review or prune:** " + ", ".join(dead)]
    _emit_summary("\n".join(md) + "\n")
    print(f"\nSummary: {kept} in-window | {total} extracted | "
          f"{n_ok}/{len(results)} sources produced events | {len(dead)} returned nothing")

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="skip scraping; re-render existing events.json")
    ap.add_argument("--horizon", type=int, default=HORIZON_DEFAULT, help="window length in days")
    ap.add_argument("--ignore-robots", action="store_true", help="bypass robots.txt for all sources")
    ap.add_argument("--render-js", action="store_true", help="render JS-heavy pages with a headless browser when static text is thin")
    args = ap.parse_args(argv)
    results: list[dict] = []

    existing = json.load(open(DATA))["events"] if os.path.exists(DATA) else []

    if args.no_fetch:
        events = existing
    else:
        try:
            import yaml
            from anthropic import Anthropic
        except ImportError as e:
            print(f"Missing dependency: {e}. Run: pip install -r scripts/requirements.txt"); return 1
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY not set — re-rendering existing data only.")
            events = existing
        else:
            client = Anthropic()
            srcs = yaml.safe_load(open(SOURCES))["sources"]
            scraped: list[dict] = []
            for src in srcs:
                print(f"- {src['name']}")
                ig = args.ignore_robots or src.get("ignore_robots", False)
                rj = args.render_js or src.get("render_js", False)
                text, status, image = fetch_text(src["url"], ignore_robots=ig, render=rj)
                print(f"  fetched {len(text or '')} chars (status: {status})")
                n = 0
                if text:
                    evs = extract_events(text, src["url"], args.horizon, client)
                    n = len(evs)
                    print(f"  extracted {n} candidate event(s)")
                    # attach the page's share image only on focused pages (1-3 events),
                    # so big aggregator listings don't stamp a generic banner on everything
                    if image and 1 <= n <= 3:
                        for e in evs:
                            e.setdefault("image", image)
                    scraped.extend(evs)
                    status = "ok" if n else "no events"
                results.append({"name": src["name"], "status": status, "events": n})
                time.sleep(RATE_LIMIT_SECONDS)
            events = merge(existing, scraped) if scraped else existing

    kept = [e for e in events if in_window(e, args.horizon)]
    kept.sort(key=lambda e: (e.get("start_date") or "9999", e.get("title", "")))

    json.dump({"updated": today().isoformat(), "window_days": args.horizon, "events": kept},
              open(DATA, "w"), indent=2, ensure_ascii=False)
    print(f"events.json: {len(kept)} event(s) in window")

    render(kept, args.horizon)
    write_summary(results, len(kept), args.horizon)
    return 0

if __name__ == "__main__":
    sys.exit(main())
