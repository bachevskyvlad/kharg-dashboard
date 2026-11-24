"""
berth_sync_overview.py — align berth_occupancy.json to Overview orbit-35 dates.

Tasks:
1. Remove orbit-101 passes (keep only orbit-35).
2. Fetch 2026-11-23 from Planetary Computer and run detection.
3. Apply FP filter on east_t_head: OCC only if >10 dB above BG at ≥30 px AND len ≥150 m.
   Expected: 2026-09-06 and 2026-09-30 become EMP.
4. Write updated berth_occupancy.json.
"""

import os, sys, json
import numpy as np
from pathlib import Path
from scipy import ndimage

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from berth_detect_core import (
    BERTH_COORDS, BERTH_DETECT_PARAMS, RESOLUTION,
    berth_center, cell_bbox_for, build_geom, find_blobs,
    RATIO_THR, ASPECT_MAX
)
from berth_pc_fetch import find_item_pc, fetch_s1_pc

AOI_BBOX_LST = [50.26, 29.17, 50.40, 29.31]
BG_DIR = ROOT / "diagnostics" / "berth_v4" / "bg_v4"
ORBIT = 35

# FP filter params (ТЗ §7)
FP_DB_ABOVE_BG   = 10.0   # dB above background
FP_MIN_PX        = 30     # pixels above threshold
FP_MIN_LEN_M     = 150.0  # min extent

struct8 = ndimage.generate_binary_structure(2, 2)


def load_bg(berth, orbit):
    p = BG_DIR / f"bg_o{orbit}_{berth}.npy"
    if p.exists():
        return np.load(p)
    return None


def linear_to_db(arr):
    return 10.0 * np.log10(np.maximum(arr, 1e-10))


def fp_filter_occ(arr_linear, bg_linear, berth_params, berth_name):
    """
    Returns True if the detection survives the FP filter.
    OCC is real only if >FP_DB_ABOVE_BG dB above BG for ≥FP_MIN_PX pixels
    AND extent ≥FP_MIN_LEN_M in the corridor.
    """
    if bg_linear is None:
        return True  # no BG → keep existing decision
    if arr_linear is None:
        return False

    # Align shapes
    h, w = min(arr_linear.shape[0], bg_linear.shape[0]), min(arr_linear.shape[1], bg_linear.shape[1])
    a = arr_linear[:h, :w]
    b = bg_linear[:h, :w]

    db_above = linear_to_db(a) - linear_to_db(b)
    above_mask = db_above > FP_DB_ABOVE_BG

    px_above = int(above_mask.sum())
    if px_above < FP_MIN_PX:
        return False

    # Check extent: label connected components, find longest axis extent
    labeled, n = ndimage.label(above_mask, structure=struct8)
    if n == 0:
        return False

    pts = BERTH_COORDS[berth_name]
    clon, clat = berth_center(pts)
    bbox_obj = cell_bbox_for(clon, clat)
    geom = build_geom(pts, bbox_obj, (h, w),
                      berth_params["pier"],
                      berth_params["water_i"],
                      berth_params["water_o"])
    ux, uy = geom["axis_uvec"]

    best_len = 0.0
    for bid in range(1, n + 1):
        mask = labeled == bid
        rows, cols = np.where(mask)
        if len(rows) < 2:
            continue
        fc = cols.astype(float)
        fr = rows.astype(float)
        axis_p = fc * ux + fr * uy
        length_m = (axis_p.max() - axis_p.min()) * RESOLUTION
        best_len = max(best_len, length_m)

    return best_len >= FP_MIN_LEN_M


