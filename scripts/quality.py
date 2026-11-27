"""The site's quality checker ("the agent").

Every hourly build (fetch_prices.py) calls `fix_city()` on each city before it is written:
  * categories: each product gets one category (scripts/categorize.py); products that break a
    category's rules (red-flag words, see RED_FLAGS) are taken out of that category;
  * duplicates: the same product listed under several barcodes with the same name and the same
    price in every store is merged into one; same-name products that really differ (loose vs
    packed produce, colors/models, names the chain cut short) get a short tag so they can be told apart;
  * everything it found or fixed goes to site/data/quality.json.

Once a day .github/workflows/daily-check.yml runs this file on the live site
(python3 scripts/quality.py --site URL), checks every city again plus price outliers and stale
data, and writes a report (GitHub job summary and an issue in the repository). The report also
shows coverage per category, ideas for new categories (THEMES), the most common first words of
uncategorized products, and a random sample per category to check by eye.
"""
import json, os, re, statistics, sys, urllib.request
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import categorize as C

W = C.W
# words that must never appear in a category's products (checked on the name, independently of
# the classifier, so a classifier mistake is caught and removed)
RED_FLAGS = {
    "fruit": C.TO_PANTRY | C.TO_DRINKS | C.TO_SNACKS | C.FROZEN | C.FLAVOR | C.TO_DELI | W(
        "שוקולד גלידה גומי סוכריות מסטיק עוגה עוגיות חטיף ממתק יוגורט גבינה שמפו סבון כביסה כלים מרכך "
        "ניקוי בשר עוף נקניק לחם קמח פסטה אורז שמן ויטמין קרם תה קפה בירה יין וודקה ליקר הריבו מנטוס "
        "חלב דגני גרנולה קורנפלקס פופקורן במבה ביסלי קטשופ אבקת תוסף כמוסות לפנקייק מקלוני קרקר"),
    "dairy": W("שמפו כביסה כלים אקונומיקה פחם בשר עוף נקניק ציפס במבה ביסלי בירה יין וודקה משחת דאודורנט "
               "סיגריות סבון לכלים טיטולים"),
    "meat": W("שוקולד סוכריות גלידה במבה ביסלי שמפו כביסה בירה יין וודקה עוגיות משחת דאודורנט סבון ויטמין "
              "לכלב לחתול"),
    "bread": W("שמפו כביסה בירה יין וודקה משחת דאודורנט סבון אקונומיקה"),
    "deli": W("שמפו כביסה בירה יין וודקה משחת דאודורנט סבון אקונומיקה"),
    "canned": W("שמפו כביסה לכביסה אקונומיקה משחת דאודורנט טיטולים בירה וודקה טרי טריה שוקולד גלידה"),
    "alcohol": W("שמפו כביסה לכביסה אקונומיקה משחת דאודורנט טיטולים ללא_אלכוהול נטול_אלכוהול"),
    "baking": W("שמפו כביסה לכביסה אקונומיקה משחת דאודורנט טיטולים בירה וודקה טונה זיתים"),
    "pantry": W("שמפו כביסה לכביסה אקונומיקה משחת דאודורנט טיטולים בירה וודקה"),
    "snacks": W("שמפו כביסה לכביסה אקונומיקה משחת דאודורנט טיטולים טרי"),
    "drinks": W("שמפו כביסה לכביסה אקונומיקה משחת דאודורנט טיטולים מיסלרי מיסלריים מיסלר"),
    "frozen": W("שמפו כביסה לכביסה אקונומיקה משחת דאודורנט טיטולים"),
    "home": W("יוגורט גבינה שוקולד במבה ביסלי עוגיות לחם בירה יין"),
    "care": W("יוגורט גבינה שוקולד במבה ביסלי עוגיות לחם בירה יין אקונומיקה"),
}
# allowed exceptions to a red flag (e.g. "חסה" fresh-cut is fine though "חתוך" is not a flag)
FLAG_OK = {
    "fruit": W("מלפפון מלפפונים"),
}


def red_flag(cat, name):
    """The word that makes `name` wrong for `cat`, or None."""
    if not cat or cat not in RED_FLAGS:
        return None
    toks = C.tokens(name)
    hit = set(toks) & RED_FLAGS[cat]
    if cat == "fruit":
        hit -= FLAG_OK["fruit"] if (set(toks) & W("ארוז ארוזה")) else set()
        if C.VOLUME.search(" ".join(toks)):
            hit.add("נפח (מ\"ל/ליטר)")
    return sorted(hit)[0] if hit else None


def norm_name(n):
    return " ".join(C.tokens(n))


def is_internal(code):
    """Chains' own short codes (loose produce, bakery, deli counter) are not real barcodes."""
    return len(code.split(".")[-1]) < 8


