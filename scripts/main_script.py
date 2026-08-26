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
import json
import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")

from config import (
    sb, RAW_CACHE_PATH, DAILY_PATH, WEEKLY_PATH, PLOT_PATH,
    RESOLUTION, BUFFER_METERS, EWMA_ALPHA,
)
from locations import raw_tanks, get_super_bbox
from sar_fetch import load_cache, get_all_scene_dates, build_crescent_masks, fetch_new_records
from volume import compute_volumes, aggregate_daily, aggregate_weekly
from export_data import (
    save_raw_cache, upsert_raw_observations,
    save_daily_json, save_weekly_json, refresh_gross_flows,
)
from notify import send_sar_alert
from locations import TERMINALS

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

print("\nKharg pipeline complete!")

# ── All non-Kharg terminals ───────────────────────────────────────────────────
TERMINAL_MAX_CAP = {
    "lavan":         7.5,
    "siri":          3.2,
    "jask":          9.8,
    "al_flaw":      13.5,
    "halul":         3.5,
    "sitra":         3.7,
    "kozmino":       1.8,
    "de_kastri":     1.2,
    "primorsk":      3.8,
    "novorossiysk":  3.2,
    "ras_laffan":   11.8,
    "ras_tanura":   28.5,
    "fujairah":     19.4,
    "mina_ahmadi":   8.4,
    "ruwais":        11.5,
    "das_island":     5.2,
}
TERMINALS_DIR = os.path.join(os.path.dirname(DAILY_PATH), "terminals")
os.makedirs(TERMINALS_DIR, exist_ok=True)

def _terminal_bbox(tanks, buffer_m=300):
    from sentinelhub import BBox, CRS
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min(lats))))
    return BBox([min(lons)-dlon, min(lats)-dlat, max(lons)+dlon, max(lats)+dlat], crs=CRS.WGS84)

def _load_terminal_dates(tid):
    rows = []; offset = 0
    while True:
        batch = (sb.table("terminal_observations").select("date")
                   .eq("terminal_id", tid).range(offset, offset+999).execute().data)
        rows.extend(batch)
        if len(batch) < 1000: break
        offset += 1000
    return {pd.to_datetime(r["date"]).normalize() for r in rows}

def _upsert_terminal_obs(records, tid):
    if not records: return
    sb.table("terminal_observations").upsert(records, on_conflict="terminal_id,date,tank_id").execute()
    print(f"  Supabase terminal_observations: {len(records)} rows ({tid})")

def _upsert_terminal_vol(daily_df, tid):
    if daily_df.empty: return
    rows = [{"terminal_id": tid, "date": r["day"].strftime("%Y-%m-%d"),
             "vol_mn_bbl": round(float(r["vol_mn_bbl"]), 3),
             "aggregation": "daily", "smoothed": False}
            for _, r in daily_df.iterrows()]
    sb.table("terminal_volume_timeseries").upsert(
        rows, on_conflict="terminal_id,date,aggregation,smoothed").execute()
    print(f"  Supabase terminal_volume_timeseries: {len(rows)} rows ({tid})")

def _write_terminal_json(daily_df, tid, name):
    payload = {
        "terminal": tid, "name": name,
        "max_capacity_mb": TERMINAL_MAX_CAP.get(tid),
        "dates":  daily_df["day"].dt.strftime("%Y-%m-%d").tolist(),
        "values": daily_df["vol_mn_bbl"].round(3).tolist(),
    }
    path = os.path.join(TERMINALS_DIR, f"{tid}_daily.json")
    with open(path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"  Wrote {path} ({len(payload['dates'])} rows)")
    return payload

iran_series = {}  # tid → daily_df (for iran_total)

