"""
main_script.py — Pipeline orchestrator for Kharg Island SAR processing.

Execution order:
  1. Load cache (local JSON → fallback to Supabase full pull)
  2. Query Sentinel Hub catalog for all descending-orbit scenes since 2014
  3. Identify new dates not yet in cache
  4. For new dates: download SAR imagery, extract per-tank crescent dB means
  5. Save updated raw cache + upsert to Supabase raw_observations
  6. Compute volumes, aggregate daily + weekly
  7. Write data_daily.json and data.json (frontend data files)
  8. Upsert volume_timeseries + refresh kharg_gross_flows in Supabase
  9. Send SAR alert emails (only if new dates were found)
  10. Generate volume chart PNG

Run via GitHub Actions (.github/workflows/monitor.yml):
  - Cron: twice daily (03:00 and 15:00 UTC)
  - Gate job skips the heavy fetch if < REVISIT_GATE_DAYS since last data point
  - force_reset=true input skips cache restore and reprocesses full history

Environment variables required (GitHub Actions secrets):
  CLIENT_ID       — Sentinel Hub / Copernicus Dataspace client ID
  CLIENT_SECRET   — Sentinel Hub client secret
  NOTIFY_SECRET   — Bearer token for /auth/notify-sar email endpoint
"""

import sys
import os
import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")

from config import (
    sb, RAW_CACHE_PATH, DAILY_PATH, WEEKLY_PATH, PLOT_PATH,
    RESOLUTION, BUFFER_METERS, EWMA_ALPHA,
)
from tanks import raw_tanks, get_super_bbox
from sar_fetch import load_cache, get_all_scene_dates, build_crescent_masks, fetch_new_records
from volume import compute_volumes, aggregate_daily, aggregate_weekly
from export_data import (
    save_raw_cache, upsert_raw_observations,
    save_daily_json, save_weekly_json, refresh_gross_flows,
)
from notify import send_sar_alert

os.makedirs("public", exist_ok=True)

print(f"Loaded {len(raw_tanks)} tanks.")
super_bbox = get_super_bbox(raw_tanks, buffer_m=BUFFER_METERS)

# ── 1. Load cache ─────────────────────────────────────────────────────────────
cached_records, cached_dates = load_cache(RAW_CACHE_PATH, sb)

# ── 2–3. Catalog query + new date detection ───────────────────────────────────
all_dates = get_all_scene_dates(super_bbox)
new_dates  = [d for d in all_dates if d not in cached_dates]
print(f"Total: {len(all_dates)} | Cached: {len(cached_dates)} | New: {len(new_dates)}")

# ── 4–5. Fetch new passes ──────────────────────────────────────────────────────
if new_dates:
    # Image dimensions derived from bbox size and pixel resolution
    width_m    = (super_bbox.max_x - super_bbox.min_x) * 111320 * np.cos(np.deg2rad(raw_tanks[0]["lat"]))
    height_m   = (super_bbox.max_y - super_bbox.min_y) * 111320
    img_width  = int(width_m  / RESOLUTION)
    img_height = int(height_m / RESOLUTION)

    tanks_data  = build_crescent_masks(raw_tanks, super_bbox, img_width, img_height)
    new_records = fetch_new_records(new_dates, tanks_data, super_bbox, img_width, img_height)

    if new_records:
        cached_records.extend(new_records)
        save_raw_cache(cached_records, RAW_CACHE_PATH)
        upsert_raw_observations(new_records, sb)

if not cached_records:
    print("CRITICAL: No Sentinel-1 imagery was successfully processed!")
    sys.exit(1)

# ── 6. Compute volumes ────────────────────────────────────────────────────────
df = pd.DataFrame(cached_records)
df["date"] = pd.to_datetime(df["date"])
df, dB_min, dB_max = compute_volumes(df)

daily_total  = aggregate_daily(df)
weekly_total = aggregate_weekly(df)

# ── 7–8. Export data files + Supabase ────────────────────────────────────────
save_daily_json(daily_total,  DAILY_PATH,  sb)
save_weekly_json(weekly_total, WEEKLY_PATH, sb)
refresh_gross_flows(sb)

print("\nPipeline complete!")

# ── 9. SAR alert emails ───────────────────────────────────────────────────────
send_sar_alert(daily_total, new_dates)

# ── 10. Volume chart ──────────────────────────────────────────────────────────
plt.figure(figsize=(14, 7))
plt.plot(weekly_total["week_start"], weekly_total["vol_mn_bbl"],
         marker=".", linestyle="", color="gray", alpha=0.4, label="Raw Weekly Extractions")
plt.plot(weekly_total["week_start"], weekly_total["smoothed_vol"],
         marker="s", linestyle="-", color="indigo", linewidth=3,
         label=f"Inverse Production Model (EWMA α={EWMA_ALPHA})")
plt.title(
    f"Production Output: Kharg Island Storage ({len(raw_tanks)} Tanks)\n"
    f"Inverse Crescent Radiometry (EWMA α={EWMA_ALPHA})",
    fontsize=16,
)
plt.xlabel("Date", fontsize=13)
plt.ylabel("Total Active Volume (Mn. Barrels)", fontsize=13)
plt.grid(True, linestyle="--", alpha=0.5)
max_cap = sum(np.pi * t["radius"]**2 * t["h_max"] * 6.2898 for t in raw_tanks) / 1_000_000
plt.axhline(y=max_cap, color="r", linestyle="--", linewidth=1, label=f"Max Capacity (~{max_cap:.1f} M)")
plt.legend(fontsize=12, loc="upper right")
plt.tight_layout()
plt.savefig(PLOT_PATH)
