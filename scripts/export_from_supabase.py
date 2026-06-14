"""
export_from_supabase.py
Reads dbt output tables + raw history from Supabase,
writes data/data.json, data/data_daily.json, data/lng_data.json
"""
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from supabase import create_client

SUPABASE_URL = "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

DATA_DIR = Path(__file__).parent.parent / "data"


def fetch_all(sb, table, select="*", filters=None, order="date"):
    """Fetch all rows with pagination (Supabase default limit is 1000)."""
    all_rows = []
    page_size = 1000
    offset = 0
    while True:
        q = sb.table(table).select(select).order(order).range(offset, offset + page_size - 1)
        if filters:
            for col, val in filters.items():
                q = q.eq(col, val)
        rows = q.execute().data
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return all_rows


def main():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. data.json ── weekly smoothed Kharg volumes (index page chart)
    rows = fetch_all(sb, "volume_timeseries",
                     select="date,vol_mn_bbl",
                     filters={"aggregation": "weekly", "smoothed": True})
    with open(DATA_DIR / "data.json", "w") as f:
        json.dump({"dates": [r["date"] for r in rows],
                   "values": [r["vol_mn_bbl"] for r in rows]}, f)
    print(f"data.json: {len(rows)} rows")

    # ── 2. data_daily.json ── from dbt khark_daily mart (monitor page chart)
    rows = fetch_all(sb, "kharg_daily", select="date,vol_mn_bbl")
    with open(DATA_DIR / "data_daily.json", "w") as f:
        json.dump({"dates": [r["date"] for r in rows],
                   "values": [r["vol_mn_bbl"] for r in rows]}, f)
    print(f"data_daily.json: {len(rows)} rows")

    # ── 3. lng_data.json ── full history from lng_raw (gas-storage page)
    rows = fetch_all(sb, "lng_raw")
    countries = defaultdict(lambda: {"name": "", "data": []})
    for r in rows:
        code = r["country_code"]
        countries[code]["name"] = r["country_name"] or code
        countries[code]["data"].append({
            "date":         r["date"],
            "full":         r["full_pct"],
            "gasInStorage": r["gas_in_storage_twh"],
            "injection":    r["injection_twh"],
            "withdrawal":   r["withdrawal_twh"],
            "trend":        r["trend"],
        })
    # sort each country's data by date
    for code in countries:
        countries[code]["data"].sort(key=lambda x: x["date"])

    with open(DATA_DIR / "lng_data.json", "w") as f:
        json.dump({
            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source":     "GIE AGSI+  https://agsi.gie.eu",
            "countries":  dict(countries),
        }, f, ensure_ascii=False, separators=(",", ":"))
    print(f"lng_data.json: {len(rows)} rows across {len(countries)} countries")

    print("\nExport complete.")


if __name__ == "__main__":
    main()
