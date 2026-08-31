"""
ingest.py — SAR acquisition: Sentinel Hub → raw_observations / terminal_observations.

Step 1 of 3 in the pipeline (ingest → compute → export).
Fetches new SAR scenes for Kharg and all terminals; saves to local cache
and upserts to Supabase. No volume computation here.
"""

import sys
import os
import numpy as np
import warnings

warnings.filterwarnings("ignore")

from config import sb, RAW_CACHE_PATH, RESOLUTION, BUFFER_METERS
from locations import raw_tanks, get_super_bbox, TERMINALS
from sar_fetch import load_cache, get_all_scene_dates, build_crescent_masks, fetch_new_records
from export_data import save_raw_cache, upsert_raw_observations

os.makedirs("public", exist_ok=True)


# ── Kharg ─────────────────────────────────────────────────────────────────────
print(f"[ingest] Kharg: {len(raw_tanks)} tanks")
super_bbox = get_super_bbox(raw_tanks, buffer_m=BUFFER_METERS)

cached_records, cached_dates = load_cache(RAW_CACHE_PATH, sb)

all_dates = get_all_scene_dates(super_bbox)
new_dates  = [d for d in all_dates if d not in cached_dates]
print(f"[ingest] Kharg — total: {len(all_dates)} | cached: {len(cached_dates)} | new: {len(new_dates)}")

if new_dates:
    width_m   = (super_bbox.max_x - super_bbox.min_x) * 111320 * np.cos(np.deg2rad(raw_tanks[0]["lat"]))
    height_m  = (super_bbox.max_y - super_bbox.min_y) * 111320
    img_w = int(width_m  / RESOLUTION)
    img_h = int(height_m / RESOLUTION)

    tanks_data  = build_crescent_masks(raw_tanks, super_bbox, img_w, img_h)
    new_records = fetch_new_records(new_dates, tanks_data, super_bbox, img_w, img_h)

    if new_records:
        cached_records.extend(new_records)
        save_raw_cache(cached_records, RAW_CACHE_PATH)
        upsert_raw_observations(new_records, sb)
        print(f"[ingest] Kharg — {len(new_records)} new records saved")

if not cached_records:
    print("CRITICAL: No Kharg data — aborting.")
    sys.exit(1)


# ── Terminals ─────────────────────────────────────────────────────────────────
def _bbox(tanks, buffer_m=300):
    from sentinelhub import BBox, CRS
    import pandas as pd
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min(lats))))
    return BBox([min(lons)-dlon, min(lats)-dlat, max(lons)+dlon, max(lats)+dlat], crs=CRS.WGS84)

def _existing_dates(tid):
    import pandas as pd
    rows = []; offset = 0
    while True:
        batch = (sb.table("terminal_observations").select("date")
                   .eq("terminal_id", tid).range(offset, offset+999).execute().data)
        rows.extend(batch)
        if len(batch) < 1000: break
        offset += 1000
    return {pd.to_datetime(r["date"]).normalize() for r in rows}

for terminal in TERMINALS:
    tid   = terminal["id"]
    tanks = terminal["tanks"]
    print(f"\n[ingest] {terminal['name']} ({tid}) — {len(tanks)} tanks")

    bbox  = _bbox(tanks)
    lat0  = min(t["lat"] for t in tanks)
    img_w = int((bbox.max_x - bbox.min_x) * 111320 * np.cos(np.deg2rad(lat0)) / RESOLUTION)
    img_h = int((bbox.max_y - bbox.min_y) * 111320 / RESOLUTION)

    try:
        all_t = get_all_scene_dates(bbox)
    except Exception as e:
        print(f"  [!] Catalog failed: {e}"); continue

    new_t = [d for d in all_t if d not in _existing_dates(tid)]
    print(f"  total: {len(all_t)} | new: {len(new_t)}")

    if not new_t:
        continue

    try:
        t_data = build_crescent_masks(tanks, bbox, img_w, img_h)
        raw    = fetch_new_records(new_t, t_data, bbox, img_w, img_h)
        for r in raw:
            r["terminal_id"] = tid
        if raw:
            sb.table("terminal_observations").upsert(raw, on_conflict="terminal_id,date,tank_id").execute()
            print(f"  upserted {len(raw)} rows")
    except Exception as e:
        print(f"  [!] Fetch failed: {e}")

print("\n[ingest] Done.")
