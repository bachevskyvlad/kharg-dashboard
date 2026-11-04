"""
export_ras_tanura_json.py — Export ras_tanura daily volume to static JSON.

Writes: data/terminals/ras_tanura_daily.json
Format: {"terminal": "ras_tanura", "name": "Ras Tanura", "max_capacity_mb": 135,
         "dates": [...], "values": [...]}
"""

import json
import os
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or ""
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or ""
if not SUPABASE_URL or not SUPABASE_KEY:
    import sys; sys.exit("FATAL: SUPABASE_URL and SUPABASE_KEY env vars must be set")
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "terminals")
os.makedirs(OUTPUT_DIR, exist_ok=True)

rows = []
offset = 0
while True:
    batch = (
        sb.table("terminal_volume_timeseries")
        .select("date,vol_mn_bbl")
        .eq("terminal_id", "ras_tanura")
        .eq("aggregation", "daily")
        .order("date")
        .range(offset, offset + 999)
        .execute()
        .data
    )
    rows.extend(batch)
    if len(batch) < 1000:
        break
    offset += 1000

if not rows:
    print("No data found for ras_tanura — run compute.py first.")
    raise SystemExit(1)

rows.sort(key=lambda r: r["date"])
payload = {
    "terminal": "ras_tanura",
    "name": "Ras Tanura",
    "max_capacity_mb": 135,
    "dates": [r["date"] for r in rows],
    "values": [round(float(r["vol_mn_bbl"]), 3) for r in rows],
}

path = os.path.join(OUTPUT_DIR, "ras_tanura_daily.json")
with open(path, "w") as f:
    json.dump(payload, f, separators=(",", ":"))

print(f"Wrote {path} ({len(rows)} rows, {rows[0]['date']} → {rows[-1]['date']})")
