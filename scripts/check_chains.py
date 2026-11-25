"""Every two weeks: check which chains publish fresh price files, and look for chains we don't cover yet.

Writes a Markdown report to stdout. The workflow posts it as a GitHub issue when something needs attention.
"""
import sys, os, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp

# usernames on the shared portal that some chains have used; new ones get reported when they appear with data
CANDIDATES = ["RamiLevi", "osherad", "yohananof", "TivTaam", "Keshet", "doralon", "freshmarket", "Stop_Market",
              "SalachD", "politzer", "Paz_bo", "yuda_ho", "SuperCofixApp", "HaziHinam", "nativ", "victory",
              "MegaMarket", "SuperYuda", "Shefa_Birkat_Hashem", "BareketMarket", "zolvebegadol", "maayan2000"]


def main():
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
    print("## בדיקת רשתות דו־שבועית\n")
    print("\n".join(alerts) if alerts else "הכל תקין, אין רשתות חדשות.")
    print("\n" + "\n".join(lines))
    with open(os.environ.get("ALERT_FILE", "/dev/null"), "w") as f:
        f.write("1" if alerts else "")


if __name__ == "__main__":
    main()
