"""
backfill_rt_v2.py — Stage-1 ingest for ras_tanura_v2.

Loads Sentinel-1 from 2017-01-01 → today for all orbits covering Ras Tanura
(35 DESC + 101 ASC; script also checks catalog for any additional orbits).
LOS azimuth is derived per scene from the sar:heading catalog property
(los = (heading + 90) % 360 for right-looking SAR) — never a constant.

Two geographic clusters (north/south) each get a separate bounding box, so
the small-radius tanks in the dense south group are not blurred by a giant bbox.

Usage:
  python backfill_rt_v2.py --orbit 35   # dry-run for orbit 35
  python backfill_rt_v2.py --orbit 35 --commit
  python backfill_rt_v2.py --orbit 101 --commit
  python backfill_rt_v2.py --all-orbits --commit   # both in sequence
  python backfill_rt_v2.py --orbit 35 --reset      # wipe checkpoint, restart

Writes to terminal_observations with terminal_id = ras_tanura_v2.
"""

import argparse
import datetime
import json
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from sentinelhub import SentinelHubCatalog, BBox, CRS

# ── credentials guard ─────────────────────────────────────────────────────────
for _var in ("CLIENT_ID", "CLIENT_SECRET", "SUPABASE_URL", "SUPABASE_KEY"):
    if not os.environ.get(_var):
        sys.exit(f"FATAL: {_var} env var must be set")

# ── shared infrastructure ─────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from config import config, S1_CDSE, DB_FLOOR, RESOLUTION, WIDTH_WIDE
from sar_fetch import build_crescent_masks, fetch_new_records
from tank_registry import LOCATIONS

from supabase import create_client
_sb_url = os.environ["SUPABASE_URL"]
_sb_key = os.environ["SUPABASE_KEY"]
sb = create_client(_sb_url, _sb_key)

# ── constants ─────────────────────────────────────────────────────────────────
TERMINAL_ID    = "ras_tanura_v2"
BACKFILL_START = datetime.datetime(2017, 1, 1)
MIN_VALID_FRAC = 0.5
BATCH_SLEEP_S  = 0.5
MAX_RETRIES    = 4
RETRY_BASE_S   = 2
_CHUNK         = 500

# ── resolve registry entry ────────────────────────────────────────────────────
_loc = next((l for l in LOCATIONS if l["id"] == TERMINAL_ID), None)
if _loc is None:
    sys.exit(f"ABORT: '{TERMINAL_ID}' not found in LOCATIONS (tank_registry.py)")

_tanks_all = [t for t in _loc["tanks"]]

# ── cluster bboxes ────────────────────────────────────────────────────────────
def _bbox(tanks, buffer_m=400):
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min(lats))))
    return BBox([min(lons) - dlon, min(lats) - dlat,
                 max(lons) + dlon, max(lats) + dlat], crs=CRS.WGS84)


def _build_clusters():
    tank_by_id = {t["id"]: t for t in _tanks_all}
    clusters = []
    seen = set()
    for c in _loc.get("clusters", []):
        c_tanks = [tank_by_id[i] for i in c["tank_ids"]
                   if i in tank_by_id and i not in seen]
        seen.update(t["id"] for t in c_tanks)
        if c_tanks:
            clusters.append((c["name"], c_tanks))
    unassigned = [t for t in _tanks_all if t["id"] not in seen]
    if unassigned:
        clusters.append(("default", unassigned))
    return clusters


CLUSTERS = _build_clusters()


def _to_pipeline_tank(t):
    return {"id": t["id"], "lat": t["lat"], "lon": t["lon"],
            "radius": t["radius_m"], "h_max": t["h_max_m"]}


