# On View — San Diego Art Guide

A multi-page static website listing San Diego art shows, openings, and art walks
(June 6–16, 2026). Structure modeled on *Sees Journal* — a light home, an events
listing split into sections, an event-detail template, a Standouts blog, plus
About, Contact, and Privacy — sharing a header (account + hamburger menu +
centered logo) and footer (logo + mission + About/Contact + Privacy).

Populated with original content and its own "view / san diego" branding
(not Sees Journal's logo, mission text, or articles).

No build step. No framework. Static files — ready for Netlify.

## Structure

```
san-diego-art-onview/
├── index.html        # Home — standout feature + this week's openings
├── onview.html       # On View — Editor's Picks, Also On View, day-by-day, ongoing, tips
├── event.html        # Event-detail template (Cat Gunn @ ICA Central)
├── standouts.html    # Standouts — short featured writeups (blog style)
├── about.html        # About the guide
├── contact.html      # Contact + newsletter (Netlify Forms)
├── thanks.html       # Form success page
├── privacy.html      # Privacy policy
├── 404.html          # Styled not-found page (Netlify serves automatically)
├── favicon.svg
├── netlify.toml      # publish dir + headers, no build command
├── assets/
│   ├── style.css     # shared styles for every page
│   ├── main.js       # menu toggle, back-to-top, share buttons
│   └── images/       # drop exhibition photos here (optional)
└── README.md
```

## Deploy to Netlify — pick one

### 1. Drag & drop (fastest)
1. Go to https://app.netlify.com/drop
2. Drag this whole folder onto the page → live URL instantly. Drag again to update.

### 2. Git-based (auto-deploys on push)
1. Push this folder to a GitHub/GitLab/Bitbucket repo.
2. Netlify → **Add new site → Import an existing project** → pick the repo.
3. Build command: *(empty)* · Publish directory: `.`
4. **Deploy.**

### 3. Netlify CLI
```bash
npm install -g netlify-cli
netlify login
netlify deploy          # preview
netlify deploy --prod   # production
```

## Contact form (Netlify Forms)

The contact and newsletter forms use **Netlify Forms** — they work automatically
once deployed to Netlify (no backend needed). Submissions appear under
**Forms** in your Netlify site dashboard. To get email notifications, set them up
in **Site settings → Forms → Form notifications**. Forms only work on the
deployed Netlify site, not when opening the file locally.

Replace the placeholder address `hello@yourdomain.com` in `contact.html` with
your real email before launch.

## Adding real exhibition images

Cards and the event hero use colored panels as placeholders. To use a photo:
1. Put it in `assets/images/` (e.g. `iturbide.jpg`).
2. In the page, set the panel background, e.g.:
   ```html
   <div class="card__panel is-blue"
        style="background-image:url('assets/images/iturbide.jpg');background-size:cover;background-position:center">
   ```
Use images you have the rights to display.

## Notes
- Internal links are relative, so you can also open `index.html` locally to preview
  (forms excepted). On Netlify everything resolves from the site root.
- Fonts load from Google Fonts CDN; self-host the families (Lora, Caveat,
  Quicksand) into `assets/` if you'd prefer no external requests.
- Attach a custom domain in Netlify under **Domain settings**.

---

## Automated updates (every 3 days)

The site can refresh itself on a schedule. A GitHub Actions job reads a list of
art sources, uses Claude to extract current exhibitions/events from each page,
writes `data/events.json`, and re-renders the marked regions of `index.html`
and `onview.html`. Because your repo is the Netlify source, the bot's commit
triggers a Netlify rebuild automatically — no extra infrastructure.

### What's added
```
scripts/
├── sources.yaml          # the sites to read (edit freely)
├── update_listings.py    # fetch → extract (Claude) → events.json → re-render
└── requirements.txt
data/events.json          # generated, committed (the data behind the pages)
.github/workflows/update-listings.yml   # the schedule
```
The pages contain HTML markers (`<!-- AUTO:PICKS:START -->` … `END`, plus
HIGHLIGHTS, CALENDAR, UPDATED). The script only rewrites text *between* markers,
so the design and all other content stay put. If a run finds no events for a
region (e.g. nothing dated in the window yet), it leaves that region unchanged.

### One-time setup
1. Put this project in a **GitHub repo** and connect it to Netlify (publish dir `.`).
2. Create an Anthropic API key at console.anthropic.com.
3. In the repo: **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `ANTHROPIC_API_KEY`  ·  Value: your key.
4. Open the **Actions** tab and enable workflows if prompted.
5. (Optional) Trigger a first run now: Actions → "Update listings" → **Run workflow**.

### The schedule
`cron: "0 9 */3 * *"` runs at 09:00 UTC on days 1, 4, 7, … of each month —
the standard "every ~3 days" approximation (cron can't express a true rolling
72-hour gap, so the interval resets at month boundaries). Adjust the cron line
to taste. Two GitHub realities to know: scheduled runs can be delayed when
Actions is busy, and GitHub **pauses schedules after ~60 days with no repo
activity** — the bot's own commits count as activity, so an active site stays live.

### Run it yourself
```bash
pip install -r scripts/requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python scripts/update_listings.py                 # full run
python scripts/update_listings.py --no-fetch       # re-render from existing data
python scripts/update_listings.py --horizon 14     # widen the window to 14 days
```

### Editing what it watches
Add or remove entries in `scripts/sources.yaml`. Prefer a venue's official
**iCal/RSS feed or API** when it has one — more reliable and clearly permitted
(the script has a place to plug those in). A one-off broad research sweep is
still the best way to *discover* brand-new venues; fold anything new into
`sources.yaml` so the recurring job keeps watching it.

### Responsible scraping
The script identifies itself with a descriptive User-Agent, **checks each site's
`robots.txt` and skips disallowed pages**, rate-limits requests, and truncates
page text. You are responsible for complying with each site's Terms of Use —
remove any source that doesn't permit automated access.

### Recommended: review before publishing
LLM extraction is good but not perfect (a wrong date or a missed show happens).
For a human glance before anything goes live, have the job open a **pull request**
instead of pushing to `main`. Replace the commit step with the
`peter-evans/create-pull-request` action, or point the schedule at a `draft`
branch and set Netlify to deploy `main` only. The site keeps its
"confirm with the venue" note either way, and every event in `events.json`
carries its `source` URL and a `confidence` flag.

### Cost
A run makes one small Claude (Haiku) call per source — a few cents every 3 days
at this source-list size.
