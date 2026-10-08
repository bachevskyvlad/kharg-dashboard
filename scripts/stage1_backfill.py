"""
stage1_backfill.py — Stage 1 baseline ingest for non-Kharg terminals.

Fetches the full SAR history for a single terminal using its primary
allowed_orbits entry (first entry = DESC primary per orbit-fix), accumulates
rows in terminal_observations, dry-runs by default.

Usage:
  python stage1_backfill.py --terminal jask              # dry-run
  python stage1_backfill.py --terminal jask --commit     # write to Supabase
  python stage1_backfill.py --terminal jask --reset      # wipe checkpoint, restart

Supported terminals: any LOCATIONS entry that has allowed_orbits in registry.
Jask start overridden to 2024-06-01 (construction before; no valid oil signal).

The script re-uses sar_fetch.py infrastructure (catalog → crescent masks →
pixel extraction) exactly as backfill_ras_tanura.py does, so the row format
in terminal_observations is identical.
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

# ── credentials ──────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""

# ── SH config ────────────────────────────────────────────────────────────────
from sentinelhub import SHConfig, BBox, CRS, DataCollection as _DC
sh_config = SHConfig()
sh_config.sh_client_id     = SH_CLIENT_ID
sh_config.sh_client_secret = SH_CLIENT_SECRET
sh_config.sh_base_url      = "https://sh.dataspace.copernicus.eu"
sh_config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

os.environ.setdefault("CLIENT_ID",     SH_CLIENT_ID)
os.environ.setdefault("CLIENT_SECRET", SH_CLIENT_SECRET)

sys.path.insert(0, os.path.dirname(__file__))
from sar_fetch import get_all_scene_dates, build_crescent_masks, fetch_new_records
from locations import TERMINALS
from tank_registry import LOCATIONS
from config import DB_FLOOR

from supabase import create_client
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── per-terminal overrides ────────────────────────────────────────────────────
# Jask: construction before 2024-06-01, no valid oil signal prior
JASK_START = datetime.datetime(2024, 6, 1)
DEFAULT_START = datetime.datetime(2017, 1, 1)

# ── constants ─────────────────────────────────────────────────────────────────
MIN_VALID_FRAC = 0.0   # Stage-1 baseline: no pixel guard (document coverage first)
BATCH_SLEEP_S  = 0.5
MAX_RETRIES    = 4
RETRY_BASE_S   = 2
_CHUNK         = 500


# ── helpers ───────────────────────────────────────────────────────────────────
def _bbox(tanks, buffer_m=500):
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min(lats))))
    return BBox([min(lons)-dlon, min(lats)-dlat, max(lons)+dlon, max(lats)+dlat], crs=CRS.WGS84)


def _ckpt_path(terminal_id):
    return f".stage1_ckpt_{terminal_id}.json"


def _load_checkpoint(terminal_id):
    path = _ckpt_path(terminal_id)
    if os.path.exists(path):
        with open(path) as f:
            return set(json.load(f).get("done_dates", []))
    return set()


def _save_checkpoint(terminal_id, done_dates):
    with open(_ckpt_path(terminal_id), "w") as f:
        json.dump({"terminal_id": terminal_id,
                   "done_dates": sorted(done_dates),
                   "updated": datetime.datetime.utcnow().isoformat()}, f)


def _bulk_insert(rows):
    for i in range(0, len(rows), _CHUNK):
        sb.table("terminal_observations").insert(rows[i:i+_CHUNK]).execute()


def _to_pipeline_tank(t):
    return {"id": t["id"], "lat": t["lat"], "lon": t["lon"],
            "radius": t["radius_m"], "h_max": t["h_max_m"]}


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal", required=True,
                        help="terminal_id from LOCATIONS (e.g. jask, lavan, siri)")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--reset",  action="store_true")
    args = parser.parse_args()

    tid = args.terminal

    # Resolve location entry
    loc = next((l for l in LOCATIONS if l["id"] == tid), None)
    if loc is None:
        print(f"ABORT: '{tid}' not found in LOCATIONS")
        sys.exit(1)

    # Must have allowed_orbits
    allowed = loc.get("allowed_orbits")
    if not allowed:
        print(f"ABORT: '{tid}' has no allowed_orbits in registry. Run orbit-fix first.")
        sys.exit(1)

    # Primary orbit = first entry
    primary = allowed[0]
    rel_orbit = primary["relative_orbit"]
    direction = primary["direction"]
    los_az    = primary["los_azimuth"]
    print(f"[stage1] Terminal : {tid}")
    print(f"[stage1] Primary orbit: rel={rel_orbit} {direction} los={los_az}")

    start_dt = JASK_START if tid == "jask" else DEFAULT_START
    print(f"[stage1] Survey start: {start_dt.date()}")

    tanks_raw = [_to_pipeline_tank(t) for t in loc["tanks"]
                 if not t.get("uncertain")]
    print(f"[stage1] Tanks (confirmed): {len(tanks_raw)}")

    if args.reset:
        ckpt = _ckpt_path(tid)
        if os.path.exists(ckpt):
            os.remove(ckpt)
            print(f"[stage1] Checkpoint reset.")

    done_dates = _load_checkpoint(tid)
    print(f"[stage1] Checkpoint: {len(done_dates)} dates already processed")

    # ── Step 1: catalog ───────────────────────────────────────────────────────
    bbox = _bbox(tanks_raw)
    # direction may be "DESC" or "ASCE" in registry; normalize for catalog
    dir_norm = direction.lower()
    if dir_norm == "asce":
        dir_norm = "ascending"
    elif dir_norm == "desc":
        dir_norm = "descending"

    print(f"\n[stage1] Querying catalog: {dir_norm} rel={rel_orbit} from {start_dt.date()} …")
    all_dates = get_all_scene_dates(
        bbox,
        start_date=start_dt,
        orbit_state=dir_norm,
        relative_orbit=rel_orbit,
    )
    print(f"[stage1] Catalog: {len(all_dates)} scene dates")

    pending = [d for d in all_dates if d.strftime("%Y-%m-%d") not in done_dates]
    print(f"[stage1] Pending (not yet processed): {len(pending)} dates")
    if pending:
        print(f"[stage1] Date range: {pending[0].date()} → {pending[-1].date()}")

    # ── Step 2: fetch + extract ───────────────────────────────────────────────
    img_w = int((bbox.max_x - bbox.min_x) * 111320 * np.cos(np.deg2rad(min(t["lat"] for t in tanks_raw))) / 5)
    img_h = int((bbox.max_y - bbox.min_y) * 111320 / 5)
    tanks_data = build_crescent_masks(tanks_raw, bbox, img_w, img_h, los_azimuth=los_az)

    all_rows  = []
    new_done  = set(done_dates)
    n_scenes  = 0
    n_skipped = 0

    for d in pending:
        d_str = d.strftime("%Y-%m-%d")
        date_rows = []

        for attempt in range(MAX_RETRIES):
            try:
                raw = fetch_new_records(
                    [d], tanks_data, bbox, img_w, img_h,
                    min_valid_fraction=MIN_VALID_FRAC,
                )
                date_rows.extend(raw)
                break
            except Exception as e:
                wait = RETRY_BASE_S * (2 ** attempt)
                print(f"  [retry {attempt+1}] {d_str}: {e} — wait {wait}s")
                time.sleep(wait)
        else:
            print(f"  [SKIP] {d_str}: all retries exhausted")
            n_skipped += 1

        for r in date_rows:
            r["terminal_id"]     = tid
            r["relative_orbit"]  = rel_orbit
            r["orbit_direction"] = direction
            if r.get("db_mean") is not None:
                r["db_mean"] = max(r["db_mean"], DB_FLOOR)

        all_rows.extend(date_rows)
        new_done.add(d_str)
        n_scenes += 1

        if n_scenes % 10 == 0:
            _save_checkpoint(tid, new_done)
            print(f"  … {n_scenes}/{len(pending)} scenes, {len(all_rows)} rows so far")

        time.sleep(BATCH_SLEEP_S)

    _save_checkpoint(tid, new_done)

    # ── Step 3: dry-run summary ───────────────────────────────────────────────
    df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame(
        columns=["date", "tank_id", "terminal_id", "relative_orbit", "orbit_direction", "db_mean"])

    print("\n" + "="*60)
    print(f"STAGE-1 BASELINE — {tid.upper()}")
    print("="*60)
    print(f"  Orbit                : rel={rel_orbit} {direction}")
    print(f"  Catalog scenes       : {len(all_dates)}")
    print(f"  Scenes processed     : {n_scenes}")
    print(f"  Scenes skipped(err)  : {n_skipped}")
    print(f"  Rows prepared        : {len(all_rows)}")
    if len(df) > 0:
        print(f"  Distinct dates       : {df['date'].nunique()}")
        print(f"  Distinct tanks       : {df['tank_id'].nunique()}")
        print(f"  Date range           : {df['date'].min()} → {df['date'].max()}")
        tpd = df.groupby("date")["tank_id"].count()
        print(f"  Tanks/date (med/min/max): {tpd.median():.0f} / {tpd.min()} / {tpd.max()}")
        # dB distribution
        print(f"  db_mean (p5/p50/p95): "
              f"{df['db_mean'].quantile(.05):.2f} / "
              f"{df['db_mean'].quantile(.5):.2f} / "
              f"{df['db_mean'].quantile(.95):.2f} dB")
    print("="*60)

    if not args.commit:
        print(f"\n[stage1] Dry-run complete for {tid}. Pass --commit to write to Supabase.")
        return

    # ── Step 4: commit ────────────────────────────────────────────────────────
    if len(all_rows) == 0:
        print(f"\n[stage1] ABORT: 0 rows prepared — refusing to overwrite existing data.")
        sys.exit(1)

    existing = sb.table("terminal_observations").select("date", count="exact") \
                 .eq("terminal_id", tid).execute()
    existing_n = existing.count if hasattr(existing, "count") else "?"
    print(f"\n[stage1] Existing rows for {tid}: {existing_n}")
    print(f"[stage1] Deleting existing rows …")
    sb.table("terminal_observations").delete().eq("terminal_id", tid).execute()

    print(f"[stage1] Inserting {len(all_rows)} rows …")
    try:
        _bulk_insert(all_rows)
        print(f"[stage1] Insert complete. {tid} Stage-1 baseline committed.")
    except Exception as e:
        print(f"\n[stage1] INSERT FAILED: {e}")
        print(f"  terminal_observations is now empty for {tid}.")
        sys.exit(1)


if __name__ == "__main__":
    main()
