/**
 * thefolio archive scraper
 * ---------------------------------------------------------------
 * Scrapes the Cargo-hosted archive at thefolioarchivo.com, extracts
 * each cover's metadata + image URL, and writes public/covers.json
 * for the 3D universe visualization to consume.
 *
 * Design notes:
 * - Cargo sites embed their full page state (every page's content and
 *   media library) as `window.__PRELOADED_STATE__` in the server-rendered
 *   homepage HTML. That means a single plain `fetch()` of the homepage
 *   gets us every cover's real data — no headless browser required, and
 *   no risk of missing lazy-rendered images.
 * - The actual front-cover photo is NOT among a cover page's own gallery
 *   images — that gallery holds interior/editorial spreads. The real
 *   cover lives on the archive grid pages (recent-archive-index and
 *   vintage-archive-index), each tagged with its own dedicated
 *   <media-item hash="..." href="<slug>"> distinct from the detail page's
 *   media. buildCoverImageMap() reads those two grid pages to get the
 *   authoritative cover image per slug.
 * - One page (i-d_jolene-1) isn't linked from either grid, so it has no
 *   authoritative cover image; we fall back to the first image in its own
 *   gallery for that case only, and warn about it.
 * - Parsing is isolated in parseState() / parsePage() so a change to
 *   Cargo's markup only touches those functions.
 * - Defensive throughout: one broken page must not kill the whole run.
 * - Idempotent: re-running on unchanged content yields identical JSON
 *   (covers are sorted by slug), so git only commits real changes.
 */

import { writeFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';

const BASE = 'https://thefolioarchivo.com';
const OUTPUT = 'public/covers.json';

// Cargo's image CDN. We normalise every image URL to a known width so the
// viz gets predictable, optimised files. Cargo resizes on the fly via /w/<n>/.
const THUMB_WIDTH = 600;
const FULL_WIDTH = 1600;

// Utility pages that live in the same page list as covers but aren't ones
// (nav bars, footers, index overlays, the profile/about pages, etc).
const SKIP_PURLS = new Set([
  'about-us-header',
  'archivo-texto',
  'main-footer-—-desktop',
  'main-—-recent-archive-1',
  'main-—-vintage-archive',
  'profile',
  'profile-1',
  'propiedad-int',
  'recent-archive-index',
  'recent-archive-nav-—-desktop',
  'texts-header',
  'texts-index',
  'vertical-borders',
  'vintage-archive-index',
  'vintage-archive-nav-—-desktop',
]);

// The two grid pages that hold each cover's real front-cover thumbnail.
const GRID_PURLS = ['recent-archive-index', 'vintage-archive-index'];

/** Build a Cargo freight image URL at a given width. */
function imageUrl(hash, name, width) {
  return `https://freight.cargo.site/w/${width}/i/${hash}/${encodeURIComponent(name)}`;
}

/** Decode HTML entities Cargo emits in `content` (numeric + the common named ones). */
function decodeEntities(str) {
  return str
    .replace(/&#x([0-9a-fA-F]+);/g, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, dec) => String.fromCodePoint(parseInt(dec, 10)))
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'");
}

/** Strip tags, turning <br> into newlines, then decode entities. */
function toText(html) {
  return decodeEntities(html.replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, ''));
}

/** Split on every line break — for short single-line fields (titles, meta). */
function toLines(html) {
  return toText(html)
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
}

/** Split on blank lines only — for body copy, so a lone <br> stays mid-paragraph. */
function toParagraphs(html) {
  return toText(html)
    .split(/\n[ \t ]*\n+/)
    .map((p) => p.trim())
    .filter(Boolean);
}

/** Pull the raw inner HTML of `<column-unit slot="N">` from a column-set's HTML. */
function columnUnit(setHtml, slot) {
  const m = setHtml.match(new RegExp(`<column-unit\\b[^>]*\\bslot="${slot}"[^>]*>([\\s\\S]*?)</column-unit>`));
  return m ? m[1] : '';
}

/**
 * Cargo lays out every cover page as two <column-set> rows:
 *   1. title (slot 0) + a "(Close)" link (slot 1)
 *   2. a short meta column (slot 0: subtitle + issue number) and the
 *      body copy (slot 1), followed by the page's own image gallery.
 * Reading title/sub/meta/body out of their own column keeps them from
 * bleeding into each other (unlike a flat, whole-page text dump).
 */
function parseContent(contentHtml) {
  const sets = [...contentHtml.matchAll(/<column-set\b[^>]*>([\s\S]*?)<\/column-set>/g)].map((m) => m[1]);
  const title = toLines(columnUnit(sets[0] || '', '0'))[0] || null;
  const metaLines = toLines(columnUnit(sets[1] || '', '0'));
  const body = toParagraphs(columnUnit(sets[1] || '', '1'));
  return { title, sub: metaLines[0] || '', meta: metaLines.slice(1).join(' · '), body };
}

/**
 * Pull window.__PRELOADED_STATE__ out of the homepage HTML.
 * This one object contains every page's content + media library.
 */
function parseState(html) {
  const match = html.match(/window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*<\/script>/s);
  if (!match) throw new Error('Could not find __PRELOADED_STATE__ in homepage HTML');
  return JSON.parse(match[1]);
}

/**
 * Build a slug -> media map of the real front-cover image, read from the
 * archive grid pages. Each grid's content has one
 * <media-item hash="..." href="<slug>"> per cover, and its own `media`
 * array resolves that hash to the actual file.
 */
function buildCoverImageMap(pages) {
  const map = new Map();
  for (const purl of GRID_PURLS) {
    const grid = Object.values(pages).find((p) => p.purl === purl);
    if (!grid) continue;
    const mediaByHash = new Map((grid.media || []).map((m) => [m.hash, m]));
    for (const tag of grid.content.matchAll(/<media-item\b[^>]*>/g)) {
      const hash = tag[0].match(/\bhash="([^"]+)"/)?.[1];
      const href = tag[0].match(/\bhref="([^"]+)"/)?.[1];
      if (hash && href && mediaByHash.has(hash)) {
        map.set(href, mediaByHash.get(hash));
      }
    }
  }
  return map;
}

