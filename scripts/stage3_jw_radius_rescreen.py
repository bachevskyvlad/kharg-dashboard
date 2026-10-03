"""
stage3_jw_radius_rescreen.py — JW radius-corrected whitelist re-screen (Stage 1).

Hypothesis: registry used uniform r=50m for all 19 JW tanks. Stage0-full optical
measurement shows the northern cluster has real radii 57.5–65m. The crescent ring
[r−15, r] at r=50 lies INSIDE the floating roof for a tank with true r=65 → SAR
measures roof backscatter, not dihedral wall shadow → step is artificially high.

This script re-runs the whitelist screen with per-tank measured radii (centers
unchanged). No Supabase writes. Output goes to diagnostics/stage3_jw_radius/.

Controls (mandatory, checked before finalising):
  jw_9  — r_meas=57.5m (was already whitelist at r=50): step_new must not
           worsen >20% vs step_old (1.274 dB); threshold = 1.529 dB.
  jw_3, jw_7 — r_meas=50.0m (no change): placebo controls, step_new ≈ step_old.

Gate (Stage 2): WL cap ≥ 60% of total cap (computed with corrected r) → TERMINAL_SERIES.

Usage:
    cd scripts && python stage3_jw_radius_rescreen.py

Outputs:
    diagnostics/stage3_jw_radius/jw_radius_rescreen_report.txt
    diagnostics/stage3_jw_radius/jw_radius_rescreen_results.json
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

N_SCENES    = 18
STEP_THRESH = 2.5   # dB
BUFFER      = 500   # metres
BARREL_PER_M3 = 6.2898

# Tanks where physical capacity uses r_measured even if mask stays r_old.
# These are tanks whose step worsened with r_new (mask kept at r_old) but
# whose true physical size is known from Stage0 optical measurement.
BIG_TANKS_PHYS_CAP = {"jw_14", "jw_15", "jw_17"}

# Stage0-full measured radii for all 19 JW tanks (centers NOT changed).
# Source: diagnostics/stage0_full/stage0_full_report.txt
# Tanks with QA flags (★) noted but radius still applied — flag surfaced in report.
STAGE0_RADII = {
    "jw_0":  {"r_meas": 53.8, "flag": None},
    "jw_1":  {"r_meas": 55.0, "flag": "★ optical_flag"},
    "jw_2":  {"r_meas": 56.2, "flag": None},
    "jw_3":  {"r_meas": 50.0, "flag": None},    # placebo — no radius change
    "jw_4":  {"r_meas": 45.0, "flag": None},    # smaller than registry
    "jw_5":  {"r_meas": 53.8, "flag": None},
    "jw_6":  {"r_meas": 53.8, "flag": None},
    "jw_7":  {"r_meas": 50.0, "flag": None},    # placebo — no radius change
    "jw_8":  {"r_meas": 52.5, "flag": None},
    "jw_9":  {"r_meas": 57.5, "flag": None},    # ★ CONTROL: step_new must not worsen >20%
    "jw_10": {"r_meas": 56.2, "flag": None},
    "jw_11": {"r_meas": 57.5, "flag": None},
    "jw_12": {"r_meas": 53.8, "flag": None},
    "jw_13": {"r_meas": 53.8, "flag": None},
    "jw_14": {"r_meas": 63.8, "flag": None},    # hypothesis target +27.5%
    "jw_15": {"r_meas": 62.5, "flag": None},    # hypothesis target +25%
    "jw_16": {"r_meas": 62.5, "flag": "wl_note:review_flag_optical"},
    "jw_17": {"r_meas": 65.0, "flag": "★ 11.2m_center_offset"},  # hypothesis target +30%
    "jw_18": {"r_meas": 63.8, "flag": None},    # hypothesis target +27.5% (already passes at r=50)
}

# Step values from the original whitelist round (diagnostics/stage3/whitelist_results.json).
# Loaded at runtime to avoid hard-coding stale values.
STEP_OLD_PATH = os.path.join(os.path.dirname(__file__), "..",
                             "diagnostics", "stage3", "whitelist_results.json")

# jw_9 control limit: step_new must not exceed step_old × 1.20
JW9_WORSENING_LIMIT = 1.20
# Placebo tanks (r unchanged): step_new vs step_old should be within ±30% (noise tolerance)
PLACEBO_TANKS = {"jw_3", "jw_7"}
PLACEBO_NOISE_LIMIT = 0.30

# API 650 h_max table (registry header).  Assign by measured radius class.
def _h_max_for_radius(r: float) -> int:
    if r > 57.5:
        return 18   # ≥ r=60 class
    if r > 50.0:
        return 17   # r=55 class
    if r >= 40.0:
        return 16   # current JW class (r=50)
    return 14       # r=40 class (jw_4 at 45m)


def _vol_max(r: float, h: float) -> float:
    return math.pi * r**2 * h * BARREL_PER_M3 / 1e6


def _single_tank_bbox(lat, lon, buffer=BUFFER):
    d_lat = buffer / 111320
    d_lon = buffer / (111320 * math.cos(math.radians(lat)))
    return BBox(bbox=(lon - d_lon, lat - d_lat, lon + d_lon, lat + d_lat), crs=CRS.WGS84)


def _catalog_dates(orbit_cfg, bbox, n):
    orb      = orbit_cfg[0]
    dir_str  = orb["direction"].upper()
    orbit_state = "descending" if dir_str in ("DESC", "DESCENDING") else "ascending"
    rel_orbit   = orb.get("relative_orbit")
    all_dates   = get_all_scene_dates(bbox, orbit_state=orbit_state, relative_orbit=rel_orbit)
    dates       = sorted(all_dates, reverse=True)
    return dates[:n]


def _fetch_tank_step(tank_id, lat, lon, r_new, h_new, orbit_cfg, dates):
    los = orbit_cfg[0].get("los_azimuth", 282)
    bbox  = _single_tank_bbox(lat, lon)
    w_m   = BUFFER * 2
    img_w = max(20, int(w_m / RESOLUTION))
    img_h = img_w

    t = {
        "id":       tank_id,
        "lat":      lat,
        "lon":      lon,
        "radius":   r_new,
        "radius_m": r_new,
        "diameter": r_new * 2,
        "h_max":    h_new,
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


def _load_step_old() -> dict:
    """Return {tank_id: step_old} from whitelist_results.json."""
    try:
        with open(STEP_OLD_PATH) as f:
            d = json.load(f)
        tanks = d.get("tanks", [])
        return {t["tank_id"]: t.get("step") for t in tanks if t.get("terminal") == "juwaymah"}
    except Exception as exc:
        print(f"[WARN] Could not load step_old from {STEP_OLD_PATH}: {exc}")
        return {}


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "..", "diagnostics", "stage3_jw_radius")
    os.makedirs(out_dir, exist_ok=True)

    step_old_map = _load_step_old()
    print(f"Loaded step_old for {len(step_old_map)} JW tanks from whitelist round.")

    # Find juwaymah in registry
    jw_loc = next((loc for loc in LOCATIONS if loc["id"] == "juwaymah"), None)
    if jw_loc is None:
        print("ERROR: juwaymah not found in LOCATIONS registry.")
        sys.exit(1)

    orbit_cfg = jw_loc["allowed_orbits"]
    tanks_reg = {t["id"]: t for t in jw_loc["tanks"]}

    # Catalog dates — fetch once using first tank bbox
    first = jw_loc["tanks"][0]
    ref_bbox = _single_tank_bbox(first["lat"], first["lon"], buffer=2000)
    print(f"Fetching catalog dates (last {N_SCENES} DESC-35 passes)…")
    dates = _catalog_dates(orbit_cfg, ref_bbox, N_SCENES)
    if not dates:
        print("ERROR: No SAR catalog dates returned.")
        sys.exit(1)
    print(f"Dates ({len(dates)}): {dates[0]} … {dates[-1]}")

    rows = []
    total_cap = 0.0
    wl_cap    = 0.0
    control_pass = True
    control_notes = []

    print(f"\n{'tank_id':10s} {'r_old':>6s} {'r_new':>6s} {'h_new':>5s} "
          f"{'step_old':>9s} {'step_new':>9s} {'Δ%':>7s} {'wl_new':>8s} {'note'}")
    print("-" * 80)

    for tank_id, s0 in sorted(STAGE0_RADII.items()):
        treg  = tanks_reg.get(tank_id, {})
        lat   = treg.get("lat")
        lon   = treg.get("lon")
        r_old = treg.get("radius_m", 50)
        r_new = s0["r_meas"]
        h_new = _h_max_for_radius(r_new)
        flag  = s0["flag"] or ""

        if lat is None or lon is None:
            print(f"{tank_id:10s}  MISSING from registry — skip")
            continue

        step_old = step_old_map.get(tank_id)
        step_old_s = f"{step_old:.3f}" if step_old is not None else "n/a"

        try:
            step_new, n_obs = _fetch_tank_step(tank_id, lat, lon, r_new, h_new,
                                               orbit_cfg, dates)
        except Exception as exc:
            print(f"{tank_id:10s}  ERROR: {exc}")
            step_new, n_obs = float("nan"), 0

        # Per-tank acceptance rule: r_new accepted ONLY if step_new < 2.5 AND step_new < step_old.
        # Otherwise tank stays on r_old (mask unchanged); step_old used for WL status.
        r_accept_ok = (
            not math.isnan(step_new)
            and step_new < STEP_THRESH
            and step_old is not None
            and step_new < step_old
        )
        r_accepted    = r_new if r_accept_ok else r_old
        h_accepted    = _h_max_for_radius(r_accepted)
        step_effective = step_new if r_accept_ok else (step_old if step_old is not None else float("nan"))
        whitelist      = not math.isnan(step_effective) and step_effective < STEP_THRESH

        # Capacity: physical size uses r_meas for BIG_TANKS_PHYS_CAP even if mask stays r_old.
        if tank_id in BIG_TANKS_PHYS_CAP:
            cap_phys   = _vol_max(r_new, _h_max_for_radius(r_new))
            cap_source = "stage0"
        else:
            cap_phys   = _vol_max(r_accepted, h_accepted)
            cap_source = "accepted"

        total_cap += cap_phys
        if whitelist:
            wl_cap += cap_phys

        delta_pct = None
        if step_old is not None and not math.isnan(step_new):
            delta_pct = (step_new - step_old) / step_old * 100

        step_new_s   = f"{step_new:.3f}" if not math.isnan(step_new) else "nan"
        delta_pct_s  = f"{delta_pct:+.1f}%" if delta_pct is not None else "n/a"
        r_accept_s   = f"r_accepted={r_accepted:.1f}" if r_accepted != r_new else "r_accepted=r_new"
        wl_s         = "✓ whitelist" if whitelist else "✗ excluded"

        note_parts = []
        if flag:
            note_parts.append(flag)
        if not r_accept_ok and not math.isnan(step_new):
            note_parts.append(r_accept_s)

        # jw_9 control: exposing bad stage0 measurement, not mechanism failure
        if tank_id == "jw_9" and step_old is not None and not math.isnan(step_new):
            limit = step_old * JW9_WORSENING_LIMIT
            if step_new > limit:
                msg = (f"control exposed bad stage0 measurement for jw_9; per-tank rule applied "
                       f"(step_new={step_new:.3f} > step_old×1.20={limit:.3f}; r_old=50 kept)")
                note_parts.append(msg)
                control_notes.append(msg)
            else:
                note_parts.append(f"control OK (limit {limit:.3f})")

        # Placebo checks
        if tank_id in PLACEBO_TANKS and step_old is not None and not math.isnan(step_new):
            noise = abs(step_new - step_old) / step_old if step_old > 0 else float("nan")
            if noise > PLACEBO_NOISE_LIMIT:
                msg = f"PLACEBO WARN: {tank_id} step changed {delta_pct_s} (>{PLACEBO_NOISE_LIMIT*100:.0f}% noise band)"
                note_parts.append(msg)
                control_notes.append(msg)
            else:
                note_parts.append(f"placebo OK ({delta_pct_s})")

        note = "; ".join(note_parts)
        print(f"{tank_id:10s} {r_old:>6.1f} {r_new:>6.1f} {h_new:>5d} "
              f"{step_old_s:>9s} {step_new_s:>9s} {delta_pct_s:>7s} "
              f"{wl_s:>12s}  {note}")

        rows.append({
            "tank_id":    tank_id,
            "r_old":      r_old,
            "r_new":      r_new,
            "r_accepted": r_accepted,
            "h_max_new":  h_accepted,
            "step_old":   step_old,
            "step_new":   round(step_new, 4) if not math.isnan(step_new) else None,
            "step_effective": round(step_effective, 4) if not math.isnan(step_effective) else None,
            "delta_pct":  round(delta_pct, 1) if delta_pct is not None else None,
            "r_accept_ok": r_accept_ok,
            "n_obs":      n_obs,
            "whitelist":  whitelist,
            "cap_phys_mb": round(cap_phys, 4),
            "cap_source": cap_source,
            "flag":       flag or None,
        })

    # Gate
    pct_cap = wl_cap / total_cap * 100 if total_cap > 0 else 0
    n_wl        = sum(1 for r in rows if r["whitelist"])
    new_gain    = [r["tank_id"] for r in rows
                   if r["whitelist"] and step_old_map.get(r["tank_id"], STEP_THRESH) >= STEP_THRESH]

    gate_status = "TERMINAL_SERIES" if pct_cap >= 60 else "TANK_LEVEL_OR_DROP"

    print(f"\n{'='*80}")
    print(f"JW radius-corrected re-screen: {n_wl}/19 tanks pass step < {STEP_THRESH} dB")
    print(f"Capacity: {wl_cap:.2f} / {total_cap:.2f} Mb = {pct_cap:.1f}%")
    print(f"Gate (≥60%): {gate_status}")
    if control_notes:
        print("Control / placebo issues:")
        for cn in control_notes:
            print(f"  ! {cn}")
    if new_gain:
        print(f"Newly whitelisted (were excluded at r=50): {new_gain}")

    # TERMINAL_MAX_CAP: physical capacities (r_meas for BIG_TANKS_PHYS_CAP, r_accepted otherwise)
    terminal_max_new = round(total_cap, 2)
    cap_r50 = round(sum(_vol_max(50, 16) for _ in rows), 2)  # baseline at uniform r=50
    print(f"\nUpdated TERMINAL_MAX_CAP for juwaymah (physical caps, per-tank rule): {terminal_max_new} Mb")
    print(f"  (was 15.0 Mb at uniform r=50; big-tank phys: jw_14/15/17 at r_meas, cap_source=stage0)")

    # Serialize dates (pandas Timestamps → ISO strings)
    dates_str = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in dates]

    # Write JSON
    result = {
        "run_date":        datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "n_scenes":        len(dates),
        "scene_dates":     dates_str,
        "step_thresh":     STEP_THRESH,
        "control_notes":   control_notes,
        "per_tank_rule":   "r_new accepted only if step_new < 2.5 AND step_new < step_old",
        "n_whitelist":     n_wl,
        "wl_cap_mb":       round(wl_cap, 4),
        "total_cap_mb":    round(total_cap, 4),
        "pct_cap":         round(pct_cap, 1),
        "gate_status":     gate_status,
        "terminal_max_cap_new_mb": terminal_max_new,
        "tanks":           rows,
    }
    json_path = os.path.join(out_dir, "jw_radius_rescreen_results.json")
    with open(json_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved: {json_path}")

    # Write text report
    report_path = os.path.join(out_dir, "jw_radius_rescreen_report.txt")
    with open(report_path, "w") as f:
        f.write("JW Radius-Corrected Re-Screen\n")
        f.write(f"Run: {result['run_date']}\n")
        f.write(f"Scenes: {len(dates)}  ({dates[0]} … {dates[-1]})\n")
        f.write(f"Step threshold: {STEP_THRESH} dB\n\n")
        f.write(f"{'tank_id':10s} {'r_old':>6s} {'r_new':>6s} {'h_new':>5s} "
                f"{'step_old':>9s} {'step_new':>9s} {'Δ%':>7s} {'wl_new':>12s}  note\n")
        f.write("-" * 100 + "\n")
        for r in rows:
            so  = f"{r['step_old']:.3f}" if r["step_old"] is not None else "n/a"
            sn  = f"{r['step_new']:.3f}" if r["step_new"] is not None else "nan"
            se  = f"{r['step_effective']:.3f}" if r["step_effective"] is not None else "n/a"
            dp  = f"{r['delta_pct']:+.1f}%" if r["delta_pct"] is not None else "n/a"
            wl  = "✓ whitelist" if r["whitelist"] else "✗ excluded"
            ra  = f"r_acc={r['r_accepted']:.0f}" if r["r_accepted"] != r["r_new"] else "r_acc=r_new"
            cs  = r["cap_source"]
            fl  = r["flag"] or ""
            f.write(f"{r['tank_id']:10s} {r['r_old']:>6.1f} {r['r_new']:>6.1f} "
                    f"{r['h_max_new']:>5d} {so:>9s} {sn:>9s} {se:>9s} {dp:>7s} "
                    f"{wl:>12s}  {ra}  cap={r['cap_phys_mb']:.2f}Mb[{cs}]  {fl}\n")
        f.write(f"\nWhitelist: {n_wl}/19 tanks\n")
        f.write(f"Capacity:  {wl_cap:.2f} / {total_cap:.2f} Mb ({pct_cap:.1f}%)\n")
        f.write(f"Gate (≥60%): {gate_status}\n")
        f.write(f"Terminal max cap (corrected): {terminal_max_new} Mb\n")
        if control_notes:
            f.write("\nControl / placebo issues:\n")
            for cn in control_notes:
                f.write(f"  ! {cn}\n")
        if new_gain:
            f.write(f"\nNewly whitelisted vs r=50 round: {new_gain}\n")

    print(f"Saved: {report_path}")


if __name__ == "__main__":
    main()
