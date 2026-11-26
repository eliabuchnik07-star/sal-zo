"""Download supermarket price files (Israeli price transparency law) and build the site's data.

Run: python3 scripts/fetch_prices.py
Writes site/data/index.json (cities list) and site/data/city/<n>.json (all products and
branch prices for one city). Supported chains: Shufersal. Others are added as new fetchers.
"""
import gzip, json, os, re, sys, time, html, datetime as dt
import urllib.request, urllib.parse, http.cookiejar
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
    "3616": "מעלה אדומים", "1015": "מבשרת ציון", "6700": "טבריה", "2500": "נשר", "9300": "זכרון יעקב",
    "195": "קדימה-צורן", "246": "נתיבות", "3570": "אריאל", "31": "אופקים", "1031": "שדרות", "2400": "אור יהודה",
    "240": "יקנעם עילית", "28": "מזכרת בתיה", "666": "עומר", "1063": "מעלות-תרשיחא", "4100": "קצרין",
    "1161": "רהט", "2034": "חצור הגלילית", "831": "ירוחם", "168": "כפר יונה", "2300": "קרית טבעון",
    "1309": "אלעד", "3797": "מודיעין עילית", "922": "רכסים", "2550": "גדרה", "2200": "דימונה",
    "874": "מגדל העמק", "3780": "ביתר עילית", "1020": "אור עקיבא", "2620": "קרית אונו", "1167": "קיסריה",
    "9500": "קרית ביאליק", "2100": "טירת כרמל", "9600": "קרית ים", "1061": "נוף הגליל", "681": "גבעת שמואל",
    "182": "אבן יהודה", "7200": "נס ציונה", "9800": "בנימינה-גבעת עדה", "2560": "ערד", "7600": "עכו",
    "154": "תל מונד", "166": "גן יבנה", "469": "קרית עקרון", "1268": "מיתר", "171": "פרדסיה",
}

UNKNOWN = {}  # city codes we have no name for (printed for maintenance)

# Online stores (StoreType 2 in the store files). They get a delivery fee added to the basket total.
ONLINE_RE = re.compile(r"אונליין|און ?- ?ליין|ONLINE|אינטרנט|ליקוט", re.I)
DELIVERY_FEE = 35.9  # most chains charge 35.90 for a supermarket delivery (TheMarker, July 2026)
# online stores that deliver to most of the country: shown in every city
NATIONAL_ONLINE = {"shufersal-413", "ramilevi-39"}


def is_online(store_type, name):
    return store_type.strip() == "2" and bool(ONLINE_RE.search(name))


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
    if data[:2] == b"PK":  # some files are zip archives despite the .gz name
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            data = z.read(z.namelist()[0])
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
            sid = str(int(s.findtext("StoreID")))
            name = (s.findtext("StoreName") or "").strip()
            online = is_online(s.findtext("StoreType") or "", name)
            if online and "shufersal-" + sid in NATIONAL_ONLINE:
                city = next(iter(CITY))
            if city not in CITY:
                UNKNOWN.setdefault(city, (s.findtext("StoreName") or "") + " / שופרסל")
                continue
            stores[sid] = {"id": "shufersal-" + sid, "chain": "שופרסל", "sub": subname, "name": name,
                           "address": (s.findtext("Address") or "").strip(), "city": CITY[city]}
            if online:
                stores[sid]["online"] = DELIVERY_FEE
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



# ---------- Cerberus portal (url.publishedprices.co.il): many chains, public usernames, no password ----------
CERB = "https://url.publishedprices.co.il"
CERB_CHAINS = {"RamiLevi": "רמי לוי", "yohananof": "יוחננוף", "TivTaam": "טיב טעם",
               "doralon": "דור אלון", "freshmarket": "פרשמרקט", "Stop_Market": "סטופ מרקט", "SalachD": "סאלח דבאח",
               "osherad": "אושר עד", "politzer": "פוליצר", "Keshet": "קשת טעמים"}


def cerberus_session(user):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = list(UA.items())
    csrf = lambda h: re.search(r'name="csrftoken" content="([^"]+)"', h).group(1)
    h = op.open(CERB + "/login", timeout=60).read().decode()
    op.open(CERB + "/login/user", urllib.parse.urlencode(
        {"r": "", "username": user, "password": "", "csrftoken": csrf(h)}).encode(), timeout=60).read()
    token = csrf(op.open(CERB + "/file", timeout=60).read().decode())

    def listing(search):
        body = urllib.parse.urlencode({"iDisplayLength": 100000, "sSearch": search, "csrftoken": token}).encode()
        return [x["fname"] for x in json.load(op.open(CERB + "/file/json/dir", body, timeout=120))["aaData"]]

    def download(fname):
        for i in range(4):
            try:
                return op.open(CERB + "/file/d/" + urllib.parse.quote(fname), timeout=180).read()
            except Exception:
                if i == 3:
                    raise
                time.sleep(3 * (i + 1))
    return listing, download


