"""Look up product photos by barcode on the free Open Food Facts family of databases and cache
them in site/data/images.json.

Sources (all free to reuse, CC BY-SA licence): Open Food Facts (food), Open Beauty Facts
(cosmetics, toiletries), Open Products Facts (cleaning, household) and Open Pet Food Facts.

For each barcode we try the code as written and its 12/13 digit twin (a leading 0 added or
removed), and take the best photo the product has: the front photo in Hebrew, then a front
photo in any language, then any other photo of the product that was uploaded.

Cache values:
  "path/after/images/products/"        photo on Open Food Facts
  "b:path" / "p:path" / "t:path"        photo on Open Beauty / Products / Pet Food Facts
  "-YYYY-MM-DD"                         nothing found on that day (checked again after RECHECK days)

Each run checks up to LIMIT barcodes, never-checked and most common first, asking the four
databases side by side, politely (under 100 reads a minute per database). Set IMAGE_LIMIT and IMAGE_MINUTES to change how much one run does.
"""
import json, os, sys, time, glob, datetime as dt
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "site", "data")
CACHE = os.path.join(DATA, "images.json")
LIMIT = int(os.environ.get("IMAGE_LIMIT", "6000"))
MINUTES = float(os.environ.get("IMAGE_MINUTES", "45"))
RECHECK = int(os.environ.get("IMAGE_RECHECK_DAYS", "14"))
UA = {"User-Agent": "sal-zo basket comparison (github.com/eliabuchnik07-star/sal-zo)"}
FIELDS = "code,images,image_front_url,image_url,lang"
# (cache prefix, API host): checked in this order, the next one only when the product is not found
SOURCES = [("", "world.openfoodfacts.org"), ("b:", "world.openbeautyfacts.org"),
           ("p:", "world.openproductsfacts.org"), ("t:", "world.openpetfoodfacts.org")]
PATH_MARK = "/images/products/"
SIZE = "400"


def folder(code):
    """Image folder for a barcode, as Open Food Facts builds it."""
    c = code.zfill(13) if len(code) <= 13 else code
    return f"{c[0:3]}/{c[3:6]}/{c[6:9]}/{c[9:]}" if len(c) == 13 else c


def variants(code):
    out = [code]
    if len(code) == 12:
        out.append("0" + code)
    elif len(code) == 13 and code.startswith("0"):
        out.append(code[1:])
    return out


def pick(product, code):
    """Best photo path (after /images/products/) for a product, or ''."""
    imgs = product.get("images") or {}
    base = folder(product.get("code") or code)

    def sel(key):
        v = imgs.get(key)
        if isinstance(v, dict) and v.get("rev"):
            return f"{base}/{key}.{v['rev']}.{SIZE}.jpg"
        return ""

    lang = product.get("lang") or ""
    for key in ["front_he", f"front_{lang}" if lang else ""] + sorted(k for k in imgs if k.startswith("front_")):
        if key and sel(key):
            return sel(key)
    for url in (product.get("image_front_url"), product.get("image_url")):
        if url and PATH_MARK in url:
            return url.split(PATH_MARK, 1)[1].replace(".full.jpg", f".{SIZE}.jpg")
    raw = sorted((int(k) for k in imgs if k.isdigit()))
    if raw:  # the first photo uploaded is usually the front of the package
        return f"{base}/{raw[0]}.{SIZE}.jpg"
    for key in sorted(imgs):  # any other selected photo (e.g. ingredients) beats nothing
        if sel(key):
            return sel(key)
    return ""


