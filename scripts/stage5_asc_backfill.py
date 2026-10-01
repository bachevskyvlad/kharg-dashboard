"""
stage5_asc_backfill.py — ASC 101 backfill for ras_tanura.

Fetches Sentinel-1 ASC orbit 101 scenes for all ras_tanura clusters and
upserts them into terminal_observations.

Rules (ТЗ):
  - ASC rows only; existing DESC rows are NEVER touched.
  - Per-orbit calibration is the caller's responsibility (volume.py).
  - Dry-run by default — prints a summary without writing.
  - --execute: backs up existing ras_tanura rows to CSV, then upserts.

Usage:
    cd scripts
    python stage5_asc_backfill.py                   # dry-run (default)
    python stage5_asc_backfill.py --execute         # backup + write
    python stage5_asc_backfill.py --start 2020-01-01  # full history
"""

import argparse
import csv
import datetime
import math
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
SUPABASE_URL     = os.environ.get("SUPABASE_URL")  or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY     = os.environ.get("SUPABASE_KEY")  or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

TERMINAL_ID  = "ras_tanura"
ASC_ORBIT    = 101
ASC_AZ       = 98
RESOLUTION   = 5    # m/px
BUFFER_M     = 300  # catalog search bbox padding
BUFFER_FETCH = 500  # per-tank fetch bbox — matches pilot exactly (img=200×200 @ 5m/px)
MIN_VALID    = 0.5  # pixel validity guard
DB_FLOOR     = -20.0  # floor stored in DB; pipeline reads with -5.0 clip (not changed here)

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "diagnostics", "rt_decomp")
os.makedirs(OUT_DIR, exist_ok=True)

parser = argparse.ArgumentParser()
parser.add_argument("--execute",        action="store_true", help="Actually write to Supabase (default: dry-run)")
parser.add_argument("--start",          default=None,        help="Catalog start date YYYY-MM-DD (default: 14d lookback or 2020-01-01 if no ASC data exists)")
parser.add_argument("--force",          action="store_true", help="Re-fetch all ASC history from --start (ignore existing ASC dates)")
parser.add_argument("--disabled-only",  action="store_true", help="Process only disabled tanks (enabled=False) — for supplemental backfill")
parser.add_argument("--all-tanks",      action="store_true", help="Process all tanks including disabled")
args = parser.parse_args()

DRY_RUN = not args.execute


def _bbox(tanks, buf=BUFFER_M):
    from sentinelhub import BBox, CRS
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buf / 111320
    dlon = buf / (111320 * math.cos(math.radians(min(lats))))
    return BBox([min(lons)-dlon, min(lats)-dlat, max(lons)+dlon, max(lats)+dlat], crs=CRS.WGS84)


def _existing_asc_dates(sb) -> set:
    """Dates already in terminal_observations for ras_tanura ASC."""
    rows = []; offset = 0
    while True:
        batch = (sb.table("terminal_observations")
                   .select("date")
                   .eq("terminal_id", TERMINAL_ID)
                   .eq("orbit_direction", "ASC")
                   .range(offset, offset + 999).execute().data)
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return {pd.to_datetime(r["date"]).normalize() for r in rows}


def _backup_existing(sb, path: str):
    """Dump all ras_tanura rows to CSV before any write."""
    rows = []; offset = 0
    while True:
        batch = (sb.table("terminal_observations")
                   .select("*")
                   .eq("terminal_id", TERMINAL_ID)
                   .range(offset, offset + 999).execute().data)
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    if not rows:
        print(f"  [backup] No existing rows — nothing to back up.")
        return
    keys = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"  [backup] {len(rows)} rows → {path}")


def _build_crescent(tank, bbox, img_w, img_h, az, width_m):
    """Build crescent mask for one tank (half-ring far-side)."""
    from sentinelhub import BBox
    import numpy as np
    Y, X = np.indices((img_h, img_w))
    # pixel coordinates of tank centre
    cx = (tank["lon"] - bbox.min_x) / (bbox.max_x - bbox.min_x) * img_w
    cy = (bbox.max_y - tank["lat"]) / (bbox.max_y - bbox.min_y) * img_h
    dx = np.sin(np.radians(az))
    dy = -np.cos(np.radians(az))
    dist_sq   = (X - cx)**2 + (Y - cy)**2
    dot       = (X - cx)*dx + (Y - cy)*dy
    r_out_px  = tank["radius_m"] / RESOLUTION
    r_in_px   = max(0, tank["radius_m"] - width_m) / RESOLUTION
    return (dist_sq <= r_out_px**2) & (dist_sq >= r_in_px**2) & (dot > 0)


