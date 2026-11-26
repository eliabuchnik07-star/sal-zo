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


# price sites we already read; anything else on the government list is reported as a possible new chain
KNOWN_HOSTS = ["prices.shufersal.co.il", "publishedprices.co.il", "binaprojects.com", "laibcatalog.co.il",
               "prices.carrefour.co.il", "hazi-hinam.co.il"]


IGNORE_HOSTS = ["facebook.", "twitter.", "x.com", "youtube.", "instagram.", "linkedin.", "whatsapp.", "t.me",
                "google.", "apple.com", "microsoft.", "nevo.co.il", "knesset."]


def gov_report():
    """The government page (saved daily by the computer in Israel): links to every chain's price site."""
    import json, urllib.parse
    try:
        p = json.loads(fp.text(fp.get(fp.LOCAL_URL.format("gov-links"))))
    except Exception as e:
        return [f"- לא הצלחתי לקרוא את רשימת הרשתות של משרד הכלכלה ({e}). האם המחשב בישראל דלוק?"], []
    lines = [f"\n### רשימת משרד הכלכלה (נשמרה {p['fetched']})\n"]
    alerts = []
    age = dt.datetime.now() - dt.datetime.fromisoformat(p["fetched"])
    if age > dt.timedelta(days=3):
        alerts.append(f"- רשימת משרד הכלכלה לא עודכנה מאז {p['fetched']}.")
    if not p["links"]:
        alerts.append("- בדף של משרד הכלכלה לא נמצאו קישורים (אולי הדף נטען עם JavaScript). צריך לשנות את הבדיקה.")
    for u in p["links"]:
        host = urllib.parse.urlparse(u).netloc.lower()
        if any(x in host for x in IGNORE_HOSTS):
            continue
        known = any(host == k or host.endswith("." + k) for k in KNOWN_HOSTS)
        lines.append(f"- {'✅' if known else '🆕'} {u}")
        if not known:
            alerts.append(f"- אתר מחירים שאנחנו לא קוראים עדיין: {u}")
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