def file_store_and_stamp(fname):
    """PriceFull<chain>-<store>-<yyyymmddhhmm>  or  PriceFull<chain>-<sub>-<store>-<yyyymmdd>-<hhmmss>"""
    nums = re.findall(r"\d+", fname.split(".")[0])
    if len(nums) >= 5:
        return str(int(nums[2])), nums[3] + nums[4][:4]
    return str(int(nums[1])), nums[2][:12]


ALIASES = {'ת"א': "5000", "תל אביב": "5000", 'ב"ש': "9000", 'ראשל"צ': "8300", 'פ"ת': "7900", 'כ"ס': "6900",
           "מודיעין": "1200", "קריית": None}


def guess_city(text_):
    """Some chains publish City=0. Find a known city name inside the store name or address."""
    t = " " + text_.replace("קריית", "קרית").replace("-", " ") + " "
    best = None
    for code, name in CITY.items():
        for variant in {name, name.split("-")[0]}:
            if f" {variant} " in t or f" {variant}," in t or t.rstrip().endswith(" " + variant):
                if best is None or len(variant) > len(CITY[best]):
                    best = code
    if best:
        return best
    for a, code in ALIASES.items():
        if code and a in t:
            return code
    return ""


def parse_stores(xml, chain_key, chain_name):
    stores = {}
    root = ET.fromstring(xml)
    for s in root.iter():
        if s.tag.lower() != "store":
            continue
        g = lambda t: next((c.text or "" for c in s if c.tag.lower() == t.lower()), "").strip()
        city = g("City")
        sid = str(int(g("StoreID") or 0))
        online = is_online(g("StoreType"), g("StoreName"))
        if online and f"{chain_key}-{sid}" in NATIONAL_ONLINE:
            city = next(iter(CITY))
        if city not in CITY:
            city = guess_city(g("StoreName") + " " + g("Address"))
        if city not in CITY:
            UNKNOWN.setdefault(city, g("StoreName") + " / " + chain_name)
            continue
        stores[sid] = {"id": f"{chain_key}-{sid}", "chain": chain_name, "sub": "", "name": g("StoreName"),
                       "address": g("Address"), "city": CITY[city]}
        if online:
            stores[sid]["online"] = DELIVERY_FEE
    return stores


def cerberus(user, chain_name):
    listing, download = cerberus_session(user)
    st = sorted(listing("Stores"))
    if not st:
        return []
    stores = parse_stores(text(download(st[-1])), user.lower(), chain_name)
    fresh = (dt.date.today() - dt.timedelta(days=3)).strftime("%Y%m%d")
    latest = {}
    for f in listing("PriceFull"):
        try:
            sid, stamp = file_store_and_stamp(f)
        except (IndexError, ValueError):
            continue
        if sid in stores and stamp[:8] >= fresh and (sid not in latest or stamp > latest[sid][0]):
            latest[sid] = (stamp, f)
    print(f"{user}: {len(stores)} stores in known cities, {len(latest)} fresh price files", file=sys.stderr)
    out = []
    for sid, (_, f) in latest.items():
        try:
            out.append((stores[sid], parse_items(text(download(f)))))
        except Exception as e:
            print("failed", user, sid, e, file=sys.stderr)
    return out


def make_cerberus(user, name):
    f = lambda: cerberus(user, name)
    f.__name__ = user
    return f


# ---------- binaprojects.com portals (King Store, Maayan 2000, Zol Vebegadol, ...) ----------
BINA_CHAINS = {"kingstore": "קינג סטור", "maayan2000": "מעיין 2000", "zolvebegadol": "זול ובגדול",
               "shefabirkathashem": "שפע ברכת השם", "supersapir": "סופר ספיר", "shuk-hayir": "שוק העיר"}


