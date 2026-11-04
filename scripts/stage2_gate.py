"""
stage2_gate.py — Stage 2 geometry-impact gate.

For each of 10 probe tanks, fetches the 10 most-recent SAR scenes and computes
the median consecutive-pass dB step under both old and new geometry.

No writes to Supabase. Output: diagnostics/stage2/stage2_gate_report.txt
                               diagnostics/stage2/stage2_gate_results.json

Usage:
    python scripts/stage2_gate.py

Probe tanks (hardcoded per ТЗ):
  main:    jw_17, jw_1, rt_35, rt_30
  control: yb_3, yb_6, rt_3, rt_4  (controls: geometry was nearly correct → step unchanged)
  clean:   jw_9, yb_2

Pre-written gate expectations (from ТЗ, frozen before run):
  PASS: ≥1 tank in {jw_17, jw_1, rt_30, jw_9} has drop% ≥ 10%
  Controls: all 4 control tanks have |drop%| < 10%

Physics reminder (inverse crescent radiometry):
  Lower dB = more oil. fill_ratio = (dB_max - db_mean) / (dB_max - dB_min).
  Step = median |db_mean[i] - db_mean[i-1]| across consecutive passes.
  Larger radius → more pixels → more averaging → smaller per-pass noise.
  Geometry change mainly affects: (a) pixel count in crescent, (b) wall geometry.
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

# ── Stub supabase if not installed (not needed here) ─────────────────────────
try:
    from supabase import create_client as _sc
except ImportError:
    pass

from sentinelhub import SHConfig, BBox, CRS, DataCollection, SentinelHubCatalog

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

# ── Probe tank definitions ────────────────────────────────────────────────────
# Each entry: (tank_id, terminal_id, old_geometry, new_geometry, orbit_cfg, role)
# old_geometry / new_geometry: {lat, lon, radius_m, h_max_m, los_azimuth}

PROBE_TANKS = [
    # ── JW main ──────────────────────────────────────────────────────────────
    {
        "id": "jw_17", "terminal": "juwaymah", "role": "main",
        "orbit": [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}],
        "old": {"lat": 26.824515110824244, "lon": 49.985368840304960, "radius_m": 50.0, "h_max_m": 16, "los_azimuth": 282},
        "new": {"lat": 26.82461354,        "lon": 49.98539613,        "radius_m": 65.0, "h_max_m": 18, "los_azimuth": 282},
    },
    {
        "id": "jw_1", "terminal": "juwaymah", "role": "secondary",
        "orbit": [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}],
        "old": {"lat": 26.816115167835957, "lon": 49.985801747261085, "radius_m": 50.0, "h_max_m": 16, "los_azimuth": 282},
        "new": {"lat": 26.81614271,        "lon": 49.98578305,        "radius_m": 55.0, "h_max_m": 17, "los_azimuth": 282},
    },
    {
        "id": "jw_9", "terminal": "juwaymah", "role": "clean",
        "orbit": [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}],
        "old": {"lat": 26.819531458247540, "lon": 49.992284316605510, "radius_m": 50.0, "h_max_m": 16, "los_azimuth": 282},
        "new": {"lat": 26.81955777,        "lon": 49.99228491,        "radius_m": 57.5, "h_max_m": 18, "los_azimuth": 282},
    },
    # ── RT main ───────────────────────────────────────────────────────────────
    {
        "id": "rt_35", "terminal": "ras_tanura", "role": "main",
        "orbit": [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}],
        "old": {"lat": 26.646200394, "lon": 50.154249852, "radius_m": 35.0, "h_max_m": 13, "los_azimuth": 282},
        "new": {"lat": 26.64620085,  "lon": 50.15422885,  "radius_m": 20.0, "h_max_m": 9,  "los_azimuth": 282},
    },
    {
        "id": "rt_30", "terminal": "ras_tanura", "role": "secondary",
        "orbit": [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}],
        "old": {"lat": 26.646641703, "lon": 50.152926255, "radius_m": 35.0, "h_max_m": 13, "los_azimuth": 282},
        "new": {"lat": 26.64659394,  "lon": 50.15295213,  "radius_m": 45.0, "h_max_m": 15, "los_azimuth": 282},
    },
    # ── RT controls (small Δr) ────────────────────────────────────────────────
    {
        "id": "rt_3", "terminal": "ras_tanura", "role": "control",
        "orbit": [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}],
        "old": {"lat": 26.650024, "lon": 50.152807, "radius_m": 60.0, "h_max_m": 18, "los_azimuth": 282},
        "new": {"lat": 26.65001070, "lon": 50.15277696, "radius_m": 56.2, "h_max_m": 17, "los_azimuth": 282},
    },
    {
        "id": "rt_4", "terminal": "ras_tanura", "role": "control",
        "orbit": [{"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}],
        "old": {"lat": 26.652085, "lon": 50.149920, "radius_m": 60.0, "h_max_m": 18, "los_azimuth": 282},
        "new": {"lat": 26.65205187, "lon": 50.14991839, "radius_m": 57.5, "h_max_m": 18, "los_azimuth": 282},
    },
    # ── YB controls (small Δr) ────────────────────────────────────────────────
    {
        "id": "yb_3", "terminal": "yanbu", "role": "control",
        "orbit": [{"relative_orbit": 14, "direction": "ASC", "los_azimuth": 98}],
        "old": {"lat": 23.965544,   "lon": 38.244086,   "radius_m": 60.0, "h_max_m": 18, "los_azimuth": 98},
        "new": {"lat": 23.96557261, "lon": 38.24409060,  "radius_m": 60.0, "h_max_m": 18, "los_azimuth": 98},
    },
    {
        "id": "yb_6", "terminal": "yanbu", "role": "control",
        "orbit": [{"relative_orbit": 14, "direction": "ASC", "los_azimuth": 98}],
        "old": {"lat": 23.967960,   "lon": 38.246187,   "radius_m": 60.0, "h_max_m": 18, "los_azimuth": 98},
        "new": {"lat": 23.96797745, "lon": 38.24621048,  "radius_m": 57.5, "h_max_m": 18, "los_azimuth": 98},
    },
    # ── YB clean ─────────────────────────────────────────────────────────────
    {
        "id": "yb_2", "terminal": "yanbu", "role": "clean",
        "orbit": [{"relative_orbit": 14, "direction": "ASC", "los_azimuth": 98}],
        "old": {"lat": 23.964053,   "lon": 38.245977,   "radius_m": 60.0, "h_max_m": 18, "los_azimuth": 98},
        "new": {"lat": 23.96408611, "lon": 38.24601985,  "radius_m": 60.0, "h_max_m": 18, "los_azimuth": 98},
    },
]

N_SCENES = 10  # most-recent scenes to use
BUFFER   = 500  # metres around single tank for bbox


def _single_tank_bbox(tank_geom):
    lat, lon = tank_geom["lat"], tank_geom["lon"]
    d_lat = BUFFER / 111320
    d_lon = BUFFER / (111320 * math.cos(math.radians(lat)))
    return BBox(bbox=(lon - d_lon, lat - d_lat, lon + d_lon, lat + d_lat), crs=CRS.WGS84)


def _fetch_db_series(tank_geom, orbit_cfg, dates):
    """Return list of (date_str, db_mean) for a single tank geometry."""
    bbox = _single_tank_bbox(tank_geom)
    w_m = BUFFER * 2
    img_w = max(20, int(w_m / RESOLUTION))
    img_h = img_w

    # Build a minimal tank dict that build_crescent_masks expects
    t = {
        "id":       "probe",
        "lat":      tank_geom["lat"],
        "lon":      tank_geom["lon"],
        "radius":   tank_geom["radius_m"],   # build_crescent_masks key
        "radius_m": tank_geom["radius_m"],
        "diameter": tank_geom["radius_m"] * 2,
        "h_max":    tank_geom["h_max_m"],
    }
    tanks_data = build_crescent_masks([t], bbox, img_w, img_h,
                                      los_azimuth=tank_geom.get("los_azimuth", 282))

    records = fetch_new_records(dates, tanks_data, bbox, img_w, img_h,
                                orbit_cfg=orbit_cfg)
    series = []
    for r in records:
        if r.get("db_mean") is not None:
            db = max(r["db_mean"], DB_FLOOR)
            series.append((r["date"], db))
    series.sort(key=lambda x: x[0])
    return series


def _median_step(series):
    """Median of |db[i] - db[i-1]| for consecutive passes."""
    if len(series) < 2:
        return float("nan")
    diffs = [abs(series[i][1] - series[i-1][1]) for i in range(1, len(series))]
    return float(np.median(diffs))


def _get_dates_for_orbit(orbit_cfg):
    """Get N_SCENES most-recent catalog dates for this orbit."""
    orb = orbit_cfg[0]
    dir_str = orb["direction"].upper()
    if dir_str == "DESC": dir_str = "DESCENDING"
    elif dir_str == "ASC": dir_str = "ASCENDING"

    # Use a large bbox that covers the relevant region — catalog dates don't need per-tank bbox
    # JW/RT are near 26.8N, 50E; YB is near 24N, 38E
    # We use a 1° box centred on the first probe's lat/lon (doesn't matter much for catalog)
    return None  # computed per-terminal below


def main():
    os.makedirs("diagnostics/stage2", exist_ok=True)

    results = []
    terminal_dates_cache = {}  # terminal_id → sorted list of dates

    for probe in PROBE_TANKS:
        tid = probe["terminal"]
        orb = probe["orbit"]
        print(f"\n── {probe['id']} ({tid}, {probe['role']}) ──")
        print(f"   old r={probe['old']['radius_m']}m  →  new r={probe['new']['radius_m']}m  "
              f"(Δ={(probe['new']['radius_m']/probe['old']['radius_m']-1)*100:+.0f}%)")

        # ── Get catalog dates for this terminal (cache per terminal) ──────────
        if tid not in terminal_dates_cache:
            o = orb[0]
            d_str = o["direction"].upper()
            if d_str in ("DESC", "DESCENDING"):
                orbit_state = "descending"
            else:
                orbit_state = "ascending"

            # Use a bbox around the probe tank for catalog query
            bbox_cat = _single_tank_bbox(probe["old"])
            try:
                all_dates = get_all_scene_dates(
                    bbox_cat,
                    orbit_state=orbit_state,
                    relative_orbit=o.get("relative_orbit"),
                )
                all_dates_sorted = sorted(all_dates, reverse=True)
                terminal_dates_cache[tid] = all_dates_sorted
                print(f"   Catalog: {len(all_dates_sorted)} dates for {tid}")
            except Exception as e:
                print(f"   [!] Catalog failed: {e}")
                terminal_dates_cache[tid] = []

        dates = terminal_dates_cache[tid][:N_SCENES]
        if len(dates) < 2:
            print(f"   [!] Not enough dates ({len(dates)}), skipping")
            results.append({"id": probe["id"], "terminal": tid, "role": probe["role"],
                            "n_dates": len(dates), "step_old": None, "step_new": None,
                            "drop_pct": None, "error": "not enough dates"})
            continue

        print(f"   Using {len(dates)} scenes: {dates[-1]} … {dates[0]}")

        # ── Fetch old geometry ─────────────────────────────────────────────────
        try:
            series_old = _fetch_db_series(probe["old"], orb, dates)
            step_old   = _median_step(series_old)
            print(f"   OLD: {len(series_old)} records, median step = {step_old:.3f} dB")
        except Exception as e:
            print(f"   [!] Old geometry fetch failed: {e}")
            step_old = float("nan")

        # ── Fetch new geometry ─────────────────────────────────────────────────
        try:
            series_new = _fetch_db_series(probe["new"], orb, dates)
            step_new   = _median_step(series_new)
            print(f"   NEW: {len(series_new)} records, median step = {step_new:.3f} dB")
        except Exception as e:
            print(f"   [!] New geometry fetch failed: {e}")
            step_new = float("nan")

        # ── Drop % ────────────────────────────────────────────────────────────
        if math.isnan(step_old) or math.isnan(step_new) or step_old == 0:
            drop_pct = float("nan")
        else:
            drop_pct = (step_old - step_new) / step_old * 100  # positive = improvement

        print(f"   drop% = {drop_pct:+.1f}%")
        results.append({
            "id":        probe["id"],
            "terminal":  tid,
            "role":      probe["role"],
            "r_old":     probe["old"]["radius_m"],
            "r_new":     probe["new"]["radius_m"],
            "n_dates":   len(dates),
            "step_old":  round(step_old, 4) if not math.isnan(step_old) else None,
            "step_new":  round(step_new, 4) if not math.isnan(step_new) else None,
            "drop_pct":  round(drop_pct, 1) if not math.isnan(drop_pct) else None,
        })

    # ── Gate evaluation ───────────────────────────────────────────────────────
    main_tanks   = {"jw_17", "jw_1", "rt_30", "jw_9"}
    control_tanks = {"yb_3", "yb_6", "rt_3", "rt_4"}

    main_results    = [r for r in results if r["id"] in main_tanks]
    control_results = [r for r in results if r["id"] in control_tanks]

    n_main_pass    = sum(1 for r in main_results
                        if r["drop_pct"] is not None and r["drop_pct"] >= 10)
    n_ctrl_fail    = sum(1 for r in control_results
                        if r["drop_pct"] is not None and abs(r["drop_pct"]) >= 10)

    gate_pass = n_main_pass >= 1 and n_ctrl_fail == 0
    gate_note = ("PASS — geometry explains ≥1 main tank, controls clean"
                 if gate_pass else
                 f"INCONCLUSIVE — main_pass={n_main_pass}/4, ctrl_fail={n_ctrl_fail}/4")

    # ── Report ────────────────────────────────────────────────────────────────
    lines = [
        "=" * 72,
        "Stage 2 Gate Report — Geometry Impact Test",
        f"Run: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"Scenes per tank: {N_SCENES}",
        "=" * 72,
        "",
        "Pre-written expectations (frozen before run):",
        "  PASS if: ≥1 of {jw_17, jw_1, rt_30, jw_9} has drop% ≥ 10%",
        "        AND all 4 controls have |drop%| < 10%",
        "",
        f"{'Tank':<10} {'Terminal':<12} {'Role':<10} {'r_old':>6} {'r_new':>6} "
        f"{'step_old':>9} {'step_new':>9} {'drop%':>7} {'n':>3}",
        "-" * 72,
    ]
    for r in results:
        s_old = f"{r['step_old']:.3f}" if r["step_old"] is not None else "  n/a"
        s_new = f"{r['step_new']:.3f}" if r["step_new"] is not None else "  n/a"
        s_drp = f"{r['drop_pct']:+.1f}%" if r["drop_pct"] is not None else "   n/a"
        lines.append(
            f"{r['id']:<10} {r['terminal']:<12} {r['role']:<10} "
            f"{r.get('r_old',0):>6.1f} {r.get('r_new',0):>6.1f} "
            f"{s_old:>9} {s_new:>9} {s_drp:>7} {r['n_dates']:>3}"
        )
    lines += [
        "-" * 72,
        "",
        f"Gate result: {gate_note}",
        "",
        "Outcome legend:",
        "  drop% > 0  →  new geometry reduces noise (improvement)",
        "  drop% < 0  →  new geometry increases noise (regression)",
        "  drop% ≈ 0  →  geometry was not the cause of this tank's noise",
        "",
        "Legitimate outcomes:",
        "  (a) PASS: geometry was wrong for ≥1 main tank → backfill justified",
        "  (b) INCONCLUSIVE main=0: geometry not the cause → noise is operational",
        "  (c) MIXED: Stage 3 only for confirmed terminals",
    ]

    report_text = "\n".join(lines)
    print("\n" + report_text)

    report_path = "diagnostics/stage2/stage2_gate_report.txt"
    json_path   = "diagnostics/stage2/stage2_gate_results.json"
    with open(report_path, "w") as f:
        f.write(report_text + "\n")
    with open(json_path, "w") as f:
        json.dump({"gate": gate_note, "gate_pass": gate_pass, "results": results}, f, indent=2)
    print(f"\n[stage2] Wrote {report_path}")
    print(f"[stage2] Wrote {json_path}")

    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
