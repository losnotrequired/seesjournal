# Adding a rating to a Calendar listing

Ratings live in `data/editorial.json`. The scraper **never** writes this file, so anything you put here survives every re-scrape — it's merged into the listings only when the site is rendered.

## 1. Find the show's `norm_key`

Each listing has a key built from its title and venue:

    norm_key = <title, letters+digits only, lowercased> + "|" + <first 10 letters/digits of the venue>

Example — a show titled **"Niki de Saint Phalle: Joy Revolution"** at the **Museum of Contemporary Art San Diego**:

    nikidesaintphallejoyrevolution|museumofco

The easy way to get exact keys — run:

    python scripts/update_listings.py --keys

It lists every current show with its key, sorted by venue, and marks the ones you've already rated. (The rule by hand, if you ever need it, is above.)

## 2. Add an entry

In `data/editorial.json`, add a block keyed by that norm_key:

```json
"nikidesaintphallejoyrevolution|museumofco": {
  "rating": 5,
  "why": "One of the strongest exhibitions in Southern California this month. Gallery 3 is essential.",
  "who": "Anyone serious about contemporary art",
  "time": "45-60 min",
  "price": "Paid",
  "first_timer": true,
  "hidden_gem": false
}
```

Every field is optional — include only what you want shown.

| field | meaning |
|---|---|
| `rating` | 1–5, shown as stars |
| `why` | ≤40 words, why it matters |
| `who` | who should go |
| `time` | how long to budget |
| `price` | `Free` or `Paid` (overrides auto-detect) |
| `first_timer` | `true` for first-visit friendly |
| `hidden_gem` | `true` to flag a hidden gem |
| `status` | optional override: `Opening` / `On View` / `Last Chance` |

## 3. What the scraper fills on its own

Even with no entry, a listing automatically shows a **Last chance** badge when it closes within 10 days, and **Free** when the listing text says so. Your entry adds the judgment on top — the stars, the case, who it's for.

## 4. Commit

Commit `data/editorial.json` and push. The rating appears the next time the site renders. Keys starting with `_` (like `_README`) are ignored — they're just notes.
