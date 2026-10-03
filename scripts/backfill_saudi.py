"""
backfill_saudi.py — Stage 3: full history rebuild for Saudi terminals.

Generalised version of backfill_ras_tanura.py. Supports ras_tanura, juwaymah,
and yanbu. Uses allowed_orbits from tank_registry — the same orbit config
consumed by the regular ingest path.

Usage:
  # Dry-run (print summary, no DB writes):
  python scripts/backfill_saudi.py --terminal ras_tanura
  python scripts/backfill_saudi.py --terminal juwaymah
  python scripts/backfill_saudi.py --terminal yanbu

  # Commit (DELETE old rows + bulk INSERT):
  python scripts/backfill_saudi.py --terminal ras_tanura --commit

  # Resume after crash (checkpoint file per terminal):
  python scripts/backfill_saudi.py --terminal ras_tanura --reset  # wipe and restart

Options:
  --terminal   Terminal ID (ras_tanura | juwaymah | yanbu)
  --from       Start date ISO-8601 (default 2017-01-01)
  --commit     Write to Supabase after dry-run summary
  --reset      Wipe checkpoint and restart from scratch

Gate chain:
  Backup must exist in backups/saudi_pre_rebuild_*/  before --commit is used.
  The script does NOT enforce this — caller's responsibility (Stage 0 gate).

Security: only writes to terminal_id = --terminal value.
Kharg (raw_observations / volume_timeseries) is never touched.
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

# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------
SUPABASE_URL     = os.environ.get("SUPABASE_URL")     or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY     = os.environ.get("SUPABASE_KEY")     or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")        or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET")    or ""

SAUDI_TERMINALS  = {"ras_tanura", "juwaymah", "yanbu"}
DEFAULT_FROM     = datetime.datetime(2017, 1, 1)
MIN_VALID_FRAC   = 0.5   # pixel guard: ≥50% of crescent mask pixels must be valid
BATCH_SLEEP_S    = 0.5
MAX_RETRIES      = 4
RETRY_BASE_S     = 2
_CHUNK           = 500   # rows per Supabase insert call

os.environ.setdefault("CLIENT_ID",     SH_CLIENT_ID)
os.environ.setdefault("CLIENT_SECRET", SH_CLIENT_SECRET)

# ---------------------------------------------------------------------------
# sentinelhub bootstrap
# ---------------------------------------------------------------------------
from sentinelhub import SHConfig, BBox, CRS, DataCollection

sh_config = SHConfig()
sh_config.sh_client_id     = SH_CLIENT_ID
sh_config.sh_client_secret = SH_CLIENT_SECRET
sh_config.sh_base_url      = "https://sh.dataspace.copernicus.eu"
sh_config.sh_token_url     = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE"
    "/protocol/openid-connect/token"
)

_S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_backfill", service_url="https://sh.dataspace.copernicus.eu"
)

sys.path.insert(0, os.path.dirname(__file__))
from sar_fetch import get_all_scene_dates, build_crescent_masks, fetch_new_records
from locations import TERMINALS
from config import DB_FLOOR


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _bbox(tank_list, buffer_m=300):
    lats = [t["lat"] for t in tank_list]
    lons = [t["lon"] for t in tank_list]
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min(lats))))
    return BBox(
        [min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat],
        crs=CRS.WGS84,
    )


def _checkpoint_path(terminal_id):
    return f".backfill_checkpoint_{terminal_id}.json"


def _load_checkpoint(terminal_id):
    path = _checkpoint_path(terminal_id)
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        return set(data.get("done_dates", []))
    return set()


def _save_checkpoint(terminal_id, done_dates):
    path = _checkpoint_path(terminal_id)
    with open(path, "w") as f:
        json.dump(
            {"done_dates": sorted(done_dates),
             "updated": datetime.datetime.utcnow().isoformat()},
            f,
        )


def _bulk_insert(sb, rows):
    for i in range(0, len(rows), _CHUNK):
        chunk = rows[i : i + _CHUNK]
        sb.table("terminal_observations").insert(chunk).execute()


# ---------------------------------------------------------------------------
# per-tank dB step distribution (quality metric)
# ---------------------------------------------------------------------------

def _median_step(rows):
    """Median |Δ db_mean| between consecutive observations per tank."""
    if not rows:
        return np.nan
    df = pd.DataFrame(rows)[["tank_id", "date", "db_mean"]].dropna()
    steps = []
    for _, grp in df.groupby("tank_id"):
        vals = grp.sort_values("date")["db_mean"].values
        if len(vals) >= 2:
            steps.extend(np.abs(np.diff(vals)).tolist())
    return float(np.median(steps)) if steps else np.nan


# ---------------------------------------------------------------------------
# dry-run summary
# ---------------------------------------------------------------------------

def _print_summary(terminal_id, all_dates, n_scenes, n_skipped, all_rows, allowed_orbit):
    df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame(
        columns=["date", "tank_id", "terminal_id", "relative_orbit", "orbit_direction", "db_mean"]
    )
    rel_orbit = allowed_orbit["relative_orbit"]

    print("\n" + "=" * 65)
    print(f"DRY-RUN SUMMARY — {terminal_id}")
    print("=" * 65)
    print(f"  Orbit              : {allowed_orbit['direction']} {rel_orbit}, "
          f"los_az={allowed_orbit['los_azimuth']}°")
    print(f"  Scenes in catalog  : {len(all_dates)}")
    print(f"  Scenes processed   : {n_scenes}")
    print(f"  Scenes skipped(err): {n_skipped}")
    print(f"  Rows prepared      : {len(all_rows)}")

    if len(df) > 0:
        print(f"  Distinct dates     : {df['date'].nunique()}")
        print(f"  Distinct tanks     : {df['tank_id'].nunique()}")
        print(f"  Date range         : {df['date'].min()} → {df['date'].max()}")

        tanks_per_date = df.groupby("date")["tank_id"].count()
        print(f"  Median tanks/date  : {tanks_per_date.median():.0f}")
        print(f"  Min / Max          : {tanks_per_date.min()} / {tanks_per_date.max()}")

        print("\n  Tanks-per-date histogram:")
        n_tanks_total = df["tank_id"].nunique()
        buckets = [(1, 1), (2, 5), (6, max(6, n_tanks_total // 3)),
                   (max(6, n_tanks_total // 3) + 1, n_tanks_total - 1),
                   (n_tanks_total, n_tanks_total), (n_tanks_total + 1, 999)]
        for lo, hi in buckets:
            if lo > hi:
                continue
            n = int(((tanks_per_date >= lo) & (tanks_per_date <= hi)).sum())
            if n == 0:
                continue
            label = f"{lo}" if lo == hi else f"{lo}–{hi}"
            bar = "#" * min(n, 50)
            print(f"    [{label:>8}] {n:4d}  {bar}")

        if "relative_orbit" in df.columns:
            bad = int((df["relative_orbit"] != rel_orbit).sum())
            print(f"\n  Wrong-orbit rows   : {bad}  ← expect 0")

        med_step = _median_step(all_rows)
        print(f"  Median |Δ dB| step : {med_step:.2f} dB  "
              f"(target <2.5; Kharg ref 0.5–2.5)")

    print("=" * 65)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal", required=True,
                        choices=sorted(SAUDI_TERMINALS),
                        help="Terminal to backfill")
    parser.add_argument("--from", dest="from_date", default="2017-01-01",
                        help="Start date ISO-8601 (default 2017-01-01)")
    parser.add_argument("--commit", action="store_true",
                        help="Write to Supabase after dry-run summary")
    parser.add_argument("--reset", action="store_true",
                        help="Wipe checkpoint and restart from scratch")
    args = parser.parse_args()

    terminal_id = args.terminal
    backfill_start = datetime.datetime.fromisoformat(args.from_date)

    # resolve terminal config
    terminal = next((t for t in TERMINALS if t["id"] == terminal_id), None)
    if terminal is None:
        print(f"ABORT: {terminal_id} not found in TERMINALS")
        sys.exit(1)

    allowed_orbits = terminal.get("allowed_orbits")
    if not allowed_orbits:
        print(f"ABORT: {terminal_id} has no allowed_orbits in registry.")
        sys.exit(1)
    allowed_orbit = allowed_orbits[0]  # primary orbit only

    rel_orbit = allowed_orbit["relative_orbit"]
    direction = allowed_orbit["direction"]
    los_az    = allowed_orbit["los_azimuth"]

    tanks      = terminal["tanks"]
    clusters   = terminal.get("clusters", [])
    tank_by_id = {t["id"]: t for t in tanks}

    # build fetch clusters
    fetch_clusters = []
    if clusters:
        seen = set()
        for c in clusters:
            c_tanks = [tank_by_id[i] for i in c["tank_ids"] if i in tank_by_id and i not in seen]
            seen.update(t["id"] for t in c_tanks)
            if c_tanks:
                fetch_clusters.append((c["name"], c_tanks))
        unassigned = [t for t in tanks if t["id"] not in seen]
        if unassigned:
            fetch_clusters.append(("default", unassigned))
    else:
        fetch_clusters = [("default", tanks)]

    # checkpoint
    if args.reset:
        cp = _checkpoint_path(terminal_id)
        if os.path.exists(cp):
            os.remove(cp)
            print(f"[backfill:{terminal_id}] Checkpoint reset.")

    done_dates = _load_checkpoint(terminal_id)
    print(f"[backfill:{terminal_id}] Checkpoint: {len(done_dates)} dates already done")

    # ------------------------------------------------------------------
    # Step 1: catalog
    # ------------------------------------------------------------------
    print(f"\n[backfill:{terminal_id}] Catalog {direction} {rel_orbit} "
          f"from {backfill_start.date()} ...")
    all_dates_set = set()
    for c_name, c_tanks in fetch_clusters:
        c_dates = get_all_scene_dates(
            _bbox(c_tanks),
            start_date=backfill_start,
            orbit_state=direction,
            relative_orbit=rel_orbit,
        )
        all_dates_set.update(c_dates)
    all_dates = sorted(all_dates_set)
    print(f"[backfill:{terminal_id}] Catalog: {len(all_dates)} scene dates")

    pending = [d for d in all_dates if d.strftime("%Y-%m-%d") not in done_dates]
    print(f"[backfill:{terminal_id}] Pending: {len(pending)} dates")

    # ------------------------------------------------------------------
    # Step 2: fetch + pixel guard
    # ------------------------------------------------------------------
    all_rows = []
    new_done = set(done_dates)
    n_scenes = 0
    n_skipped = 0

    for d in pending:
        d_str = d.strftime("%Y-%m-%d")
        date_rows = []

        for c_name, c_tanks in fetch_clusters:
            c_bbox = _bbox(c_tanks)
            lat0   = min(t["lat"] for t in c_tanks)
            img_w  = max(10, int((c_bbox.max_x - c_bbox.min_x) * 111320
                                 * np.cos(np.deg2rad(lat0)) / 5))
            img_h  = max(10, int((c_bbox.max_y - c_bbox.min_y) * 111320 / 5))
            t_data = build_crescent_masks(c_tanks, c_bbox, img_w, img_h,
                                          los_azimuth=los_az)

            for attempt in range(MAX_RETRIES):
                try:
                    raw = fetch_new_records(
                        [d], t_data, c_bbox, img_w, img_h,
                        min_valid_fraction=MIN_VALID_FRAC,
                    )
                    date_rows.extend(raw)
                    break
                except Exception as e:
                    wait = RETRY_BASE_S * (2 ** attempt)
                    print(f"  [retry {attempt+1}] {d_str} cluster={c_name}: {e} — wait {wait}s")
                    time.sleep(wait)
            else:
                print(f"  [SKIP] {d_str} cluster={c_name}: all retries exhausted")
                n_skipped += 1

        for r in date_rows:
            r["terminal_id"]    = terminal_id
            r["relative_orbit"] = rel_orbit
            r["orbit_direction"] = direction
            if r.get("db_mean") is not None:
                r["db_mean"] = max(r["db_mean"], DB_FLOOR)

        all_rows.extend(date_rows)
        new_done.add(d_str)
        n_scenes += 1

        if n_scenes % 10 == 0:
            _save_checkpoint(terminal_id, new_done)
            print(f"  ... {n_scenes}/{len(pending)} scenes, {len(all_rows)} rows")

        time.sleep(BATCH_SLEEP_S)

    _save_checkpoint(terminal_id, new_done)

    # ------------------------------------------------------------------
    # Step 3: dry-run summary
    # ------------------------------------------------------------------
    _print_summary(terminal_id, all_dates, n_scenes, n_skipped, all_rows, allowed_orbit)

    if not args.commit:
        print(f"\n[backfill:{terminal_id}] Dry-run complete. Pass --commit to write.")
        return

    # ------------------------------------------------------------------
    # Step 4: commit
    # ------------------------------------------------------------------
    if len(all_rows) == 0:
        print(f"\n[backfill:{terminal_id}] ABORT: 0 rows — refusing to delete.")
        sys.exit(1)

    from supabase import create_client
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    print(f"\n[backfill:{terminal_id}] Deleting existing rows ...")
    del_resp = (
        sb.table("terminal_observations")
        .delete()
        .eq("terminal_id", terminal_id)
        .execute()
    )
    deleted = len(del_resp.data) if del_resp.data else "?"
    print(f"  Deleted {deleted} old rows.")

    print(f"[backfill:{terminal_id}] Inserting {len(all_rows)} rows ...")
    try:
        _bulk_insert(sb, all_rows)
        print(f"[backfill:{terminal_id}] Insert complete.")
    except Exception as e:
        print(f"\n[backfill:{terminal_id}] INSERT FAILED: {e}")
        print(f"  Table is now empty for {terminal_id}.")
        print(f"  Restore from: backups/saudi_pre_rebuild_*/  {terminal_id}/")
        sys.exit(1)

    print(f"\n[backfill:{terminal_id}] Rebuild committed.")


if __name__ == "__main__":
    main()
