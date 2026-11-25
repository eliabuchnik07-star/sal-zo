"""Look up product photos on Open Food Facts by barcode and cache them in site/data/images.json.

Each run checks up to LIMIT barcodes that were never checked (or were missing for 30+ days),
politely (Open Food Facts allows about 100 product reads a minute).
"""
import json, os, sys, time, glob, datetime as dt
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "site", "data")
CACHE = os.path.join(DATA, "images.json")
LIMIT = int(os.environ.get("IMAGE_LIMIT", "1200"))
API = "https://world.openfoodfacts.org/api/v2/product/{}.json?fields=image_front_small_url"
UA = {"User-Agent": "sal-zo basket comparison (github.com/eliabuchnik07-star/sal-zo)"}
PREFIX = "https://images.openfoodfacts.org/images/products/"


def main():
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    # cache values: "path/after/prefix" for found images, or "-YYYY-MM-DD" when not found
    count = {}
    for f in glob.glob(os.path.join(DATA, "city", "*.json")):
        for p in json.load(open(f, encoding="utf-8"))["products"]:
            if len(p[0]) >= 12:  # real barcodes only, not store-internal codes
                count[p[0]] = count.get(p[0], 0) + 1
    today = dt.date.today()

    def due(code):
        v = cache.get(code)
        if v is None:
            return True
        if v.startswith("-"):
            return (today - dt.date.fromisoformat(v[1:])).days > 30
        return False

    todo = sorted((c for c in count if due(c)), key=lambda c: -count[c])[:LIMIT]
    print(f"{len(count)} barcodes, {len(todo)} to look up", file=sys.stderr)
    for i, code in enumerate(todo):
        try:
            with urllib.request.urlopen(urllib.request.Request(API.format(code), headers=UA), timeout=20) as r:
                url = (json.load(r).get("product") or {}).get("image_front_small_url") or ""
        except Exception:
            url = ""
        cache[code] = url[len(PREFIX):] if url.startswith(PREFIX) else "-" + today.isoformat()
        time.sleep(0.65)
        if i % 200 == 199:
            json.dump(cache, open(CACHE, "w", encoding="utf-8"), separators=(",", ":"))
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"images known: {sum(1 for v in cache.values() if not v.startswith('-'))}", file=sys.stderr)


if __name__ == "__main__":
    main()
