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

from config import sb
from locations import TERMINALS
from volume import compute_volumes, aggregate_daily, aggregate_weekly
from coverage_filter import apply_coverage_filter

os.makedirs("data", exist_ok=True)


# ── Kharg ─────────────────────────────────────────────────────────────────────
print("[compute] Loading Kharg raw_observations from Supabase...")
cached_records = []
offset = 0
while True:
    rows = (sb.table("raw_observations")
              .select("date,tank_id,radius_m,diameter,h_max,db_mean")
              .range(offset, offset + 999)
              .execute().data)
    cached_records.extend(rows)
    if len(rows) < 1000:
        break
    offset += 1000
print(f"[compute] Loaded {len(cached_records)} Kharg records")

if not cached_records:
    print("CRITICAL: No Kharg data in Supabase — run ingest.py first.")
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
                       .select("date,tank_id,db_mean")
                       .eq("terminal_id", tid).range(offset, offset+999).execute().data)
            all_obs.extend(batch)
            if len(batch) < 1000: break
            offset += 1000

        if not all_obs:
            print(f"  no observations — skipping"); continue

        # Build tank geometry lookup — only enabled tanks.
        # Disabled tanks may have rows in terminal_observations from before the whitelist;
        # excluding them here keeps them out of the volume curve without deleting data.
        tank_geom = {t["id"]: {"radius_m": t.get("radius_m", t.get("radius")),
                               "h_max_m":  t.get("h_max_m",  t.get("h_max"))}
                     for t in tanks if t.get("enabled", True)}

        df_t = pd.DataFrame(all_obs)
        df_t["date"] = pd.to_datetime(df_t["date"])
        df_t["radius_m"] = df_t["tank_id"].map(lambda t_id: tank_geom.get(t_id, {}).get("radius_m"))
        df_t["h_max"]    = df_t["tank_id"].map(lambda t_id: tank_geom.get(t_id, {}).get("h_max_m"))
        df_t = df_t.dropna(subset=["radius_m", "h_max"])

        df_t, n_dropped, max_tanks = apply_coverage_filter(df_t, min_share=0.5)
        if n_dropped:
            print(f"  dropping {n_dropped} partial-coverage dates (max_tanks={max_tanks})")

        df_t, _, _ = compute_volumes(df_t)
        daily_t = aggregate_daily(df_t)

        rows = [{"terminal_id": tid, "date": r["day"].strftime("%Y-%m-%d"),
                 "vol_mn_bbl": round(float(r["vol_mn_bbl"]), 3),
                 "aggregation": "daily", "smoothed": False}
                for _, r in daily_t.iterrows()]
        # Delete existing rows before re-inserting so partial-coverage rows
        # that were excluded by the tank-count filter are removed.
        sb.table("terminal_volume_timeseries").delete().eq(
            "terminal_id", tid).eq("aggregation", "daily").execute()
        sb.table("terminal_volume_timeseries").insert(rows).execute()
        print(f"  {len(rows)} rows inserted (replaced)")
        terminal_daily[tid] = daily_t

    except Exception as e:
        print(f"  [!] failed: {e}")

with open("data/_terminal_daily.pkl", "wb") as f:
    pickle.dump(terminal_daily, f)

print(f"\n[compute] Done. Terminals: {list(terminal_daily.keys())}")
