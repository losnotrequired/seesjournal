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
EDITORIAL_PATH = os.path.join(ROOT, "data", "editorial.json")  # editor-owned ratings; survives re-scrapes
SOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.yaml")

MODEL = "claude-haiku-4-5-20251001"   # cheap, fast extraction
HORIZON_DEFAULT = 12
STYLE_VERSION = "32"   # bump when assets/style.css changes; render() stamps it into the pages
MAX_TEXT_CHARS = 60000
REQUEST_TIMEOUT = 20
RATE_LIMIT_SECONDS = 1.5
RENDER_MIN_CHARS = 800   # below this, retry with a headless browser (if --render-js)
USER_AGENT = "SeesJournalBot/1.0 (+https://seesjournal.com; San Diego art listings, contact hello@seesjournal.com)"
# Some public-event sites reject unknown bots with a 403. For those we retry once with a
# common browser UA — these are public listings pages and we still rate-limit politely.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

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

# Image URLs that are site chrome / branding / default share graphics rather than a
# specific event photo. Matched as delimited path or filename tokens so a real photo
# like "navarro_18880.jpg" is never dropped for containing "nav".
_GENERIC_IMG = re.compile(
    r"(?:^|[/_.\-])(logos?|icons?|sprite|avatar|favicon|placeholder|blank|spacer|pixel|"
    r"banner|sponsor|header|footer|nav|button|default|share|ogimage|"
    r"og[-_]?image|social|brand|branding|fallback|missing|noimage|no[-_]?image|"
    r"watermark|site[-_]?logo|default[-_]?image|transparent|1x1|lqip|lazyload|dummy)s?(?:$|[/_.\-])", re.I)

def _is_generic_image_url(url: str) -> bool:
    """True for logos, default/share graphics, and other non-event branding images."""
    return bool(url) and bool(_GENERIC_IMG.search(url))

def _img_url(tag, base_url: str) -> str:
    """Best real image URL for an <img>, handling common lazy-load attributes."""
    from urllib.parse import urljoin
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        v = (tag.get(attr) or "").strip()
        if v and not v.startswith("data:"):
            return urljoin(base_url, v)
    srcset = (tag.get("srcset") or tag.get("data-srcset") or "").strip()
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        if first and not first.startswith("data:"):
            return urljoin(base_url, first)
    return ""

def _extract_text(html: str, url: str):
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, "html.parser")
    image = _og_image(soup, url)
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    # Strip site chrome before the text gets measured against MAX_TEXT_CHARS: the budget must
    # be spent on event content, not navigation. Some sources (e.g. KPBS) carry a duplicated
    # mega-menu that alone exceeds the cap, pushing every event out of the window so 0 are
    # extracted. Nav and footer are always chrome. A <header> is removed only when it is
    # site-level — one nested inside an article/main/section is usually an event's own title
    # block, so it stays. <form> is deliberately left alone (some sites, e.g. ASP.NET
    # WebForms, wrap the entire page body in a single <form>).
    for tag in soup.find_all(["nav", "footer"]):
        tag.decompose()
    for tag in soup.find_all("header"):
        if not tag.find_parent(["article", "main", "section"]):
            tag.decompose()
    for tag in soup.find_all(attrs={"role": re.compile(r"^(navigation|banner|contentinfo|search)$", re.I)}):
        tag.decompose()
    # Many CMS sites (e.g. KPBS/Brightspot) build their menu/header/footer from <div>s with
    # descriptive class or id names rather than semantic tags, so the strips above miss them
    # and the menu (bloated further because we append every link's full URL below) blows past
    # the cap. Remove elements whose id/class marks them as chrome — but never one that already
    # contains date-like text, which is probably an events block in a class we'd otherwise hit.
    _CHROME = re.compile(r"(?:^|[^a-z])(nav|navbar|navigation|menu|megamenu|mainmenu|submenu|"
                         r"masthead|topbar|siteheader|site-header|globalheader|global-header|"
                         r"pageheader|page-header|sitefooter|site-footer|globalfooter|colophon|"
                         r"drawer|offcanvas|off-canvas|hamburger|flyout|breadcrumb|skiplink|"
                         r"skip-link|sharebar|socialbar|social-bar|subscribe|newsletter|cookie|"
                         r"gdpr|utility-nav|utilitynav)(?:$|[^a-z])", re.I)
    _DATEISH = re.compile(r"\b(january|february|march|april|may|june|july|august|september|"
                          r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|"
                          r"oct|nov|dec)\b[ .,\-/]*\d", re.I)
    for tag in soup.find_all(True):
        if tag.parent is None or tag.attrs is None:
            continue  # detached when an ancestor was already decomposed in this loop
        if tag.name in ("a", "li", "html", "body", "main", "article", "time"):
            continue
        ident = ((tag.get("id") or "") + " " + " ".join(tag.get("class") or [])).strip()
        if ident and _CHROME.search(ident) \
           and not _DATEISH.search(tag.get_text(" ", strip=True)[:400]):
            tag.decompose()
    # Append each link's absolute URL inline (e.g. "Show Title [https://...]") so the
    # model can return the real per-event link instead of falling back to the homepage.
    # But Add-to-Calendar / social-share / .ics links carry huge URL-encoded payloads and
    # are never the event's own page, so skip inlining those (they'd blow the char budget
    # and starve later events on calendars like KPBS).
    _SKIP_HREF = re.compile(
        r"(calendar\.google\.|outlook\.(live|office)\.|calendar\.yahoo\.|addtoany\.|"
        r"action=TEMPLATE|[?&](u|url|text|body|subject)=https?|\.ics(\?|$)|"
        r"//(www\.)?(facebook|twitter|x|linkedin|pinterest|reddit)\.com)", re.I)
    for a in soup.find_all("a", href=True):
        href = urljoin(url, (a.get("href") or "").strip())
        if href.startswith("http") and not _SKIP_HREF.search(href):
            a.append(f" [{href}] ")
    # Append each content image's URL inline (e.g. "{image: https://...}") so the model
    # can attach the right show photo to each event. Skip logos/icons/sprites.
    SKIP = re.compile(r"(logo|icon|sprite|avatar|favicon|placeholder|blank|spacer|pixel)", re.I)
    for im in soup.find_all("img"):
        src = _img_url(im, url)
        if src and src.startswith("http") and not SKIP.search(src):
            im.append(f" {{image: {src}}} ")
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n"))[:MAX_TEXT_CHARS]
    return (text if text.strip() else None), image

def _fetch_static(url: str):
    import requests
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        # If the site blocks our bot (403/401/429), retry once as a browser before giving up.
        if r.status_code in (401, 403, 429):
            r = requests.get(url, headers={"User-Agent": BROWSER_UA,
                                           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                           "Accept-Language": "en-US,en;q=0.9"},
                             timeout=REQUEST_TIMEOUT)
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
            page = browser.new_page(user_agent=BROWSER_UA)
            page.set_default_timeout(30000)
            page.goto(url, wait_until="load")
            # Many calendars (SpinGo, Eventbrite, Tribe) fetch their events by XHR *after*
            # load. Wait for the network to settle so that data is in the DOM before we read
            # it; ad/analytics-heavy pages may never idle, so cap it and proceed regardless.
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            page.wait_for_timeout(2500)  # a final beat for the widget to paint
            html = page.content()
            browser.close()
    except Exception as e:
        print(f"  [warn] render failed {url}: {e}")
        return None, "error", None
    text, image = _extract_text(html, url)
    return (text, "ok", image) if text else (None, "empty", image)