def same_product(a, b):
    """Do two names from different chains describe the same product? (word overlap)"""
    ta, tb = set(C.tokens(a)) - C.NOISE, set(C.tokens(b)) - C.NOISE
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.5


# ---------- per-city fixes, run by every build ----------
def fix_city(products, prices, cats):
    """products: [[code, name, manufacturer, unit], ...]; prices: per store [[i, price, days, promo?], ...];
    cats: {code: category}. Returns (products, prices, findings) with category as the 5th field
    of each product and duplicates merged."""
    findings = {"removed_from_category": [], "merged_duplicates": [], "same_name_different_product": [],
                "dropped_prices": [], "dropped_nameless": []}

    # 0. prices that can't be right: placeholder prices (0.01) and a branch at 4x the city median for the
    #    same barcode (a box or a kilo priced against single units) are left out of the comparison
    per = defaultdict(list)
    for row in prices:
        for r in row:
            per[r[0]].append(r[1])
    med = {i: statistics.median(v) for i, v in per.items() if len(v) >= 4}
    for si, row in enumerate(prices):
        keep = []
        for r in row:
            m = med.get(r[0])
            if r[1] < 0.1 or (m and (r[1] > m * 4 or r[1] < m / 4)):
                findings["dropped_prices"].append([products[r[0]][0], products[r[0]][1], r[1], m])
            else:
                keep.append(r)
        prices[si] = keep
    # products without a name (some online stores publish empty names) can't be shown
    nameless = {i for i, p in enumerate(products) if not (p[1] or "").strip()}
    if nameless:
        findings["dropped_nameless"] = [products[i][0] for i in sorted(nameless)]
        prices = [[r for r in row if r[0] not in nameless] for row in prices]

    # 1. duplicates: same normalized name + unit, same price in every store that sells both -> merge
    groups = defaultdict(list)
    for i, p in enumerate(products):
        groups[(norm_name(p[1]), p[3])].append(i)
    store_price = [dict((r[0], r[1]) for r in row) for row in prices]
    carried = Counter(r[0] for row in prices for r in row)
    merge_into = {}
    for (nm, unit), idx in groups.items():
        if len(idx) < 2 or not nm:
            continue
        idx.sort(key=lambda i: (-carried[i], is_internal(products[i][0])))
        keep = [idx[0]]
        for j in idx[1:]:
            for k in keep:
                same = all(sp[j] == sp[k] for sp in store_price if j in sp and k in sp)
                together = any(j in sp and k in sp for sp in store_price)
                if same and (together or products[j][2] == products[k][2]):
                    merge_into[j] = k
                    findings["merged_duplicates"].append([products[j][0], products[k][0], products[k][1]])
                    break
            else:
                keep.append(j)

    if merge_into:
        new_index, out = {}, []
        for i, p in enumerate(products):
            if i not in merge_into:
                new_index[i] = len(out)
                out.append(p)
        for j, k in merge_into.items():
            while k in merge_into:
                k = merge_into[k]
            new_index[j] = new_index[k]
        new_prices = []
        for row in prices:
            seen, r2 = set(), []
            for r in row:
                ni = new_index[r[0]]
                if ni in seen:
                    continue
                seen.add(ni)
                r2.append([ni] + r[1:])
            new_prices.append(r2)
        products, prices = out, new_prices

    # same name, really different products (loose vs packed, models, names the chain cut short):
    # add what tells them apart, so the shopper doesn't see one product at several prices
    by_name = defaultdict(list)
    for i, p in enumerate(products):
        by_name[norm_name(p[1])].append(i)
    for nm, idx in by_name.items():
        if len(idx) < 2 or not nm:
            continue
        units = [products[i][3] for i in idx]
        for i in idx:
            p = products[i]
            code = p[0].split(".")[-1]
            if len(set(units)) == len(units) and p[3]:
                tag = p[3]
            elif is_internal(code):
                tag = "קוד " + code
            else:
                tag = "ברקוד …" + code[-6:]
            p[1] = f"{p[1]} ({tag})"
        findings["same_name_different_product"].append([products[i][0] for i in idx])

    # 2. categories + red-flag removal
    for p in products:
        cat = None if is_internal(p[0].split(".")[-1]) else cats.get(p[0])
        if cat is None:
            cat = C.classify(p[1], p[2])
        flag = red_flag(cat, p[1])
        if flag:
            alt = C.classify(p[1], p[2])
            fixed = alt if alt != cat and not red_flag(alt, p[1]) else None
            findings["removed_from_category"].append([p[0], p[1], cat, flag, fixed])
            cat = fixed
        p[4:] = [cat]
    return products, prices, findings