def run_backfill(tanks, clusters, catalog_start):
    from sentinelhub import (SentinelHubCatalog, DataCollection, SentinelHubRequest,
                              MimeType, SHConfig, CRS, BBox)

    sh_cfg = SHConfig()
    sh_cfg.sh_client_id     = SH_CLIENT_ID
    sh_cfg.sh_client_secret = SH_CLIENT_SECRET
    sh_cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"
    sh_cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

    try:
        S1 = DataCollection.SENTINEL1_IW.define_from(
            "s1iw_asc_backfill",
            service_url="https://sh.dataspace.copernicus.eu"
        )
    except ValueError:
        S1 = DataCollection["s1iw_asc_backfill"]

    catalog = SentinelHubCatalog(config=sh_cfg)

    tank_by_id = {t["id"]: t for t in tanks}
    fetch_clusters = []
    for c in clusters:
        c_tanks = [tank_by_id[tid] for tid in c["tank_ids"] if tid in tank_by_id]
        if c_tanks:
            fetch_clusters.append((c["name"], c_tanks))

    # Collect scene dates per cluster
    all_dates_set = set()
    for c_name, c_tanks in fetch_clusters:
        bbox = _bbox(c_tanks)
        search_iter = catalog.search(
            S1,
            bbox=bbox,
            time=(catalog_start.strftime("%Y-%m-%d"), datetime.datetime.utcnow().strftime("%Y-%m-%d")),
            filter="sat:orbit_state = 'ascending'",
            fields={"include": ["properties.datetime", "properties.sat:relative_orbit"], "exclude": []},
        )
        for item in search_iter:
            rel_orbit = item.get("properties", {}).get("sat:relative_orbit")
            if rel_orbit is not None and int(rel_orbit) != ASC_ORBIT:
                continue
            d = pd.to_datetime(item["properties"]["datetime"]).normalize()
            all_dates_set.add(d)

    all_dates = sorted(all_dates_set)
    print(f"  Catalog: {len(all_dates)} ASC scenes ({catalog_start.date()} → today)")
    if not all_dates:
        print("  No ASC scenes found — verify orbit 101 coverage.")
        return []

    from supabase import create_client
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    existing = set() if args.force else _existing_asc_dates(sb)
    new_dates = [d for d in all_dates if d not in existing]
    print(f"  Existing ASC rows: {len(existing)}  New dates: {len(new_dates)}")

    if not new_dates:
        print("  Nothing to fetch — ASC already up to date.")
        return []

    if DRY_RUN:
        print(f"\n  [DRY-RUN] Would fetch {len(new_dates)} dates:")
        for d in new_dates[:10]:
            print(f"    {d.date()}")
        if len(new_dates) > 10:
            print(f"    ... ({len(new_dates)-10} more)")
        n_tanks = sum(len(ct) for _, ct in fetch_clusters)
        print(f"  Would insert up to {len(new_dates) * n_tanks} rows into terminal_observations.")
        print(f"  (per-tank fetch, BUFFER_FETCH={BUFFER_FETCH}m, img={int(BUFFER_FETCH*2/RESOLUTION)}²px — pilot geometry)")
        print(f"\n  Re-run with --execute to write.")
        return []

    # ── Execute path ──────────────────────────────────────────────────────────
    backup_path = os.path.join(OUT_DIR, f"backup_ras_tanura_{datetime.date.today()}.csv")
    _backup_existing(sb, backup_path)

    all_raw = []

    _EVALSCRIPT = """
//VERSION=3
function setup() { return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(s) { return [s.VV]; }
"""

    # Per-tank fetch with individual square bbox (matches pilot geometry exactly).
    # BUFFER_FETCH=500 → img=200×200 @ 5m/px — identical to task7_rt_decomposition pilot.
    img_size = int(BUFFER_FETCH * 2 / RESOLUTION)  # 200

    all_tanks_flat = [t for _, ct in fetch_clusters for t in ct]
    # deduplicate (a tank may appear in multiple clusters)
    seen = set(); unique_tanks = []
    for t in all_tanks_flat:
        if t["id"] not in seen:
            seen.add(t["id"]); unique_tanks.append(t)

    for t in unique_tanks:
        t_bbox = _bbox([t], buf=BUFFER_FETCH)
        mask   = _build_crescent(t, t_bbox, img_size, img_size, ASC_AZ, 15)
        total_px = int(mask.sum())
        if total_px == 0:
            print(f"  [!] {t['id']}: zero-pixel crescent mask — skip")
            continue

        for d in new_dates:
            d_str  = d.strftime("%Y-%m-%d")
            d_next = (d + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            req = SentinelHubRequest(
                evalscript=_EVALSCRIPT,
                input_data=[SentinelHubRequest.input_data(
                    data_collection=S1,
                    time_interval=(d_str, d_next),
                    other_args={
                        "processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN", "demInstance": "COPERNICUS_30"},
                        "dataFilter": {"orbitDirection": "ASCENDING", "relativeOrbitNumber": ASC_ORBIT},
                        "mosaicking": "ORBIT",
                    },
                )],
                responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
                bbox=t_bbox,
                size=(img_size, img_size),
                config=sh_cfg,
            )
            try:
                data = req.get_data()
                if not data:
                    continue
                img = data[0].squeeze()
                pixels_all = img[mask]
                valid      = pixels_all[np.isfinite(pixels_all)]
                valid_frac = len(valid) / total_px
                if valid_frac < MIN_VALID:
                    continue
                if valid.size == 0:
                    continue
                db_mean = float(10 * np.log10(np.clip(np.mean(valid), 1e-10, None)))
                if not np.isfinite(db_mean):
                    continue
                all_raw.append({
                    "terminal_id":     TERMINAL_ID,
                    "date":            d_str,
                    "tank_id":         t["id"],
                    "radius_m":        float(t["radius_m"]),
                    "diameter":        float(t["radius_m"] * 2),
                    "h_max":           float(t["h_max_m"]),
                    "db_mean":         max(db_mean, DB_FLOOR),
                    "relative_orbit":  ASC_ORBIT,
                    "orbit_direction": "ASC",
                })
            except Exception as e:
                print(f"  [!] {t['id']} {d_str}: {e}")

    print(f"\n  Fetched {len(all_raw)} ASC records across {len(new_dates)} dates")
    if all_raw:
        sb.table("terminal_observations").upsert(
            all_raw, on_conflict="terminal_id,date,tank_id"
        ).execute()
        print(f"  Upserted {len(all_raw)} rows.")
    return all_raw