def ask(host, code):
    """(status, product) from one database; status None when the database could not be reached."""
    url = f"https://{host}/api/v2/product/{code}.json?fields={FIELDS}"
    for attempt in range(2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                j = json.load(r)
            return j.get("status"), j.get("product")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 0, None
            time.sleep(3)
        except Exception:
            time.sleep(3)
    return None, None


def lookup(code, stats, pool):
    """Ask all databases at once (each has its own rate limit), keep the first one in SOURCES order."""
    for c in variants(code):
        answers = list(pool.map(lambda src: ask(src[1], c), SOURCES))
        for (prefix, host), (status, product) in zip(SOURCES, answers):
            if status is None:
                stats["errors"] += 1
            elif status == 1 and product:
                path = pick(product, c)
                if path:
                    stats[host] = stats.get(host, 0) + 1
                    return prefix + path
    return ""


# Nightly full exports of each database (tab separated, one product per line). Reading them is far
# faster than asking product by product: one download covers every barcode on the site at once.
EXPORTS = [("", "https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz"),
           ("b:", "https://static.openbeautyfacts.org/data/en.openbeautyfacts.org.products.csv"),
           ("p:", "https://static.openproductsfacts.org/data/en.openproductsfacts.org.products.csv"),
           ("t:", "https://static.openpetfoodfacts.org/data/en.openpetfoodfacts.org.products.csv")]


def from_exports(wanted, cache):
    """Fill cache from the full exports for every wanted barcode (the first database that has the
    product wins). Returns the barcodes found in any export, or None if an export could not be read."""
    import csv, gzip, io
    csv.field_size_limit(1 << 30)
    alias = {}
    for c in wanted:
        for v in variants(c):
            alias.setdefault(v, c)
    seen, stats = set(), {}
    for prefix, url in EXPORTS:
        n = 0
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120)
            raw = gzip.GzipFile(fileobj=r) if url.endswith(".gz") else r
            rows = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline=""),
                              delimiter="\t", quoting=csv.QUOTE_NONE)
            head = next(rows)
            ci, ii, si = head.index("code"), head.index("image_url"), head.index("image_small_url")
            for row in rows:
                if len(row) <= max(ci, ii, si):
                    continue
                code = alias.get(row[ci])
                if not code or code in seen:
                    continue
                seen.add(code)
                url_ = row[ii] or row[si]
                if PATH_MARK in url_:
                    cache[code] = prefix + url_.split(PATH_MARK, 1)[1].replace(".200.jpg", f".{SIZE}.jpg")
                    n += 1
        except Exception as e:
            print(f"could not read {url}: {e}", file=sys.stderr)
            return None
        stats[url] = n
    print(f"from full exports: {stats}", file=sys.stderr)
    return seen


def main():
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    count = {}
    for f in glob.glob(os.path.join(DATA, "city", "*.json")):
        for p in json.load(open(f, encoding="utf-8"))["products"]:
            code = str(p[0])
            if len(code) >= 12 and code.isdigit():  # real barcodes only, not store-internal codes
                count[code] = count.get(code, 0) + 1
    today = dt.date.today()

    def due(code):
        v = cache.get(code)
        if v is None:
            return True
        if v.startswith("-"):
            try:
                return (today - dt.date.fromisoformat(v[1:])).days > RECHECK
            except ValueError:
                return True
        return v.endswith(".200.jpg")  # older entries: look again for the bigger, better photo

    found = sum(1 for c in count if c in cache and not cache[c].startswith("-"))
    print(f"{len(count)} barcodes on the site, {found} with a photo ({100 * found / max(1, len(count)):.1f}%)",
          file=sys.stderr)
    if os.environ.get("IMAGE_EXPORTS", "1") == "1":
        seen = from_exports(set(count), cache)
        if seen is not None:  # a barcode missing from every export is not in any database: no need to ask one by one
            for c in count:
                if c not in seen and (cache.get(c) is None or cache[c].startswith("-")):
                    cache[c] = "-" + today.isoformat()
    todo = sorted((c for c in count if due(c)), key=lambda c: (cache.get(c) is not None, -count[c]))[:LIMIT]
    print(f"{len(todo)} to look up now", file=sys.stderr)
    stats = {"errors": 0}
    end = time.time() + MINUTES * 60
    pool = ThreadPoolExecutor(len(SOURCES))
    for i, code in enumerate(todo):
        t0 = time.time()
        if t0 > end:
            print(f"time is up after {i} lookups", file=sys.stderr)
            break
        old = cache.get(code, "")
        path = lookup(code, stats, pool)
        time.sleep(max(0, 0.65 - (time.time() - t0)))  # under 100 reads a minute per database
        if path:
            cache[code] = path
        elif not old or old.startswith("-"):
            cache[code] = "-" + today.isoformat()
        if i % 200 == 199:
            json.dump(cache, open(CACHE, "w", encoding="utf-8"), separators=(",", ":"))
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), separators=(",", ":"))
    found = sum(1 for c in count if c in cache and not cache[c].startswith("-"))
    print(f"now {found} of {len(count)} barcodes have a photo ({100 * found / max(1, len(count)):.1f}%); "
          f"new by source: {stats}", file=sys.stderr)


if __name__ == "__main__":
    main()
