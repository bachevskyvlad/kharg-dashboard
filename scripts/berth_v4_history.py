"""
berth_v4_history.py — v4 detection on all S1 scenes 2024-01-01 → today.

v4 config (fixed per berth):
  sea_island:     BG_V3  pier=20 water=20-80 len≥120 aspect≥2.0 merge=40
  east_t_head:    BG_V4  pier=20 water=20-80 len≥150 aspect≥3.0 merge=0
  southeast_pier: BG_V3  pier=20 water=20-80 len≥150 aspect≥3.0 merge=0

Output (v5.0):
  diagnostics/berth_v4/history_v4/berth_v4_history.csv  (hull-level rows)
  data/berth_occupancy.json  (site widget, v5.0 format)

DB write: if SUPABASE_SERVICE_KEY is set, upserts rows into berth_occupancy table.
"""

import os, sys, csv, json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).parent))

from berth_detect_core import (
    RESOLUTION, CELL_KM, CORRIDOR_END_M, RATIO_THR, struct8,
    BERTH_COORDS,
    metres_per_deg_lat, metres_per_deg_lon, berth_center,
    cell_bbox_for, build_geom, find_blobs,
)

import numpy as np
from scipy import ndimage
from scipy.ndimage import zoom as nd_zoom

from sentinelhub import BBox, CRS
from shapely.geometry import LineString
import shapely

from berth_pc_fetch import discover_dates_pc, find_item_pc, fetch_s1_pc

ROOT    = Path(__file__).parent.parent
BG_V3   = ROOT / "diagnostics" / "berth_v4" / "bg_v3"
BG_V4   = ROOT / "diagnostics" / "berth_v4" / "bg_v4"
# east_t_head o101 2026 background: median of optically-empty 2026 scenes.
# Used for east_t_head o101 passes from BG_2026_CUTOFF onward.
BG_2026_CUTOFF = "2026-08-15"
BG_2026_PATH   = BG_V4 / "bg_o101_east_t_head_2026.npy"
OUT_DIR = ROOT / "diagnostics" / "berth_v4" / "history_v4"
JSON_OUT = ROOT / "data" / "berth_occupancy.json"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HISTORY_START   = "2024-01-01"
ORBITS          = [35, 101]

BERTHS = BERTH_COORDS  # alias for local use

V4_CONFIG = {
    # all BGs rebuilt from PC and saved in BG_V4
    "sea_island":     {"pier": 20, "water_i": 20, "water_o": 80, "min_len": 120, "max_len": 420, "aspect_thr": 2.0, "merge_gap": 120, "bg_dir": BG_V4},
    "east_t_head":    {"pier": 20, "water_i": 20, "water_o": 80, "min_len": 150, "max_len": 420, "aspect_thr": 2.5, "merge_gap": 120, "bg_dir": BG_V4},
    "southeast_pier": {"pier": 20, "water_i": 20, "water_o": 80, "min_len": 150, "max_len": 420, "aspect_thr": 3.0, "merge_gap": 0,   "bg_dir": BG_V4},
}

from berth_detect_core import VISIT_MIN_LEN_M

STATIC_POS_TOL_M        = 60.0  # ±m along pier axis — same-position threshold
STATIC_WINDOW_DAYS      = 30    # primary rolling window for orbit-asymmetry test
STATIC_WINDOW_DAYS_EXT  = 60    # extended window used when <STATIC_ASYM_MIN_PASSES on any orbit
STATIC_ASYM_HI          = 0.75  # presence rate on dominant orbit ≥ this → candidate
STATIC_ASYM_LO          = 0.25  # presence rate on other orbit ≤ this → asymmetric
STATIC_ASYM_MIN_PASSES  = 2     # min passes of EACH orbit in window to apply test

AOI_BBOX     = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)
AOI_BBOX_LST = [50.26, 29.17, 50.40, 29.31]

# ── Catalog & fetch (Planetary Computer) ──────────────────────────────────────

def discover_dates(date_from, date_to):
    print(f"Discovering S1 catalog {date_from} → {date_to} (Planetary Computer) ...")
    entries = discover_dates_pc(date_from, date_to, AOI_BBOX_LST, orbits=tuple(ORBITS))
    # rename 'item' → keep as-is; add 'scene_dt' alias for compatibility
    for e in entries:
        e["scene_dt"] = e["dt_raw"]
    print(f"  unique (date, orbit) pairs: {len(entries)}")
    return entries

def fetch_s1_linear(entry_or_dt, bbox_obj):
    # entry_or_dt is now a dict entry from discover_dates (has 'item')
    item = entry_or_dt.get("item") if isinstance(entry_or_dt, dict) else None
    if item is None:
        print("  fetch_s1_linear: no item"); return None
    return fetch_s1_pc(item, bbox_obj)

# ── Hull classification ────────────────────────────────────────────────────────

def classify_hull(len_m):
    if len_m >= 300:
        return "VLCC"
    return "SuezAframax"

# ── Loading event detection ────────────────────────────────────────────────────

def is_new_load(pos_m, len_m, perp_m, prev_hulls):
    """Return True if no hull in prev_hulls matches position/length/side.

    Tolerances: ±200 m along axis, ±50 m length, same pier side (sign of perp_m).
    prev_hulls may lack 'perp_m' (old JSON) — falls back to side-agnostic match.
    """
    return not any(
        abs(pos_m - ph["pos_m"]) <= 200 and
        abs(len_m - ph["len_m"]) <= 50 and
        (
            ph.get("perp_m") is None or
            (perp_m >= 0) == (ph["perp_m"] >= 0)
        )
        for ph in prev_hulls
    )

def is_new_load_v2(pos_m, len_m, perp_m, prev_same_orbit, recent_any_hulls, tol_m=200.0):
    """True if no matching hull in prev_same_orbit (same orbit) or recent_any_hulls (any orbit).

    prev_same_orbit: list of hull dicts from last pass of this orbit
    recent_any_hulls: flat list of hull dicts from last 2 passes of ANY orbit
    tol_m: position tolerance along pier axis (default same as original is_new_load)
    """
    all_prev = list(prev_same_orbit) + list(recent_any_hulls)
    return not any(
        abs(pos_m - ph["pos_m"]) <= tol_m and
        abs(len_m - ph["len_m"]) <= 50 and
        (ph.get("perp_m") is None or (perp_m >= 0) == (ph["perp_m"] >= 0))
        for ph in all_prev
    )


