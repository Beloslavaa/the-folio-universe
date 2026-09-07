"""
Precompute per-cover embeddings for the covers in public/covers.json:
a CNN feature vector (subject/composition) and a color-histogram vector
(palette). Feeds the "similar covers" highlight in index.html, which blends
both. Run via `npm run embed` (or directly: `python3 scripts/embed.py`) after
scrape.mjs has produced/updated public/covers.json. The GitHub Action runs
this daily, so it only needs to embed whatever covers are new since the
last run.

Design notes:
- Incremental per vector kind: a cover already holding a "cnn" or "color"
  entry keeps it as-is; only whichever is missing gets (re)computed. Slugs
  no longer in covers.json are dropped. This lets the color histogram be
  added to an archive that already has cached CNN vectors without re-running
  TensorFlow over the whole set.
- Uses image_thumb (the 600px CDN size index.html already loads) rather than
  image_full, since both the CNN (224x224) and the histogram (64x64) resize
  down anyway.
- CNN: include_top=False drops the classification layer; pooling='avg'
  collapses the feature maps into one 1280-number vector per cover.
- Color: an HSV histogram (12 hue x 4 saturation x 4 value bins = 192 dims)
  captures the palette's *distribution*, not just its average — a
  half-red/half-green cover stays distinct from a uniformly muted one, which
  a single mean-color vector couldn't tell apart.
- Both vectors are L2-normalized so index.html can use a plain dot product
  as cosine similarity for either one.
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

DOCS_DIR = Path(__file__).resolve().parent.parent
COVERS_JSON = DOCS_DIR / "public" / "covers.json"
EMBEDDINGS_JSON = DOCS_DIR / "public" / "embeddings.json"
CNN_MODEL_NAME = "mobilenetv2_1280"
COLOR_MODEL_NAME = "hsv_histogram_12x4x4"
COLOR_BINS = (12, 4, 4)


def fetch_image(url):
    req = urllib.request.Request(url, headers={"User-Agent": "folio-embed-script/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return Image.open(BytesIO(resp.read())).convert("RGB")


def embed_cnn(covers_by_slug):
    if not covers_by_slug:
        return {}

    # Imported lazily so failures unrelated to TensorFlow (e.g. missing
    # covers.json) don't pay its import cost first.
    from tensorflow.keras.applications import MobileNetV2
    from tensorflow.keras.applications.mobilenet_v2 import preprocess_input

    model = MobileNetV2(weights="imagenet", include_top=False, pooling="avg")

    def embed_one(url):
        img = fetch_image(url).resize((224, 224))
        x = np.asarray(img, dtype=np.float32)
        x = preprocess_input(x)
        x = np.expand_dims(x, axis=0)
        vec = model.predict(x, verbose=0)[0]
        return vec / np.linalg.norm(vec)

    out = {}
    for slug, url in covers_by_slug.items():
        try:
            out[slug] = embed_one(url).tolist()
            print(f"cnn: embedded {slug}")
        except Exception as err:  # one broken image shouldn't kill the run
            print(f"  ! cnn failed {slug}: {err}", file=sys.stderr)
    return out


def color_histogram(img):
    hsv = np.asarray(img.convert("HSV").resize((64, 64)), dtype=np.float32)
    h, s, v = hsv[..., 0].ravel(), hsv[..., 1].ravel(), hsv[..., 2].ravel()
    hist, _ = np.histogramdd((h, s, v), bins=COLOR_BINS, range=((0, 256),) * 3)
    hist = hist.ravel()
    total = hist.sum()
    if total > 0:
        hist = hist / total
    norm = np.linalg.norm(hist)
    return (hist / norm if norm > 0 else hist).tolist()


def embed_colors(covers_by_slug):
    out = {}
    for slug, url in covers_by_slug.items():
        try:
            out[slug] = color_histogram(fetch_image(url))
            print(f"color: embedded {slug}")
        except Exception as err:  # one broken image shouldn't kill the run
            print(f"  ! color failed {slug}: {err}", file=sys.stderr)
    return out


def main():
    if not COVERS_JSON.exists():
        raise SystemExit(f"{COVERS_JSON} not found — run the scraper first.")

    covers = json.loads(COVERS_JSON.read_text())["covers"]
    covers_by_slug = {c["slug"]: c["image_thumb"] for c in covers if c.get("image_thumb")}

    raw_existing = {}
    if EMBEDDINGS_JSON.exists():
        raw_existing = json.loads(EMBEDDINGS_JSON.read_text()).get("embeddings", {})

    # Keep entries for covers that still exist; drop the rest. Older files
    # store a bare CNN vector per slug instead of {"cnn": ..., "color": ...} —
    # normalize those on the fly.
    existing = {
        slug: (entry if isinstance(entry, dict) else {"cnn": entry})
        for slug, entry in raw_existing.items()
        if slug in covers_by_slug
    }

    need_cnn = {s: u for s, u in covers_by_slug.items() if "cnn" not in existing.get(s, {})}
    need_color = {s: u for s, u in covers_by_slug.items() if "color" not in existing.get(s, {})}

    print(f"{len(covers_by_slug) - len(need_cnn)} cnn cached, {len(need_cnn)} to embed.")
    print(f"{len(covers_by_slug) - len(need_color)} color cached, {len(need_color)} to embed.")

    new_cnn = embed_cnn(need_cnn)
    new_color = embed_colors(need_color)

    embeddings = {}
    for slug in covers_by_slug:
        entry = dict(existing.get(slug, {}))
        if slug in new_cnn:
            entry["cnn"] = new_cnn[slug]
        if slug in new_color:
            entry["color"] = new_color[slug]
        embeddings[slug] = entry

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cnn_model": CNN_MODEL_NAME,
        "color_model": COLOR_MODEL_NAME,
        "count": len(embeddings),
        "embeddings": embeddings,
    }
    EMBEDDINGS_JSON.write_text(json.dumps(payload) + "\n")
    print(f"\nWrote {len(embeddings)} embeddings to {EMBEDDINGS_JSON}")


if __name__ == "__main__":
    main()
