#!/usr/bin/env python3
"""Sees Journal — Press & Writings aggregator.

Auto-search pipeline: ask Claude (with the web_search tool) to find recent essays,
reviews, criticism, and press about San Diego-area visual art, validate that each
link actually resolves, dedupe against what we already have, then render press.html
grouped by Year -> Month (newest first).

Usage:
  python scripts/update_writings.py              # search + validate + render
  python scripts/update_writings.py --no-search  # re-render from existing data only
  python scripts/update_writings.py --no-validate # add finds without HTTP-checking them
  python scripts/update_writings.py --months 6    # search window (default 12)

Without ANTHROPIC_API_KEY set, the search step is skipped and the page is just
re-rendered from data/writings.json (so it never crashes in CI without a key).
"""
import os
import re
import json
import argparse
import datetime as dt
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "writings.json")
PAGE = os.path.join(ROOT, "press.html")

MODEL = "claude-sonnet-4-6"           # web search benefits from a capable model
SEARCH_MONTHS = 12
MAX_SEARCHES = 12
USER_AGENT = "SeesJournal-writings-bot/1.0 (+https://seesjournal.com)"
REQUEST_TIMEOUT = 20

TYPES = {"Review", "Profile", "News", "Essay", "Preview", "Interview", "Feature", "Institutional"}
MONTHS = ["", "January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

SEARCH_PROMPT = """You are a research assistant for an independent San Diego art publication. Using web search, find RECENT essays, reviews, criticism, artist profiles, and substantive press coverage about SAN DIEGO-area visual art: exhibitions, galleries, museums, artists, and art events (including the San Diego-Tijuana border art scene).

Focus on pieces published in the last {months} months (since {since}). Search across local outlets (San Diego Union-Tribune, KPBS, Voice of San Diego, San Diego Magazine, La Jolla Light, Times of San Diego), regional/national art press (Hyperallergic, Artforum, ARTnews / Art in America, Brooklyn Rail, KCET / PBS SoCal Artbound, Los Angeles Times), and institutional/museum press (MCASD, San Diego Museum of Art, Mingei, Oceanside Museum of Art, ICA San Diego, Bread & Salt).

Return ONLY a JSON array (no prose, no markdown fences). Each element:
{{
  "title": "exact article headline",
  "author": "author name or empty string",
  "outlet": "publication name",
  "date": "YYYY-MM-DD publication date (use YYYY-MM if only the month is known)",
  "url": "direct deep link to the specific article (never a homepage or section index)",
  "summary": "one or two factual sentences on what the piece covers",
  "venue": "San Diego venue/neighborhood/artist it concerns, or empty string",
  "type": "one of: Review, Profile, News, Essay, Preview, Interview, Feature, Institutional"
}}

Rules:
- Only include REAL articles you actually found via search, each with a specific, working URL to the article itself. If you are unsure a URL is exact, omit that item.
- Never output an outlet homepage or a generic section/listing/calendar page as the URL.
- Only San Diego-area visual art. Ignore music/theater/film unless the piece is primarily about visual art.
- Prefer criticism, reviews, essays, and profiles over pure event listings.
- Aim for 10-25 distinct, verifiable items. If you find nothing suitable, return [].
"""


def today() -> dt.date:
    iso = os.environ.get("REFERENCE_DATE")
    if iso:
        try:
            return dt.date.fromisoformat(iso)
        except ValueError:
            pass
    return dt.date.today()


def load() -> dict:
    try:
        with open(DATA) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"writings": []}


def norm_url(u: str) -> str:
    u = (u or "").strip().rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", u).lower()


def is_deep_url(u: str) -> bool:
    if not u:
        return False
    p = urlparse(u)
    return p.scheme in ("http", "https") and bool(p.path.strip("/"))


def validate(url: str) -> bool:
    """Return True if the URL resolves to something real. We treat auth/forbidden
    (401/403/405) as 'exists' since some outlets block bots but the page is live;
    only 404/410 and server errors are treated as dead."""
    import requests
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT},
                          timeout=REQUEST_TIMEOUT, allow_redirects=True, stream=True)
        code = r.status_code
        r.close()
        return code not in (404, 410) and code < 500
    except Exception:
        return False


