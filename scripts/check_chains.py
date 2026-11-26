"""Every day: check which chains publish fresh price files, and look for chains we don't cover yet.

Writes a Markdown report to stdout. The workflow posts it as a GitHub issue when something needs attention.
"""
import sys, os, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp

# usernames on the shared portal that some chains have used; new ones get reported when they appear with data
CANDIDATES = ["RamiLevi", "osherad", "yohananof", "TivTaam", "Keshet", "doralon", "freshmarket", "Stop_Market",
              "SalachD", "politzer", "Paz_bo", "yuda_ho", "SuperCofixApp", "HaziHinam", "nativ", "victory",
              "MegaMarket", "SuperYuda", "Shefa_Birkat_Hashem", "BareketMarket", "zolvebegadol", "maayan2000"]


# The government list of price sites (gov.il/he/pages/cpfta_prices_regulations), read on 2026-11-26.
# The page is built by JavaScript and refuses servers abroad, so it is copied here; compare it by hand now and then.
# chain name on our site -> its price site; None = deliberately left out (with the reason)
GOV_LIST = {
    "קינג סטור": "kingstore.binaprojects.com", "מעיין 2000": "maayan2000.binaprojects.com",
    "קרפור": "prices.carrefour.co.il", "דור אלון": "publishedprices: doralon",
    "ויקטורי": "laibcatalog.co.il/victory", "זול ובגדול": "zolvebegadol.binaprojects.com",
    "ח. כהן": "laibcatalog.co.il/hcohen", "טיב טעם": "publishedprices: TivTaam",
    "מחסני השוק": "laibcatalog.co.il/mshuk", "חצי חינם": "shop.hazi-hinam.co.il",
    "יוחננוף": "publishedprices: yohananof", "אושר עד": "publishedprices: osherad",
    "סאלח דבאח": "publishedprices: SalachD", "סופר ספיר": "supersapir.binaprojects.com",
    "סיטי מרקט": "citymarketkiryatgat.binaprojects.com", "סטופ מרקט": "publishedprices: Stop_Market",
    "סופר ברקת": "superbareket.binaprojects.com", "פוליצר": "publishedprices: politzer",
    "פרשמרקט": "publishedprices: freshmarket", "משנת יוסף": "ktshivuk.binaprojects.com",
    "קשת טעמים": "publishedprices: Keshet", "רמי לוי": "publishedprices: RamiLevi",
    "שופרסל": "prices.shufersal.co.il", "שוק העיר": "shuk-hayir.binaprojects.com",
    "שפע ברכת השם": "shefabirkathashem.binaprojects.com",
}
NOT_YET = {
    "וולט מרקט": "wolt.com: אתר בפורמט אחר, עוד לא נבדק",
    "נתיב החסד": "app.netiv-hesed.com: אתר בפורמט אחר, עוד לא נבדק",
    "יילו / סופר יודה (פז)": "publishedprices עם סיסמה",
    "גוד פארם, סופר פארם": "פארם, לא סופרמרקט",
}
SITE_INDEX = "https://eliabuchnik07-star.github.io/sal-zo/data/index.json"


def gov_report():
    """Every chain on the government list must have branches on the live site."""
    import json
    try:
        have = json.loads(fp.get(SITE_INDEX)).get("chains", {})
    except Exception as e:
        return [f"- לא הצלחתי לקרוא את האתר ({e})."], []
    lines, alerts = ["\n### רשימת משרד הכלכלה מול האתר\n", "| רשת | אתר המחירים | סניפים באתר |", "|---|---|---|"], []
    for name, src in GOV_LIST.items():
        n = have.get(name, 0)
        lines.append(f"| {name} | {src} | {n or '❌ 0'} |")
        if not n:
            alerts.append(f"- **{name}** ברשימת משרד הכלכלה, אבל אין לה סניפים באתר היום ({src}).")
    lines += [f"| {k} | {v} | לא מחובר |" for k, v in NOT_YET.items()]
    return alerts, lines


def main():
    gov_alerts, gov_lines = gov_report()
    fresh = (dt.date.today() - dt.timedelta(days=3)).strftime("%Y%m%d")
    lines, alerts = ["| חשבון | סטטוס | קבצי מחירים טריים |", "|---|---|---|"], []
    for u in CANDIDATES:
        try:
            listing, _ = fp.cerberus_session(u)
            files = listing("PriceFull")
            n = sum(1 for f in files if fp.file_store_and_stamp(f)[1][:8] >= fresh)
        except Exception as e:
            lines.append(f"| {u} | אין גישה | - |")
            continue
        covered = u in fp.CERB_CHAINS
        lines.append(f"| {u} | {'מחובר לאתר' if covered else 'לא מחובר'} | {n} |")
        if covered and n == 0:
            alerts.append(f"- הרשת **{fp.CERB_CHAINS[u]}** ({u}) לא פרסמה קבצים ב־3 הימים האחרונים.")
        if not covered and n > 0:
            alerts.append(f"- נמצאה רשת חדשה עם מחירים: **{u}** ({n} קבצים). צריך להוסיף אותה ל־CERB_CHAINS.")
    alerts += gov_alerts
    lines += gov_lines
    print("## בדיקת רשתות יומית\n")
    print("\n".join(alerts) if alerts else "הכל תקין, אין רשתות חדשות.")
    print("\n" + "\n".join(lines))
    with open(os.environ.get("ALERT_FILE", "/dev/null"), "w") as f:
        f.write("1" if alerts else "")


if __name__ == "__main__":
    main()
