"""
Precompute CNN embeddings for the covers in public/covers.json.

Feeds the "similar covers" highlight in index.html. Run via `npm run embed`
(or directly: `python3 scripts/embed.py`) after scrape.mjs has produced/updated
public/covers.json. The GitHub Action runs this daily, so it only needs to
embed whatever covers are new since the last run.

Design notes:
- Incremental: embeddings for slugs already present in public/embeddings.json
  are kept as-is (not recomputed), and slugs no longer in covers.json are
  dropped. Only genuinely new covers get embedded.
- Uses image_thumb (the 600px CDN size index.html already loads) rather than
  image_full, since MobileNetV2 resizes to 224x224 anyway.
- include_top=False drops the classification layer; pooling='avg' collapses
  the feature maps into one 1280-number vector per cover.
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
MODEL_NAME = "mobilenetv2_1280"


def fetch_image(url):
    req = urllib.request.Request(url, headers={"User-Agent": "folio-embed-script/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return Image.open(BytesIO(resp.read())).convert("RGB")


def embed_all(covers_by_slug):
    # Imported lazily so `--help`-less failures (e.g. missing covers.json)
    # don't pay TensorFlow's import cost first.
    from tensorflow.keras.applications import MobileNetV2
    from tensorflow.keras.applications.mobilenet_v2 import preprocess_input

    model = MobileNetV2(weights="imagenet", include_top=False, pooling="avg")

    def embed_one(slug, url):
        img = fetch_image(url).resize((224, 224))
        x = np.asarray(img, dtype=np.float32)
        x = preprocess_input(x)
        x = np.expand_dims(x, axis=0)
        vec = model.predict(x, verbose=0)[0]
        return vec / np.linalg.norm(vec)

    out = {}
    for slug, url in covers_by_slug.items():
        try:
            out[slug] = embed_one(slug, url).tolist()
            print(f"embedded {slug}")
        except Exception as err:  # one broken image shouldn't kill the run
            print(f"  ! failed {slug}: {err}", file=sys.stderr)
    return out


def main():
    if not COVERS_JSON.exists():
        raise SystemExit(f"{COVERS_JSON} not found — run the scraper first.")

    covers = json.loads(COVERS_JSON.read_text())["covers"]
    covers_by_slug = {c["slug"]: c["image_thumb"] for c in covers if c.get("image_thumb")}

    existing = {}
    if EMBEDDINGS_JSON.exists():
        existing = json.loads(EMBEDDINGS_JSON.read_text()).get("embeddings", {})

    # Keep embeddings for covers that still exist; drop the rest.
    kept = {slug: vec for slug, vec in existing.items() if slug in covers_by_slug}
    to_embed = {slug: url for slug, url in covers_by_slug.items() if slug not in kept}

    print(f"{len(kept)} cached, {len(to_embed)} new cover(s) to embed.")
    new = embed_all(to_embed) if to_embed else {}

    embeddings = {**kept, **new}
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL_NAME,
        "count": len(embeddings),
        "embeddings": embeddings,
    }
    EMBEDDINGS_JSON.write_text(json.dumps(payload) + "\n")
    print(f"\nWrote {len(embeddings)} embeddings to {EMBEDDINGS_JSON}")


if __name__ == "__main__":
    main()
