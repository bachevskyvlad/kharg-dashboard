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
from config import sb, RESOLUTION, BUFFER_METERS, START_DATE, DB_FLOOR
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
        clipped = sum(1 for r in new_records if r.get("db_mean") is not None and r["db_mean"] < DB_FLOOR)
        for r in new_records:
            if r.get("db_mean") is not None:
                r["db_mean"] = max(r["db_mean"], DB_FLOOR)
        if new_records:
            print(f"[ingest] Kharg — clipped {clipped}/{len(new_records)} records to DB_FLOOR={DB_FLOOR}")

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

    # Filter disabled tanks — enabled=False tanks are not fetched or written.
    # Absence of the field defaults to True (Kharg path and non-Saudi terminals).
    tanks_enabled = [t for t in tanks if t.get("enabled", True)]
    n_disabled = len(tanks) - len(tanks_enabled)
    print(f"\n[ingest] {terminal['name']} ({tid}) — {len(tanks_enabled)} tanks"
          + (f" ({n_disabled} disabled)" if n_disabled else ""))
    tanks = tanks_enabled

    # Normalise orbit config: terminals may use the new allowed_orbits list or
    # the legacy top-level orbit_state / los_azimuth / relative_orbit keys.
    allowed_orbits = terminal.get("allowed_orbits")
    if not allowed_orbits:
        orbit_state    = terminal.get("orbit_state", "descending")
        los_azimuth    = terminal.get("los_azimuth", None)
        relative_orbit = terminal.get("relative_orbit", None)
        allowed_orbits = [{"relative_orbit": relative_orbit,
                            "direction": orbit_state,
                            "los_azimuth": los_azimuth}]

    existing = set() if args.force else _existing_dates_terminal(tid)

    if args.force:
        catalog_start = START_DATE
    elif existing:
        # look back 14 days from latest known date to catch any delayed scenes
        catalog_start = max(existing).to_pydatetime() - datetime.timedelta(days=14)
    else:
        # new terminal — full history from START_DATE
        catalog_start = START_DATE

    # Build list of (cluster_name, cluster_tanks) to fetch independently.
    # Terminals with a "clusters" key get one bbox per cluster; others get one bbox for all tanks.
    clusters_def = terminal.get("clusters")
    if clusters_def:
        tank_by_id = {t["id"]: t for t in tanks}
        fetch_clusters = []
        seen_ids = set()
        for c in clusters_def:
            c_tanks = [tank_by_id[tid_] for tid_ in c["tank_ids"] if tid_ in tank_by_id]
            overlap = seen_ids & {t["id"] for t in c_tanks}
            if overlap:
                print(f"  [!] Duplicate tank ids across clusters: {overlap} — skipping duplicates")
                c_tanks = [t for t in c_tanks if t["id"] not in seen_ids]
            seen_ids.update(t["id"] for t in c_tanks)
            if c_tanks:
                fetch_clusters.append((c["name"], c_tanks))
        # Any tanks not assigned to a cluster go into a fallback group
        unassigned = [t for t in tanks if t["id"] not in seen_ids]
        if unassigned:
            fetch_clusters.append(("default", unassigned))
    else:
        fetch_clusters = [("default", tanks)]

    # Iterate over each allowed orbit: catalog → deduplicate → fetch.
    # Using min_valid_fraction=0.5 for all terminals with explicit allowed_orbits
    # (guards against edge-of-frame partial illumination like the rt_65 artefact).
    has_orbit_config = terminal.get("allowed_orbits") is not None
    min_valid_frac   = 0.5 if has_orbit_config else 0.0

    all_t_set = set()
    orbit_for_date = {}  # date → (relative_orbit, direction) for tagging

    for orbit_cfg in allowed_orbits:
        rel_orbit  = orbit_cfg.get("relative_orbit")
        direction  = orbit_cfg.get("direction", "descending").lower()
        los_az     = orbit_cfg.get("los_azimuth")

        for c_name, c_tanks in fetch_clusters:
            try:
                c_dates = get_all_scene_dates(
                    _bbox(c_tanks),
                    start_date=catalog_start,
                    orbit_state=direction,
                    relative_orbit=rel_orbit,
                )
                for d in c_dates:
                    all_t_set.add(d)
                    orbit_for_date.setdefault(d, (rel_orbit, direction.upper()))
            except Exception as e:
                print(f"  [!] Catalog failed for cluster '{c_name}' orbit {rel_orbit}: {e}")

    if not all_t_set:
        print(f"  [!] No scene dates found — skipping")
        continue
    all_t = sorted(all_t_set)

    new_t = [d for d in all_t if d not in existing]
    print(f"  total: {len(all_t)} | existing: {len(existing)} | new: {len(new_t)}{' (force)' if args.force else ''}")
    print(f"  allowed orbits: {[o.get('relative_orbit') for o in allowed_orbits]} "
          f"| pixel guard: {int(min_valid_frac*100)}%")
    summary[tid] = {"name": terminal["name"], "new": len(new_t), "country": terminal.get("country", "")}

    if not new_t:
        continue

    all_raw = []
    for orbit_cfg in allowed_orbits:
        rel_orbit  = orbit_cfg.get("relative_orbit")
        direction  = orbit_cfg.get("direction", "descending").lower()
        los_az     = orbit_cfg.get("los_azimuth")

        # Filter new_t to dates that belong to this orbit
        orbit_dates = [d for d in new_t if orbit_for_date.get(d) == (rel_orbit, direction.upper())]
        if not orbit_dates:
            continue

        for c_name, c_tanks in fetch_clusters:
            label = f"  [{c_name}/orbit{rel_orbit}]" if len(fetch_clusters) > 1 else f"  [orbit{rel_orbit}]"
            try:
                c_bbox  = _bbox(c_tanks)
                lat0    = min(t["lat"] for t in c_tanks)
                img_w   = int((c_bbox.max_x - c_bbox.min_x) * 111320 * np.cos(np.deg2rad(lat0)) / RESOLUTION)
                img_h   = int((c_bbox.max_y - c_bbox.min_y) * 111320 / RESOLUTION)
                t_data  = build_crescent_masks(c_tanks, c_bbox, img_w, img_h, los_azimuth=los_az)
                raw     = fetch_new_records(orbit_dates, t_data, c_bbox, img_w, img_h,
                                            min_valid_fraction=min_valid_frac,
                                            orbit_cfg=allowed_orbits)
                # Tag orbit provenance on every record
                for r in raw:
                    r["relative_orbit"]  = rel_orbit
                    r["orbit_direction"] = direction.upper()
                print(f"{label} {len(c_tanks)} tanks × {len(orbit_dates)} dates → {len(raw)} records")
                all_raw.extend(raw)
            except Exception as e:
                print(f"{label} [!] Fetch failed: {e}")

    clipped_t = sum(1 for r in all_raw if r.get("db_mean") is not None and r["db_mean"] < DB_FLOOR)
    for r in all_raw:
        r["terminal_id"] = tid
        if r.get("db_mean") is not None:
            r["db_mean"] = max(r["db_mean"], DB_FLOOR)
    if all_raw:
        print(f"  clipped {clipped_t}/{len(all_raw)} records to DB_FLOOR={DB_FLOOR}")
    if all_raw:
        sb.table("terminal_observations").upsert(
            all_raw, on_conflict="terminal_id,date,tank_id"
        ).execute()
        print(f"  upserted {len(all_raw)} rows total")

with open("data/ingest_summary.json", "w") as f:
    json.dump(summary, f)

print("\n[ingest] Done.")
