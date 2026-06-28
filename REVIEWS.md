# Writing a review

Reviews live as Markdown files in `data/reviews/`. Each file is one review: a small front-matter block of metadata and scores, then the review itself in Markdown. Run `python gen_reviews.py` to (re)build `reviews.html` and the per-review pages in `reviews/`.

## 1. Create the file

Copy `data/reviews/_template.md` to a new file named for the show — the **filename becomes the URL**:

    data/reviews/okonkwo-tidal-register.md   ->   reviews/okonkwo-tidal-register.html

Files whose name starts with `_` are never built (so `_template.md` stays private).

## 2. Front-matter

The block between the two `---` lines:

```
title: A Tide That Refuses to Settle      # the review's headline
exhibition: Tidal Register                 # the show
artist: Maya Okonkwo                        # optional
venue: Harbor Annex
neighborhood: Barrio Logan                  # optional
dates: June 6 – August 30, 2026             # optional, free text
author: R. Alvarez
date: 2026-06-22                            # YYYY-MM-DD; sorts the index, newest first
dek: One sentence that sets up the review.
originality: 5                              # the five axes, each 0–5
installation: 4
concept: 5
historical_importance: 3
emotional_impact: 5
image:                                      # optional hero image URL
status: published                           # only `published` reviews are built
```

The five scores average into the **Sees Score** shown on the page and the index. Keep the rubric consistent across reviews — that consistency is what makes the score mean something.

## 3. The body

Plain Markdown:

- blank line between paragraphs
- `## Subhead` for a section break
- `> a line` for a pull quote
- `**bold**`, `*italic*`, `[link text](https://…)`

## 4. Publish

Set `status: published`, run `python gen_reviews.py`, then commit `reviews.html`, the new file in `reviews/`, and your source in `data/reviews/`. Push.

> The file `data/reviews/sample-tidal-register.md` is a sample reviewing a fictional show, included so the section isn't empty. Delete it before launch.
