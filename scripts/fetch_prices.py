"""Download supermarket price files (Israeli price transparency law) and build the site's data.

Run: python3 scripts/fetch_prices.py
Writes site/data/index.json (cities list) and site/data/city/<n>.json (all products and
branch prices for one city). Supported chains: Shufersal. Others are added as new fetchers.
"""
import gzip, json, os, re, sys, time, html, datetime as dt
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "site", "data")
UA = {"User-Agent": "Mozilla/5.0 (basket-compare)"}

# Israeli CBS locality codes used in the chains' store files.
CITY = {
    "5000": "תל אביב-יפו", "3000": "ירושלים", "4000": "חיפה", "9000": "באר שבע", "8300": "ראשון לציון",
    "7900": "פתח תקווה", "70": "אשדוד", "7400": "נתניה", "6100": "בני ברק", "6600": "חולון",
    "8400": "רחובות", "6400": "הרצליה", "8600": "רמת גן", "6200": "בת ים", "8700": "רעננה",
    "6900": "כפר סבא", "7100": "אשקלון", "6500": "חדרה", "2610": "בית שמש", "1200": "מודיעין-מכבים-רעות",
    "2630": "קרית גת", "2640": "ראש העין", "7000": "לוד", "8500": "רמלה", "2600": "אילת",
    "9700": "הוד השרון", "6300": "גבעתיים", "2530": "באר יעקב", "1304": "שוהם", "2650": "רמת השרון",
    "1247": "חריש", "7700": "עפולה", "7800": "פרדס חנה-כרכור", "6800": "קרית אתא", "1139": "כרמיאל",
    "8200": "קרית מוצקין", "9100": "נהריה", "8000": "צפת", "9400": "יהוד-מונוסון", "2660": "יבנה",
    "2800": "קרית שמונה", "1263": "כפר ורדים",
}


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def text(data):
    try:
        data = gzip.decompress(data)
    except OSError:
        pass
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", "replace")


def parse_items(xml):
    items = {}
    for it in ET.fromstring(xml).iter("Item"):
        code = (it.findtext("ItemCode") or "").strip().lstrip("0")
        try:
            price = round(float(it.findtext("ItemPrice") or 0), 2)
        except ValueError:
            continue
        if not code or price <= 0:
            continue
        q, u = (it.findtext("Quantity") or "").strip(), (it.findtext("UnitQty") or "").strip()
        items[code] = (
            price,
            (it.findtext("ItemName") or "").strip(),
            (it.findtext("ManufactureName") or it.findtext("ManufacturerName") or "").strip(),
            fmt_unit(q, u),
            (it.findtext("LastSaleDateTime") or "").strip()[:10],
        )
    return items


def fmt_unit(qty, unit):
    try:
        q = float(qty)
        q = int(q) if q == int(q) else q
    except ValueError:
        return unit
    if unit in ("יחידה", "יח'", "לא ידוע") and q in (0, 1):
        return ""
    return f"{q} {unit}".strip() if q else unit


# ---------- Shufersal ----------
SHUF = "https://prices.shufersal.co.il/FileObject/UpdateCategory?catID={cat}&storeId=0&page={page}"


def shufersal_links(cat, pattern):
    links, page = [], 1
    while page < 300:
        h = get(SHUF.format(cat=cat, page=page)).decode("utf-8", "replace")
        found = [html.unescape(u) for u in re.findall(r'href="(https://pricesprodpublic[^"]+)"', h) if pattern in u]
        if not found:
            break
        links += found
        page += 1
    return links


def shufersal():
    root = ET.fromstring(text(get(shufersal_links(5, "Stores")[0])))
    stores = {}
    for sub in root.iter("SubChain"):
        subname = (sub.findtext("SubChainName") or "").strip()
        if "BE" in subname.upper():  # Shufersal BE is a pharmacy chain
            continue
        for s in sub.iter("Store"):
            city = (s.findtext("City") or "").strip()
            if city not in CITY:
                continue
            sid = str(int(s.findtext("StoreID")))
            stores[sid] = {"id": "shufersal-" + sid, "chain": "שופרסל", "sub": subname,
                           "name": (s.findtext("StoreName") or "").strip(),
                           "address": (s.findtext("Address") or "").strip(), "city": CITY[city]}
    # newest PriceFull file per store
    latest = {}
    for u in shufersal_links(2, "PriceFull"):
        m = re.search(r"PriceFull\d+-\d+-(\d+)-(\d+)", u)
        sid, stamp = str(int(m.group(1))), m.group(2)
        if sid in stores and (sid not in latest or stamp > latest[sid][0]):
            latest[sid] = (stamp, u)
    print(f"shufersal: {len(stores)} stores in known cities, {len(latest)} price files", file=sys.stderr)

    def one(sid):
        try:
            return sid, parse_items(text(get(latest[sid][1])))
        except Exception as e:
            print("failed store", sid, e, file=sys.stderr)
            return sid, None

    with ThreadPoolExecutor(8) as ex:
        return [(stores[sid], items) for sid, items in ex.map(one, latest) if items]


FETCHERS = [shufersal]


def write_city(n, city, rows, today):
    """rows: list of (store, items). Products: only barcodes that exist in this city."""
    codes = {}
    for _, items in rows:
        for c, v in items.items():
            if c not in codes:
                codes[c] = v
    code_list = sorted(codes)
    idx = {c: i for i, c in enumerate(code_list)}
    products = [[c, codes[c][1], codes[c][2], codes[c][3]] for c in code_list]
    stores, prices = [], []
    for s, items in rows:
        stores.append({k: s[k] for k in ("id", "chain", "sub", "name", "address")})
        row = []
        for c, v in items.items():
            try:
                days = (today - dt.date.fromisoformat(v[4])).days
            except ValueError:
                days = -1
            row.append([idx[c], v[0], days])
        prices.append(row)
    with open(os.path.join(DATA, "city", f"{n}.json"), "w", encoding="utf-8") as f:
        json.dump({"city": city, "products": products, "stores": stores, "prices": prices},
                  f, ensure_ascii=False, separators=(",", ":"))
    return len(products)


def main():
    os.makedirs(os.path.join(DATA, "city"), exist_ok=True)
    rows = []
    for fetch in FETCHERS:
        try:
            rows += fetch()
        except Exception as e:  # one broken chain must not stop the daily update
            print("chain failed:", fetch.__name__, e, file=sys.stderr)
    if not rows:
        sys.exit("no data fetched")
    by_city = {}
    for s, items in rows:
        by_city.setdefault(s["city"], []).append((s, items))
    today = dt.date.today()
    cities = []
    for n, city in enumerate(sorted(by_city)):
        count = write_city(n, city, by_city[city], today)
        cities.append({"name": city, "file": f"city/{n}.json", "stores": len(by_city[city]), "products": count})
    with open(os.path.join(DATA, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "cities": cities},
                  f, ensure_ascii=False, indent=1)
    print(f"wrote {len(cities)} cities from {len(rows)} stores", file=sys.stderr)


if __name__ == "__main__":
    main()
