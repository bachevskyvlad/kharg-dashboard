"""
ingest.py — SAR acquisition: Sentinel Hub → raw_observations / terminal_observations.

Step 1 of 3 in the pipeline (ingest → compute → export).
Fetches new SAR scenes for Kharg and all terminals; saves to Supabase.

Args:
  --force      Re-fetch all history from 2014 (ignore existing dates).
               Use when new tanks are added to an existing terminal.
  --terminal   'all' (default), 'kharg', or a specific terminal id.
"""

import sys
import os
import json
import argparse
import datetime
import numpy as np
import warnings

warnings.filterwarnings("ignore")

import pandas as pd
from config import sb, RESOLUTION, BUFFER_METERS, START_DATE
from locations import raw_tanks, get_super_bbox, TERMINALS
from sar_fetch import get_all_scene_dates, build_crescent_masks, fetch_new_records
from export_data import upsert_raw_observations

parser = argparse.ArgumentParser()
parser.add_argument("--force", action="store_true",
                    help="Re-fetch all history (ignore existing dates)")
parser.add_argument("--terminal", default="all",
                    help="Terminal id, 'kharg', or 'all' (default)")
args = parser.parse_args()

os.makedirs("public", exist_ok=True)
os.makedirs("data", exist_ok=True)

summary = {}  # terminal_id → {name, new, country}

run_kharg     = args.terminal in ("all", "kharg")
run_terminals = args.terminal == "all" or (args.terminal not in ("all", "kharg"))
target_tid    = None if args.terminal in ("all", "kharg") else args.terminal


# ── helpers ───────────────────────────────────────────────────────────────────
def _bbox(tanks, buffer_m=300):
    from sentinelhub import BBox, CRS
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min(lats))))
    return BBox([min(lons)-dlon, min(lats)-dlat, max(lons)+dlon, max(lats)+dlat], crs=CRS.WGS84)

def _existing_dates_terminal(tid):
    rows = []; offset = 0
    while True:
        batch = (sb.table("terminal_observations").select("date")
                   .eq("terminal_id", tid).range(offset, offset+999).execute().data)
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return {pd.to_datetime(r["date"]).normalize() for r in rows}


# ── Kharg ─────────────────────────────────────────────────────────────────────
if run_kharg:
    print(f"[ingest] Kharg: {len(raw_tanks)} tanks")
    super_bbox = get_super_bbox(raw_tanks, buffer_m=BUFFER_METERS)

    print("[ingest] Kharg — loading existing dates from Supabase...")
    cached_dates = set()
    if not args.force:
        offset = 0
        while True:
            rows = (sb.table("raw_observations")
                      .select("date")
                      .range(offset, offset + 999)
                      .execute().data)
            for r in rows:
                cached_dates.add(pd.to_datetime(r["date"]).normalize())
            if len(rows) < 1000:
                break
            offset += 1000
    print(f"[ingest] Kharg — {len(cached_dates)} dates already in Supabase{' (force mode)' if args.force else ''}")

    all_dates = get_all_scene_dates(super_bbox)
    new_dates  = [d for d in all_dates if d not in cached_dates]
    print(f"[ingest] Kharg — total: {len(all_dates)} | existing: {len(cached_dates)} | new: {len(new_dates)}")

    if new_dates:
        width_m   = (super_bbox.max_x - super_bbox.min_x) * 111320 * np.cos(np.deg2rad(raw_tanks[0]["lat"]))
        height_m  = (super_bbox.max_y - super_bbox.min_y) * 111320
        img_w = int(width_m  / RESOLUTION)
        img_h = int(height_m / RESOLUTION)

        tanks_data  = build_crescent_masks(raw_tanks, super_bbox, img_w, img_h)
        new_records = fetch_new_records(new_dates, tanks_data, super_bbox, img_w, img_h)

        if new_records:
            upsert_raw_observations(new_records, sb)
            print(f"[ingest] Kharg — {len(new_records)} new records saved")

    if not cached_dates and not new_dates:
        print("CRITICAL: No Kharg data — aborting.")
        sys.exit(1)

    summary["kharg"] = {"name": "Kharg Island", "new": len(new_dates), "country": "IR"}


# ── Terminals ─────────────────────────────────────────────────────────────────
for terminal in TERMINALS:
    tid   = terminal["id"]
    tanks = terminal["tanks"]

    if target_tid and tid != target_tid:
        continue

    print(f"\n[ingest] {terminal['name']} ({tid}) — {len(tanks)} tanks")

    bbox  = _bbox(tanks)
    lat0  = min(t["lat"] for t in tanks)
    img_w = int((bbox.max_x - bbox.min_x) * 111320 * np.cos(np.deg2rad(lat0)) / RESOLUTION)
    img_h = int((bbox.max_y - bbox.min_y) * 111320 / RESOLUTION)

    existing = set() if args.force else _existing_dates_terminal(tid)

    if args.force:
        catalog_start = START_DATE
    elif existing:
        # look back 14 days from latest known date to catch any delayed scenes
        catalog_start = max(existing).to_pydatetime() - datetime.timedelta(days=14)
    else:
        # new terminal — full history from START_DATE
        catalog_start = START_DATE

    try:
        all_t = get_all_scene_dates(bbox, start_date=catalog_start)
    except Exception as e:
        print(f"  [!] Catalog failed: {e}")
        continue

    new_t = [d for d in all_t if d not in existing]
    print(f"  total: {len(all_t)} | existing: {len(existing)} | new: {len(new_t)}{' (force)' if args.force else ''}")
    summary[tid] = {"name": terminal["name"], "new": len(new_t), "country": terminal.get("country", "")}

    if not new_t:
        continue

    try:
        t_data = build_crescent_masks(tanks, bbox, img_w, img_h)
        raw    = fetch_new_records(new_t, t_data, bbox, img_w, img_h)
        for r in raw:
            r["terminal_id"] = tid
        if raw:
            sb.table("terminal_observations").upsert(
                raw, on_conflict="terminal_id,date,tank_id"
            ).execute()
            print(f"  upserted {len(raw)} rows")
    except Exception as e:
        print(f"  [!] Fetch failed: {e}")

with open("data/ingest_summary.json", "w") as f:
    json.dump(summary, f)

print("\n[ingest] Done.")