# ---------- daily sweep of the live site ----------
def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "sal-zo-quality-check"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def check_city(d):
    """Problems in one city's published data."""
    out = defaultdict(list)
    prods = d["products"]
    names = {}
    for i, p in enumerate(prods):
        cat = p[4] if len(p) > 4 else None
        f = red_flag(cat, p[1])
        if f:
            out["wrong_category"].append(f"{p[1]} — בקטגוריה {cat} (מילה: {f})")
        guess = C.classify(p[1], p[2])
        if cat is None and guess is not None and not red_flag(guess, p[1]):
            out["missing_category"].append(p[1])
    # same name, different price in the same store
    for s, row in zip(d["stores"], d["prices"]):
        by = defaultdict(set)
        for r in row:
            by[prods[r[0]][1]].add(r[1])
        for n, ps in by.items():
            if len(ps) > 1:
                out["duplicate_in_store"].append(f"{s['chain']} {s['name']}: {n} — {sorted(ps)}")
    # price outliers: one store's price far from the city's median for the same barcode
    per = defaultdict(list)
    for s, row in zip(d["stores"], d["prices"]):
        for r in row:
            per[r[0]].append((r[1], s))
    for i, lst in per.items():
        if len(lst) < 4:
            continue
        med = statistics.median(x for x, _ in lst)
        for x, s in lst:
            if med > 0 and (x > med * 4 or x < med / 4):
                out["price_outlier"].append(f"{prods[i][1]}: {x}₪ ב{s['chain']} {s['name']} (חציון בעיר {med}₪)")
    # stores whose data is old (no item sold in the last 10 days)
    for s, row in zip(d["stores"], d["prices"]):
        days = [r[2] for r in row if r[2] >= 0]
        if days and min(days) > 10:
            out["stale_store"].append(f"{s['chain']} {s['name']}: אין מכירה מתועדת {min(days)} ימים")
    return out


LABELS = {
    "wrong_category": "מוצר בקטגוריה לא נכונה",
    "missing_category": "מוצר בלי קטגוריה שהבודק יודע לשייך",
    "duplicate_in_store": "אותו שם בכמה מחירים באותו סניף",
    "price_outlier": "מחיר חריג (פי 4 מהחציון בעיר)",
    "stale_store": "סניף עם נתונים ישנים",
}


# themes that may deserve a category of their own: (label, words in the name, categories it
# would come out of). A theme is suggested once it has SUGGEST_MIN products on the site.
SUGGEST_MIN = 150
THEMES = [
    ("תינוקות (נפרד מפארם)", W("טיטולים חיתולים מוצץ מוצצים בקבוק_תינוק מטרנה סימילאק נוטרילון פמפרס האגיס לתינוק לתינוקות"), None),
    ("חיות מחמד", C.PETS, None),
    ("חד פעמי", W("חד_פעמי חד_פעמיות חדפ מפיות קשיות"), {"home"}),
    ("כלי בית ומטבח", W("סיר מחבת קומקום סכין סכינים קערה קערות צלחת צלחות כוס כוסות מגש תבנית תבניות קופסת מיכל מספריים"), {"home", None}),
    ("ביגוד וטקסטיל", C.CLOTHES | W("מגבת מגבות סדין סדינים שמיכה כרית"), {None}),
    ("צעצועים ומשחקים", W("צעצוע צעצועי משחק משחקי בובה בובת פאזל לגו קלפים כדור_משחק פליימוביל"), {None}),
    ("חשמל ואלקטרוניקה", W("מטען כבל סוללה סוללות אוזניות נורה נורות מאוורר מפזר רמקול מצלמה מנורה פנס שעון"), {None, "home"}),
    ("כלי כתיבה", W("עט עטים עפרון עפרונות מחברת מחברות טוש טושים מחדד מחק דבק סלוטייפ"), {None}),
]


def _theme_hit(name, words):
    toks = C.tokens(name)
    joined = set(toks) | {a + "_" + b for a, b in zip(toks, toks[1:])}
    return bool(joined & words)