def fetch_text(url: str, ignore_robots: bool = False, render: bool = False, force_render: bool = False):
    """Returns (text, status, image). Polite static GET first; if that yields thin or
    empty text and rendering is enabled, retries with a headless browser. force_render
    renders regardless of static length (for JS pages whose static HTML is mostly nav)."""
    if not ignore_robots and not robots_ok(url):
        print(f"  [skip] robots.txt disallows {url}")
        return None, "robots", None
    text, status, image = _fetch_static(url)
    thin = status != "ok" or (text is not None and len(text) < RENDER_MIN_CHARS)
    if force_render or (render and thin):
        rtext, rstatus, rimage = _fetch_rendered(url)
        # For an explicit force_render, trust the rendered text even if it isn't longer
        # (static may be a 16k wall of nav that ties the cap); otherwise require a gain.
        if rstatus == "ok" and rtext and (force_render or len(rtext) > len(text or "")):
            print(f"  [js] rendered {url} ({len(rtext)} chars)")
            return rtext, "ok", (rimage or image)
    return text, status, image

# Third-party calendar / aggregator / ticketing domains. They're fine as SOURCES, but an event
# should never LINK to one of them ("another calendar site") — prefer the event's own venue or
# organizer page, falling back to the page we actually scraped it from.
_AGGREGATOR_HOSTS = (
    "sdvisualarts.net", "sandiegoreader.com", "kpbs.org", "sandiegoartdirectory.com",
    "sandiegomagazine.com", "theculturalcalendar.com", "dosd.com", "sandiego.gov",
    "mylibrary.digital", "eventbrite.", "allevents.in", "eventful.", "meetup.com",
    "do619.", "do210.", "ticketmaster.", "eventeny.", "tixr.", "seetickets.", "artsy.net",
    "artforum.com",
)

def _is_aggregator(url: str) -> bool:
    """True for third-party calendar/aggregator/ticketing sites we should not link an event to."""
    return any(h in (url or "").lower() for h in _AGGREGATOR_HOSTS)

def _is_sdvan(url: str) -> bool:
    """SDVAN specifically — its detail pages carry the show's own gallery link to swap in."""
    return "sdvisualarts.net" in (url or "").lower()

def _fetch_soup(url: str, ignore_robots: bool):
    """Fetch a detail page once and return a parsed soup (or None). Short timeout because
    this can run up to `limit` times per crawl."""
    try:
        if not ignore_robots and not robots_ok(url):
            return None
        import requests
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        if r.status_code != 200:
            return None
        from bs4 import BeautifulSoup
        return BeautifulSoup(r.text, "html.parser")
    except Exception:
        return None

def _image_from_soup(soup, base_url: str) -> str:
    """Best representative image on a detail page: the share image (og:image / twitter:image)
    if present, else a real content photo from a recognized media folder. Older sites (e.g.
    SDVAN) set no og:image but store event photos under paths like /images/events/ — target
    those and skip site chrome (logos, banners, sponsor strips, nav buttons, thumbnails).
    Skip words match only as delimited path/filename tokens, so a real photo like
    "navarro_18880.jpg" isn't dropped for containing "nav"."""
    og = _og_image(soup, base_url)
    if og and not _is_generic_image_url(og):
        return og
    # site chrome we never want as an event photo
    chrome = re.compile(r"(?:^|[/_.\-])(logo|icon|sprite|avatar|favicon|placeholder|blank|"
                        r"spacer|pixel|banner|sponsor|header|footer|nav|button|"
                        r"transparent|1x1|lqip|lazyload|dummy)s?(?:$|[/_.\-])", re.I)
    # "thumbnail" renditions are usually nav chrome, BUT some sites (e.g. Timken) serve the actual
    # event photo from a /.thumbnails/ path — so only treat thumb as chrome OUTSIDE event folders
    thumbish = re.compile(r"(?:^|[/_.\-])(thumb|thumbnail)s?(?:$|[/_.\-])", re.I)
    strong_dir = re.compile(r"(/images/events/|/events?/|/calendar/|/artwork|/exhibitions?/|/gallery/)", re.I)
    photo_dir = re.compile(r"(/images/events/|/uploads?/|/wp-content/uploads/|/events?/|/calendar/|"
                           r"/gallery/|/media/|/artwork|/exhibitions?/|/files/|/photos?/)", re.I)
    imgs = soup.find_all("img")
    # pass 1: a photo in a strong event/exhibition folder — accept even a thumbnail rendition,
    # since the resized image IS the event photo on sites like Timken (/uploads/events/.thumbnails/)
    for im in imgs:
        src = _img_url(im, base_url)
        if src and src.startswith("http") and not chrome.search(src) and strong_dir.search(src):
            return src
    # pass 2: otherwise any content photo in a media folder, skipping thumbnail/chrome renditions
    for im in imgs:
        src = _img_url(im, base_url)
        if (src and src.startswith("http") and not chrome.search(src)
                and not thumbish.search(src) and photo_dir.search(src)):
            return src
    return ""

def _canonical_link(soup, base_url: str) -> str:
    """On an aggregator detail page (SDVAN), find the show's own external link — the gallery's
    event/site page — so we can link there instead of the aggregator. Picks the most specific
    (longest) off-site link that isn't the aggregator, a social network, a map, or an email."""
    from urllib.parse import urljoin
    skip_host = ("sdvisualarts.net", "facebook.", "instagram.", "twitter.", "x.com",
                 "youtube.", "youtu.be", "linkedin.", "tiktok.", "pinterest.", "flickr.",
                 "google.com/maps", "maps.google", "goo.gl", "bit.ly", "paypal.", "eventbrite.")
    best = ""
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, (a.get("href") or "").strip())
        low = href.lower()
        if not low.startswith("http") or low.startswith("mailto"):
            continue
        if any(h in low for h in skip_host):
            continue
        if len(href) > len(best):   # the gallery's specific exhibition page is usually the longest
            best = href
    return best

def enrich_images(events: list, ignore_robots: bool, limit: int = 60) -> None:
    """Fetch each show's detail page to (a) pull a representative image when the listing
    didn't supply one OR only supplied a placeholder/spacer/logo (e.g. a lazy-load dummy or a
    transparent data-URI), replacing it with the page's real image (preferring og:image), and
    (b) for SDVAN aggregator links, swap in the gallery's own page so we point at the original
    source. One fetch per page covers both."""
    fetched = 0
    for ev in events:
        url = ev.get("url") or ""
        if not _is_event_url(url):
            continue
        is_sdvan = _is_sdvan(url)
        existing = ev.get("image") or ""
        # A placeholder/spacer/logo or a data-URI is not a usable image: treat it as missing so the
        # detail page's real image (og:image) can replace it. Real listing images are left as-is.
        needs_img = (not existing) or existing.startswith("data:") or _is_generic_image_url(existing)
        if not (needs_img or is_sdvan):    # nothing to gain from a fetch
            continue
        if fetched >= limit:
            break
        soup = _fetch_soup(url, ignore_robots)
        fetched += 1
        if soup is not None:
            if needs_img:
                img = _image_from_soup(soup, url)
                if img:
                    ev["image"] = img
            if is_sdvan:                    # set link AFTER image (image came from the SDVAN page)
                canon = _canonical_link(soup, url)
                if canon:
                    ev["url"] = canon
        time.sleep(RATE_LIMIT_SECONDS)
    print(f"  enriched {fetched} detail page(s)")


