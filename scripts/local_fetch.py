"""Runs on a computer in Israel, for chains whose price sites refuse servers abroad.

Every run: downloads the newest full price file of every branch of these chains, and uploads
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


CHAINS = {"hazihinam": hazi_hinam}


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
        payload = {"fetched": dt.datetime.now().isoformat(timespec="minutes"),
                   "rows": [[s, {c: list(v) for c, v in items.items()}] for s, items in rows]}
        upload(f"{key}.json.gz", gzip.compress(json.dumps(payload, ensure_ascii=False).encode()), token)
        print("uploaded", key, len(rows), "stores", file=sys.stderr)


if __name__ == "__main__":
    main()
