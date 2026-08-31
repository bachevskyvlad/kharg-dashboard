"""
compute.py — Volume computation: raw dB → Mn bbl aggregates → Supabase.

Step 2 of 3 in the pipeline (ingest → compute → export).
Reads from local cache (Kharg) or Supabase (terminals), applies inverse
crescent radiometry, and upserts daily/weekly results to Supabase.
Writes intermediate parquet files for the export step.
"""

import sys
import os
import pickle
import numpy as np
import pandas as pd
import warnings

warnings.filterwarnings("ignore")

from config import sb, RAW_CACHE_PATH
from locations import TERMINALS
from sar_fetch import load_cache
from volume import compute_volumes, aggregate_daily, aggregate_weekly

os.makedirs("data", exist_ok=True)


# ── Kharg ─────────────────────────────────────────────────────────────────────
cached_records, _ = load_cache(RAW_CACHE_PATH, sb)

if not cached_records:
    print("CRITICAL: No Kharg cache — run ingest.py first.")
    sys.exit(1)

df = pd.DataFrame(cached_records)
df["date"] = pd.to_datetime(df["date"])
df, _, _ = compute_volumes(df)

daily_total  = aggregate_daily(df)
weekly_total = aggregate_weekly(df)
print(f"[compute] Kharg — {len(daily_total)} daily rows, {len(weekly_total)} weekly rows")

try:
    rows = [{"date": r["day"].strftime("%Y-%m-%d"), "vol_mn_bbl": round(float(r["vol_mn_bbl"]), 3),
             "aggregation": "daily", "smoothed": False}
            for _, r in daily_total.iterrows()]
    sb.table("volume_timeseries").upsert(rows, on_conflict="date,aggregation,smoothed").execute()
    print(f"[compute] volume_timeseries daily: {len(rows)} rows")
except Exception as e:
    print(f"[compute] [!] daily upsert failed: {e}")

try:
    rows = [{"date": r["week_start"].strftime("%Y-%m-%d"), "vol_mn_bbl": round(float(r["smoothed_vol"]), 3),
             "aggregation": "weekly", "smoothed": True}
            for _, r in weekly_total.iterrows()]
    sb.table("volume_timeseries").upsert(rows, on_conflict="date,aggregation,smoothed").execute()
    print(f"[compute] volume_timeseries weekly: {len(rows)} rows")
except Exception as e:
    print(f"[compute] [!] weekly upsert failed: {e}")

# Save for export step (same job, shared filesystem)
daily_total.to_parquet("data/_kharg_daily.parquet",   index=False)
weekly_total.to_parquet("data/_kharg_weekly.parquet", index=False)


# ── Terminals ─────────────────────────────────────────────────────────────────
def _bbox(tanks, buffer_m=300):
    from sentinelhub import BBox, CRS
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min(lats))))
    return BBox([min(lons)-dlon, min(lats)-dlat, max(lons)+dlon, max(lats)+dlat], crs=CRS.WGS84)

terminal_daily = {}

for terminal in TERMINALS:
    tid   = terminal["id"]
    tanks = terminal["tanks"]
    print(f"\n[compute] {terminal['name']} ({tid})")

    try:
        all_obs = []; offset = 0
        while True:
            batch = (sb.table("terminal_observations")
                       .select("date,tank_id,db_mean,radius_m,h_max")
                       .eq("terminal_id", tid).range(offset, offset+999).execute().data)
            all_obs.extend(batch)
            if len(batch) < 1000: break
            offset += 1000

        if not all_obs:
            print(f"  no observations — skipping"); continue

        df_t = pd.DataFrame(all_obs)
        df_t["date"] = pd.to_datetime(df_t["date"])
        if "radius_m" in df_t.columns and "radius" not in df_t.columns:
            df_t = df_t.rename(columns={"radius_m": "radius"})

        df_t, _, _ = compute_volumes(df_t)
        daily_t = aggregate_daily(df_t)

        rows = [{"terminal_id": tid, "date": r["day"].strftime("%Y-%m-%d"),
                 "vol_mn_bbl": round(float(r["vol_mn_bbl"]), 3),
                 "aggregation": "daily", "smoothed": False}
                for _, r in daily_t.iterrows()]
        sb.table("terminal_volume_timeseries").upsert(
            rows, on_conflict="terminal_id,date,aggregation,smoothed").execute()
        print(f"  {len(rows)} rows upserted")
        terminal_daily[tid] = daily_t

    except Exception as e:
        print(f"  [!] failed: {e}")

with open("data/_terminal_daily.pkl", "wb") as f:
    pickle.dump(terminal_daily, f)

print(f"\n[compute] Done. Terminals: {list(terminal_daily.keys())}")
