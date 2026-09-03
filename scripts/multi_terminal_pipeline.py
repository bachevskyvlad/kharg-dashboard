"""
multi_terminal_pipeline.py — SAR pipeline for Gulf terminals beyond Kharg Island.

Processes: Al Flaw (Iraq), Lavan Island, Siri Island, Jask (Iran).
Each terminal is processed independently with its own bbox and tank set.

Data flow:
  1. For each terminal:
     a. Load existing records from Supabase (filtered by terminal_id)
     b. Query Sentinel Hub for all descending-orbit S1 scenes since 2014
     c. Fetch new dates, extract per-tank crescent dB means
     d. Upsert to terminal_observations in Supabase with terminal_id tag
  2. Volume timeseries written to terminal_volume_timeseries per terminal

Supabase tables used:
  terminal_observations    — same schema as raw_observations + terminal_id column
  terminal_volume_timeseries — date, terminal_id, vol_mn_bbl, aggregation, smoothed

IMPORTANT: Output is NOT written to data/ JSON files and NOT shown in dashboard.
This is a data collection pipeline only. Frontend integration is a separate task.

Run locally:
  python multi_terminal_pipeline.py --terminal al_flaw
  python multi_terminal_pipeline.py --terminal all

Run via GitHub Actions: .github/workflows/terminals.yml
"""

import sys
import argparse
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

from sentinelhub import BBox, CRS, SentinelHubCatalog, SentinelHubRequest, MimeType
from config import config, S1_CDSE, sb, START_DATE, RESOLUTION, BARREL_PER_M3, TANK_HEIGHT_MIN
from locations import TERMINALS
from volume import compute_volumes, aggregate_daily


# ── CLI args ──────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--terminal", default="all", help="Terminal id or 'all'")
parser.add_argument("--dry-run", action="store_true", help="Skip Supabase writes")
parser.add_argument("--force", action="store_true", help="Ignore existing dates, re-fetch all (use when new tanks added)")
args = parser.parse_args()

target_ids = (
    [t["id"] for t in TERMINALS]
    if args.terminal == "all"
    else [args.terminal]
)


# ── Supabase schema check / init ──────────────────────────────────────────────
def ensure_tables():
    """
    Create terminal_observations and terminal_volume_timeseries if they don't exist.
    Uses Supabase REST (no direct DDL) — tables must exist before first run.
    Check Supabase dashboard and run the SQL in setup_terminal_tables.sql if needed.
    """
    try:
        sb.table("terminal_observations").select("terminal_id").limit(1).execute()
        print("  terminal_observations table: OK")
    except Exception as e:
        print(f"  [!] terminal_observations missing or inaccessible: {e}")
        print("      Run setup_terminal_tables.sql in Supabase SQL editor first.")
        sys.exit(1)


# ── Per-terminal processing ───────────────────────────────────────────────────
def get_bbox(tanks, buffer_m=300):
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min(lats))))
    return BBox(
        [min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat],
        crs=CRS.WGS84
    )


def get_scene_dates(bbox):
    """Returns sorted list of all descending-orbit S1 dates covering this bbox."""
    catalog = SentinelHubCatalog(config=config)
    results = list(catalog.search(
        S1_CDSE,
        bbox=bbox,
        time=(START_DATE, pd.Timestamp.now()),
        filter="sat:orbit_state = 'DESCENDING'",
        fields={"include": ["properties.datetime"], "exclude": []}
    ))
    raw_dates = [pd.to_datetime(r["properties"]["datetime"]).normalize().tz_localize(None)
                 for r in results]
    # Deduplicate scenes that straddle UTC midnight (same physical pass)
    raw_dates.sort()
    deduped = []
    for d in raw_dates:
        if not deduped or (d - deduped[-1]).days > 0:
            deduped.append(d)
        # Within 12h of previous = same pass, skip
    return deduped


def load_existing_dates(terminal_id):
    """Loads dates already in Supabase for this terminal."""
    dates = set()
    try:
        offset = 0
        while True:
            rows = (sb.table("terminal_observations")
                      .select("date")
                      .eq("terminal_id", terminal_id)
                      .range(offset, offset + 999)
                      .execute().data)
            for r in rows:
                dates.add(pd.to_datetime(r["date"]).normalize())
            if len(rows) < 1000:
                break
            offset += 1000
        print(f"  Supabase: {len(dates)} existing dates for {terminal_id}")
    except Exception as e:
        print(f"  [!] Could not load existing dates: {e}")
    return dates