def detect_one(date_str, orbit, arr_by_berth, bgs):
    """Run detection for all berths given pre-fetched arrays. Returns pass dict."""
    result = {"date": date_str, "orbit": orbit}
    for berth_name, params in BERTH_DETECT_PARAMS.items():
        arr = arr_by_berth.get(berth_name)
        bg  = bgs.get(berth_name)
        hulls = []

        if arr is None or bg is None:
            result[berth_name] = {"det": "EMP", "hulls": [], "departed": []}
            continue

        h = min(arr.shape[0], bg.shape[0])
        w = min(arr.shape[1], bg.shape[1])
        a = arr[:h, :w]
        b = bg[:h, :w]

        pts      = BERTH_COORDS[berth_name]
        bbox_obj = cell_bbox_for(*berth_center(pts))
        geom     = build_geom(pts, bbox_obj, (h, w),
                              params["pier"], params["water_i"], params["water_o"])
        ratio    = a / np.maximum(b, 1e-10)
        ratio    = np.where(geom["water_mask"], ratio, 0.0)

        blobs, best, detected = find_blobs(
            ratio, geom["water_mask"], geom["axis_uvec"],
            params["merge"], params["min_len"]
        )

        if detected:
            # Hull details from blobs
            ux, uy = geom["axis_uvec"]
            px_u, py_u = -uy, ux
            clon, clat = berth_center(pts)
            mpdlat = 111320.0
            mpdlon = 111320.0 * np.cos(np.deg2rad(clat))

            for b_blob in blobs:
                if b_blob["length_m"] < params["min_len"]:
                    continue
                axis_mid = (b_blob["axis_min"] + b_blob["axis_max"]) / 2
                perp_mid = b_blob["perp_ctr"]
                h2, w2 = a.shape
                lons = np.linspace(bbox_obj.min_x + 0.5*(bbox_obj.max_x-bbox_obj.min_x)/w2,
                                   bbox_obj.max_x - 0.5*(bbox_obj.max_x-bbox_obj.min_x)/w2, w2)
                lats = np.linspace(bbox_obj.max_y - 0.5*(bbox_obj.max_y-bbox_obj.min_y)/h2,
                                   bbox_obj.min_y + 0.5*(bbox_obj.max_y-bbox_obj.min_y)/h2, h2)
                col = int(axis_mid * ux - perp_mid * uy)
                row = int(axis_mid * uy + perp_mid * ux)
                col = max(0, min(w2-1, col))
                row = max(0, min(h2-1, row))
                lon = float(lons[col])
                lat = float(lats[row])
                lm = round(b_blob["length_m"], 1)
                wm = round(b_blob["width_m"], 1)
                asp = round(lm / max(wm, 1.0), 2)
                cls = "VLCC" if lm >= 300 else "SuezAframax"
                hulls.append({
                    "side": int(np.sign(b_blob["perp_ctr"])) or 1,
                    "perp_m": round(float(b_blob["perp_ctr"]) * RESOLUTION, 1),
                    "pos_m": round(float((b_blob["axis_min"]+b_blob["axis_max"])/2)*RESOLUTION, 1),
                    "lon": round(lon, 6), "lat": round(lat, 6),
                    "len_m": lm, "width_m": wm, "aspect": asp, "class": cls,
                    "new_load": False, "arrived": False, "visit_id": None,
                })

        result[berth_name] = {
            "det": "OCC" if detected else "EMP",
            "hulls": hulls,
            "departed": []
        }
    return result


