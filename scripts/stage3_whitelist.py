"""
stage3_whitelist.py — Empirical tank whitelist via SAR signal quality.

For all 97 Saudi tanks (JW=19, RT=66, YB=12), fetches N_SCENES most-recent SAR
passes with dataFilter/mosaicking ORBIT (same pipeline as production ingest) and
computes per-tank step = median |db[i] - db[i-1]|.

Threshold: step < STEP_THRESH → whitelist; else → excluded.

No Supabase writes. Output:
  diagnostics/stage3/whitelist_report.txt
  diagnostics/stage3/whitelist_results.json

Venue verdict: for each terminal, sum capacity (vol_max) of whitelist tanks /
total capacity. ≥ 60% → terminal-series eligible; else → tank-level or drop.

Usage:
    python scripts/stage3_whitelist.py
"""

import os
import sys
import json
import math
import datetime
import warnings
import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

try:
    from supabase import create_client as _sc
except ImportError:
    pass

from sentinelhub import SHConfig, BBox, CRS, DataCollection

config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID")     or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse", service_url="https://sh.dataspace.copernicus.eu"
)

from sar_fetch import fetch_new_records, build_crescent_masks, get_all_scene_dates
from config import RESOLUTION, BUFFER_METERS, DB_FLOOR
from tank_registry import LOCATIONS

N_SCENES    = 18    # most-recent SAR passes per terminal
STEP_THRESH = 2.5   # dB — below this → whitelist
BUFFER      = 500   # metres around single tank for bbox
BARREL_PER_M3 = 6.2898

SAUDI_IDS = {"juwaymah", "ras_tanura", "yanbu"}


def _vol_max(tank):
    """Max theoretical volume in Mb for a single tank."""
    r = tank.get("radius_m", tank.get("radius", 0))
    h = tank.get("h_max_m", tank.get("h_max", 0))
    return math.pi * r**2 * h * BARREL_PER_M3 / 1e6


def _single_tank_bbox(lat, lon, buffer=BUFFER):
    d_lat = buffer / 111320
    d_lon = buffer / (111320 * math.cos(math.radians(lat)))
    return BBox(bbox=(lon - d_lon, lat - d_lat, lon + d_lon, lat + d_lat), crs=CRS.WGS84)


def _catalog_dates(orbit_cfg, bbox, n):
    """Return n most-recent dates (strings) from Sentinel Hub catalog for orbit."""
    orb     = orbit_cfg[0]
    dir_str = orb["direction"].upper()
    orbit_state = "descending" if dir_str in ("DESC", "DESCENDING") else "ascending"
    rel_orbit   = orb.get("relative_orbit")

    all_dates = get_all_scene_dates(bbox, orbit_state=orbit_state, relative_orbit=rel_orbit)
    dates = sorted(all_dates, reverse=True)
    return dates[:n]


def _fetch_tank_step(tank, orbit_cfg, dates):
    """Fetch db_mean series for one tank, return step."""
    lat = tank["lat"]
    lon = tank["lon"]
    r   = tank.get("radius_m", tank.get("radius"))
    h   = tank.get("h_max_m",  tank.get("h_max"))
    los = orbit_cfg[0].get("los_azimuth", 282)

    bbox  = _single_tank_bbox(lat, lon)
    w_m   = BUFFER * 2
    img_w = max(20, int(w_m / RESOLUTION))
    img_h = img_w

    t = {
        "id":       tank["id"],
        "lat":      lat,
        "lon":      lon,
        "radius":   r,
        "radius_m": r,
        "diameter": r * 2,
        "h_max":    h,
    }
    tanks_data = build_crescent_masks([t], bbox, img_w, img_h, los_azimuth=los)
    records    = fetch_new_records(dates, tanks_data, bbox, img_w, img_h,
                                   orbit_cfg=orbit_cfg)

    series = []
    for rec in records:
        if rec.get("db_mean") is not None:
            db = max(rec["db_mean"], DB_FLOOR)
            series.append((rec["date"], db))
    series.sort(key=lambda x: x[0])

    if len(series) < 2:
        return float("nan"), len(series)
    diffs = [abs(series[i][1] - series[i-1][1]) for i in range(1, len(series))]
    return float(np.median(diffs)), len(series)