def build_crescent_masks(tanks, bbox, img_w, img_h, los_azimuth=282, width_m=15):
    """
    Builds per-tank crescent masks for SAR extraction.
    Same method as sar_fetch.build_crescent_masks but self-contained here.
    """
    from sar_fetch import build_crescent_masks as _build
    # Delegate to the existing implementation — pass tanks in expected format
    return _build(tanks, bbox, img_w, img_h)


def fetch_records_for_dates(dates, tanks, bbox, img_w, img_h, terminal_id, los_azimuth=282):
    """Downloads SAR imagery and extracts per-tank dB means for the given dates."""
    from sar_fetch import build_crescent_masks, fetch_new_records

    tanks_data = build_crescent_masks(tanks, bbox, img_w, img_h)
    raw_records = fetch_new_records(dates, tanks_data, bbox, img_w, img_h)

    # Tag every record with terminal_id
    for r in raw_records:
        r["terminal_id"] = terminal_id

    return raw_records


def upsert_records(records, terminal_id):
    if not records:
        return
    try:
        sb.table("terminal_observations").upsert(
            records, on_conflict="terminal_id,date,tank_id"
        ).execute()
        print(f"  Supabase terminal_observations: {len(records)} records saved ({terminal_id})")
    except Exception as e:
        print(f"  [!] Supabase write failed for {terminal_id}: {e}")


def upsert_volume_timeseries(daily_df, terminal_id):
    if daily_df.empty:
        return
    rows = []
    for _, row in daily_df.iterrows():
        rows.append({
            "terminal_id": terminal_id,
            "date": row["day"].strftime("%Y-%m-%d"),
            "vol_mn_bbl": round(float(row["vol_mn_bbl"]), 3),
            "aggregation": "daily",
            "smoothed": False,
        })
    try:
        sb.table("terminal_volume_timeseries").upsert(
            rows, on_conflict="terminal_id,date,aggregation,smoothed"
        ).execute()
        print(f"  Supabase terminal_volume_timeseries: {len(rows)} rows saved ({terminal_id})")
    except Exception as e:
        print(f"  [!] Volume upsert failed for {terminal_id}: {e}")


# ── Main loop ─────────────────────────────────────────────────────────────────
ensure_tables()

for terminal in TERMINALS:
    if terminal["id"] not in target_ids:
        continue

    tid = terminal["id"]
    tanks = terminal["tanks"]
    print(f"\n{'='*60}")
    print(f"Terminal: {terminal['name']} ({tid}) — {len(tanks)} tanks")

    bbox = get_bbox(tanks)
    all_dates = get_scene_dates(bbox)
    print(f"  Sentinel Hub: {len(all_dates)} total dates since {START_DATE.date()}")

    existing_dates = set() if args.force else load_existing_dates(tid)
    new_dates = [d for d in all_dates if d not in existing_dates]
    print(f"  New dates to process: {len(new_dates)}{' (force mode)' if args.force else ''}")

    if not new_dates:
        print("  No new data — skipping.")
        continue

    # Image dimensions from bbox
    lat0 = min(t["lat"] for t in tanks)
    width_m_geo  = (bbox.max_x - bbox.min_x) * 111320 * np.cos(np.deg2rad(lat0))
    height_m_geo = (bbox.max_y - bbox.min_y) * 111320
    img_w = int(width_m_geo  / RESOLUTION)
    img_h = int(height_m_geo / RESOLUTION)

    print(f"  Image size: {img_w}×{img_h}px at {RESOLUTION}m/px")

    records = fetch_records_for_dates(
        new_dates, tanks, bbox, img_w, img_h, tid,
        los_azimuth=terminal["los_azimuth"]
    )
    print(f"  Extracted: {len(records)} records from {len(new_dates)} dates")

    if not records:
        print("  No records extracted — imagery may not cover this area yet.")
        continue

    if not args.dry_run:
        upsert_records(records, tid)

    # Compute volume for this terminal
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df, _, _ = compute_volumes(df)
    daily = aggregate_daily(df)
    print(f"  Volume range: {daily['vol_mn_bbl'].min():.2f}–{daily['vol_mn_bbl'].max():.2f} Mn bbl")

    if not args.dry_run:
        upsert_volume_timeseries(daily, tid)

print(f"\n{'='*60}")
print("Multi-terminal pipeline complete.")