def search_new(client, months: int) -> list:
    since = (today() - dt.timedelta(days=int(months * 31))).isoformat()
    prompt = SEARCH_PROMPT.format(months=months, since=since)
    try:
        msg = client.messages.create(
            model=MODEL, max_tokens=4096,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        print(f"  [warn] web search request failed: {e}")
        return []
    text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        print("  [warn] no JSON array in model output")
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        print("  [warn] could not parse JSON from model output")
        return []
    return items if isinstance(items, list) else []


def clean_entry(it) -> dict | None:
    if not isinstance(it, dict):
        return None
    title = (it.get("title") or "").strip()
    url = (it.get("url") or "").strip()
    if not title or not is_deep_url(url):
        return None
    typ = (it.get("type") or "").strip().title()
    if typ not in TYPES:
        typ = "Feature"
    date = (it.get("date") or "").strip()
    if not re.match(r"^\d{4}(-\d{2}(-\d{2})?)?$", date):
        date = ""
    return {
        "title": title,
        "author": (it.get("author") or "").strip(),
        "outlet": (it.get("outlet") or "").strip(),
        "date": date,
        "url": url,
        "summary": (it.get("summary") or "").strip(),
        "venue": (it.get("venue") or "").strip(),
        "type": typ,
    }


def merge(existing: list, found: list, do_validate: bool = True) -> int:
    seen = {norm_url(e["url"]) for e in existing}
    added = 0
    for it in found:
        e = clean_entry(it)
        if not e:
            continue
        k = norm_url(e["url"])
        if k in seen:
            continue
        if do_validate and not validate(e["url"]):
            print(f"  [skip] dead/unreachable: {e['url']}")
            continue
        e["added"] = today().isoformat()
        existing.append(e)
        seen.add(k)
        added += 1
        print(f"  [add] {e['outlet']}: {e['title'][:60]}")
    return added


# ---------- rendering ----------
def esc(s) -> str:
    if not s:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_date(d: str) -> str:
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            return dt.date.fromisoformat(d).strftime("%b %-d, %Y")
        if re.match(r"^\d{4}-\d{2}$", d):
            y, m = d.split("-")
            return f"{MONTHS[int(m)]} {y}"
        if re.match(r"^\d{4}$", d):
            return d
    except Exception:
        pass
    return ""


def month_key(d: str) -> str:
    if re.match(r"^\d{4}-\d{2}", d):
        return d[:7]
    if re.match(r"^\d{4}$", d):
        return d + "-00"
    return ""


def item_html(e: dict) -> str:
    typ = e.get("type", "Feature")
    bits = [f'<span class="wtag wtag--{typ.lower()}">{esc(typ)}</span>']
    if e.get("outlet"):
        bits.append(f'<span class="wlist__outlet">{esc(e["outlet"])}</span>')
    fd = fmt_date(e.get("date", ""))
    if fd:
        bits.append(f'<time>{esc(fd)}</time>')
    meta = ' <span class="dotsep">&middot;</span> '.join(bits)
    venue = f' <span class="wlist__venue">&mdash; {esc(e["venue"])}</span>' if e.get("venue") else ""
    by = f'<p class="wlist__by">by {esc(e["author"])}</p>' if e.get("author") else ""
    url = esc(e.get("url", "#"))
    return (f'<article class="wlist__item">'
            f'<div class="wlist__meta">{meta}</div>'
            f'<h3 class="wlist__title"><a href="{url}" target="_blank" rel="noopener">{esc(e.get("title",""))}</a></h3>'
            f'<p class="wlist__sum">{esc(e.get("summary",""))}{venue}</p>{by}</article>')


def render(entries: list) -> str:
    groups: dict[str, list] = {}
    undated = []
    for e in entries:
        mk = month_key(e.get("date", ""))
        (groups.setdefault(mk, []) if mk else undated).append(e)
    out = []
    for mk in sorted((k for k in groups if k), reverse=True):
        items = sorted(groups[mk], key=lambda e: e.get("date", ""), reverse=True)
        y, m = mk.split("-")
        label = f"{MONTHS[int(m)]} {y}" if m != "00" else y
        out.append(f'<section class="wmonth"><h2>{label}</h2>'
                   + "".join(item_html(e) for e in items) + '</section>')
    if undated:
        us = sorted(undated, key=lambda e: e.get("outlet", ""))
        out.append('<section class="wmonth"><h2>Ongoing &amp; Foundational</h2>'
                   + "".join(item_html(e) for e in us) + '</section>')
    return "\n".join(out) if out else '<p class="note">No writings yet.</p>'


def replace_between(text: str, start: str, end: str, inner: str) -> str:
    i = text.find(start)
    j = text.find(end)
    if i == -1 or j == -1:
        return text
    return text[:i + len(start)] + "\n" + inner + "\n" + text[j:]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-search", action="store_true", help="re-render only, no web search")
    ap.add_argument("--no-validate", action="store_true", help="skip URL validation of new finds")
    ap.add_argument("--months", type=int, default=SEARCH_MONTHS, help="search window in months")
    args = ap.parse_args()

    data = load()
    entries = data.get("writings", [])
    key = os.environ.get("ANTHROPIC_API_KEY")

    if not args.no_search and key:
        try:
            from anthropic import Anthropic
            client = Anthropic()
            found = search_new(client, args.months)
            print(f"  search returned {len(found)} candidate(s)")
            n = merge(entries, found, do_validate=not args.no_validate)
            print(f"  added {n} new writing(s)")
        except Exception as e:
            print(f"  [warn] search step skipped: {e}")
    elif not args.no_search:
        print("  [info] no ANTHROPIC_API_KEY set — re-rendering existing writings only")

    data["writings"] = entries
    with open(DATA, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    s = open(PAGE).read()
    s = replace_between(s, "<!-- AUTO:WRITINGS:START -->", "<!-- AUTO:WRITINGS:END -->", render(entries))
    stamp = today().strftime("%B %-d, %Y")
    s = replace_between(s, "<!-- AUTO:WUPDATED:START -->", "<!-- AUTO:WUPDATED:END -->",
                        f"Updated {stamp} &middot; {len(entries)} pieces")
    open(PAGE, "w").write(s)
    print(f"  rendered press.html — {len(entries)} total pieces")


if __name__ == "__main__":
    main()