def main():
    os.makedirs("diagnostics/stage3", exist_ok=True)

    # Collect Saudi terminals
    saudi_locs = [loc for loc in LOCATIONS if loc["id"] in SAUDI_IDS]

    all_results   = []
    venue_verdicts = {}

    for loc in saudi_locs:
        tid      = loc["id"]
        orbit_cfg = loc["allowed_orbits"]
        if not orbit_cfg:
            print(f"[WARN] {tid}: no allowed_orbits in registry, skipping")
            continue

        tanks = loc["tanks"]
        print(f"\n{'='*60}")
        print(f"Terminal: {tid}  ({len(tanks)} tanks)")

        # Get catalog dates once per terminal (use bounding box of first tank)
        first = tanks[0]
        ref_bbox = _single_tank_bbox(first["lat"], first["lon"], buffer=2000)
        print(f"  Fetching catalog dates (last {N_SCENES} passes)...")
        dates = _catalog_dates(orbit_cfg, ref_bbox, N_SCENES)
        print(f"  Dates ({len(dates)}): {dates[0]} … {dates[-1]}")

        tank_rows = []
        wl_cap = 0.0
        total_cap = 0.0

        for tank in tanks:
            cap = _vol_max(tank)
            total_cap += cap

            print(f"  {tank['id']:12s} ", end="", flush=True)
            try:
                step, n_obs = _fetch_tank_step(tank, orbit_cfg, dates)
            except Exception as exc:
                print(f"ERROR: {exc}")
                step, n_obs = float("nan"), 0

            whitelist = (not math.isnan(step)) and (step < STEP_THRESH)
            if whitelist:
                wl_cap += cap

            status = "whitelist" if whitelist else ("excluded" if not math.isnan(step) else "no_data")
            flag   = "✓" if whitelist else "✗"
            step_s = f"{step:.3f}" if not math.isnan(step) else "nan"
            print(f"step={step_s:6s}  n={n_obs:2d}  {flag} {status}")

            tank_rows.append({
                "terminal": tid,
                "tank_id":  tank["id"],
                "step":     step if not math.isnan(step) else None,
                "n_obs":    n_obs,
                "whitelist": whitelist,
                "status":   status,
                "vol_max_mb": round(cap, 4),
            })

        pct_cap = (wl_cap / total_cap * 100) if total_cap > 0 else 0
        n_wl    = sum(1 for r in tank_rows if r["whitelist"])
        verdict = "TERMINAL_SERIES" if pct_cap >= 60 else "TANK_LEVEL_OR_DROP"

        print(f"\n  Whitelist: {n_wl}/{len(tanks)} tanks, "
              f"{wl_cap:.2f}/{total_cap:.2f} Mb capacity ({pct_cap:.1f}%)")
        print(f"  Venue verdict: {verdict}")

        venue_verdicts[tid] = {
            "n_tanks": len(tanks),
            "n_whitelist": n_wl,
            "wl_cap_mb": round(wl_cap, 4),
            "total_cap_mb": round(total_cap, 4),
            "pct_cap": round(pct_cap, 1),
            "verdict": verdict,
        }
        all_results.extend(tank_rows)

    # ── Write results ─────────────────────────────────────────────────────────
    payload = {
        "run_date": datetime.datetime.utcnow().isoformat(),
        "n_scenes": N_SCENES,
        "step_thresh": STEP_THRESH,
        "venue_verdicts": venue_verdicts,
        "tanks": all_results,
    }
    json_path = "diagnostics/stage3/whitelist_results.json"
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)

    # ── Text report ───────────────────────────────────────────────────────────
    txt_path = "diagnostics/stage3/whitelist_report.txt"
    lines = []
    lines.append(f"Stage 3 Whitelist Report  {datetime.datetime.utcnow():%Y-%m-%d %H:%M} UTC")
    lines.append(f"N_SCENES={N_SCENES}  STEP_THRESH={STEP_THRESH} dB")
    lines.append("")
    lines.append(f"{'Terminal':<16} {'N':>4} {'WL':>4} {'WL_cap Mb':>10} {'Tot_cap Mb':>11} {'%cap':>6}  Verdict")
    lines.append("-" * 70)
    for tid, v in venue_verdicts.items():
        lines.append(
            f"{tid:<16} {v['n_tanks']:>4} {v['n_whitelist']:>4} "
            f"{v['wl_cap_mb']:>10.2f} {v['total_cap_mb']:>11.2f} "
            f"{v['pct_cap']:>5.1f}%  {v['verdict']}"
        )
    lines.append("")
    lines.append(f"{'Terminal':<16} {'Tank':>12} {'step dB':>8} {'n_obs':>6}  Status")
    lines.append("-" * 60)
    for r in all_results:
        step_s = f"{r['step']:.3f}" if r["step"] is not None else "  nan"
        lines.append(
            f"{r['terminal']:<16} {r['tank_id']:>12} {step_s:>8} {r['n_obs']:>6}  {r['status']}"
        )

    report = "\n".join(lines)
    with open(txt_path, "w") as f:
        f.write(report)

    print("\n" + "=" * 60)
    print(report)
    print(f"\nResults written to {json_path} and {txt_path}")


if __name__ == "__main__":
    main()