def main():
    from tank_registry import LOCATIONS

    rt_loc   = next(loc for loc in LOCATIONS if loc["id"] == TERMINAL_ID)
    if args.disabled_only:
        tanks = [t for t in rt_loc["tanks"] if not t.get("enabled", True)]
    elif args.all_tanks:
        tanks = rt_loc["tanks"]
    else:
        tanks = [t for t in rt_loc["tanks"] if t.get("enabled", True)]
    clusters = rt_loc.get("clusters", [])

    mode_label = "disabled-only" if args.disabled_only else ("all" if args.all_tanks else "enabled")
    print(f"[ASC Backfill] {TERMINAL_ID}  orbit={ASC_ORBIT}  az={ASC_AZ}°")
    print(f"  Tanks ({mode_label}): {len(tanks)}  Clusters: {len(clusters)}")
    print(f"  Mode: {'DRY-RUN' if DRY_RUN else 'EXECUTE'}")

    # Determine catalog start
    if args.start:
        catalog_start = datetime.datetime.strptime(args.start, "%Y-%m-%d")
    else:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        existing = _existing_asc_dates(sb)
        if existing and not args.force:
            catalog_start = max(existing).to_pydatetime() - datetime.timedelta(days=14)
            print(f"  Existing ASC data — catalog from {catalog_start.date()} (14d lookback)")
        else:
            catalog_start = datetime.datetime(2020, 1, 1)
            print(f"  No existing ASC data — full history from {catalog_start.date()}")

    run_backfill(tanks, clusters, catalog_start)
    print("\n[ASC Backfill] Done.")


if __name__ == "__main__":
    main()