def bina(sub, chain_name):
    base = f"https://{sub}.binaprojects.com"

    def listing(ftype):
        req = urllib.request.Request(base + "/MainIO_Hok.aspx", urllib.parse.urlencode(
            {"WStore": "", "WDate": "", "WFileType": ftype}).encode(), headers=UA)
        return [x["FileNm"] for x in json.load(urllib.request.urlopen(req, timeout=120))]

    def download(fname):
        req = urllib.request.Request(base + "/Download.aspx?FileNm=" + urllib.parse.quote(fname), b"", headers=UA)
        url = json.load(urllib.request.urlopen(req, timeout=60))[0]["SPath"]
        return get(url)

    st = sorted(listing(1), key=lambda f: re.findall(r"\d{8}-\d+", f)[-1])
    stores = parse_stores(text(download(st[-1])), sub, chain_name)
    fresh = (dt.date.today() - dt.timedelta(days=3)).strftime("%Y%m%d")
    latest = {}
    for f in listing(4):
        try:
            sid, stamp = file_store_and_stamp(f)
        except (IndexError, ValueError):
            continue
        if sid in stores and stamp[:8] >= fresh and (sid not in latest or stamp > latest[sid][0]):
            latest[sid] = (stamp, f)
    print(f"{sub}: {len(stores)} stores in known cities, {len(latest)} fresh price files", file=sys.stderr)
    out = []
    for sid, (_, f) in latest.items():
        try:
            out.append((stores[sid], parse_items(text(download(f)))))
        except Exception as e:
            print("failed", sub, sid, e, file=sys.stderr)
    return out


def make_bina(sub, name):
    f = lambda: bina(sub, name)
    f.__name__ = sub
    return f


# ---------- chains fetched on a computer in Israel (scripts/local_fetch.py) ----------
LOCAL_CHAINS = {"hazihinam": "חצי חינם", "carrefour": "קרפור", "victory": "ויקטורי", "mahsaneihashuk": "מחסני השוק"}
LOCAL_URL = "https://raw.githubusercontent.com/eliabuchnik07-star/sal-zo/local-data/{}.json.gz"


def local(key):
    payload = json.loads(text(get(LOCAL_URL.format(key))))
    age = dt.datetime.now() - dt.datetime.fromisoformat(payload["fetched"])
    print(f"{key}: local data from {payload['fetched']} ({payload.get('parts', 1)} parts)", file=sys.stderr)
    if age > dt.timedelta(hours=36):
        print(f"{key}: local data too old, skipped", file=sys.stderr)
        return []
    if payload.get("v") != 2:  # old format: full item tuples per store
        return [(s, {c: tuple(v) for c, v in items.items()}) for s, items in payload["rows"]]
    out = []
    for n in range(payload["parts"]):
        p = payload if n == 0 else json.loads(text(get(LOCAL_URL.format(f"{key}.{n}"))))
        if p["fetched"] != payload["fetched"]:
            print(f"{key}: part {n} is from another run, skipped", file=sys.stderr)
            continue
        prods = p["products"]
        out += [(s, {c: (price, *prods[c], last) for c, price, last in r}) for s, r in p["rows"]]
    return out


def make_local(key):
    f = lambda: local(key)
    f.__name__ = "local-" + key
    return f


FETCHERS = [shufersal] + [make_cerberus(u, n) for u, n in CERB_CHAINS.items()] + [make_bina(u, n) for u, n in BINA_CHAINS.items()] + [make_local(k) for k in LOCAL_CHAINS]


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
        stores.append({k: s[k] for k in ("id", "chain", "sub", "name", "address", "online") if k in s})
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
    def safe(fetch):
        try:
            return fetch()
        except Exception as e:  # one broken chain must not stop the update
            print("chain failed:", fetch.__name__, e, file=sys.stderr)
            return []
    with ThreadPoolExecutor(len(FETCHERS)) as ex:
        for r in ex.map(safe, FETCHERS):
            rows += r
    if not rows:
        sys.exit("no data fetched")
    rows = [(s, items) for s, items in rows if len(items) >= 300]  # skip kiosks / broken files
    by_city = {}
    national = [(s, items) for s, items in rows if s["id"] in NATIONAL_ONLINE]
    for s, items in rows:
        if s["id"] not in NATIONAL_ONLINE:
            by_city.setdefault(s["city"], []).append((s, items))
    for city in by_city:  # nationwide online stores compete in every city that has physical stores
        by_city[city] += national
    today = dt.date.today()
    cities = []
    # stable file name per city (its CBS code), so an open page never loads another city's file
    code_of = {name: code for code, name in CITY.items()}
    for city in sorted(by_city):
        n = code_of[city]
        count = write_city(n, city, by_city[city], today)
        cities.append({"name": city, "file": f"city/{n}.json", "stores": len(by_city[city]), "products": count})
    with open(os.path.join(DATA, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "cities": cities},
                  f, ensure_ascii=False, indent=1)
    print("unknown city codes:", json.dumps(UNKNOWN, ensure_ascii=False), file=sys.stderr)
    print(f"wrote {len(cities)} cities from {len(rows)} stores", file=sys.stderr)


if __name__ == "__main__":
    main()
