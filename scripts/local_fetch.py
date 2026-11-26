"""Runs on a computer in Israel, for chains whose price sites refuse servers abroad.

Every run: downloads the newest full price file of every branch of these chains
(Hatzi Hinam, Carrefour, and the laibcatalog chains: Victory, Machsanei HaShuk, H. Cohen), and uploads
one compact file per chain to the `local-data` branch of the repository. The hourly GitHub job
(fetch_prices.py) reads those files and merges them into the site.

Setup (Windows): see scripts/install_windows.ps1. Needs a GitHub token that can write
to this repository only, saved in the environment variable SAL_ZO_TOKEN.
"""
import base64, gzip, html, json, os, re, sys, datetime as dt
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp

REPO = "eliabuchnik07-star/sal-zo"
BRANCH = "local-data"
PART = 40  # stores per uploaded file


# ---------- Hatzi Hinam ----------
def hazi_hinam():
    base = "https://shop.hazi-hinam.co.il/Prices"
    links = []
    for page in range(1, 60):
        h = fp.get(f"{base}?p={page}").decode("utf-8", "replace")
        found = [html.unescape(u) for u in re.findall(r'href="(https://[^"]+\.(?:gz|xml))"', h)]
        new = [u for u in found if u not in links]
        if not new:
            break
        links += new
        # stop once we have reached files older than 2 days
        dates = re.findall(r"-(\d{8})-\d{6}", " ".join(new))
        if dates and min(dates) < (dt.date.today() - dt.timedelta(days=2)).strftime("%Y%m%d"):
            break
    stores_files = sorted((u for u in links if "/Stores" in u), key=lambda u: re.findall(r"\d{8}", u)[-1])
    if not stores_files:
        raise RuntimeError("no stores file found on Hatzi Hinam site")
    stores = fp.parse_stores(fp.text(fp.get(stores_files[-1])), "hazihinam", "חצי חינם")
    latest = {}
    for u in links:
        name = u.rsplit("/", 1)[-1]
        if not name.startswith("PriceFull"):
            continue
        sid, stamp = fp.file_store_and_stamp(name)
        if sid in stores and (sid not in latest or stamp > latest[sid][0]):
            latest[sid] = (stamp, u)
    print(f"hazi hinam: {len(stores)} stores, {len(latest)} price files", file=sys.stderr)
    return [(stores[sid], fp.parse_items(fp.text(fp.get(u)))) for sid, (_, u) in latest.items()]


# ---------- Carrefour (and Yeinot Bitan) ----------
def carrefour():
    base = "https://prices.carrefour.co.il"
    h = fp.get(base + "/").decode("utf-8", "replace")
    path = re.search(r"const path = '(\d+)'", h).group(1)
    files = [f["name"] for f in json.loads(re.search(r"const files = (\[.*?\]);", h, re.S).group(1))]
    stores_files = sorted(f for f in files if f.startswith("Stores"))
    if not stores_files:
        raise RuntimeError("no stores file on Carrefour site")
    stores = fp.parse_stores(fp.text(fp.get(f"{base}/{path}/{stores_files[-1]}")), "carrefour", "קרפור")
    latest = {}
    for name in files:
        if not name.startswith("PriceFull"):
            continue
        sid, stamp = fp.file_store_and_stamp(name)
        if sid in stores and (sid not in latest or stamp > latest[sid][0]):
            latest[sid] = (stamp, name)
    print(f"carrefour: {len(stores)} stores, {len(latest)} price files", file=sys.stderr)
    return [(stores[sid], fp.parse_items(fp.text(fp.get(f"{base}/{path}/{n}")))) for sid, (_, n) in latest.items()]


# ---------- laibcatalog.co.il (Victory, Machsanei HaShuk, H. Cohen) ----------
def laib(chain_id, key, chain_name):
    """laibcatalog.co.il/<chain>/index.html reads a JSON list of files from /webapi/api/getfiles."""
    base = "https://laibcatalog.co.il/webapi/"
    files = json.loads(fp.get(f"{base}api/getfiles?edi={chain_id}").decode("utf-8-sig"))
    st = sorted((f for f in files if f["fileType"] == "stores"), key=lambda f: f["fileDate"])
    if not st:
        raise RuntimeError(f"{key}: no files published on laibcatalog right now")
    stores = fp.parse_stores(fp.text(fp.get(f"{base}{chain_id}/{st[-1]['fileName']}")), key, chain_name)
    latest = {}
    for f in files:
        sid = str(int(f["branchNumber"]))
        if f["fileType"] == "pricefull" and sid in stores and (sid not in latest or f["fileDate"] > latest[sid]["fileDate"]):
            latest[sid] = f
    print(f"{key}: {len(stores)} stores, {len(latest)} price files", file=sys.stderr)
    return [(stores[sid], fp.parse_items(fp.text(fp.get(f"{base}{chain_id}/{f['fileName']}")))) for sid, f in latest.items()]


CHAINS = {"hazihinam": hazi_hinam, "carrefour": carrefour,
          "victory": lambda: laib("7290696200003", "victory", "ויקטורי"),
          "mahsaneihashuk": lambda: laib("7290661400001", "mahsaneihashuk", "מחסני השוק"),
          "hcohen": lambda: laib("7290455000004", "hcohen", "ח. כהן")}


def upload(path, data, token):
    api = f"https://api.github.com/repos/{REPO}/contents/{path}"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", **fp.UA}
    sha = None
    try:
        with urllib.request.urlopen(urllib.request.Request(api + f"?ref={BRANCH}", headers=hdr)) as r:
            sha = json.load(r)["sha"]
    except Exception:
        pass
    body = {"message": f"local data {path}", "branch": BRANCH, "content": base64.b64encode(data).decode()}
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(api, json.dumps(body).encode(), headers=hdr, method="PUT")
    urllib.request.urlopen(req, timeout=300).read()


def main():
    token = os.environ.get("SAL_ZO_TOKEN")
    if not token:
        sys.exit("missing SAL_ZO_TOKEN")
    for key, fetch in CHAINS.items():
        try:
            rows = fetch()
        except Exception as e:
            print("failed", key, e, file=sys.stderr)
            continue
        # shared product table + per-store prices, split into parts of PART stores (keeps each upload small)
        products, srows = {}, []
        for st, items in rows:
            r = []
            for c, v in items.items():
                products.setdefault(c, list(v[1:4]))
                r.append([c, v[0], v[4]])
            srows.append([st, r])
        parts = [srows[i:i + PART] for i in range(0, len(srows), PART)] or [[]]
        fetched = dt.datetime.now().isoformat(timespec="minutes")
        for n, chunk in enumerate(parts):
            used = {c for _, r in chunk for c, _, _ in r}
            payload = {"v": 2, "fetched": fetched, "part": n, "parts": len(parts),
                       "products": {c: products[c] for c in used}, "rows": chunk}
            name = f"{key}.json.gz" if n == 0 else f"{key}.{n}.json.gz"
            upload(name, gzip.compress(json.dumps(payload, ensure_ascii=False).encode()), token)
        print("uploaded", key, len(rows), "stores", file=sys.stderr)
    # ask GitHub to rebuild the site now (GitHub's own hourly timer is unreliable)
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/dispatches",
                                     json.dumps({"event_type": "local-prices"}).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", **fp.UA})
        urllib.request.urlopen(req, timeout=60).read()
        print("asked GitHub to update the site", file=sys.stderr)
    except Exception as e:
        print("could not trigger site update:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
