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
OUT_DIR = ROOT / "diagnostics" / "berth_v4" / "history_v4"
JSON_OUT = ROOT / "data" / "berth_occupancy.json"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HISTORY_START   = "2024-01-01"
ORBITS          = [35, 101]

BERTHS = BERTH_COORDS  # alias for local use

V4_CONFIG = {
    # all BGs rebuilt from PC and saved in BG_V4
    "sea_island":     {"pier": 20, "water_i": 20, "water_o": 80, "min_len": 120, "aspect_thr": 2.0, "merge_gap": 120, "bg_dir": BG_V4},
    "east_t_head":    {"pier": 20, "water_i": 20, "water_o": 80, "min_len": 150, "aspect_thr": 3.0, "merge_gap": 120, "bg_dir": BG_V4},
    "southeast_pier": {"pier": 20, "water_i": 20, "water_o": 80, "min_len": 150, "aspect_thr": 3.0, "merge_gap": 120, "bg_dir": BG_V4},
}

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

# ── Metric helpers ─────────────────────────────────────────────────────────────

def count_loadings(loadings, start, end):
    subset = [l for l in loadings if start <= l["date"] <= end]
    total = len(subset)
    vlcc  = sum(1 for l in subset if l["class"] == "VLCC")
    suez  = sum(1 for l in subset if l["class"] == "SuezAframax")
    return {"total": total, "VLCC": vlcc, "SuezAframax": suez}

# ── Validation report ─────────────────────────────────────────────────────────

def _print_validation_report(all_passes, all_loadings):
    """Print hull-count and loading-rate sanity table.

    Expected:
      hulls/pass mean ≤ 1.5, max ≤ 3 per berth
      loadings/month ≤ passes/month
      VLCC share 2025 — majority on sea_island and east_t_head
    """
    from collections import defaultdict
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

    print("\n=== Validation report ===")
    print(f"\n{'Berth':<20} {'Passes':>7} {'Hull/pass mean':>14} {'Hull/pass max':>13} "
          f"{'Status':>8}")
    print("-" * 68)
    for b in berths:
        hc = hull_counts[b]
        if not hc:
            print(f"{b:<20} {'—':>7}")
            continue
        mean_h = sum(hc) / len(hc)
        max_h  = max(hc)
        ok     = "OK" if mean_h <= 1.5 and max_h <= 3 else "WARN"
        print(f"{b:<20} {len(hc):>7} {mean_h:>14.2f} {max_h:>13d} {ok:>8}")

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
        print("\nWARN loadings > passes in month:")
        for (yr, mo), b, lm, pm in months_with_overcount[:10]:
            print(f"  {yr}-{mo:02d} {b}: {lm} loadings vs {pm} passes")
    else:
        print("\nOK  loadings/month ≤ passes/month for all berth-months")

    # VLCC share 2025
    print(f"\n{'Berth':<20} {'Loadings 2025':>13} {'VLCC':>6} {'VLCC%':>7}")
    print("-" * 50)
    for b in berths:
        tot = total_2025[b]
        vlc = vlcc_2025[b]
        pct = vlc / tot * 100 if tot else 0.0
        print(f"{b:<20} {tot:>13} {vlc:>6} {pct:>6.0f}%")


# ── Main ───────────────────────────────────────────────────────────────────────

def _bbox_dims(bbox_obj, resolution):
    """Return (width_px, height_px) for a sentinelhub BBox at given resolution."""
    import math
    lat_mid  = (bbox_obj.min_y + bbox_obj.max_y) / 2
    width_m  = (bbox_obj.max_x - bbox_obj.min_x) * 111320 * math.cos(math.radians(lat_mid))
    height_m = (bbox_obj.max_y - bbox_obj.min_y) * 111320
    return max(1, round(width_m / resolution)), max(1, round(height_m / resolution))