/** First image in a cover page's own gallery — used only as a fallback. */
function firstGalleryImage(page) {
  const mediaByHash = new Map((page.media || []).map((m) => [m.hash, m]));
  for (const tag of (page.content || '').matchAll(/<media-item\b[^>]*>/g)) {
    const hash = tag[0].match(/\bhash="([^"]+)"/)?.[1];
    if (hash && mediaByHash.has(hash)) return mediaByHash.get(hash);
  }
  return null;
}

/**
 * Turn one Cargo page record into a cover entry.
 */
function parsePage(purl, page, coverImageMap) {
  const { title, sub, meta, body } = parseContent(page.content || '');

  let chosen = coverImageMap.get(purl);
  if (!chosen) {
    console.warn(`  (not listed in an archive grid — falling back to first gallery image)`);
    chosen = firstGalleryImage(page);
  }

  return {
    slug: purl,
    url: `${BASE}/${purl}`,
    title: title || page.title || null,
    sub,
    meta,
    body,
    image_full: chosen ? imageUrl(chosen.hash, chosen.name, FULL_WIDTH) : null,
    image_thumb: chosen ? imageUrl(chosen.hash, chosen.name, THUMB_WIDTH) : null,
    image_source: chosen ? imageUrl(chosen.hash, chosen.name, chosen.width || FULL_WIDTH) : null,
  };
}

async function main() {
  console.log(`Fetching ${BASE} ...`);
  const res = await fetch(BASE, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (compatible; folio-archive-scraper/1.0; +https://thefolioarchivo.com)',
    },
  });
  if (!res.ok) throw new Error(`Fetch failed: ${res.status} ${res.statusText}`);
  const html = await res.text();

  const state = parseState(html);
  const pages = state?.pages?.byId || {};
  const entries = Object.values(pages).filter(
    (p) => p.page_type === 'page' && !SKIP_PURLS.has(p.purl) && (p.media || []).length > 0
  );
  console.log(`Found ${entries.length} cover pages.`);

  const coverImageMap = buildCoverImageMap(pages);

  const covers = [];
  for (const page of entries) {
    console.log(`- ${page.purl}`);
    try {
      const detail = parsePage(page.purl, page, coverImageMap);
      if (!detail.image_source) console.warn(`  (no image found for ${page.purl})`);
      covers.push(detail);
    } catch (err) {
      console.warn(`  ! failed ${page.purl}: ${err.message}`);
    }
  }

  // Deterministic order so unchanged runs produce byte-identical JSON.
  covers.sort((a, b) => a.slug.localeCompare(b.slug));

  const payload = {
    generated_at: new Date().toISOString(),
    source: BASE,
    count: covers.length,
    covers,
  };

  await mkdir(dirname(OUTPUT), { recursive: true });
  await writeFile(OUTPUT, JSON.stringify(payload, null, 2) + '\n');
  console.log(`\nWrote ${covers.length} covers to ${OUTPUT}`);

  const missing = covers.filter((c) => !c.image_source).length;
  if (missing) console.log(`  (${missing} without an image — check those pages)`);
}

main().catch((err) => {
  console.error('Fatal:', err);
  process.exit(1);
});