# ---------------------------------------------------------------- extraction
EXTRACT_PROMPT = """You are extracting art exhibitions and events from the text of a San Diego art webpage.

Return ONLY a JSON array (no prose, no markdown fences). Each element:
{
  "title": "exhibition or event title",
  "venue": "gallery or museum name",
  "neighborhood": "San Diego neighborhood or city (e.g. Balboa Park, La Jolla, Barrio Logan, Oceanside)",
  "start_date": "YYYY-MM-DD or empty string if unknown",
  "end_date": "YYYY-MM-DD or empty string if unknown",
  "time": "reception/opening time if given, else empty string",
  "type": "one of: opening, reception, closing, art_walk, talk, market, exhibition",
  "url": "link to THIS ONE event's OWN detail page — the deepest, most specific link for it, NOT a list/calendar/category page that shows many events; empty string if no specific page is shown",
  "description": "one short sentence, <= 20 words",
  "image": "URL of this show's image if one is tagged near it like {image: https://...}; else empty string",
  "notable": true/false
}

Rules:
- Extract EVERY visual-art exhibition or event shown on the page: exhibitions currently ON VIEW (ongoing), upcoming openings, receptions, closing/last-chance shows, art walks, artist talks, and art markets.
- The event MUST be primarily about VISUAL ART (painting, sculpture, photography, drawing, printmaking, ceramics, glass, textiles, installation, new media, etc.). EXCLUDE anything not centered on visual art, EVEN IF it appears on an arts/culture calendar: nature/animal/wildlife/"critter" programs, gardening, science/history/author/business lectures, music/concerts/theater/dance/comedy/film screenings, food/wine/beer/coffee events, fitness/yoga/wellness, holiday/community/neighborhood festivals, markets that aren't selling art, trivia/bingo/game nights, and children's or family activities. When you are unsure whether something is genuinely a visual-art event, leave it OUT.
- A farmers market, produce/food market, flea market, swap meet, street fair, or night market is NOT an art event even if its description mentions the word "art" or it has a few art/craft booths (e.g. "Farmers Market ... with music, art, fresh produce, and local products"). Only treat a market as an art event (type "market") when it is primarily ARTISTS SELLING THEIR OWN ARTWORK (an art fair / art market / artisan art show).
- Include a show even if only its title and a date range are listed (e.g. "Artist Name, through August 9"). NOTE: an item with an empty end_date is treated as a SINGLE-DAY event on its start_date, so leave end_date "" only for genuinely one-day events (reception, opening, talk, art walk, market); whenever an exhibition's closing/end date is stated anywhere in the text, you MUST capture it as end_date.
- Do NOT skip a show just because it opened before today — if it is still on view, include it.
- Ignore site navigation, ads, newsletter signups, store/shop product pages, and unrelated news articles.
- Do NOT include classes, workshops, courses, camps, summer or youth programs, children's activities, member-only events, fundraisers, or galas. Only public art exhibitions and art events (openings, receptions, art walks, artist talks, art markets).
- When a date range is shown (e.g. "March 22 - September 13", "through Aug 9", "on view May 1-Aug 1"), you MUST capture BOTH start_date and end_date. Never return only the start date when an end date is present in the text.
- type: ANY show with a date RANGE is "exhibition" (the multi-day run is what's on view) — even when the page also lists an opening or reception, the show itself stays "exhibition", NOT "reception"/"opening". Reserve "opening"/"reception" for a genuinely single-day opening or reception event (put that day in start_date and leave end_date ""). Use "closing" for final-day/last-chance shows; "art_walk" for art walks/crawls; "talk" for lectures/tours; "market" for art markets/fairs; otherwise "exhibition".
- Set "notable" true for museum shows, closings happening soon, and art walks.
- Links in the page text appear in square brackets, e.g. "Show Title [https://gallery.com/exhibitions/show]". For "url", copy the bracketed link that goes to THIS one event's OWN detail page — the deepest, most specific link for it (e.g. https://oma-online.org/events/artist-alliance-monthly-social-jun-28/), and STRONGLY prefer the venue's or organizer's own site. PARAMOUNT: never use a link to a page that lists many events — a homepage, or any calendar / "events" / "exhibitions" / category / listing page (e.g. https://oma-online.org/events/). Also do NOT use a different calendar, listings/aggregator site, or ticketing platform (e.g. Eventbrite, Meetup) even if one is shown. If this event has no specific same-site detail link in the text, use "".
- Images in the page text are tagged like {image: https://gallery.com/photo.jpg}. For "image", copy the one that clearly belongs to THAT specific exhibition/event (usually the closest tagged image). Leave "" if none is clearly its own.
- Today's date is {today}. Include current and upcoming items; you may include ongoing exhibitions that are on view now even if they run past {horizon} days.
- If there are genuinely no visual-art exhibitions or events in the text, return [].

PAGE TEXT:
{body}
"""

def _parse_events_json(raw: str) -> list:
    """Pull a JSON array of events out of the model's reply, tolerating code
    fences, an object wrapper, or stray prose around it."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw).strip()
    # 1) whole reply is clean JSON (array, or object containing an array)
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return v
    except json.JSONDecodeError:
        pass
    # 2) first [...] block in the reply
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    return []

_TRAIL_MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
                 r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_TRAIL_DATE = re.compile(
    r"\s*[-\u2013\u2014]\s*"                                          # " - " / " \u2013 " / "-"
    r"(?:(?:%s)\.?\s*\d{0,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?"          # Month [day][, year]
    r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s*$" % _TRAIL_MONTHS, re.I)

def clean_title(t: str) -> str:
    """Strip a date that aggregators bake onto the end of a recurring event's title, e.g.
    'First Friday Open Studios ... -July 3' or '... - November 7, 2025'. The real date lives
    in the date field; leaving it in the title causes mismatches like the standout showing
    '-July 3' while its 'More Info' link points to an older instance of the same series."""
    orig = (t or "").strip()
    t = orig
    for _ in range(3):                                  # peel stacked suffixes ("- Nov - July 3")
        nt = _TRAIL_DATE.sub("", t).strip()
        nt = re.sub(r"[\s\-\u2013\u2014:;,]+$", "", nt).strip()
        if nt == t:
            break
        t = nt
    return t if len(t) >= 3 else orig                   # never strip a title down to nothing

# Location markers clearly OUTSIDE San Diego County. Used to drop out-of-area shows from a
# source that also operates elsewhere (e.g. Oolong Gallery, which opened a New York / Tribeca
# space in 2026 and lists both NYC and San Diego County shows on one page). Only an event's
# LOCATION fields are tested — never its description — so an SD show that merely features a New
# York artist is kept. Add a source's `sd_only: true` in sources.yaml to apply this filter.
_NON_SD_LOCATION = re.compile(r"(new york|tribeca|manhattan|brooklyn|cortlandt|\bnyc\b)", re.I)

def _outside_san_diego(ev: dict) -> bool:
    """True if the event's location (venue/neighborhood/title) names a place outside SD County."""
    hay = " ".join(str(ev.get(k, "") or "") for k in ("neighborhood", "venue", "title"))
    return bool(_NON_SD_LOCATION.search(hay))