def run(incremental=False):
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
                if berth == "east_t_head" and orbit == 35 and cfg["bg_dir"] == BG_V4:
                    bg_source_cache[(berth, orbit)] = "bg_v4_o35_2026"
                else:
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

    cell_bboxes = {b: cell_bbox_for(*berth_center(pts)) for b, pts in BERTHS.items()}
    cell_dims   = {b: _bbox_dims(cell_bboxes[b], resolution=RESOLUTION) for b in BERTHS}

    # Pass-level results (for JSON output)
    passes_out = []
    loadings   = []
    hull_rows  = []  # for CSV — new rows only; merged with old_hull_rows at write time

    for entry in entries:
        date_str = entry["date_str"]
        orbit    = entry["orbit"]
        scene_dt = entry["scene_dt"]
        print(f"\n{date_str} o{orbit}  scene={scene_dt[:16]}")

        pass_record = {"date": date_str, "orbit": orbit}

        for berth, pts in BERTHS.items():
            cfg  = V4_CONFIG[berth]
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

            prev = prev_hulls.get((berth, orbit), [])
            hull_list = []

            # Step 1: keep only blobs that meet min_len threshold (after merge)
            min_len = cfg["min_len"]
            qualifying = [b for b in (blobs or []) if b["length_m"] >= min_len]

            # Step 2: physical cap — max 2 hulls per pier side (sign of perp_ctr)
            by_side: dict = {}
            for b in qualifying:
                sk = 1 if b.get("perp_ctr", 0) >= 0 else -1
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
                side        = 1 if perp_ctr >= 0 else -1
                pos_m       = round(float(axis_mid_px) * RESOLUTION, 0)
                perp_m      = round(float(perp_ctr) * RESOLUTION, 0)

                # pixel position within cell
                col_c = axis_mid_px * ux + perp_ctr * (-uy)
                row_c = axis_mid_px * uy + perp_ctr * ux
                lon_c = cbbox.min_x + (col_c / cw) * (cbbox.max_x - cbbox.min_x)
                lat_c = cbbox.max_y - (row_c / ch) * (cbbox.max_y - cbbox.min_y)

                cls      = classify_hull(len_m)
                new_load = is_new_load(pos_m, len_m, perp_m, prev)

                hull = {
                    "side":     side,
                    "perp_m":   perp_m,
                    "pos_m":    pos_m,
                    "lon":      round(lon_c, 6),
                    "lat":      round(lat_c, 6),
                    "len_m":    round(len_m, 1),
                    "width_m":  round(wid_m, 1),
                    "class":    cls,
                    "new_load": new_load,
                }
                hull_list.append(hull)

                hull_rows.append({
                    "date": date_str, "orbit": orbit, "berth": berth,
                    "hull_idx": len(hull_list) - 1,
                    "det": det_str, "bg_source": bg_src,
                    **hull,
                })

                if new_load:
                    loadings.append({
                        "date":   date_str,
                        "orbit":  orbit,
                        "berth":  berth,
                        "side":   side,
                        "pos_m":  pos_m,
                        "lon":    round(lon_c, 6),
                        "lat":    round(lat_c, 6),
                        "len_m":  round(len_m, 1),
                        "class":  cls,
                    })

            prev_hulls[(berth, orbit)] = [
                {"pos_m": h["pos_m"], "len_m": h["len_m"], "perp_m": h["perp_m"]}
                for h in hull_list
            ]

            if not hull_list and detected and best:
                # find_blobs returned a best blob but no qualifying hulls — add as fallback
                len_m = float(best["length_m"])
                perp_ctr_b = best.get("perp_ctr", 0)
                perp_m_b   = round(float(perp_ctr_b) * RESOLUTION, 0)
                hull_list.append({
                    "side": 1 if perp_ctr_b >= 0 else -1,
                    "perp_m": perp_m_b,
                    "pos_m": 0, "lon": 0, "lat": 0,
                    "len_m": round(len_m, 1),
                    "width_m": round(len_m / max(best.get("aspect", 3.0), 1), 1),
                    "class": classify_hull(len_m),
                    "new_load": is_new_load(0, len_m, perp_m_b, prev),
                })

            pass_record[berth] = {"det": det_str, "hulls": hull_list}
            print(f"  {berth}: {det_str}  hulls={len(hull_list)}")

        passes_out.append(pass_record)

    # ── CSV output (hull-level) — merge old + new ─────────────────────────────
    csv_path = OUT_DIR / "berth_v4_history.csv"
    fieldnames = ["date","orbit","berth","hull_idx","det","side","perp_m","pos_m",
                  "lon","lat","len_m","width_m","class","new_load","bg_source"]
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

    metrics = {
        "loadings_4w":     l4w,
        "loadings_4w_py":  l4w_py,
        "last_ship":       last_ship,
        "days_since_loading": days_since,
    }

    # ── JSON output for site (v5.0) — merge old passes + loadings ────────────
    all_loadings = old_loadings + loadings
    obj = {
        "generated": today,
        "version":   "v5.0",
        "note":      "lower bound — detector misses ~1/3 of ships; precision ~92% when OCC",
        "berths":    ["sea_island", "east_t_head", "southeast_pier"],
        "passes":    old_passes + passes_out,
        "loadings":  all_loadings,
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
    _print_validation_report(old_passes + passes_out, old_loadings + loadings)

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