# ── catalog query WITH heading ────────────────────────────────────────────────
def query_catalog_with_heading(bbox, start_date, orbit_state, relative_orbit):
    """
    Returns list of (date, los_azimuth) tuples.

    LOS is derived from sar:heading per scene:
        los_azimuth = (heading + 90) % 360
    for a right-looking radar.  Fallback if sar:heading absent:
        DESC → 283  (measured multi-pass average, NOT the forbidden 282)
        ASC  → 97
    """
    _fallback = {"descending": 283, "ascending": 97}
    _norm = {"desc": "descending", "asc": "ascending", "asce": "ascending"}
    orbit_api = _norm.get(orbit_state.lower(), orbit_state.lower())

    end_date = datetime.datetime.utcnow()
    catalog = SentinelHubCatalog(config=config)

    stac_filter = f"sat:orbit_state = '{orbit_api}'"
    scenes = list(catalog.search(
        S1_CDSE,
        bbox=bbox,
        time=(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
        filter=stac_filter,
        fields={"include": [
            "properties.datetime",
            "properties.sat:relative_orbit",
            "properties.sat:orbit_state",
            "properties.sar:heading",
        ]},
    ))

    # Deduplicate: one per (orbit, 12h-bucket), keep date + heading
    seen_keys = set()
    deduped = []
    for s in sorted(scenes, key=lambda x: x["properties"]["datetime"]):
        orbit = s["properties"].get("sat:relative_orbit")
        if relative_orbit is not None and orbit != relative_orbit:
            continue
        heading = s["properties"].get("sar:heading")
        dt = pd.to_datetime(s["properties"]["datetime"])
        bucket = (dt - pd.Timedelta(hours=6)).floor("12h")
        key = (orbit, bucket)
        if key not in seen_keys:
            seen_keys.add(key)
            los = round((heading + 90) % 360, 1) if heading is not None \
                else _fallback[orbit_api]
            deduped.append((dt.normalize().tz_localize(None), los))

    # Deduplicate by date (keep first LOS for the day)
    seen_dates = {}
    for d, los in deduped:
        if d not in seen_dates:
            seen_dates[d] = los
    return [(d, seen_dates[d]) for d in sorted(seen_dates)]


# ── checkpoint helpers ─────────────────────────────────────────────────────────
def _ckpt_path(orbit):
    return f".rt_v2_ckpt_orbit{orbit}.json"


def _load_checkpoint(orbit):
    path = _ckpt_path(orbit)
    if os.path.exists(path):
        with open(path) as f:
            d = json.load(f)
        return set(d.get("done_dates", []))
    return set()


def _save_checkpoint(orbit, done_dates):
    with open(_ckpt_path(orbit), "w") as f:
        json.dump({"orbit": orbit,
                   "done_dates": sorted(done_dates),
                   "updated": datetime.datetime.utcnow().isoformat()}, f)


# ── Supabase write helpers ────────────────────────────────────────────────────
def _bulk_insert(rows):
    for i in range(0, len(rows), _CHUNK):
        sb.table("terminal_observations").insert(rows[i:i + _CHUNK]).execute()


# ── single-orbit ingest ───────────────────────────────────────────────────────
def run_orbit(rel_orbit, direction, commit, reset):
    dir_upper = direction.upper()
    print(f"\n{'='*60}")
    print(f"[rt_v2] Orbit {rel_orbit} {dir_upper}")
    print(f"{'='*60}")

    if reset:
        ckpt = _ckpt_path(rel_orbit)
        if os.path.exists(ckpt):
            os.remove(ckpt)
            print(f"[rt_v2] Checkpoint reset.")

    done_dates = _load_checkpoint(rel_orbit)
    print(f"[rt_v2] Checkpoint: {len(done_dates)} dates already done")

    # Use super-bbox (union of all clusters) for catalog query
    all_tanks_pipeline = [_to_pipeline_tank(t) for t in _tanks_all]
    super_bbox = _bbox(all_tanks_pipeline)

    dir_norm = {"DESC": "descending", "ASCE": "ascending", "ASC": "ascending"}.get(
        dir_upper, dir_upper.lower())

    print(f"[rt_v2] Querying catalog: {dir_norm} rel={rel_orbit} from {BACKFILL_START.date()} …")
    catalog_entries = query_catalog_with_heading(
        super_bbox, BACKFILL_START, dir_norm, rel_orbit)
    print(f"[rt_v2] Catalog: {len(catalog_entries)} scenes")

    pending = [(d, los) for d, los in catalog_entries
               if d.strftime("%Y-%m-%d") not in done_dates]
    print(f"[rt_v2] Pending: {len(pending)}")
    if pending:
        d0, _ = pending[0]
        d1, _ = pending[-1]
        print(f"[rt_v2] Date range: {d0.date()} → {d1.date()}")
        los_vals = [l for _, l in pending]
        print(f"[rt_v2] LOS azimuth range: {min(los_vals):.1f}° – {max(los_vals):.1f}°")

    all_rows = []
    new_done = set(done_dates)
    n_scenes = 0
    n_skipped = 0

    for d, los in pending:
        d_str = d.strftime("%Y-%m-%d")
        date_rows = []

        for c_name, c_tanks in CLUSTERS:
            c_pipeline = [_to_pipeline_tank(t) for t in c_tanks]
            c_bbox = _bbox(c_pipeline)
            lat0 = min(t["lat"] for t in c_tanks)
            img_w = int((c_bbox.max_x - c_bbox.min_x) * 111320
                        * np.cos(np.deg2rad(lat0)) / 5)
            img_h = int((c_bbox.max_y - c_bbox.min_y) * 111320 / 5)

            # Masks rebuilt per scene so LOS is per-scene accurate
            t_data = build_crescent_masks(c_pipeline, c_bbox, img_w, img_h,
                                          los_azimuth=los)

            orbit_cfg = [{"relative_orbit": rel_orbit, "direction": dir_upper}]

            for attempt in range(MAX_RETRIES):
                try:
                    raw = fetch_new_records(
                        [d], t_data, c_bbox, img_w, img_h,
                        min_valid_fraction=MIN_VALID_FRAC,
                        orbit_cfg=orbit_cfg,
                    )
                    date_rows.extend(raw)
                    break
                except Exception as e:
                    wait = RETRY_BASE_S * (2 ** attempt)
                    print(f"  [retry {attempt+1}] {d_str} {c_name}: {e} — wait {wait}s")
                    time.sleep(wait)
            else:
                print(f"  [SKIP] {d_str} {c_name}: all retries exhausted")
                n_skipped += 1

        for r in date_rows:
            r["terminal_id"]     = TERMINAL_ID
            r["relative_orbit"]  = rel_orbit
            r["orbit_direction"] = dir_upper
            r["los_azimuth"]     = los
            if r.get("db_mean") is not None:
                r["db_mean"] = max(r["db_mean"], DB_FLOOR)

        all_rows.extend(date_rows)
        new_done.add(d_str)
        n_scenes += 1

        if n_scenes % 10 == 0:
            _save_checkpoint(rel_orbit, new_done)
            print(f"  … {n_scenes}/{len(pending)} scenes, {len(all_rows)} rows so far")

        time.sleep(BATCH_SLEEP_S)

    _save_checkpoint(rel_orbit, new_done)

    # ── summary ──────────────────────────────────────────────────────────────
    df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame(
        columns=["date", "tank_id", "terminal_id", "relative_orbit",
                 "orbit_direction", "los_azimuth", "db_mean"])

    print(f"\n--- Orbit {rel_orbit} {dir_upper} summary ---")
    print(f"  Catalog scenes    : {len(catalog_entries)}")
    print(f"  Scenes processed  : {n_scenes}")
    print(f"  Scenes skipped    : {n_skipped}")
    print(f"  Rows prepared     : {len(all_rows)}")
    if len(df) > 0:
        print(f"  Distinct dates    : {df['date'].nunique()}")
        print(f"  Distinct tanks    : {df['tank_id'].nunique()}")
        print(f"  Date range        : {df['date'].min()} → {df['date'].max()}")
        tpd = df.groupby("date")["tank_id"].count()
        print(f"  Tanks/date med/min/max: "
              f"{tpd.median():.0f}/{tpd.min()}/{tpd.max()}")
        if "los_azimuth" in df.columns:
            print(f"  LOS azimuths (p5/p95): "
                  f"{df['los_azimuth'].quantile(.05):.1f}° / "
                  f"{df['los_azimuth'].quantile(.95):.1f}°")
        print(f"  db_mean (p5/p50/p95): "
              f"{df['db_mean'].quantile(.05):.2f} / "
              f"{df['db_mean'].quantile(.5):.2f} / "
              f"{df['db_mean'].quantile(.95):.2f} dB")

    if not commit:
        print(f"\n[rt_v2] Dry-run orbit {rel_orbit}. Pass --commit to write.")
        return

    if len(all_rows) == 0:
        print(f"\n[rt_v2] ABORT: 0 rows — refusing to delete existing data.")
        sys.exit(1)

    existing = (sb.table("terminal_observations")
                  .select("date", count="exact")
                  .eq("terminal_id", TERMINAL_ID)
                  .eq("relative_orbit", rel_orbit)
                  .execute())
    existing_n = existing.count if hasattr(existing, "count") else "?"
    print(f"\n[rt_v2] Existing rows orbit {rel_orbit}: {existing_n}")
    print(f"[rt_v2] Deleting …")
    (sb.table("terminal_observations")
       .delete()
       .eq("terminal_id", TERMINAL_ID)
       .eq("relative_orbit", rel_orbit)
       .execute())

    print(f"[rt_v2] Inserting {len(all_rows)} rows …")
    try:
        _bulk_insert(all_rows)
        print(f"[rt_v2] Orbit {rel_orbit} committed.")
    except Exception as e:
        print(f"\n[rt_v2] INSERT FAILED: {e}")
        sys.exit(1)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--orbit", type=int,
                       help="Single relative orbit number (e.g. 35 or 101)")
    group.add_argument("--all-orbits", action="store_true",
                       help="Run all orbits from allowed_orbits in registry")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--reset",  action="store_true")
    args = parser.parse_args()

    allowed = _loc.get("allowed_orbits", [])
    if not allowed:
        sys.exit("ABORT: ras_tanura_v2 has no allowed_orbits in registry")

    if args.all_orbits:
        orbits_to_run = [(o["relative_orbit"], o["direction"]) for o in allowed]
    else:
        entry = next((o for o in allowed if o["relative_orbit"] == args.orbit), None)
        if entry is None:
            valid = [o["relative_orbit"] for o in allowed]
            sys.exit(f"ABORT: orbit {args.orbit} not in allowed_orbits {valid}")
        orbits_to_run = [(entry["relative_orbit"], entry["direction"])]

    for rel_orbit, direction in orbits_to_run:
        run_orbit(rel_orbit, direction, args.commit, args.reset)

    print("\n[rt_v2] Done.")


if __name__ == "__main__":
    main()