def extract_events(text: str, source_url: str, horizon: int, client) -> list[dict]:
    try:
        # Build with .replace() (not .format()): the prompt contains literal braces in its
        # JSON schema and in {image: ...} examples, and str.format() would try to read those
        # as fields and raise KeyError. .replace() only touches our three real placeholders.
        prompt = (EXTRACT_PROMPT
                  .replace("{today}", today().isoformat())
                  .replace("{horizon}", str(horizon))
                  .replace("{body}", text))
        msg = client.messages.create(
            model=MODEL, max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        print(f"  [warn] extraction failed for {source_url}: {e}")
        return []
    items = _parse_events_json(raw)
    if not items:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not it.get("title"):
            continue
        it["title"] = clean_title(it["title"])         # drop any date baked onto the title end
        if not it["title"]:
            continue
        it["source"] = source_url
        if not _is_event_url(it.get("url", "")):
            it["url"] = ""
        img = it.get("image", "")
        if not (isinstance(img, str) and img.startswith("http")):
            it["image"] = ""
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

LAST_CHANCE_DAYS = 10  # a run is "closing soon" / "Final Day" only within this many days of its end

def reconcile_type(ev: dict) -> str:
    """THE GENERAL RULE — applied to every event from every source.

    A label that marks a single MOMENT in a show's life — its opening/reception (the first day)
    or its closing / "Final Day" (the last day) — is only valid near that moment. Whenever the
    event's own dates contradict the label, the show is simply on view ("exhibition"). This keeps
    any source from surfacing a stale badge:
      • a show that opened weeks ago but still runs        -> not "Reception"/"Opening", just "On View"
      • a months-long run the model tagged "closing"       -> not "Final Day", just "On View"
    Genuine single-day events (no distinct end date) and still-upcoming openings are left untouched.
    Because the scraper re-runs daily, a show legitimately becomes "closing" again only once it is
    truly within LAST_CHANCE_DAYS of its end — so the kicker stays in step with the closing-soon
    overlay (both keyed off the same window and the same dates, never off the venue).
    """
    typ = ev.get("type") or "exhibition"
    sd, ed = ev.get("start_date") or "", ev.get("end_date") or ""
    if not (ed and ed != sd):          # single-day or open-ended: keep the moment label as given
        return typ
    try:
        s = dt.date.fromisoformat(sd) if sd else None
        e = dt.date.fromisoformat(ed)
    except ValueError:
        return typ                     # unparseable dates: don't second-guess the model
    t = today()
    if typ in ("opening", "reception") and s is not None and s < t:
        return "exhibition"            # the opening day has passed; it's an on-view show now
    if typ == "closing" and e > t + dt.timedelta(days=LAST_CHANCE_DAYS):
        return "exhibition"            # the end is not near; a "Final Day" badge would be false
    return typ

def merge(existing: list[dict], scraped: list[dict]) -> list[dict]:
    seen_first = {norm_key(e): e.get("first_seen") for e in existing}
    by_key: dict[str, dict] = {}
    for ev in scraped:
        k = norm_key(ev)
        # normalize null/missing dates to "" so later comparisons never hit None
        ev["start_date"] = ev.get("start_date") or ""
        ev["end_date"] = ev.get("end_date") or ""
        ev["code"] = code_for(ev.get("neighborhood", ""))
        ev["type"] = reconcile_type(ev)
        ev["is_pick"] = bool(ev.get("notable")) or ev.get("type") in {"opening", "closing", "reception", "art_walk"}
        ev["first_seen"] = seen_first.get(k, today().isoformat())
        ev.setdefault("confidence", "medium")
        if k in by_key:  # merge duplicate across sources: widen dates, fill blanks
            cur = by_key[k]
            for f in ("time", "url", "description", "image"):
                if not cur.get(f) and ev.get(f):
                    cur[f] = ev[f]
            # If the kept link is a SDVAN aggregator page but this duplicate has the show's
            # own gallery link, prefer the original source.
            if _is_aggregator(cur.get("url", "")) and ev.get("url") and not _is_aggregator(ev["url"]):
                cur["url"] = ev["url"]
            cs, es = cur.get("start_date") or "", ev.get("start_date") or ""
            if es and (not cs or es < cs):
                cur["start_date"] = es
            ce, ee = cur.get("end_date") or "", ev.get("end_date") or ""
            if ee and ee > ce:
                cur["end_date"] = ee
        else:
            by_key[k] = ev
    return list(by_key.values())

def in_window(ev: dict, horizon: int) -> bool:
    t0, t1 = today(), today() + dt.timedelta(days=horizon)
    sd = ev.get("start_date") or ""
    ed = ev.get("end_date") or ""
    try:
        s = dt.date.fromisoformat(sd) if sd else t0
        # RULE: an event with no end date is a single-day event (it occurs on its start date),
        # so a past one has ended. Only an explicit end date keeps a show on view as "ongoing".
        e = dt.date.fromisoformat(ed) if ed else s
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

_NEVER_LINK_HOSTS = ("dosd.com",)   # we discover events here, but never send readers to it

# Path segments / slugs that mean "a page that lists MANY events" (a calendar, category, or
# landing page) rather than one event's own page. PARAMOUNT RULE: we never link to these — always
# link to the specific event's detail page (e.g. /events/artist-alliance-monthly-social-jun-28/).
_LISTING_LAST = {
    "events", "event", "exhibitions", "exhibition", "exhibits", "exhibit", "calendar",
    "calendars", "shows", "show", "listings", "listing", "whats-on", "whatson", "on-view",
    "onview", "things-to-do", "programs", "program", "schedule", "agenda", "gallery",
    "galleries", "home", "index", "news", "blog", "all-events", "current", "past", "upcoming",
}
_LISTING_CONTAINS = re.compile(
    r"(events?-in-|community-events|art-events|arts?-and-culture|ongoing[-_]?events|"
    r"event-(?:list|calendar)|exhibitions?-events|single-category|category)", re.I)

def _is_listing_page(url: str) -> bool:
    """True if the URL is a page that lists many events (a calendar / category / landing page)
    rather than one specific event's own page. We never link a reader to one of these."""
    if not url or not url.startswith("http"):
        return False
    from urllib.parse import urlparse
    segs = [s for s in urlparse(url).path.split("/") if s]
    if not segs:
        return True                                      # bare domain / homepage
    last = re.sub(r"\.(php|html?|aspx?)$", "", segs[-1].lower())
    return last in _LISTING_LAST or bool(_LISTING_CONTAINS.search(last))

def event_link(ev: dict) -> str:
    """Best EXTERNAL destination for an event. Preference: the event's own specific detail page;
    otherwise the venue's own website (the source) so the reader still lands on the venue — never
    looped back to our own calendar. Returns "" only when even the source is unlinkable (a
    never-link host or not a URL); the card then renders unclickable instead of as a dead link."""
    from urllib.parse import urlparse
    u = ev.get("url", "")
    src = ev.get("source", "")
    def never(url): return any(h in (url or "").lower() for h in _NEVER_LINK_HOSTS)
    src_host = urlparse(src).netloc.lower().replace("www.", "") if isinstance(src, str) and src.startswith("http") else ""
    # 1) the event's own deep page: a real event URL, not a listing page, not a never-link host,
    #    and not a *foreign* aggregator calendar
    if _is_event_url(u) and not never(u) and not _is_listing_page(u):
        u_host = urlparse(u).netloc.lower().replace("www.", "")
        if not (_is_aggregator(u) and u_host != src_host):
            return u
    # 2) fall back to the venue's own site (the source). Even a listing/landing page is fine — it is
    #    the venue's real site. Only skip never-link hosts (we discover there but don't link out).
    if isinstance(src, str) and src.startswith("http") and not never(src):
        return src
    return ""

def card_date(ev: dict) -> str:
    s, e = ev.get("start_date") or "", ev.get("end_date") or ""
    def fmt(d):
        try:
            return dt.date.fromisoformat(d).strftime("%b %-d")
        except ValueError:
            return d
    def parse(d):
        try:
            return dt.date.fromisoformat(d)
        except ValueError:
            return None
    sd, ed, t = parse(s), parse(e), today()
    if s and e and s == e:
        return fmt(s)                                  # single-day event
    if e:
        # Anything already on view reads consistently as "Through <close date>".
        # Upcoming shows (not open yet) keep the full range so the opening date is clear.
        if sd and sd > t:
            return f"{fmt(s)} \u2013 {fmt(e)}"          # upcoming: Aug 1 – Sep 1
        return f"Through {fmt(e)}"                       # on view now: Through Sep 1
    if s:
        return f"Opens {fmt(s)}" if (sd and sd > t) else f"From {fmt(s)}"
    return ""

_EDITORIAL = None
def load_editorial() -> dict:
    """Editor-owned ratings (data/editorial.json), keyed by norm_key. Loaded once and
    cached. The scraper never writes this file, so ratings persist across re-scrapes;
    they're merged in only at render time. Keys starting with '_' are docs and ignored."""
    global _EDITORIAL
    if _EDITORIAL is None:
        try:
            with open(EDITORIAL_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            _EDITORIAL = {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}
        except (FileNotFoundError, ValueError):
            _EDITORIAL = {}
    return _EDITORIAL

def derive_facts(ev: dict) -> dict:
    """The factual half of the block — what the scraper can fill on its own."""
    closing_soon = False
    s, e = ev.get("start_date") or "", ev.get("end_date") or ""
    try:
        end = dt.date.fromisoformat(e)
        # only a genuine multi-day show that's about to close — never a single-day event
        # (an opening/talk whose end_date == start_date is happening "today", not "last chance")
        closing_soon = (s != e) and (today() <= end <= today() + dt.timedelta(days=LAST_CHANCE_DAYS))
    except ValueError:
        pass
    text = ((ev.get("title") or "") + " " + (ev.get("description") or "")).lower()
    return {"closing_soon": closing_soon, "free": bool(re.search(r"\bfree\b", text))}

def _stars(n) -> str:
    n = max(0, min(5, int(n)))
    return '<span class="stars" aria-label="%d out of 5 stars">%s%s</span>' % (n, "\u2605" * n, "\u2606" * (5 - n))

def rating_block(ev: dict, compact: bool = False) -> str:
    """The editorial block on a listing: stars + the 40-word case + fact/judgment badges.
    Editorial fields come from editorial.json; closing-soon and free are auto-derived.
    Returns '' when there's nothing to show, so un-rated listings render exactly as before."""
    ed = load_editorial().get(norm_key(ev), {})
    facts = derive_facts(ev)
    badges = []
    if (ed.get("status") == "Last Chance") or facts["closing_soon"]:
        badges.append('<span class="badge badge--last">Last chance</span>')
    elif ed.get("status") == "Opening":
        badges.append('<span class="badge badge--open">Opening</span>')
    price = ed.get("price") or ("Free" if facts["free"] else "")
    if price:
        badges.append('<span class="badge badge--%s">%s</span>' % ("free" if str(price).lower() == "free" else "paid", esc(str(price))))
    if ed.get("time"):
        badges.append('<span class="badge badge--time">%s</span>' % esc(str(ed["time"])))
    if ed.get("hidden_gem"):
        badges.append('<span class="badge badge--gem">Hidden gem</span>')
    if ed.get("first_timer"):
        badges.append('<span class="badge badge--first">First-timer friendly</span>')
    stars = _stars(ed["rating"]) if ed.get("rating") else ""
    why = esc(str(ed["why"])) if ed.get("why") else ""
    who = esc(str(ed["who"])) if ed.get("who") else ""
    if compact:                              # calendar mini-cards: stars + badges only
        if not (stars or badges):
            return ""
    elif not (stars or why or who or badges):
        return ""
    out = '<div class="card__rate">'
    if stars:
        out += stars
    if badges:
        out += '<span class="card__badges">' + "".join(badges) + "</span>"
    out += "</div>"
    if not compact:
        if why:
            out += '<p class="card__why">%s</p>' % why
        if who:
            out += '<p class="card__who"><span>For</span> %s</p>' % who
    return out

def card(ev: dict) -> str:
    kick = KICK.get(ev.get("type"), "On View")
    slug = ev.get("type") or "exhibition"
    date = card_date(ev)
    url = event_link(ev)               # external venue/event page, or "" if nothing safe to link
    place = esc(ev.get("venue", ""))
    if ev.get("neighborhood"):
        place = (place + " &middot; " if place else "") + esc(ev.get("neighborhood", ""))
    img = ev.get("image")
    panel = "is-plain has-photo" if img else "is-blue"
    photo = (f'<img class="card__photo" src="{esc(img)}" alt="" loading="lazy" '
             f"onerror=\"this.closest('.card__panel').classList.remove('has-photo');this.closest('.card__panel').classList.add('is-blue');this.remove()\">"
             if img else "")
    panel_html = (f'<div class="card__panel {panel}">{photo}'
                  f'<span class="card__kick kick--{slug}">{esc(kick)}</span>'
                  f'<span class="card__date">{esc(date)}</span></div>')
    body = (f'<div class="card__body"><div class="card__title">{esc(ev.get("title",""))}</div>'
            f'<div class="card__venue">{place}</div>'
            f'{rating_block(ev)}'
            + (f'<span class="card__more">More Info</span>' if url else "") + '</div>')
    if url:
        return f'<a class="card" href="{esc(url)}" target="_blank" rel="noopener">{panel_html}{body}</a>'
    return f'<div class="card card--static">{panel_html}{body}</div>'

def grid(events: list[dict]) -> str:
    return '<div class="grid">\n' + "\n".join(card(e) for e in events) + "\n</div>"

def calendar(events: list[dict], horizon: int) -> str:
    by_day: dict[str, list[dict]] = {}
    t0 = today()
    for ev in events:
        typ = ev.get("type", "")
        # closing / last-chance shows belong on their END date; everything else on its START date
        key = (ev.get("end_date") or "") if typ == "closing" else (ev.get("start_date") or "")
        try:
            d = dt.date.fromisoformat(key)
        except ValueError:
            continue
        if t0 <= d <= t0 + dt.timedelta(days=horizon):
            by_day.setdefault(key, []).append(ev)
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
                label = KICK.get(typ, "On View")
                img = ev.get("image")
                thumb = (f'<img class="mcard__img" src="{esc(img)}" alt="" loading="lazy" '
                         f'onerror="this.remove()">' if img else "")
                place = esc(ev.get("venue", ""))
                if ev.get("neighborhood"):
                    place = (place + " &middot; " if place else "") + esc(ev.get("neighborhood", ""))
                mwhen = ev.get("time", "") if typ in {"opening", "reception", "art_walk", "talk", "market"} else ""
                inner_m = (f'<div class="mcard__thumb thumb--{slug}">{thumb}</div>'
                           f'<div class="mcard__main"><span class="card__kick kick--{slug}">{esc(label)}</span>'
                           f'<div class="mcard__title">{esc(ev.get("title",""))}</div>'
                           f'<div class="mcard__venue">{place}</div>'
                           f'{rating_block(ev, compact=True)}</div>'
                           f'<div class="mcard__when">{esc(mwhen)}</div>')
                if url:
                    feats.append(f'<a class="mcard" href="{esc(url)}" target="_blank" rel="noopener">{inner_m}</a>')
                else:
                    feats.append(f'<div class="mcard mcard--static">{inner_m}</div>')
            else:  # routine entry: a compact row (whole tile links externally; non-clickable if no link)
                rwhen = ev.get("time", "") if typ in {"opening", "reception", "art_walk", "talk", "market"} else ""
                rimg = ev.get("image")
                rinner = (f'<img class="row__img" src="{esc(rimg)}" alt="" loading="lazy" '
                          f'onerror="this.remove()">' if rimg else "")
                inner = (f'<span class="row__sq thumb--{slug}">{rinner}</span>'
                         f'<span class="row__title">{esc(ev.get("title",""))}</span>'
                         f'<span class="row__venue">{esc(ev.get("venue") or ev.get("neighborhood") or "")}</span>'
                         f'<span class="row__time">{esc(rwhen)}</span>')
                if url:
                    rows.append(f'<a class="row" href="{esc(url)}" target="_blank" rel="noopener">{inner}</a>')
                else:
                    rows.append(f'<div class="row row--static">{inner}</div>')
        items = "".join(feats) + "".join(rows)
        blocks.append(f'<div class="dayblock"><div class="dayblock__date">'
                      f'<div class="dd">{d.strftime("%d")}</div>'
                      f'<div class="dw">{d.strftime("%A")}</div></div>'
                      f'<div class="dayblock__items">{items}</div></div>')
    return "\n".join(blocks)

WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

def calendar_grid(events: list[dict], days: int = 14) -> str:
    """A weekday-aligned, month-style grid for the next ~two weeks. Each day cell lists its events
    as compact colored bars (closing shows sit on their end date, everything else on its start
    date). The grid starts on the Sunday on/before today and runs enough whole weeks to cover the
    next `days` days; cells outside that window are dimmed and today is highlighted."""
    import math
    t0 = today()
    start = t0 - dt.timedelta(days=(t0.weekday() + 1) % 7)   # Sunday on/before today
    last_target = t0 + dt.timedelta(days=days - 1)
    weeks = max(1, math.ceil(((last_target - start).days + 1) / 7))
    last_day = start + dt.timedelta(days=weeks * 7 - 1)

    by_day: dict[str, list[dict]] = {}
    for ev in events:
        typ = ev.get("type", "")
        key = (ev.get("end_date") or "") if typ == "closing" else (ev.get("start_date") or "")
        try:
            d = dt.date.fromisoformat(key)
        except ValueError:
            continue
        if start <= d <= last_day:
            by_day.setdefault(key, []).append(ev)
    rank = {"art_walk": 0, "opening": 1, "reception": 1, "closing": 2, "talk": 3, "market": 4, "exhibition": 5}
    for k in by_day:
        by_day[k].sort(key=lambda e: (rank.get(e.get("type"), 9), e.get("title", "")))

    if not any(by_day.values()):
        return ('<p class="note">No dated events fall in the next two weeks yet &mdash; '
                'see the day-by-day list below for everything currently on view.</p>')

    SHOWN = 4
    rows = []
    cur = start
    for _ in range(weeks):
        cells = []
        for _ in range(7):
            iso = cur.isoformat()
            cls = []
            if cur == t0:
                cls.append("is-today")
            if cur < t0 or cur > last_target:
                cls.append("is-out")
            num = cur.strftime("%-d") if cur.day != 1 else cur.strftime("%b %-d")
            evs = by_day.get(iso, [])
            bars = []
            for ev in evs[:SHOWN]:
                typ = ev.get("type") or "exhibition"
                link = event_link(ev)
                href = link if (link and link != "#") else "onview.html"
                ext = "" if href.endswith(".html") else ' target="_blank" rel="noopener"'
                tm = ev.get("time", "") if typ in {"opening", "reception", "art_walk", "talk", "market"} else ""
                label = (tm + " " if tm else "") + (ev.get("title") or ev.get("venue") or "Event")
                bars.append(f'<a class="calev calev--{typ}" href="{esc(href)}"{ext} '
                            f'title="{esc(ev.get("title",""))}">{esc(label)}</a>')
            if len(evs) > SHOWN:
                bars.append(f'<span class="cal__more">+{len(evs) - SHOWN} more</span>')
            attr = (' class="' + " ".join(cls) + '"') if cls else ""
            cells.append(f'<td{attr}><span class="cal__num">{num}</span>{"".join(bars)}</td>')
            cur += dt.timedelta(days=1)
        rows.append("<tr>" + "".join(cells) + "</tr>")

    head = "".join(f"<th>{w}</th>" for w in WEEKDAYS)
    legend = (
        '<div class="callegend">'
        '<span><i style="background:var(--blue)"></i>Opening / Reception</span>'
        '<span><i style="background:var(--brick)"></i>Closing</span>'
        '<span><i style="background:#7b4fb0"></i>Art Walk</span>'
        '<span><i style="background:#2b8a86"></i>Talk</span>'
        '<span><i style="background:#b9802b"></i>Market</span>'
        '<span><i style="background:#6b6258"></i>Exhibition</span>'
        '</div>')
    return (f'<div class="calwrap"><table class="calgrid"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>{legend}</div>')

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
    """The single event chosen for the homepage hero (an art walk in the window, otherwise
    the soonest pick, otherwise the soonest event). Aggregator-*discovered* events are kept
    out of the headline: their dates can be projected forward (recurring series) and their
    links can resolve to a stale instance, which makes the standout look wrong. Within the
    chosen pool, prefer one that has an image so the standout can show a photo."""
    def canonical(e):
        return not _is_aggregator(e.get("source", "")) and not _is_aggregator(e.get("url", ""))
    walks = [e for e in events if e.get("type") == "art_walk" and canonical(e)]
    picks = [e for e in events if e.get("is_pick") and canonical(e)]
    canon = [e for e in events if canonical(e)]
    pool = walks or picks or canon or events       # fall back to all only if nothing canonical
    if not pool:
        return None
    ranked = sorted(pool, key=lambda e: (e.get("start_date") or "9999"))
    withimg = [e for e in ranked if e.get("image")]
    return withimg[0] if withimg else ranked[0]

def hero_photo(ev) -> str:
    """Background photo + legibility scrim for the standout hero. Empty when the
    event has no image, so the CSS brown gradient shows through as the fallback.
    If the image 404s, onerror removes both the img and the scrim."""
    img = ev.get("image") if ev else None
    if not img:
        return ""
    return (f'<img class="hero__photo" src="{esc(img)}" alt="" '
            f"onerror=\"var s=this.nextElementSibling; if(s){{s.remove();}} this.remove();\">"
            f'<span class="hero__scrim"></span>')

def hero_date(ev: dict) -> str:
    """Date line for the standout hero: show the show's full run (a period), not a single
    day. Mirrors card_date's logic but in the hero's fuller 'Month D, YYYY' style."""
    s, e = ev.get("start_date") or "", ev.get("end_date") or ""
    def parse(d):
        try: return dt.date.fromisoformat(d)
        except ValueError: return None
    def full(d): return d.strftime("%B %-d, %Y")      # March 22, 2025
    def md(d):   return d.strftime("%B %-d")           # March 22
    sd, ed, t = parse(s), parse(e), today()
    if sd and ed and sd != ed:
        return (f"{md(sd)} \u2013 {full(ed)}" if sd.year == ed.year   # March 22 – June 15, 2025
                else f"{full(sd)} \u2013 {full(ed)}")                  # Dec 1, 2025 – Jan 5, 2026
    if sd and ed and sd == ed:
        return full(sd)                                                # genuine one-day event
    if ed and not sd:
        return f"Through {full(ed)}"                                   # Through June 15, 2025
    if sd and not ed:
        return f"Opens {full(sd)}" if sd > t else f"From {full(sd)}"   # Opens / From March 22, 2025
    return s or e                                                      # unparseable fallback

def hero_inner(events: list[dict]) -> str | None:
    ev = hero_event(events)
    if not ev:
        return None
    dd = hero_date(ev)
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
    shown, total = len(picks), len(picks)
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

# ---------------------------------------------------------------- de-duplication
# Some shows are listed by more than one source with slightly different wording
# (e.g. "Exhibition Reception: Splash of Color" vs "Splash of Color Exhibition
# Opening Reception & Celebration", or "Clearly Indigenous" vs its full subtitle
# "Clearly Indigenous: Native Visions Reimagined in Glass"). The exact title+venue
# key in merge() can't catch those, so we fold them here, just before rendering.
# Two entries collapse ONLY when they share the same anchor date (so an opening
# and a separate closing-day entry are never merged), the same place, and the same
# distinctive title words. When folding, the richest fields win — in particular the
# hours, if any one copy has them.
_GENERIC_TITLE_WORDS = {
    "exhibition", "exhibit", "exhibitions", "reception", "opening", "openings",
    "closing", "celebration", "celebrations", "gala", "preview", "premiere",
    "artist", "talk", "tour", "market", "presents", "present", "presenting",
    "featuring", "feat", "the", "a", "an", "of", "and", "with", "at", "in",
    "on", "for", "art", "arts", "juried", "annual", "free", "party", "members",
    "member", "fundraiser", "benefit", "new", "amp", "to", "by",
}
_TYPE_PRIORITY = ["art_walk", "opening", "reception", "closing", "market", "talk", "exhibition"]

def _title_tokens(title: str) -> set:
    t = re.sub(r"[^a-z0-9]+", " ", (title or "").lower())
    return {w for w in t.split() if w and w not in _GENERIC_TITLE_WORDS}

def _place_match(a: dict, b: dict) -> bool:
    va = re.sub(r"[^a-z0-9]+", "", (a.get("venue") or "").lower())
    vb = re.sub(r"[^a-z0-9]+", "", (b.get("venue") or "").lower())
    na = re.sub(r"[^a-z0-9]+", "", (a.get("neighborhood") or "").lower())
    nb = re.sub(r"[^a-z0-9]+", "", (b.get("neighborhood") or "").lower())
    if va and vb and (va == vb or va in vb or vb in va):
        return True
    if na and nb and na == nb:
        return True
    return False

def _anchor_date(ev: dict) -> str:
    return (ev.get("end_date") or "") if ev.get("type") == "closing" else (ev.get("start_date") or "")

def _same_event(a: dict, b: dict) -> bool:
    if _anchor_date(a) != _anchor_date(b):
        return False
    if not _place_match(a, b):
        return False
    ta, tb = _title_tokens(a.get("title", "")), _title_tokens(b.get("title", ""))
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    # the smaller title's words are fully contained in the larger's; require real
    # signal so a one-word generic core can't swallow an unrelated show
    if small <= big and (len(small) >= 2 or (len(small) == 1 and len(next(iter(small))) >= 4)):
        return True
    return False

def _merge_group(group: list[dict]) -> dict:
    if len(group) == 1:
        return group[0]
    def tprio(ev):
        t = ev.get("type", "exhibition")
        return _TYPE_PRIORITY.index(t) if t in _TYPE_PRIORITY else len(_TYPE_PRIORITY)
    base = dict(min(group, key=tprio))                  # most actionable type wins
    def first(field):
        for e in group:
            if e.get(field):
                return e[field]
        return ""
    titles = [e.get("title", "").strip() for e in group if e.get("title", "").strip()]
    if titles:
        base["title"] = min(titles, key=len)            # cleanest (shortest) title
    venues = [e.get("venue", "").strip() for e in group if e.get("venue", "").strip()]
    if venues:
        base["venue"] = max(venues, key=len)            # most specific venue name
    base["time"] = first("time")                        # KEEP the hours if any copy has them
    base["neighborhood"] = first("neighborhood") or base.get("neighborhood", "")
    base["image"] = first("image")
    descs = [e.get("description", "") for e in group if e.get("description")]
    if descs:
        base["description"] = max(descs, key=len)
    best = next((e["url"] for e in group if _is_event_url(e.get("url", "")) and not _is_aggregator(e["url"])), "")
    if not best:
        best = next((e["url"] for e in group if _is_event_url(e.get("url", ""))), "")
    if not best:
        best = first("url")
    base["url"] = best
    if not base.get("source"):
        base["source"] = first("source")
    base["code"] = code_for(base.get("neighborhood", ""))
    return base

def collapse_duplicates(events: list[dict]) -> list[dict]:
    """Fold entries that are the same real-world event listed by multiple sources."""
    groups: list[list[dict]] = []
    for ev in events:
        for g in groups:
            if _same_event(g[0], ev):
                g.append(ev)
                break
        else:
            groups.append([ev])
    return [_merge_group(g) for g in groups]

def drop_generic_images(events: list[dict]) -> None:
    """Blank images that are site branding / default share graphics rather than a real event photo.
    Two signals: (1) the URL matches a generic logo/default/share pattern; (2) the SAME image is
    reused across events that are genuinely DIFFERENT shows — a site default (e.g. an aggregator's
    own logo, like DoSD's) gets stamped on unrelated events all over town. An image shared only
    among entries for the SAME show (which share a title word — e.g. an exhibition and its
    closing-day reminder, like Mingei's "Clearly Indigenous") is a real photo and is kept. Cleared
    images fall back to the card's colored panel."""
    from collections import defaultdict
    for e in events:
        if _is_generic_image_url(e.get("image", "")):
            e["image"] = ""
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("image"):
            groups[e["image"]].append(e)
    for img, evs in groups.items():
        if len(evs) < 2:
            continue
        common = None
        for e in evs:
            toks = _title_tokens(e.get("title", ""))
            common = toks if common is None else (common & toks)
        if not common:                      # no title word in common -> different shows -> generic
            for e in evs:
                e["image"] = ""

def birthday_payload() -> str:
    """Compact JSON array of the birthday artists, embedded into the home page so the
    'Born on This Day' feature needs no runtime fetch. The per-day choice is made client-side
    (assets/birthday.js) from the visitor's own date, so it changes daily between builds too."""
    try:
        data = json.load(open(os.path.join(ROOT, "data", "birthdays.json")))
        artists = data.get("artists", [])
    except Exception:
        artists = []
    payload = json.dumps(artists, ensure_ascii=False, separators=(",", ":"))
    return payload.replace("<", "\\u003c")  # never let a stray '<' break the <script> block

def render(events: list[dict], horizon: int) -> None:
    events = collapse_duplicates(events)  # fold same-event duplicates from multiple sources
    drop_generic_images(events)           # blank site-logo / default / reused-across-events photos
    picks = sorted([e for e in events if e.get("is_pick")],
                   key=lambda e: (e.get("start_date") or "9999"))[:8]
    stamp = today().strftime("%B %-d, %Y")

    # onview.html — picks grid, day-by-day calendar, "updated" stamp
    p = os.path.join(ROOT, "onview.html"); s = open(p).read()
    stats = onview_stats(events) or "Listings updating &mdash; new shows are being gathered."
    s = replace_between(s, "<!-- AUTO:ONVIEWSTATS:START -->", "<!-- AUTO:ONVIEWSTATS:END -->", stats)
    s = replace_between(s, "<!-- AUTO:CALGRID:START -->", "<!-- AUTO:CALGRID:END -->", calendar_grid(events, 14))
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
    s = re.sub(r"style\.css\?v=\d+", f"style.css?v={STYLE_VERSION}", s)
    open(p, "w").write(s)

    # index.html — date range, hero standout, and highlight cards
    p = os.path.join(ROOT, "index.html"); s = open(p).read()
    s = replace_between(s, "<!-- AUTO:DATERANGE:START -->", "<!-- AUTO:DATERANGE:END -->", daterange_str(horizon))
    hev = hero_event(events)
    hero = hero_inner(events) or (
        '<h3>This week&rsquo;s standout is on its way</h3>'
        '<p class="hero__cat">Meanwhile, see everything on view across San Diego.</p>'
        '<a class="pill" href="onview.html">Browse the calendar</a>')
    s = replace_between(s, "<!-- AUTO:HERO:START -->", "<!-- AUTO:HERO:END -->", hero)
    s = replace_between(s, "<!-- AUTO:HEROIMG:START -->", "<!-- AUTO:HEROIMG:END -->", hero_photo(hev))
    ordered = picks + [e for e in events if not e.get("is_pick")]
    # On View This Week: up to 9 cards (3 rows), no more than two per institution
    home_picks, _seen_venue = [], {}
    for _e in ordered:
        if _e is hev:
            continue
        _v = (_e.get("venue") or "").strip().lower()
        if _seen_venue.get(_v, 0) >= 2:
            continue
        _seen_venue[_v] = _seen_venue.get(_v, 0) + 1
        home_picks.append(_e)
        if len(home_picks) >= 9:
            break
    if home_picks:
        tag, head, sub = opening_header(home_picks)
        highlights = grid(home_picks)
    else:
        tag, head, sub = ("This Week", "What&rsquo;s On",
                          "We&rsquo;re lining up this week&rsquo;s openings and exhibitions.")
        highlights = '<p class="note">New shows are added as venues announce them &mdash; check back soon.</p>'
    s = replace_between(s, "<!-- AUTO:OPENTAG:START -->", "<!-- AUTO:OPENTAG:END -->", tag)
    s = replace_between(s, "<!-- AUTO:OPENHEAD:START -->", "<!-- AUTO:OPENHEAD:END -->", head)
    s = replace_between(s, "<!-- AUTO:OPENSUB:START -->", "<!-- AUTO:OPENSUB:END -->", sub)
    s = replace_between(s, "<!-- AUTO:HIGHLIGHTS:START -->", "<!-- AUTO:HIGHLIGHTS:END -->", highlights)
    s = replace_between(s, "<!-- AUTO:BIRTHDAY:START -->", "<!-- AUTO:BIRTHDAY:END -->", birthday_payload())
    s = re.sub(r"birthday\.js\?v=\d+", f"birthday.js?v={STYLE_VERSION}", s)
    s = re.sub(r"style\.css\?v=\d+", f"style.css?v={STYLE_VERSION}", s)
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

def dump_keys(events: list[dict]) -> None:
    """List current events with their norm_key, so a rating can be added to
    data/editorial.json by copy-paste. Marks shows that already have a rating."""
    ed = load_editorial()
    rows = [e for e in events if isinstance(e, dict) and e.get("title")]
    if not rows:
        print("No events in events.json yet \u2014 run a full scrape first (needs ANTHROPIC_API_KEY),")
        print("then `--keys` will list every show with its key.")
        return
    print(f"{len(rows)} event(s) in events.json.  \u2713 = already rated in editorial.json\n")
    for e in sorted(rows, key=lambda x: ((x.get("venue") or "").lower(), (x.get("title") or "").lower())):
        k = norm_key(e)
        mark = "\u2713" if k in ed else " "
        print(f"  {mark} {k}")
        print(f"      {e.get('title','')}  \u2014  {e.get('venue','')}")
    print("\nTo rate one: add a block keyed by its norm_key to data/editorial.json (see EDITORIAL.md).")

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="skip scraping; re-render existing events.json")
    ap.add_argument("--horizon", type=int, default=HORIZON_DEFAULT, help="window length in days")
    ap.add_argument("--ignore-robots", action="store_true", help="bypass robots.txt for all sources")
    ap.add_argument("--render-js", action="store_true", help="render JS-heavy pages with a headless browser when static text is thin")
    ap.add_argument("--keys", action="store_true", help="list each current event with its norm_key (for editorial.json), then exit")
    ap.add_argument("--debug", metavar="NAME_OR_URL", help="fetch ONE source (by name substring or URL), dump what the scraper extracts + a raw-HTML sample, then exit")
    args = ap.parse_args(argv)
    results: list[dict] = []

    existing = []
    if os.path.exists(DATA):
        try:
            existing = json.load(open(DATA)).get("events", [])
        except (json.JSONDecodeError, ValueError, AttributeError, KeyError, TypeError) as e:
            # corrupt file (e.g. a git merge conflict left markers in it) -> start fresh
            # rather than crashing every run until someone hand-fixes the JSON
            print(f"  [warn] {DATA} is unreadable ({e}); starting from an empty set")
            existing = []
    for e in existing:                        # clean any date baked onto cached titles too
        if isinstance(e, dict) and e.get("title"):
            e["title"] = clean_title(e["title"])

    if args.keys:
        dump_keys(existing)
        return 0

    if args.debug:
        import yaml as _yaml
        try:
            _srcs = _yaml.safe_load(open(SOURCES))["sources"]
        except Exception:
            _srcs = []
        m = next((s for s in _srcs if args.debug.lower() in (s.get("name", "") or "").lower()), None)
        durl = (m["url"] if m else args.debug)
        dfr = bool(m.get("force_render")) if m else False
        print(f"DEBUG  {(m['name'] if m else durl)}\n  url: {durl}  force_render={dfr}\n")
        text, status, image = fetch_text(durl, ignore_robots=True, render=True, force_render=dfr)
        print(f"  fetch status: {status}")
        if text:
            print(f"  extracted text: {len(text)} chars | inline links: {text.count('[http')} | og:image: {image}")
            print("\n  ===== EXTRACTED TEXT (first 1800 chars) =====\n" + text[:1800])
            print("\n  ===== EXTRACTED TEXT (last 700 chars) =====\n" + text[-700:])
        else:
            print("  (no text extracted)")
        try:
            import requests as _rq
            _r = _rq.get(durl, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
            print(f"\n  ===== RAW HTML (first 2500 chars, status {_r.status_code}) =====\n" + _r.text[:2500])
        except Exception as _e:
            print("  raw fetch failed:", _e)
        return 0

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
                try:
                    print(f"- {src['name']}")
                    ig = args.ignore_robots or src.get("ignore_robots", False)
                    rj = args.render_js or src.get("render_js", False)
                    fr = src.get("force_render", False)
                    text, status, image = fetch_text(src["url"], ignore_robots=ig, render=rj, force_render=fr)
                    print(f"  fetched {len(text or '')} chars (status: {status})")
                    n = 0
                    if text:
                        evs = extract_events(text, src["url"], args.horizon, client)
                        n = len(evs)
                        print(f"  extracted {n} candidate event(s)")
                        # Sources that also operate outside the county (e.g. Oolong's New York
                        # space) are marked sd_only — drop any show whose location is out of area.
                        if src.get("sd_only"):
                            kept = [e for e in evs if not _outside_san_diego(e)]
                            if len(kept) != len(evs):
                                print(f"  dropped {len(evs) - len(kept)} out-of-county (non–San Diego) event(s)")
                            evs = kept
                            n = len(evs)
                        # If the model didn't find a per-show image, fall back to the page's
                        # share image — but only on focused pages (1-3 events), so a generic
                        # banner doesn't get stamped on every card of a big listing page.
                        if image and 1 <= n <= 3 and not _is_generic_image_url(image):
                            for e in evs:
                                if not e.get("image"):
                                    e["image"] = image
                        scraped.extend(evs)
                        status = "ok" if n else "no events"
                    results.append({"name": src["name"], "status": status, "events": n})
                except Exception as e:  # one bad source must never kill the whole batch
                    print(f"  [warn] source failed {src.get('name','?')}: {e}")
                    results.append({"name": src.get("name", "?"), "status": "error", "events": 0})
                finally:
                    time.sleep(RATE_LIMIT_SECONDS)
            events = merge(existing, scraped) if scraped else existing

    # RULE: an event with no end date is a single-day event. Anchor end_date to start_date so
    # it shows on that one day across cards/calendar/grid and a past one drops out of the window
    # (rather than lingering forever as an "ongoing" show). Applies to cached and scraped events.
    for e in events:
        if isinstance(e, dict) and e.get("start_date") and not e.get("end_date"):
            e["end_date"] = e["start_date"]

    kept = [e for e in events if in_window(e, args.horizon)]
    kept.sort(key=lambda e: (e.get("start_date") or "9999", e.get("title", "")))

    if not args.no_fetch:
        enrich_images(kept, args.ignore_robots)

    json.dump({"updated": today().isoformat(), "window_days": args.horizon, "events": kept},
              open(DATA, "w"), indent=2, ensure_ascii=False)
    print(f"events.json: {len(kept)} event(s) in window")

    render(kept, args.horizon)
    write_summary(results, len(kept), args.horizon)
    return 0

if __name__ == "__main__":
    sys.exit(main())
