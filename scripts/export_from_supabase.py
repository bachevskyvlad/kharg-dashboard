"""
export_from_supabase.py
Reads dbt output tables from Supabase,
writes data/data.json and data/data_daily.json
(lng_data.json is written directly by fetch_lng_data.py)
"""
import json
from pathlib import Path
from supabase import create_client

SUPABASE_URL = "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

DATA_DIR = Path(__file__).parent.parent / "data"


def fetch_all(sb, table, select="*", filters=None, order="date"):
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

    # ── 1. data.json ── weekly smoothed Kharg volumes
    rows = fetch_all(sb, "volume_timeseries",
                     select="date,vol_mn_bbl",
                     filters={"aggregation": "weekly", "smoothed": True})
    with open(DATA_DIR / "data.json", "w") as f:
        json.dump({"dates": [r["date"] for r in rows],
                   "values": [r["vol_mn_bbl"] for r in rows]}, f)
    print(f"data.json: {len(rows)} rows")

    # ── 2. data_daily.json ── from dbt kharg_daily mart, fallback to volume_timeseries
    try:
        rows = fetch_all(sb, "kharg_daily", select="date,vol_mn_bbl")
        if not rows:
            raise ValueError("kharg_daily is empty")
        print(f"data_daily.json: {len(rows)} rows (from kharg_daily)")
    except Exception as e:
        print(f"kharg_daily not available ({e}), falling back to volume_timeseries daily")
        rows = fetch_all(sb, "volume_timeseries",
                         select="date,vol_mn_bbl",
                         filters={"aggregation": "daily", "smoothed": False})
        print(f"data_daily.json: {len(rows)} rows (fallback)")
    with open(DATA_DIR / "data_daily.json", "w") as f:
        json.dump({"dates": [r["date"] for r in rows],
                   "values": [r["vol_mn_bbl"] for r in rows]}, f)

    print("\nExport complete.")


if __name__ == "__main__":
    main()