def _build_static_set(all_hull_positions):
    """Orbit-asymmetry rule: a position is static when the dominant orbit sees it on
    ≥STATIC_ASYM_HI of its passes AND the other orbit sees it on ≤STATIC_ASYM_LO of
    its passes in a rolling window, provided both orbits have ≥STATIC_ASYM_MIN_PASSES.

    Primary window: STATIC_WINDOW_DAYS (30d).  If either orbit has <STATIC_ASYM_MIN_PASSES
    in the 30d window, the test falls back to STATIC_WINDOW_DAYS_EXT (60d) — this handles
    the end of the time series where few future passes exist.

    Real ship traffic is visible on both orbits; a persistent non-ship artefact
    reflects differently on ascending vs descending passes, or is invisible from one geometry.
    """
    from datetime import date as _date, timedelta as _td

    static_keys: set = set()

    pass_dates: dict = {}   # (berth, orbit) → sorted list of date strings
    for h in all_hull_positions:
        key = (h["berth"], h["orbit"])
        pass_dates.setdefault(key, set()).add(h["date"])
    pass_dates = {k: sorted(v) for k, v in pass_dates.items()}

    by_bs: dict = {}
    for h in all_hull_positions:
        key = (h["berth"], h["side"])
        by_bs.setdefault(key, []).append(h)

    all_orbits = sorted({h["orbit"] for h in all_hull_positions})

    def _counts_in_window(berth, side, pos0, orbits, win_start, win_end):
        pass_cnt = {}
        hit_cnt  = {}
        for orb in orbits:
            pdates = pass_dates.get((berth, orb), [])
            pass_cnt[orb] = sum(1 for d in pdates if win_start <= d <= win_end)
            hit_cnt[orb]  = sum(
                1 for h in by_bs.get((berth, side), [])
                if h["orbit"] == orb
                and win_start <= h["date"] <= win_end
                and abs(h["pos_m"] - pos0) <= STATIC_POS_TOL_M
            )
        return pass_cnt, hit_cnt

    def _test_asymmetry(pass_cnt, hit_cnt, orbits):
        for orb_a in orbits:
            n_a = pass_cnt.get(orb_a, 0)
            if n_a < STATIC_ASYM_MIN_PASSES:
                continue
            rate_a = hit_cnt.get(orb_a, 0) / n_a
            if rate_a < STATIC_ASYM_HI:
                continue
            for orb_b in orbits:
                if orb_b == orb_a:
                    continue
                n_b = pass_cnt.get(orb_b, 0)
                if n_b < STATIC_ASYM_MIN_PASSES:
                    continue
                if hit_cnt.get(orb_b, 0) / n_b <= STATIC_ASYM_LO:
                    return True
        return False

    for (_berth, _side), detections in by_bs.items():
        for det in detections:
            d0   = _date.fromisoformat(det["date"])
            pos0 = det["pos_m"]
            half = STATIC_WINDOW_DAYS // 2
            ws   = (d0 - _td(days=half)).isoformat()
            we   = (d0 + _td(days=half)).isoformat()

            pc, hc = _counts_in_window(_berth, _side, pos0, all_orbits, ws, we)

            # Fall back to extended window if either orbit has too few passes
            if any(pc.get(orb, 0) < STATIC_ASYM_MIN_PASSES for orb in all_orbits):
                half_ext = STATIC_WINDOW_DAYS_EXT // 2
                ws = (d0 - _td(days=half_ext)).isoformat()
                we = (d0 + _td(days=half_ext)).isoformat()
                pc, hc = _counts_in_window(_berth, _side, pos0, all_orbits, ws, we)

            if _test_asymmetry(pc, hc, all_orbits):
                static_keys.add((_berth, det["orbit"], det["date"], det["pos_m"], _side))

    return static_keys


# ── Metric helpers ─────────────────────────────────────────────────────────────

def count_loadings(loadings, start, end):
    subset = [l for l in loadings if start <= l["date"] <= end]
    total = len(subset)
    vlcc  = sum(1 for l in subset if l["class"] == "VLCC")
    suez  = sum(1 for l in subset if l["class"] == "SuezAframax")
    return {"total": total, "VLCC": vlcc, "SuezAframax": suez}

# ── Validation report ─────────────────────────────────────────────────────────