def suggestions(products):
    """products: {barcode: (name, {category: number of cities})}. Ideas for the owner: themes that
    could become categories, the most common first words among uncategorized products, and
    products that got different categories in different cities."""
    out = []
    for label, words, source in THEMES:
        hits = [(n, c) for n, cats in products.values() for c in [max(cats, key=cats.get)]
                if (source is None or c in source) and _theme_hit(n, words)]
        if len(hits) >= SUGGEST_MIN:
            where = Counter(C_NAME.get(c, "בלי קטגוריה") for _, c in hits).most_common(3)
            out.append(f"- **לשקול קטגוריה \"{label}\"**: {len(hits):,} מוצרים באתר"
                       f" (כרגע ב: {', '.join(f'{w} {k:,}' for w, k in where)}). לדוגמה: "
                       + " · ".join(n for n, _ in hits[:4]))
    heads = Counter()
    for n, cats in products.values():
        if max(cats, key=cats.get) is None:
            t = C.tokens(n)
            if t:
                heads[t[C._head(t)]] += 1
    if heads:
        out.append("- **המילים הנפוצות בתחילת שם של מוצרים בלי קטגוריה** (לבדוק אם צריך כלל חדש): "
                   + ", ".join(f"{w} ({k})" for w, k in heads.most_common(25)))
    mixed = [(n, cats) for n, cats in products.values() if len(cats) > 1]
    if mixed:
        out.append(f"- **{len(mixed):,} מוצרים קיבלו קטגוריה שונה בערים שונות** (בדרך כלל שם שונה בכל רשת). לדוגמה: "
                   + " · ".join(f"{n} ({'/'.join(C_NAME.get(c, 'בלי') for c in cats)})" for n, cats in mixed[:6]))
    return out


C_NAME = {c[0]: c[2] for c in C.CATEGORIES}


def sweep(site, sample=8, seed=None):
    import random
    site = site.rstrip("/")
    idx = get_json(site + "/data/index.json")
    totals, examples = Counter(), defaultdict(list)
    products = {}                      # barcode -> [name, Counter(category -> cities)]
    stores = prices = 0
    for c in idx["cities"]:
        try:
            d = get_json(f"{site}/data/{c['file']}")
        except Exception as e:
            totals["city_failed"] += 1
            examples["city_failed"].append(f"{c['name']}: {e}")
            continue
        stores += len(d["stores"])
        prices += sum(len(r) for r in d["prices"])
        for p in d["products"]:
            cat = p[4] if len(p) > 4 else None
            e = products.setdefault(p[0], [p[1], Counter()])
            e[1][cat] += 1
        for k, v in check_city(d).items():
            totals[k] += len(v)
            examples[k] += [f"{c['name']}: {x}" for x in v[:3]]
    uniq = {k: (v[0], v[1]) for k, v in products.items()}
    per_cat = Counter(max(cats, key=cats.get) for _, cats in uniq.values())
    n = len(uniq) or 1
    lines = ["# דוח בודק האיכות היומי",
             f"נתוני האתר עודכנו: {idx.get('updated')}", "",
             "## מה נבדק היום",
             f"כל האתר: {len(idx['cities']) - totals['city_failed']} מתוך {len(idx['cities'])} ערים, {stores:,} סניפים, "
             f"{prices:,} מחירים, {len(uniq):,} מוצרים שונים. כל מוצר נבדק מול הכללים של הקטגוריה שלו.",
             "",
             "| קטגוריה | מוצרים | אחוז |", "|---|---|---|"]
    for cat, k in sorted(per_cat.items(), key=lambda x: -x[1]):
        lines.append(f"| {C_NAME.get(cat, 'בלי קטגוריה')} | {k:,} | {100 * k / n:.1f}% |")
    lines.append("")
    if not totals:
        lines.append("לא נמצאו בעיות. ✅")
    for k, cnt in totals.most_common():
        lines.append(f"## {LABELS.get(k, k)}: {cnt}")
        lines += [f"- {x}" for x in examples[k][:15]]
        lines.append("")
    lines += ["## הצעות לשיפור", "הבודק לא משנה את אלה לבד. הם רעיונות לבעל האתר.", ""]
    lines += suggestions(uniq) or ["אין הצעות היום."]
    lines.append("")
    # a random handful from every category, so a person can eyeball mistakes the rules miss
    rnd = random.Random(seed)
    lines += ["## מדגם אקראי לבדיקה בעין", "כמה מוצרים אקראיים מכל קטגוריה. מוצר שלא שייך לקטגוריה = כלל חדש שצריך להוסיף.", ""]
    by_cat = defaultdict(list)
    for name, cats in uniq.values():
        by_cat[max(cats, key=cats.get)].append(name)
    for cat in [c[0] for c in C.CATEGORIES] + [None]:
        names = by_cat.get(cat) or []
        if names:
            lines.append(f"- **{C_NAME.get(cat, 'בלי קטגוריה')}**: " + " · ".join(rnd.sample(names, min(sample, len(names)))))
    return "\n".join(lines), totals


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--site":
        report, totals = sweep(sys.argv[2])
        print(report)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(report + "\n")
        with open("quality-report.md", "w", encoding="utf-8") as f:
            f.write(report + "\n")
    else:
        for n in sys.argv[1:]:
            c = C.classify(n)
            print(c, red_flag(c, n), n)
