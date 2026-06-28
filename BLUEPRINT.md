# Sees Journal — Editorial Blueprint

**Thesis.** Sees is not a calendar. It is the intelligence layer for San Diego art — the place the question *"what's actually worth seeing this week?"* resolves to. The long game is the **Art Atlas of San Diego**: one city's entire art ecosystem — every exhibition, review, artwork, opening, artist, curator, residency, lecture, studio visit — searchable, at a genuine editorial level. No one has built that for a single city.

---

## The spine: six sections

Everything organizes under six sections. Most already exist on the site in seed form.

| Section | What it is | Where it stands today |
|---|---|---|
| **Today** | What to see *today* — a tight, opinionated front door | Evolve from the current Home |
| **Calendar** | The definitive art calendar, but *editorial* (see ratings below) | Evolve from On View — the scraper already feeds it |
| **Standouts** | One exceptional work from every show → a growing archive | Expand the existing Standouts concept |
| **Reviews** | Rigorous criticism, 10–15/month — not previews | New section |
| **Atlas** | Searchable encyclopedia of the ecosystem + filterable map | Evolve from Places — the venue directory + map is the seed |
| **Essays** | Long-form writing + interviews (studio visits, curators) | Evolve from the writings pipeline |

About / Contact / Press move to a secondary footer menu — they're not part of the editorial product.

---

## The editorial layer (what turns listings into intelligence)

This is the heart of idea #1. Every Calendar listing carries a structured editorial block:

```
sees_rating        ★ 1–5
why_it_matters     ≤ 40 words
who_should_go      short phrase
time_required      e.g. "30–45 min"
price              Free | Paid
first_time_friendly  true/false
hidden_gem         true/false
status             Opening | On View | Last Chance
```

And Reviews score each exhibition on a fixed rubric (idea #8), which readers learn to trust:

```
originality · installation · concept · historical_importance · emotional_impact   (each 1–5) → composite
```

**The one principle that protects all of it:** the rating, the *why it matters*, and the reviews must be **human editorial judgment**. The entire value proposition — the reason a reader trusts Sees over a calendar — is that a person with taste stood behind the call. Auto-generating stars would quietly destroy that. So the split is:

- **Automatable (I build):** the factual fields — dates, price, status (opening/last-chance), a time-required estimate, neighborhood, medium — pre-filled by the scraper so the editor starts from a populated draft.
- **Editorial (you):** the rating, the 40-word case, who-should-go, hidden-gem flag, and every review.

---

## The twelve ideas → mapped and phased

| # | Idea | Today | Build phase |
|---|---|---|---|
| 1 | Editorial calendar (rating block) | listings exist, no editorial fields | **1** |
| 2 | Weekly Top Five (Thursdays) | — | **1** (module) |
| 3 | Map with layers + filters | Places map exists | **1** venues / **2** event medium-filters |
| 4 | Standouts → "100 Great Works" archive | one-off concept | **1** data model / **2** archive |
| 5 | Emerging artists (studio/grad/collector/curator weekly) | — | **2** (feeds Essays) |
| 6 | "One Day in San Diego" routes + add-to-calendar | — | **2** (route builder + .ics) |
| 7 | Reviews, 10–15/month | — | **1** scaffold / ongoing content |
| 8 | Score every exhibition (rubric) | — | **1** schema, tied to Reviews |
| 9 | Encyclopedia (venue/artist/curator pages) | venue data exists in Atlas | **1** venues / **2** artists + curators |
| 10 | Annual Sees Awards | — | **3** |
| 11 | Community "My Seen List" | — | **3** (needs saved state) |
| 12 | Beautiful data (timelines, closing-soon) | events.json exists | **2** |

---

## Build order

**Phase 1 — structure + schema (the bones).**
Adopt the six-section navigation. Reframe Places → **Atlas** with a neighborhood filter and a page per venue. Add the editorial rating block to the Calendar data model + listing UI (scraper pre-fills the factual fields; editor fills the judgment). Stand up **Reviews** and **Essays** sections. Add a **Top Five** module to Today. Give Standouts a real archive data model.

**Phase 2 — depth.**
Event map filters by medium (Photography / Sculpture / Painting / Performance / Installation / Experimental / Free). "Closing soon" + opening/talk/public-art timelines from events.json. The "One Day" route builder with one-click Google Calendar export. Emerging-artist cadence in Essays. Standouts grows toward the 100-work archive. Encyclopedia pages for artists and curators.

**Phase 3 — community + prestige.**
"My Seen List" (visited / wishlist / favorites). The annual Sees Awards.

---

## What I need from you vs. what I just build

- **I build, unattended:** all structure, schemas, the Atlas + map filters, venue/artist/curator page templates, the timelines and data viz, the route builder + .ics export, the saved-list mechanics, and the scraper changes that pre-fill factual fields.
- **Only you can supply:** the ratings, the 40-word cases, the reviews and criticism, the studio visits, the weekly Top Five picks, the award choices. That judgment *is* the product.

**Proposed first move (Phase 1):** the six-section restructure + Atlas (it's closest to done) + the rating block wired into the Calendar listings.