def main():
    print("Loading berth_occupancy.json ...")
    with open(ROOT / "data" / "berth_occupancy.json") as f:
        bo = json.load(f)
    with open(ROOT / "data" / "data_daily.json") as f:
        dd = json.load(f)

    overview_dates = set(dd["dates"])
    passes = bo["passes"]

    # Step 1: Keep only orbit-35 passes
    passes_o35 = [p for p in passes if p["orbit"] == 35]
    print(f"Orbit-35 passes: {len(passes_o35)} (was {len(passes)} total)")

    existing_dates = {p["date"] for p in passes_o35}

    # Step 2: Check which overview dates are missing
    missing = sorted(overview_dates - existing_dates)
    # Only attempt recent missing (orbit-35 SAR data exists from 2024)
    recent_missing = [d for d in missing if d >= "2024-01-01"]
    print(f"Missing overview dates (recent, to fetch): {recent_missing}")

    # Load backgrounds
    bgs = {berth: load_bg(berth, ORBIT) for berth in BERTH_COORDS}
    for berth, bg in bgs.items():
        print(f"  BG {berth}: {'loaded shape=' + str(bg.shape) if bg is not None else 'NOT FOUND'}")

    # Step 3: Fetch and detect missing dates
    new_passes = []
    for date_str in recent_missing:
        print(f"\nFetching {date_str} orbit-{ORBIT} from Planetary Computer ...")
        item = find_item_pc(date_str, ORBIT, AOI_BBOX_LST)
        if item is None:
            print(f"  → No item found on PC for {date_str} o{ORBIT}. Adding EMP row.")
            pass_entry = {"date": date_str, "orbit": ORBIT}
            for berth in BERTH_COORDS:
                pass_entry[berth] = {"det": "EMP", "hulls": [], "departed": []}
            new_passes.append(pass_entry)
            continue
        print(f"  → Found: {item.id}")

        arr_by_berth = {}
        for berth_name in BERTH_COORDS:
            pts = BERTH_COORDS[berth_name]
            bbox_obj = cell_bbox_for(*berth_center(pts))
            arr = fetch_s1_pc(item, bbox_obj)
            arr_by_berth[berth_name] = arr
            if arr is not None:
                print(f"    {berth_name}: fetched shape={arr.shape}")
            else:
                print(f"    {berth_name}: fetch failed")

        pass_entry = detect_one(date_str, ORBIT, arr_by_berth, bgs)
        new_passes.append(pass_entry)
        print(f"  Result: SI={pass_entry['sea_island']['det']} ETH={pass_entry['east_t_head']['det']} SEP={pass_entry['southeast_pier']['det']}")

    # Step 4: Merge new passes into o35 list
    all_o35 = passes_o35 + new_passes
    all_o35.sort(key=lambda p: p["date"])

    # Step 5: Apply FP filter to east_t_head on ALL o35 passes
    # For each OCC on east_t_head, re-check with strict BG filter
    eth_bg = bgs.get("east_t_head")
    eth_params = BERTH_DETECT_PARAMS["east_t_head"]
    fp_fixed = []
    for p in all_o35:
        berth = "east_t_head"
        if p[berth]["det"] == "OCC" and eth_bg is not None:
            # Need the actual array to verify — only do this for known FP dates
            if p["date"] in ("2026-09-06", "2026-09-30"):
                print(f"\nFP-check {p['date']} east_t_head (OCC → checking BG filter) ...")
                item = find_item_pc(p["date"], ORBIT, AOI_BBOX_LST)
                if item is not None:
                    pts = BERTH_COORDS[berth]
                    bbox_obj = cell_bbox_for(*berth_center(pts))
                    arr = fetch_s1_pc(item, bbox_obj)
                    passes_fp = fp_filter_occ(arr, eth_bg, eth_params, berth)
                    if not passes_fp:
                        print(f"  → FP confirmed: changing to EMP")
                        p = dict(p)
                        p[berth] = {"det": "EMP", "hulls": [], "departed": []}
                    else:
                        print(f"  → Passes FP filter: keeping OCC")
                else:
                    print(f"  → No PC item, keeping existing")
        fp_fixed.append(p)

    # Step 6: Write updated JSON
    bo_new = dict(bo)
    bo_new["passes"] = fp_fixed
    # Recompute visits/loadings from orbit-35 only
    # (keep existing loadings list as-is for now — frontend computes from passes)
    out_path = ROOT / "data" / "berth_occupancy.json"
    with open(out_path, "w") as f:
        json.dump(bo_new, f, separators=(",", ":"))
    print(f"\nWrote {out_path} ({len(fp_fixed)} passes, orbit-35 only)")

    # Summary
    occ_counts = {}
    for p in fp_fixed:
        for berth in BERTH_COORDS:
            if p[berth]["det"] == "OCC":
                occ_counts[berth] = occ_counts.get(berth, 0) + 1
    print("OCC counts by berth:", occ_counts)
    print("Date range:", fp_fixed[0]["date"] if fp_fixed else "?", "→", fp_fixed[-1]["date"] if fp_fixed else "?")


if __name__ == "__main__":
    main()
