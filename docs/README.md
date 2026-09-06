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