def _print_validation_report(all_passes, all_loadings, loadings_before_static=None, static_points=None, visits=None, hull_rows_all=None):
    """Print hull-count and loading-rate sanity table.

    Expected:
      hulls/pass mean ≤ 1.5, max ≤ 3 per berth
      loadings/month ≤ passes/month
      VLCC share 2025 — majority on sea_island and east_t_head
    """
    from collections import defaultdict
    from datetime import date as _date
    import calendar

    berths = ["sea_island", "east_t_head", "southeast_pier"]

    # hulls per pass per berth
    hull_counts: dict = defaultdict(list)  # berth → [n_hulls]
    for p in all_passes:
        for b in berths:
            bd = p.get(b, {})
            if bd.get("det") in ("OCC", "EMP"):
                hull_counts[b].append(len(bd.get("hulls", [])))

    # passes per calendar month
    month_passes: dict = defaultdict(lambda: defaultdict(int))  # (year,month) → berth → n
    for p in all_passes:
        try:
            dt = datetime.strptime(p["date"], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        for b in berths:
            if p.get(b, {}).get("det") in ("OCC", "EMP"):
                month_passes[(dt.year, dt.month)][b] += 1

    # loadings per calendar month
    month_loads: dict = defaultdict(lambda: defaultdict(int))
    for l in all_loadings:
        try:
            dt = datetime.strptime(l["date"], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        month_loads[(dt.year, dt.month)][l["berth"]] += 1

    # VLCC share 2025
    vlcc_2025: dict = defaultdict(int)
    total_2025: dict = defaultdict(int)
    for l in all_loadings:
        if l["date"].startswith("2025"):
            total_2025[l["berth"]] += 1
            if l["class"] == "VLCC":
                vlcc_2025[l["berth"]] += 1

    lines = []
    lines.append("\n=== Validation report ===")
    lines.append(f"\n{'Berth':<20} {'Passes':>7} {'Hull/pass mean':>14} {'Hull/pass max':>13} "
          f"{'Status':>8}")
    lines.append("-" * 68)
    for b in berths:
        hc = hull_counts[b]
        if not hc:
            lines.append(f"{b:<20} {'—':>7}")
            continue
        mean_h = sum(hc) / len(hc)
        max_h  = max(hc)
        ok     = "OK" if mean_h <= 1.5 and max_h <= 3 else "WARN"
        lines.append(f"{b:<20} {len(hc):>7} {mean_h:>14.2f} {max_h:>13d} {ok:>8}")

    # loadings/month vs passes/month per berth
    all_months = sorted(set(month_passes) | set(month_loads))
    months_with_overcount = []
    for ym in all_months:
        for b in berths:
            lm = month_loads[ym].get(b, 0)
            pm = month_passes[ym].get(b, 0)
            if lm > pm and pm > 0:
                months_with_overcount.append((ym, b, lm, pm))

    if months_with_overcount:
        lines.append("\nWARN loadings > passes in month:")
        for (yr, mo), b, lm, pm in months_with_overcount[:10]:
            lines.append(f"  {yr}-{mo:02d} {b}: {lm} loadings vs {pm} passes")
    else:
        lines.append("\nOK  loadings/month ≤ passes/month for all berth-months")

    # VLCC share 2025
    lines.append(f"\n{'Berth':<20} {'Loadings 2025':>13} {'VLCC':>6} {'VLCC%':>7}")
    lines.append("-" * 50)
    for b in berths:
        tot = total_2025[b]
        vlc = vlcc_2025[b]
        pct = vlc / tot * 100 if tot else 0.0
        lines.append(f"{b:<20} {tot:>13} {vlc:>6} {pct:>6.0f}%")

    # ── Before/after static filter ─────────────────────────────────────────────
    if loadings_before_static is not None:
        lines.append(f"\n=== Before/after static filter ===")

        # loadings/month per berth — 2025 and Sep-Nov 2026
        def _monthly_table(loadings_list, label):
            ml: dict = defaultdict(lambda: defaultdict(int))
            for l in loadings_list:
                try:
                    dt = datetime.strptime(l["date"], "%Y-%m-%d")
                except (KeyError, ValueError):
                    continue
                ml[(dt.year, dt.month)][l["berth"]] += 1
            rows = []
            # 2025 months
            for mo in range(1, 13):
                ym = (2025, mo)
                total = sum(ml[ym].get(b, 0) for b in berths)
                if total or any(ml.get(ym2, {}) for ym2 in [ym]):
                    row = f"  {ym[0]}-{ym[1]:02d}  " + "  ".join(
                        f"{b[:12]:<12}={ml[ym].get(b,0)}" for b in berths
                    ) + f"  total={total}"
                    rows.append(row)
            # Sep-Nov 2026
            for mo in [9, 10, 11]:
                ym = (2026, mo)
                total = sum(ml[ym].get(b, 0) for b in berths)
                row = f"  {ym[0]}-{ym[1]:02d}  " + "  ".join(
                    f"{b[:12]:<12}={ml[ym].get(b,0)}" for b in berths
                ) + f"  total={total}"
                rows.append(row)
            return rows

        lines.append(f"\n  BEFORE filter ({len(loadings_before_static)} loadings):")
        for r in _monthly_table(loadings_before_static, "before"):
            lines.append(r)
        lines.append(f"\n  AFTER filter ({len(all_loadings)} loadings, "
                     f"-{len(loadings_before_static) - len(all_loadings)} removed):")
        for r in _monthly_table(all_loadings, "after"):
            lines.append(r)

    if static_points:
        lines.append(f"\n=== Static objects ({len(static_points)} detections) ===")
        lines.append(f"  {'Date':<12} {'Orbit':>5} {'Berth':<20} {'pos_m':>7} {'side':>5}")
        lines.append("  " + "-" * 55)
        for sp in static_points:
            lines.append(f"  {sp['date']:<12} o{sp['orbit']:>3} {sp['berth']:<20} "
                         f"{sp['pos_m']:>7.0f} {sp['side']:>+5d}")

    # ── Acceptance gates (b-f) ────────────────────────────────────────────────
    lines.append("\n=== Acceptance gates ===")

    # b) hulls/pass: east_t_head mean≤2.0 max≤4 (2 sides × 2 berths); others mean≤1.5 max≤3
    def _b_ok(b, hc):
        if not hc:
            return True
        mean_limit = 2.0 if b == "east_t_head" else 1.5
        max_limit  = 4   if b == "east_t_head" else 3
        return sum(hc) / len(hc) <= mean_limit and max(hc) <= max_limit
    gate_b = all(_b_ok(b, hull_counts[b]) for b in berths)
    lines.append(f"  b) hulls/pass east_t_head mean≤2.0 max≤4, others mean≤1.5 max≤3: {'✅' if gate_b else '❌'}")
    for b in berths:
        hc = hull_counts[b]
        if hc:
            mean_h = sum(hc) / len(hc)
            mean_limit = 2.0 if b == "east_t_head" else 1.5
            max_limit  = 4   if b == "east_t_head" else 3
            ok_b = _b_ok(b, hc)
            lines.append(f"       {b}: mean={mean_h:.2f} ≤{mean_limit} max={max(hc)} ≤{max_limit} {'✅' if ok_b else '❌'}")

    # c) visits/month ≤ passes/month × 2 per berth
    visit_months: dict = defaultdict(lambda: defaultdict(int))
    if visits:
        for v in visits:
            try:
                dt = datetime.strptime(v.get("start_date", ""), "%Y-%m-%d")
                visit_months[(dt.year, dt.month)][v["berth"]] += 1
            except (KeyError, ValueError):
                pass
    gate_c_fails = []
    for ym in sorted(set(month_passes) | set(visit_months)):
        for b in berths:
            vm = visit_months[ym].get(b, 0)
            pm = month_passes[ym].get(b, 0)
            if pm > 0 and vm > pm * 2:
                gate_c_fails.append((ym, b, vm, pm))
    gate_c = len(gate_c_fails) == 0
    lines.append(f"  c) visits/month ≤ passes/month×2: {'✅' if gate_c else '❌'}")
    for (yr, mo), b, vm, pm in gate_c_fails[:5]:
        lines.append(f"       FAIL {yr}-{mo:02d} {b}: {vm} visits vs {pm} passes")

    # d) Sep-Nov 2026: ≤3 visits per berth over 3 months
    gate_d_fails = []
    for b in berths:
        cnt = sum(visit_months.get((2026, mo), {}).get(b, 0) for mo in [9, 10, 11])
        if cnt > 3:
            gate_d_fails.append((b, cnt))
    gate_d = len(gate_d_fails) == 0
    lines.append(f"  d) Sep-Nov 2026 ≤3 visits/berth: {'✅' if gate_d else '❌'}")
    for b, cnt in gate_d_fails:
        lines.append(f"       FAIL {b}: {cnt} visits")

    # d-detail) Print east_t_head Sep-Nov 2026 visits (always, for optics cross-check)
    OPTICS_EMPTY_DATES = {"2026-09-28", "2026-10-18", "2026-10-28"}
    eth_aut_visits = []
    if visits:
        for v in visits:
            if v.get("berth") == "east_t_head":
                sd = v.get("start_date", "")
                if "2026-09-01" <= sd <= "2026-11-30":
                    eth_aut_visits.append(v)
    if eth_aut_visits:
        lines.append(f"\n  d-detail) east_t_head Sep-Nov 2026 visits ({len(eth_aut_visits)}):")
        lines.append(f"    {'date':<12} {'orbit':>5} {'pos_m':>7} {'perp_m':>7} {'len_m':>7} {'aspect':>6} {'vdays':>6}  note")
        lines.append("    " + "-" * 70)
        # Build a lookup: visit_id → first loading row for orbit/pos/len/aspect
        vid_to_load = {}
        if hull_rows_all:
            for r in hull_rows_all:
                vid = r.get("visit_id", "")
                if vid and vid not in vid_to_load:
                    vid_to_load[vid] = r
        for v in sorted(eth_aut_visits, key=lambda x: x["start_date"]):
            vid = v.get("visit_id", "")
            lr = vid_to_load.get(vid, {})
            orbit = lr.get("orbit", v.get("orbit", "?"))
            pos_m = lr.get("pos_m", v.get("pos_m", "?"))
            perp_m = lr.get("perp_m", "?")
            len_m = lr.get("len_m", v.get("len_max", "?"))
            # aspect: not stored in visit; get from hull_rows_all if available
            aspect = "?"
            if hull_rows_all:
                for r in hull_rows_all:
                    if r.get("visit_id") == vid:
                        aspect = r.get("aspect", "?")
                        break
            vdays = v.get("visit_days", "?")
            sd = v["start_date"]
            optics_note = ""
            # Check if start_date or nearby dates match optics empty dates
            for od in OPTICS_EMPTY_DATES:
                try:
                    diff = abs((_date.fromisoformat(sd) - _date.fromisoformat(od)).days)
                    if diff <= 6:
                        optics_note = f"[OPTICS: EMPTY {od}]"
                        break
                except Exception:
                    pass
            lines.append(f"    {sd:<12} {str(orbit):>5} {str(pos_m):>7} {str(perp_m):>7} "
                         f"{str(len_m):>7} {str(aspect):>6} {str(vdays):>6}  {optics_note}")

    # e) avg visit_days 1-4 days (2025)
    gate_e_rows = []
    if visits:
        for b in berths:
            bv = [v["visit_days"] for v in visits
                  if v["berth"] == b and v.get("start_date", "").startswith("2025")
                  and v.get("visit_days", 0) > 0]
            avg = sum(bv) / len(bv) if bv else None
            max_days = 5.0 if b == "east_t_head" else 4.0
            ok = avg is not None and 1.0 <= avg <= max_days
            gate_e_rows.append((b, avg, ok, max_days))
    gate_e = all(ok for _, _, ok, _ in gate_e_rows)
    lines.append(f"  e) avg visit_days 1-4 (east_t_head ≤5, 2025): {'✅' if gate_e else '❌'}")
    for b, avg, ok, max_days in gate_e_rows:
        lines.append(f"       {b}: avg={avg:.1f}d ≤{max_days:.0f}d {'✅' if ok else '❌'}" if avg is not None else f"       {b}: no data")

    # f) southeast_pier VLCC class — removed; size shown as n/a on site
    gate_f = True
    sp_loads_2025 = [l for l in all_loadings if l["berth"] == "southeast_pier" and l["date"].startswith("2025")]
    sp_vlcc_2025  = [l for l in sp_loads_2025 if l.get("class") == "VLCC"]
    sp_pct = len(sp_vlcc_2025) / len(sp_loads_2025) * 100 if sp_loads_2025 else 0.0
    lines.append(f"  f) southeast_pier VLCC class: n/a (size not shown on site) ✅"
                 f"  [{len(sp_vlcc_2025)}/{len(sp_loads_2025)} = {sp_pct:.0f}% VLCC, info only]")

    # f-detail) Table of southeast_pier hulls ≥300m in 2025
    sep_big_rows = []
    if hull_rows_all:
        for r in hull_rows_all:
            if (r.get("berth") == "southeast_pier"
                    and r.get("date", "").startswith("2025")
                    and str(r.get("static", "0")) != "1"):
                try:
                    lm = float(r.get("len_m", 0))
                except (ValueError, TypeError):
                    lm = 0.0
                if lm >= 300:
                    sep_big_rows.append(r)
    sep_big_rows.sort(key=lambda r: r["date"])
    if sep_big_rows:
        lines.append(f"\n  f-detail) southeast_pier hulls ≥300m in 2025 ({len(sep_big_rows)} rows):")
        lines.append(f"    {'date':<12} {'orbit':>5} {'pos_m':>7} {'perp_m':>7} {'len_m':>7} {'aspect':>6}")
        lines.append("    " + "-" * 50)
        for r in sep_big_rows:
            lines.append(f"    {r.get('date',''):<12} {str(r.get('orbit','?')):>5} "
                         f"{str(r.get('pos_m','?')):>7} {str(r.get('perp_m','?')):>7} "
                         f"{str(r.get('len_m','?')):>7} {str(r.get('aspect','?')):>6}")

    # perp_m distribution per berth — 2025 non-static hull rows
    if hull_rows_all:
        lines.append("\n=== perp_m distribution 2025 (non-static hull rows) ===")
        lines.append(f"  {'Berth':<20} {'side':>5} {'min':>7} {'median':>8} {'max':>7}  note")
        lines.append("  " + "-" * 60)
        import statistics as _stats
        for b in berths:
            by_side_perp: dict = {}
            for r in hull_rows_all:
                if (r.get("berth") == b
                        and r.get("date", "").startswith("2025")
                        and str(r.get("static", "0")) != "1"):
                    try:
                        pm = float(r["perp_m"])
                        s = int(r.get("side", 0))
                    except (ValueError, TypeError, KeyError):
                        continue
                    by_side_perp.setdefault(s, []).append(pm)
            if not by_side_perp:
                lines.append(f"  {b:<20}  — no 2025 data")
                continue
            for side_k in sorted(by_side_perp):
                vals = by_side_perp[side_k]
                mn  = min(vals)
                mx  = max(vals)
                med = _stats.median(vals)
                rng = mx - mn
                note = "⚠ possibly constant — not pier-relative" if rng < 50 else ""
                lines.append(f"  {b:<20} {side_k:>+5} {mn:>7.0f} {med:>8.0f} {mx:>7.0f}  {note}")

    all_pass = gate_b and gate_c and gate_d and gate_e and gate_f
    lines.append(f"\n  Overall b-f: {'ALL ✅' if all_pass else 'SOME ❌ — see above'}")

    for line in lines:
        print(line)

    val_txt = OUT_DIR / "validation_report.txt"
    with open(val_txt, "w") as f:
        f.write("\n".join(lines) + "\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def _bbox_dims(bbox_obj, resolution):
    """Return (width_px, height_px) for a sentinelhub BBox at given resolution."""
    import math
    lat_mid  = (bbox_obj.min_y + bbox_obj.max_y) / 2
    width_m  = (bbox_obj.max_x - bbox_obj.min_x) * 111320 * math.cos(math.radians(lat_mid))
    height_m = (bbox_obj.max_y - bbox_obj.min_y) * 111320
    return max(1, round(width_m / resolution)), max(1, round(height_m / resolution))


def run(incremental=False):
    from datetime import date as _date
    today = datetime.utcnow().strftime("%Y-%m-%d")

    start_date   = HISTORY_START
    old_passes   = []
    old_loadings = []
    old_hull_rows = []

    if incremental and JSON_OUT.exists():
        try:
            old_data     = json.loads(JSON_OUT.read_text())
            old_passes   = old_data.get("passes", [])
            old_loadings = old_data.get("loadings", [])
            if old_passes:
                last_date  = max(p["date"] for p in old_passes)
                start_date = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                print(f"Incremental: resuming from {start_date} (last processed {last_date})")
            csv_path_inc = OUT_DIR / "berth_v4_history.csv"
            if csv_path_inc.exists():
                with open(csv_path_inc, newline="") as _f:
                    for _r in csv.DictReader(_f):
                        old_hull_rows.append(_r)
        except Exception as _exc:
            print(f"WARNING: incremental load failed ({_exc}), running full history")
            start_date = HISTORY_START
            old_passes = []
            old_loadings = []
            old_hull_rows = []

    if start_date > today:
        print(f"Already up to date (start={start_date} > today={today}). Nothing to do.")
        return

    # Init prev_hulls from last known pass per (berth, orbit) in old_passes
    prev_hulls: dict = {}
    recent_hulls: dict = {berth: deque(maxlen=2) for berth in V4_CONFIG}  # cross-orbit dedup
    all_hull_positions: list = []  # for post-run static detection
    if old_passes:
        for orbit in ORBITS:
            orbit_passes = [p for p in old_passes if p.get("orbit") == orbit]
            if orbit_passes:
                lp = orbit_passes[-1]
                for berth in V4_CONFIG:
                    prev_hulls[(berth, orbit)] = [
                        {
                            "pos_m":  h["pos_m"],
                            "len_m":  h["len_m"],
                            # perp_m not in old JSON — use side sign as proxy
                            "perp_m": h.get("perp_m", float(h.get("side", 0))),
                        }
                        for h in lp.get(berth, {}).get("hulls", [])
                    ]

    try:
        entries = discover_dates(start_date, today)
    except Exception as exc:
        sys.exit(f"FATAL: PC catalog search failed — {exc}")

    # Pre-load BG per (berth, orbit)
    bg_cache = {}
    bg_source_cache = {}
    for berth, cfg in V4_CONFIG.items():
        for orbit in ORBITS:
            p = cfg["bg_dir"] / f"bg_o{orbit}_{berth}.npy"
            if p.exists():
                bg_cache[(berth, orbit)] = np.load(p)
                bg_source_cache[(berth, orbit)] = cfg["bg_dir"].name
            else:
                p_v3 = BG_V3 / f"bg_o{orbit}_{berth}.npy"
                if p_v3.exists():
                    bg_cache[(berth, orbit)] = np.load(p_v3)
                    bg_source_cache[(berth, orbit)] = "bg_v3_fallback"
                    print(f"  BG fallback: {berth} o{orbit} → bg_v3")
                else:
                    bg_cache[(berth, orbit)] = None
                    bg_source_cache[(berth, orbit)] = None
                    print(f"  MISSING BG: {berth} o{orbit}")

    # 2026 BG for east_t_head o101 (median of optically-empty 2026 scenes)
    if BG_2026_PATH.exists():
        bg_cache[("east_t_head", 101, "2026")] = np.load(BG_2026_PATH)
        print(f"  Loaded 2026 BG: {BG_2026_PATH.name}")
    else:
        bg_cache[("east_t_head", 101, "2026")] = None
        print(f"  MISSING 2026 BG: {BG_2026_PATH.name} — will fall back to standard BG")

    cell_bboxes = {b: cell_bbox_for(*berth_center(pts)) for b, pts in BERTHS.items()}
    cell_dims   = {b: _bbox_dims(cell_bboxes[b], resolution=RESOLUTION) for b in BERTHS}

    # Pass-level results (for JSON output)
    passes_out = []
    loadings   = []
    hull_rows  = []  # for CSV — new rows only; merged with old_hull_rows at write time

    # Visit tracking
    visit_counter: dict = defaultdict(int)  # berth → next int
    active_visits: dict = {}  # (berth, side) → {visit_id, berth, side, pos_m, len_max, class_max, start_date, last_seen_date, last_valid_seen_date}
    visits: list = []  # completed + ongoing, finalized after main loop

    for entry in entries:
        date_str = entry["date_str"]
        orbit    = entry["orbit"]
        scene_dt = entry["scene_dt"]
        print(f"\n{date_str} o{orbit}  scene={scene_dt[:16]}")

        pass_record = {"date": date_str, "orbit": orbit}

        for berth, pts in BERTHS.items():
            cfg  = V4_CONFIG[berth]
            # east_t_head o101: switch to 2026 BG for dates ≥ BG_2026_CUTOFF
            if berth == "east_t_head" and orbit == 101 and date_str >= BG_2026_CUTOFF:
                bg = bg_cache.get(("east_t_head", 101, "2026")) or bg_cache.get((berth, orbit))
                bg_src = "bg_v4_o101_2026" if bg_cache.get(("east_t_head", 101, "2026")) is not None else bg_source_cache.get((berth, orbit))
            else:
                bg   = bg_cache.get((berth, orbit))
                bg_src = bg_source_cache.get((berth, orbit))
            if bg is None:
                pass_record[berth] = {"det": "NO_BG", "hulls": []}
                continue

            linear = fetch_s1_linear(entry, cell_bboxes[berth])
            if linear is None:
                pass_record[berth] = {"det": "ERR", "hulls": []}
                continue

            if bg.shape != linear.shape:
                bg_r = nd_zoom(bg, (linear.shape[0]/bg.shape[0],
                                    linear.shape[1]/bg.shape[1]), order=1)
            else:
                bg_r = bg

            ratio = np.where((linear>0)&(bg_r>0),
                             10*np.log10(linear/bg_r), np.nan).astype(np.float32)
            geom = build_geom(pts, cell_bboxes[berth], linear.shape,
                              cfg["pier"], cfg["water_i"], cfg["water_o"])

            blobs, best, detected = find_blobs(
                ratio, geom["water_mask"], geom["axis_uvec"],
                merge_gap_m=cfg.get("merge_gap", 0),
                min_len_m=cfg["min_len"],
                min_aspect=cfg.get("aspect_thr", 3.0),
                return_all=True,
            )

            det_str = "OCC" if detected else "EMP"

            # Build hull list from all qualifying blobs
            cw, ch = cell_dims[berth]
            cbbox  = cell_bboxes[berth]
            ux, uy = geom["axis_uvec"]

            # Pier axis perpendicular offset in pixel space.
            # perp_ctr from find_blobs is absolute (from image top-left corner);
            # subtract the pier axis's own perp coordinate to get pier-relative signed distance.
            _clon_b, _clat_b = berth_center(pts)
            _cw, _ch = cell_dims[berth]
            _cbbox = cell_bboxes[berth]
            _ux, _uy = ux, uy
            _col_ctr_b = (_clon_b - _cbbox.min_x) / (_cbbox.max_x - _cbbox.min_x) * _cw
            _row_ctr_b = (_cbbox.max_y - _clat_b) / (_cbbox.max_y - _cbbox.min_y) * _ch
            axis_perp_px = _col_ctr_b * (-_uy) + _row_ctr_b * _ux

            prev = prev_hulls.get((berth, orbit), [])
            hull_list = []

            # Step 1: keep only blobs that meet min_len/max_len thresholds (after merge)
            min_len = cfg["min_len"]
            max_len = cfg.get("max_len", float("inf"))
            qualifying = [b for b in (blobs or []) if b["length_m"] >= min_len and b["length_m"] <= max_len]

            # Step 2: physical cap — max 2 hulls per pier side (corrected signed perp)
            by_side: dict = {}
            for b in qualifying:
                sk = 1 if (b.get("perp_ctr", 0) - axis_perp_px) >= 0 else -1
                by_side.setdefault(sk, []).append(b)

            capped: list = []
            for sk, side_blobs in by_side.items():
                side_blobs.sort(key=lambda b: b["length_m"], reverse=True)
                capped.extend(side_blobs[:2])
                if len(side_blobs) > 2:
                    extras = side_blobs[2:]
                    print(f"  CAP {berth} side={sk:+d}: dropping "
                          f"{len(extras)} hull(s) "
                          f"len={[round(b['length_m']) for b in extras]}m")

            for blob in capped:
                len_m = float(blob["length_m"])
                wid_m = float(blob.get("width_m", len_m / max(blob.get("aspect", 3.0), 1)))

                # Geographic centre of hull
                axis_mid_px = (blob["axis_min"] + blob["axis_max"]) / 2
                perp_ctr    = blob.get("perp_ctr", 0)
                # Signed distance from pier axis (not from image corner)
                _corr_perp  = float(perp_ctr) - axis_perp_px
                side        = 1 if _corr_perp >= 0 else -1
                pos_m       = round(float(axis_mid_px) * RESOLUTION, 0)
                perp_m      = round(_corr_perp * RESOLUTION, 0)

                # pixel position within cell
                col_c = axis_mid_px * ux + perp_ctr * (-uy)
                row_c = axis_mid_px * uy + perp_ctr * ux
                lon_c = cbbox.min_x + (col_c / cw) * (cbbox.max_x - cbbox.min_x)
                lat_c = cbbox.max_y - (row_c / ch) * (cbbox.max_y - cbbox.min_y)

                # Hulls 150–<200m: OCC signal but not tracked as visits/loadings
                is_sub_visit = len_m < VISIT_MIN_LEN_M

                if is_sub_visit:
                    cls      = "hull <200m"
                    new_load = False
                    arrived  = False
                    vid      = ""
                else:
                    cls        = classify_hull(len_m)
                    recent_any = [h for hl in recent_hulls[berth] for h in hl]
                    new_load   = is_new_load_v2(pos_m, len_m, perp_m, prev, recent_any)

                    # Visit tracking: assign visit_id and arrived flag
                    arrived = new_load
                    ak = (berth, side)
                    if arrived:
                        visit_counter[berth] += 1
                        vid = f"v{visit_counter[berth]:05d}"
                        active_visits[ak] = {
                            "visit_id": vid, "berth": berth, "side": side,
                            "pos_m": pos_m, "len_max": len_m, "class_max": cls,
                            "start_date": date_str, "last_seen_date": date_str,
                            "last_valid_seen_date": date_str,
                        }
                    elif ak in active_visits and abs(pos_m - active_visits[ak]["pos_m"]) <= 200:
                        av = active_visits[ak]
                        av["last_seen_date"] = date_str
                        av["last_valid_seen_date"] = date_str
                        if len_m > av["len_max"]:
                            av["len_max"] = len_m
                            av["class_max"] = classify_hull(len_m)
                        vid = av["visit_id"]
                    else:
                        # Different position on same side → distinct new arrival
                        arrived  = True
                        new_load = True
                        visit_counter[berth] += 1
                        vid = f"v{visit_counter[berth]:05d}"
                        active_visits[ak] = {
                            "visit_id": vid, "berth": berth, "side": side,
                            "pos_m": pos_m, "len_max": len_m, "class_max": cls,
                            "start_date": date_str, "last_seen_date": date_str,
                            "last_valid_seen_date": date_str,
                        }

                hull = {
                    "side":     side,
                    "perp_m":   perp_m,
                    "pos_m":    pos_m,
                    "lon":      round(lon_c, 6),
                    "lat":      round(lat_c, 6),
                    "len_m":    round(len_m, 1),
                    "width_m":  round(wid_m, 1),
                    "aspect":   round(float(blob.get("aspect", 0)), 2),
                    "class":    cls,
                    "new_load": new_load,
                    "arrived":  arrived,
                    "visit_id": vid,
                }
                hull_list.append(hull)

                hull_rows.append({
                    "date": date_str, "orbit": orbit, "berth": berth,
                    "hull_idx": len(hull_list) - 1,
                    "det": det_str, "bg_source": bg_src,
                    **hull,
                })

                if not is_sub_visit and new_load:
                    loadings.append({
                        "date":     date_str,
                        "orbit":    orbit,
                        "berth":    berth,
                        "side":     side,
                        "pos_m":    pos_m,
                        "lon":      round(lon_c, 6),
                        "lat":      round(lat_c, 6),
                        "len_m":    round(len_m, 1),
                        "class":    cls,
                        "visit_id": vid,
                    })

            # Only include visit-qualifying hulls (≥200m) in dedup state
            compact = [{"pos_m": h["pos_m"], "len_m": h["len_m"], "perp_m": h["perp_m"]}
                       for h in hull_list if h["class"] != "hull <200m"]
            prev_hulls[(berth, orbit)] = compact
            # Cross-orbit dedup: keep last 2 passes (any orbit) per berth
            recent_hulls[berth].append(compact)
            # Collect positions for static filter (post-run pass)
            for h in hull_list:
                all_hull_positions.append({
                    "berth": berth, "orbit": orbit, "date": date_str,
                    "pos_m": h["pos_m"], "side": h["side"], "len_m": h["len_m"],
                })

            if not hull_list and detected and best:
                # find_blobs returned a best blob but no qualifying hulls — add as fallback
                len_m = float(best["length_m"])
                perp_ctr_b = best.get("perp_ctr", 0)
                _corr_b    = float(perp_ctr_b) - axis_perp_px
                perp_m_b   = round(_corr_b * RESOLUTION, 0)
                hull_list.append({
                    "side": 1 if _corr_b >= 0 else -1,
                    "perp_m": perp_m_b,
                    "pos_m": 0, "lon": 0, "lat": 0,
                    "len_m": round(len_m, 1),
                    "width_m": round(len_m / max(best.get("aspect", 3.0), 1), 1),
                    "class": classify_hull(len_m),
                    "new_load": is_new_load_v2(0, len_m, perp_m_b, prev,
                                               [h for hl in recent_hulls[berth] for h in hl]),
                })

            # Departed detection: only on valid passes (OCC/EMP), not NO_BG/ERR.
            # A visit closes if:
            #   1) valid pass + hull absent on that side (confirmed empty), OR
            #   2) >6 days gap since last_valid_seen_date (no valid passes for >6d)
            departed_this_pass = []
            current_sides = {h["side"] for h in hull_list}
            for ak2, av2 in list(active_visits.items()):
                if ak2[0] != berth:
                    continue
                lvsd = _date.fromisoformat(av2.get("last_valid_seen_date", av2["start_date"]))
                cur_dt = _date.fromisoformat(date_str)
                gap_days = (cur_dt - lvsd).days
                # Condition 1: valid pass, hull absent on this side
                close_by_empty = (det_str in ("OCC", "EMP")) and (ak2[1] not in current_sides)
                # Condition 2: >6 days gap without any valid hull pass
                close_by_gap = gap_days > 6
                if close_by_empty or close_by_gap:
                    departed_this_pass.append(av2["visit_id"])
                    av2["end_date"] = date_str
                    lvsd_str = av2.get("last_valid_seen_date", av2["start_date"])
                    vdays = (_date.fromisoformat(lvsd_str) - _date.fromisoformat(av2["start_date"])).days + 1
                    visits.append({**av2, "visit_days": vdays})
                    del active_visits[ak2]

            pass_record[berth] = {"det": det_str, "hulls": hull_list,
                                  "departed": departed_this_pass}
            if departed_this_pass:
                print(f"  {berth}: DEPARTED {departed_this_pass}")
            print(f"  {berth}: {det_str}  hulls={len(hull_list)}")

        passes_out.append(pass_record)

    # ── Close any visits still open at end of history ────────────────────────
    for ak, av in active_visits.items():
        lvsd_str = av.get("last_valid_seen_date", av["start_date"])
        vdays = (_date.fromisoformat(lvsd_str) - _date.fromisoformat(av["start_date"])).days + 1
        visits.append({**av, "visit_days": vdays})

    # ── Static object filter (post-run, full-history pass) ────────────────────
    static_set = _build_static_set(all_hull_positions)
    static_points = sorted(
        [{"berth": b, "orbit": o, "date": d, "pos_m": p, "side": s}
         for (b, o, d, p, s) in static_set],
        key=lambda x: (x["berth"], x["orbit"], x["date"])
    )
    loadings_before_static = loadings[:]
    loadings = [
        l for l in loadings
        if (l["berth"], l["orbit"], l["date"], l["pos_m"], l["side"]) not in static_set
    ]
    n_removed = len(loadings_before_static) - len(loadings)
    print(f"\nStatic filter: {len(static_set)} hull detections flagged, "
          f"{n_removed} new loadings removed")
    if static_points:
        print(f"Static objects ({len(static_points)} detections):")
        for sp in static_points[:30]:
            print(f"  {sp['date']} o{sp['orbit']:>3} {sp['berth']:<20} "
                  f"pos={sp['pos_m']:.0f}m side={sp['side']:+d}")
        if len(static_points) > 30:
            print(f"  ... ({len(static_points) - 30} more)")

    # Filter visits to only those with at least one valid loading
    valid_visit_ids = {l["visit_id"] for l in loadings if "visit_id" in l}
    # Also keep visits from old_loadings
    valid_visit_ids |= {l["visit_id"] for l in old_loadings if "visit_id" in l}
    visits = [v for v in visits if v["visit_id"] in valid_visit_ids]

    # Compute visit_days per visit (use max of hull-observed days)
    for v in visits:
        if "visit_days" not in v:
            v["visit_days"] = 0

    # Annotate loadings with class from their visit (use visit's class_max)
    vid_to_class = {v["visit_id"]: v["class_max"] for v in visits}
    for l in loadings:
        if "visit_id" in l and l["visit_id"] in vid_to_class:
            l["class"] = vid_to_class[l["visit_id"]]

    # ── Generate static_objects.csv ───────────────────────────────────────────
    # Group static detections by (berth, orbit, side, pos cluster) → one row per cluster
    static_obj_rows = []
    # Build lookup for len_m from all_hull_positions
    _len_lookup: dict = {}
    for h in all_hull_positions:
        k = (h["berth"], h["orbit"], h["date"], h["pos_m"], h["side"])
        _len_lookup[k] = h.get("len_m", "")

    by_bos2: dict = {}
    for (berth, orbit, date_s, pos_m, side) in static_set:
        key = (berth, orbit, side)
        lm = _len_lookup.get((berth, orbit, date_s, pos_m, side), "")
        by_bos2.setdefault(key, []).append({"date": date_s, "pos_m": pos_m, "len_m": lm})
    for (berth, orbit, side), obs in by_bos2.items():
        obs_s = sorted(obs, key=lambda x: x["date"])
        dates = [o["date"] for o in obs_s]
        positions = [o["pos_m"] for o in obs_s]
        lens = [o["len_m"] for o in obs_s if o["len_m"] != ""]
        avg_len = round(sum(float(l) for l in lens) / len(lens), 1) if lens else ""
        static_obj_rows.append({
            "berth": berth, "orbit": orbit, "side": side,
            "pos_m": round(sum(positions) / len(positions), 0),
            "len_m": avg_len,
            "perp_m": "",
            "first_date": dates[0], "last_date": dates[-1],
            "n_passes": len(obs_s),
        })
    static_obj_rows.sort(key=lambda r: (r["berth"], r["orbit"], r["first_date"]))
    static_csv = OUT_DIR / "static_objects.csv"
    with open(static_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["berth","orbit","side","pos_m","len_m","perp_m",
                                           "first_date","last_date","n_passes"])
        w.writeheader()
        for r in static_obj_rows:
            w.writerow(r)
    print(f"Saved: {static_csv} ({len(static_obj_rows)} static objects)")

    # Mark hull_rows with static flag
    for r in hull_rows:
        key = (r["berth"], r["orbit"], r["date"], r["pos_m"], r["side"])
        r["static"] = "1" if key in static_set else "0"

    # ── CSV output (hull-level) — merge old + new ─────────────────────────────
    csv_path = OUT_DIR / "berth_v4_history.csv"
    fieldnames = ["date","orbit","berth","hull_idx","det","side","perp_m","pos_m",
                  "lon","lat","len_m","width_m","aspect","class","new_load","arrived","visit_id","static","bg_source"]
    all_hull_rows = old_hull_rows + hull_rows
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in all_hull_rows:
            w.writerow(r)
    print(f"\nSaved: {csv_path} ({len(all_hull_rows)} hull rows)")

    # ── Metrics ───────────────────────────────────────────────────────────────
    from datetime import date as _date
    today_dt = _date.fromisoformat(today)
    d4w_start  = (today_dt - timedelta(weeks=4)).isoformat()
    d4w_py_end = (today_dt - timedelta(weeks=52)).isoformat()
    d4w_py_start = (today_dt - timedelta(weeks=56)).isoformat()

    _all_load = old_loadings + loadings
    l4w    = count_loadings(_all_load, d4w_start, today)
    l4w_py = count_loadings(_all_load, d4w_py_start, d4w_py_end)

    # Last loading event
    last_ship = None
    if _all_load:
        ls = _all_load[-1]
        last_ship = {
            "date":   ls["date"],
            "berth":  ls["berth"],
            "class":  ls["class"],
            "len_m":  ls["len_m"],
        }

    # Days since last loading
    days_since = None
    if last_ship:
        last_dt = _date.fromisoformat(last_ship["date"])
        days_since = (today_dt - last_dt).days

    # avg visit_days 2025 per berth
    visits_2025 = [v for v in visits if v.get("start_date", "").startswith("2025")]
    avg_visit_days_2025: dict = {}
    for b in ["sea_island", "east_t_head", "southeast_pier"]:
        bv = [v["visit_days"] for v in visits_2025 if v["berth"] == b and v.get("visit_days", 0) > 0]
        avg_visit_days_2025[b] = round(sum(bv) / len(bv), 1) if bv else None

    metrics = {
        "loadings_4w":        l4w,
        "loadings_4w_py":     l4w_py,
        "last_ship":          last_ship,
        "days_since_loading": days_since,
        "avg_visit_days_2025": avg_visit_days_2025,
    }

    # ── JSON output for site (v2) — merge old passes + loadings + visits ────────────
    all_loadings = old_loadings + loadings

    obj = {
        "generated": today,
        "version":   "v2.0",
        "note":      "lower bound — detector misses ~1/3 of ships; precision ~92% when OCC. Class by hull length (±50 m). Visits shorter than the gap between passes may be missed.",
        "berths":    ["sea_island", "east_t_head", "southeast_pier"],
        "passes":    old_passes + passes_out,
        "loadings":  all_loadings,
        "visits":    visits,
        "metrics":   metrics,
    }
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(obj, separators=(",",":")))
    print(f"Saved: {JSON_OUT} ({len(passes_out)} passes, {len(loadings)} loadings)")

    # ── Detection summary ─────────────────────────────────────────────────────
    print("\n=== Detection summary ===")
    print(f"Period: {HISTORY_START} → {today}")
    print(f"Loadings detected: {len(loadings)}")
    print(f"Loadings last 4w: {l4w}")
    if last_ship:
        print(f"Last ship: {last_ship}")

    # ── Validation report (table) ─────────────────────────────────────────────
    _print_validation_report(
        old_passes + passes_out,
        old_loadings + loadings,
        loadings_before_static=old_loadings + loadings_before_static,
        static_points=static_points,
        visits=visits,
        hull_rows_all=all_hull_rows,
    )

    # ── Supabase write ────────────────────────────────────────────────────────
    svc_key  = os.environ.get("SUPABASE_SERVICE_KEY") or ""
    supa_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    if not svc_key:
        print("\nSUPABASE_SERVICE_KEY not set — history saved to CSV/JSON only, DB write skipped.")
        return
    if not supa_url:
        sys.exit("FATAL: SUPABASE_SERVICE_KEY set but SUPABASE_URL is empty")

    # DB rows: backward-compat with old berth_occupancy schema
    # Use best hull (max len_m) per (date, orbit, berth)
    db_map = {}
    for r in hull_rows:
        key = (r["date"], r["orbit"], r["berth"])
        if key not in db_map or r["len_m"] > db_map[key].get("length_m", 0):
            db_map[key] = {
                "date": r["date"], "orbit": r["orbit"], "berth": r["berth"],
                "det": r["det"], "length_m": r["len_m"], "aspect": None,
            }
    # Include EMP rows from passes without hulls
    for p in passes_out:
        for berth in ["sea_island","east_t_head","southeast_pier"]:
            bd = p.get(berth, {})
            key = (p["date"], p["orbit"], berth)
            if key not in db_map and bd.get("det") in ("EMP","NO_BG","ERR"):
                db_map[key] = {
                    "date": p["date"], "orbit": p["orbit"], "berth": berth,
                    "det": bd["det"], "length_m": None, "aspect": None,
                }

    import urllib.request as _ureq
    db_rows = list(db_map.values())
    payload = json.dumps(db_rows).encode()
    req = _ureq.Request(
        f"{supa_url}/rest/v1/berth_occupancy",
        data=payload,
        headers={
            "apikey": svc_key,
            "Authorization": f"Bearer {svc_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        },
        method="POST",
    )
    try:
        with _ureq.urlopen(req) as resp:
            print(f"\nDB write OK: HTTP {resp.status} — {len(db_rows)} rows upserted")
    except Exception as exc:
        print(f"WARNING: DB write skipped — {exc}")


if __name__ == "__main__":
    import argparse as _ap
    _p = _ap.ArgumentParser()
    _p.add_argument("--incremental", action="store_true")
    _args = _p.parse_args()
    run(incremental=_args.incremental)