for terminal in TERMINALS:
    tid = terminal["id"]

    tanks = terminal["tanks"]
    print(f"\n{'='*50}\nTerminal: {terminal['name']} ({tid}) — {len(tanks)} tanks")

    bbox = _terminal_bbox(tanks)
    lat0 = min(t["lat"] for t in tanks)
    img_w = int((bbox.max_x - bbox.min_x) * 111320 * np.cos(np.deg2rad(lat0)) / RESOLUTION)
    img_h = int((bbox.max_y - bbox.min_y) * 111320 / RESOLUTION)

    try:
        from sar_fetch import get_all_scene_dates as _get_dates
        all_t_dates = _get_dates(bbox)
    except Exception as e:
        print(f"  [!] Catalog query failed: {e}"); continue

    existing = _load_terminal_dates(tid)
    new_t_dates = [d for d in all_t_dates if d not in existing]
    print(f"  Sentinel Hub: {len(all_t_dates)} total | New: {len(new_t_dates)}")

    new_records = []
    if new_t_dates:
        try:
            t_data = build_crescent_masks(tanks, bbox, img_w, img_h)
            raw = fetch_new_records(new_t_dates, t_data, bbox, img_w, img_h)
            for r in raw:
                r["terminal_id"] = tid
            new_records = raw
            print(f"  Extracted: {len(new_records)} records")
            _upsert_terminal_obs(new_records, tid)
        except Exception as e:
            print(f"  [!] Fetch failed: {e}")

    # Load full history from Supabase for volume computation
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
            # Fall back to just-fetched records if Supabase is empty
            all_obs = [{"date": r["date"], "tank_id": r["tank_id"],
                        "db_mean": r["db_mean"],
                        "radius_m": r.get("radius_m", next((t["radius"] for t in tanks if t["id"]==r["tank_id"]), 40)),
                        "h_max": r.get("h_max", next((t["h_max"] for t in tanks if t["id"]==r["tank_id"]), 14))}
                       for r in new_records]
        df_t = pd.DataFrame(all_obs)
        df_t["date"] = pd.to_datetime(df_t["date"])
        # Rename radius_m → radius for compute_volumes compatibility
        if "radius_m" in df_t.columns and "radius" not in df_t.columns:
            df_t = df_t.rename(columns={"radius_m": "radius"})
        df_t, _, _ = compute_volumes(df_t)
        daily_t = aggregate_daily(df_t)
        _upsert_terminal_vol(daily_t, tid)
        payload = _write_terminal_json(daily_t, tid, terminal["name"])
        iran_series[tid] = daily_t
    except Exception as e:
        print(f"  [!] Volume computation failed: {e}")

# ── Iran Total aggregate (Kharg + Jask + Lavan + Siri) ────────────────────────
IRAN_TERMINAL_IDS = {"lavan", "siri", "jask"}
iran_series_filtered = {tid: df for tid, df in iran_series.items() if tid in IRAN_TERMINAL_IDS}
if iran_series_filtered:
    print("\nBuilding iran_total…")
    kharg_df = daily_total[["day", "vol_mn_bbl"]].rename(columns={"vol_mn_bbl": "kharg"})
    merged = kharg_df.copy()
    for tid, df_t in iran_series_filtered.items():
        tmp = df_t[["day", "vol_mn_bbl"]].rename(columns={"vol_mn_bbl": tid})
        merged = merged.merge(tmp, on="day", how="outer")
    merged = merged.sort_values("day")
    vol_cols = [c for c in merged.columns if c != "day"]
    merged["vol_total"] = merged[vol_cols].sum(axis=1, min_count=1)
    merged = merged.dropna(subset=["vol_total"])
    total_max = 33.2 + sum(TERMINAL_MAX_CAP.get(t, 0) for t in iran_series_filtered)
    iran_total_payload = {
        "terminal": "iran_total", "name": "Iran Total",
        "max_capacity_mb": round(total_max, 1),
        "dates":  merged["day"].dt.strftime("%Y-%m-%d").tolist(),
        "values": merged["vol_total"].round(3).tolist(),
    }
    path = os.path.join(TERMINALS_DIR, "iran_total_daily.json")
    with open(path, "w") as f:
        json.dump(iran_total_payload, f, separators=(",", ":"))
    print(f"  Wrote {path} ({len(iran_total_payload['dates'])} rows)")

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
