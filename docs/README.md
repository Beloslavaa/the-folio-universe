# folio archive scraper

Scrapes [thefolioarchivo.com](https://thefolioarchivo.com) (a Cargo-hosted
site) into a single `public/covers.json`, and serves `index.html` — a Three.js
"floating universe" of the covers — that reads it. A GitHub Action re-runs the
scrape daily so new covers flow through automatically. The same Action also
computes CNN embeddings for any new covers (`public/embeddings.json`), which
`index.html` uses to highlight similar covers when one is focused.

## Pipeline

```
Cargo site  ->  GitHub Action (daily)  ->  scrape.mjs  ->  public/covers.json  ->  index.html fetch()
                                        ->  embed.py    ->  public/embeddings.json ->  index.html fetch()
```

1. You add a cover to the site in Cargo, as normal.
2. The scheduled Action fetches the homepage and reads Cargo's embedded
   `window.__PRELOADED_STATE__` JSON (every page's content and media library,
   already rendered server-side — no headless browser needed).
3. It writes `public/covers.json` and commits it back to the repo **only if
   something changed**.
4. `index.html` `fetch()`es that JSON on load and builds the scene — nothing
   is baked into the page, so a new Action run updates the universe on the
   next page load.

The Cargo site stays the single source of truth — no separate database or
sheet to keep in sync.

## How the scraper reads Cargo's data

Cargo embeds the *entire* site's page content and media library as one JSON
blob (`window.__PRELOADED_STATE__`) in the server-rendered homepage HTML —
so a single `fetch()` of `/` is enough to get every cover's data.

Two things aren't obvious from that JSON, and both are handled in
`scripts/scrape.mjs`:

- **The real front-cover photo isn't in a cover page's own gallery.** A
  cover's detail page holds interior/editorial spread images. The actual
  cover photo is tagged separately on the two archive grid pages
  (`recent-archive-index`, `vintage-archive-index`), each with its own
  `<media-item hash="..." href="<slug>">` pointing at the real cover file.
  `buildCoverImageMap()` reads those two grids to resolve it. One page
  (`i-d_jolene-1`) isn't linked from either grid, so it falls back to the
  first image in its own gallery — logged as a warning when this happens.
- **Title/subtitle/issue/body text live in specific HTML columns**, not as
  one flat block — `parseContent()` reads each column separately
  (`<column-set>` / `<column-unit slot="N">`) so, e.g., a subtitle doesn't
  bleed into the body copy.

## Output shape

```json
{
  "generated_at": "2026-09-02T06:00:00.000Z",
  "source": "https://thefolioarchivo.com",
  "count": 29,
  "covers": [
    {
      "slug": "kingkong_2022",
      "url": "https://thefolioarchivo.com/kingkong_2022",
      "title": "King Kong (2022)",
      "sub": "Issue 14",
      "meta": "",
      "body": ["KING KONG es una revista independiente...", "..."],
      "image_full":   "https://freight.cargo.site/w/1600/i/<hash>/file.jpg",
      "image_thumb":  "https://freight.cargo.site/w/600/i/<hash>/file.jpg",
      "image_source": "https://freight.cargo.site/w/<native-width>/i/<hash>/file.jpg"
    }
  ]
}
```

- `image_thumb` — what `index.html` uses for the floating sprites (fast load).
- `image_full` — a higher-res version, for whenever a cover is zoomed/focused.
- `image_source` — the same file at its native width.
- `sub` / `meta` — short metadata lines (subtitle, issue number/date). `meta`
  is empty when a cover only has one metadata line.
- `body` — the editorial copy, as paragraphs.

`public/embeddings.json` (produced by `scripts/embed.py`) looks like:

```json
{
  "generated_at": "2026-09-03T06:00:00.000Z",
  "model": "mobilenetv2_1280",
  "count": 29,
  "embeddings": {
    "kingkong_2022": [0.0123, -0.0456, "... 1280 floats, L2-normalized"]
  }
}
```

Each vector is a normalized MobileNetV2 (`include_top=False, pooling='avg'`)
feature vector of a cover's `image_thumb`, so cosine similarity between two
covers is just their dot product. `index.html` uses this to highlight the
most similar covers when one is focused. The file is optional — if it's
missing, that highlighting is silently skipped.

Regeneration is incremental: `embed.py` keeps embeddings for slugs already in
the file and only computes new ones for slugs it hasn't seen, so a daily run
with a couple of new covers doesn't recompute the whole archive.

## Local run

```bash
npm run scrape
```

No install step — the scraper is plain Node (18+) `fetch`, no dependencies.

To (re)compute embeddings locally:

```bash
pip install -r requirements.txt
npm run embed
```

This needs `public/covers.json` to already exist (run the scraper first) and
downloads each cover's `image_thumb` from Cargo's CDN, so it needs network
access. TensorFlow's first import is slow; subsequent covers are fast.

To preview `index.html` locally (it needs `public/covers.json` served over
HTTP, not opened as a `file://` URL):

```bash
npx http-server .   # or: python3 -m http.server
```

## GitHub setup

The workflow lives at the repo root (`.github/workflows/scrape.yml`, one
level up from this folder). This folder is named `docs/` (rather than
`folio-scraper/`) specifically so GitHub Pages can serve it directly —
Pages only serves from the repo root or a `/docs` folder.

1. Push `the-folio-universe` to GitHub.
2. The workflow already has `permissions: contents: write`, so it can commit
   the JSON. No secrets needed (the site is public).
3. Go to the **Actions** tab and run **Scrape covers** manually once to verify.
4. After that it runs daily at 06:00 UTC. Change the `cron` in
   `.github/workflows/scrape.yml` to adjust.

## Serving the universe

In the repo's **Settings → Pages**, set the source to the `main` branch,
`/docs` folder. GitHub then serves this folder at
`https://<user>.github.io/<repo>/` — `index.html` fetches
`./public/covers.json` relative to itself, so no URLs need hardcoding.

## Notes & maintenance

- No headless browser, no dependencies — just `fetch` + regex/JSON parsing.
  Parsing is isolated in `parseState()` / `parsePage()` / `parseContent()` so
  a change to Cargo's markup only touches those functions.
- The scraper is defensive: one broken cover page logs a warning and is
  skipped rather than failing the whole run.
- Output is sorted by slug so unchanged content produces identical JSON and
  git stays quiet.
- If Cargo changes their page markup, `parseContent()` and
  `buildCoverImageMap()` in `scripts/scrape.mjs` are the first place to look.
