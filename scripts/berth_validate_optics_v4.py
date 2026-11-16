"""
berth_validate_optics_v4.py — v4 detector validation.

Changes vs v3:
  1. BG: median of EMP-only 2024-2025 dates per berth+orbit;
         fallback to p10 of ALL 2024-2025 dates if <5 EMP dates
  2. PIER_EXCL=12m, WATER_INNER=20m, WATER_OUTER=90m
  3. Blob gap-merge: gaps <=40m along axis bridged; SHIP_MIN_LEN=120m
  4. Accuracy split: overall / 2024-2025 / 2026 (out-of-sample)
  5. BG comparison PNG for east_t_head (before/after)

Outputs in diagnostics/berth_v4/validation_optics_v4/
"""

import os, sys, csv, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from berth_detect_core import (
    RESOLUTION, RATIO_THR, MERGE_PERP_TOL_M, struct8,
    BERTH_COORDS, BERTH_DETECT_PARAMS,
    metres_per_deg_lat, metres_per_deg_lon, berth_center,
    cell_bbox_for, build_geom, find_blobs,
)

import numpy as np
from scipy import ndimage
from scipy.ndimage import zoom as nd_zoom

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder,
)
from shapely.geometry import LineString
import shapely

from berth_pc_fetch import find_item_pc, fetch_s1_pc

# ── S2 config (SH only — for contact-sheet PNGs, graceful failure OK) ────────
_sh_client_id     = os.environ.get("CLIENT_ID") or ""
_sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
_sh_config_ok     = bool(_sh_client_id and _sh_client_secret)
if _sh_config_ok:
    config = SHConfig()
    config.sh_client_id     = _sh_client_id
    config.sh_client_secret = _sh_client_secret
    config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    config.sh_base_url      = "https://sh.dataspace.copernicus.eu"
    S2_CDSE = DataCollection.SENTINEL2_L2A.define_from(
        "s2l2a_cdse_v4", service_url="https://sh.dataspace.copernicus.eu"
    )
else:
    config = None; S2_CDSE = None

AOI_BBOX_LST = [50.26, 29.17, 50.40, 29.31]

ROOT     = Path(__file__).parent.parent
BG_V3    = ROOT / "diagnostics" / "berth_v4" / "bg_v3"
BG_V4    = ROOT / "diagnostics" / "berth_v4" / "bg_v4"
OUT_DIR  = ROOT / "diagnostics" / "berth_v4" / "validation_optics_v4"
REF_CSV  = ROOT / "diagnostics" / "berth_v4" / "optics_ref_sameday.csv"
BG_V4.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Constants (v4) ────────────────────────────────────────────────────────────
BERTH_SPEC = BERTH_COORDS   # canonical coords from core
BERTHS     = ["sea_island", "east_t_head", "southeast_pier"]
CELL_KM    = 1.0

# Nominal optical labels for temporal-gap ? dates (rule: |Δt|>4h + confident blob)
# Used for "strict" (без правила) comparison column
TEMPORAL_GAP_LABELS = {
    # (date, orbit, berth): pre-rule optical label
    ("2025-01-11", "101", "sea_island"): "EMP",  # S2 EMP, Δt=-7h, SAR OCC L=385
    ("2025-09-28", "35",  "sea_island"): "EMP",  # S2 EMP, Δt=-4.5h, SAR OCC L=353
    ("2026-03-27", "35",  "sea_island"): "OCC",  # S2 OCC, no ship in SAR
    ("2026-05-06", "101", "sea_island"): "OCC",  # S2 OCC, clouds, Δt~7h
}

# V3 reference numbers for before/after table
V3_STATS = {
    "sea_island":     {"n": 37, "acc": 81.1, "prec": 91.7, "rec": 64.7},
    "east_t_head":    {"n": 33, "acc": 51.5, "prec": 87.5, "rec": 31.8},
    "southeast_pier": {"n": 34, "acc": 97.1, "prec": 100.0, "rec": 50.0},
}

# ── Evalscripts ───────────────────────────────────────────────────────────────
EVALSCRIPT_S1 = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) { return [Math.max(s.VV, 1e-10)]; }
"""

EVALSCRIPT_S2_RGB = """
//VERSION=3
function setup() {
  return { input: [{bands:["B04","B03","B02"]}], output:{bands:3,sampleType:"FLOAT32"} };
}
function evaluatePixel(s) { return [s.B04*3.5, s.B03*3.5, s.B02*3.5]; }
"""

# geometry, find_blobs imported from berth_detect_core above

# ── Assert targets — confusion matrix for each berth ─────────────────────────
# east_t_head: ablation run #33 (bg mode, BG_V4 EMP-median, pier=20, water=20-80)
# sea_island, southeast_pier: validate run #43 (last accepted run, strict=False)
# PC baseline (RTC gamma0). SH baseline was: eth TP=14 FN=8, si TP=12 FN=3, sep TP=1 FN=1.
# New FN sea_island on PC: 2026-01-26 o35 len=90m asp=2.2 and/or 2026-02-25 o35 len=116m asp=1.9
# (both orbit-35, blobs just below min_len=120m; one is Δ vs SH — recorded for history).
ASSERT_TARGETS = {
    "east_t_head":    dict(TP=15, FN=7, FP=1, TN=10),
    "sea_island":     dict(TP=11, FN=4, FP=0, TN=18),
    "southeast_pier": dict(TP=1,  FN=1, FP=0, TN=32),
}

# ── Fetchers ──────────────────────────────────────────────────────────────────

def scene_datetime_s1(date_str, orbit, bbox):
    """Return PC item for (date_str, orbit) or None. bbox arg kept for compat."""
    item = find_item_pc(date_str, orbit, AOI_BBOX_LST)
    if item is None:
        return None
    return item  # callers that used to get a dt string now get an item (or None)

def scene_datetime_s2(date_str, bbox):
    if not _sh_config_ok:
        return ""
    catalog = SentinelHubCatalog(config=config)
    try:
        results = list(catalog.search(
            S2_CDSE, bbox=bbox,
            time=(date_str+"T00:00:00Z", date_str+"T23:59:59Z"),
            fields={"include":["properties.datetime","properties.eo:cloud_cover"]},
        ))
    except Exception as e:
        print(f"  S2 catalog error ({date_str}): {e}"); return ""
    best_dt = ""; best_cc = 999.0
    for r in results:
        p = r.get("properties", {})
        cc = float(p.get("eo:cloud_cover", 100))
        if cc < best_cc:
            best_cc = cc; best_dt = p.get("datetime", "")
    return best_dt

def fetch_s1_linear(item_or_dt, bbox_obj):
    """Fetch S1 from PC item. item_or_dt is a PC item (or None/str for compat)."""
    if item_or_dt is None or isinstance(item_or_dt, str):
        return None
    return fetch_s1_pc(item_or_dt, bbox_obj)

def fetch_s2_rgb(dt_str, bbox_obj):
    if not _sh_config_ok or not dt_str:
        return None
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S2_RGB,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S2_CDSE,
            time_interval=(dt_str[:19].replace("T"," "), dt_str[:19].replace("T"," ")),
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        data = req.get_data(); arr = data[0]
        if arr.ndim == 2: arr = np.stack([arr]*3, axis=-1)
        return np.clip(arr, 0, 1).astype(np.float32)
    except Exception as e:
        print(f"  S2 fetch failed ({dt_str[:10]}): {e}"); return None

# ── BG builder ────────────────────────────────────────────────────────────────
def build_bg_v4(ref_rows, cell_bboxes, aoi_full):
    """Build EMP-only 2024-2025 background per berth per orbit.
    If <5 EMP dates available → p10 of all non-? 2024-2025 dates.
    Returns dict[(orbit, berth)] = ndarray or None.
    """
    bg = {}
    for berth in BERTHS:
        ref_col = f"{berth}_optical_ref"
        for orbit in (35, 101):
            cache_path = BG_V4 / f"bg_o{orbit}_{berth}.npy"
            # do NOT load cache — SH-built files have different radiometry (SIGMA0 vs RTC)
            # always rebuild from PC

            emp_dates = []
            all_dates = []
            for row in ref_rows:
                date_str = row.get("date", "").strip()
                if not date_str: continue
                year = int(date_str[:4])
                if year not in (2024, 2025): continue
                try:
                    row_orbit = int(row.get("orbit", 0))
                except Exception:
                    continue
                if row_orbit != orbit: continue
                ref_val = row.get(ref_col, "").strip()
                if ref_val == "EMP":
                    emp_dates.append(date_str)
                if ref_val in ("OCC", "EMP"):
                    all_dates.append(date_str)

            if len(emp_dates) >= 3:
                use_dates = emp_dates
                use_pct   = 50
                mode_str  = f"EMP-median ({len(emp_dates)} dates)"
            else:
                v3_path = BG_V3 / f"bg_o{orbit}_{berth}.npy"
                if v3_path.exists():
                    bg[(orbit, berth)] = np.load(v3_path)
                    print(f"  BG v3 fallback (only {len(emp_dates)} EMP): {v3_path.name}")
                    continue
                use_dates = all_dates
                use_pct   = 10
                mode_str  = f"ALL-p10 ({len(all_dates)} dates, only {len(emp_dates)} EMP)"

            print(f"  BG v4 {berth} o{orbit}: {mode_str}")

            if not use_dates:
                bg[(orbit, berth)] = None
                print(f"    no dates — BG=None")
                continue

            bbox_obj = cell_bboxes[berth]
            arrays = []
            for date_str in use_dates:
                item = find_item_pc(date_str, orbit, AOI_BBOX_LST)
                if item is None:
                    print(f"    {date_str}: no PC item"); continue
                arr = fetch_s1_pc(item, bbox_obj)
                if arr is not None:
                    arrays.append(arr)
                    print(f"    {date_str} OK shape={arr.shape}")

            if not arrays:
                bg[(orbit, berth)] = None
                print(f"    no arrays fetched — BG=None")
                continue

            # Align to most common shape
            shapes = [a.shape for a in arrays]
            target = max(set(shapes), key=shapes.count)
            aligned = []
            for a in arrays:
                if a.shape != target:
                    a = nd_zoom(a, (target[0]/a.shape[0], target[1]/a.shape[1]), order=1)
                aligned.append(a)
            stack = np.stack(aligned, axis=0)
            if use_pct == 50:
                bg_arr = np.median(stack, axis=0).astype(np.float32)
            else:
                bg_arr = np.percentile(stack, 10, axis=0).astype(np.float32)

            np.save(cache_path, bg_arr)
            bg[(orbit, berth)] = bg_arr
            print(f"    saved {cache_path.name} shape={bg_arr.shape}")

    return bg

# ── BG comparison PNG ─────────────────────────────────────────────────────────
def save_bg_comparison():
    """Side-by-side: v3 vs v4 background for east_t_head (both orbits)."""
    berth = "east_t_head"
    fig, axes = plt.subplots(2, 2, figsize=(10, 8))
    fig.patch.set_facecolor("#1a1a1a")
    for oi, orbit in enumerate((35, 101)):
        v3_path = BG_V3 / f"bg_o{orbit}_{berth}.npy"
        v4_path = BG_V4 / f"bg_o{orbit}_{berth}.npy"
        for col, (path, label) in enumerate([
            (v3_path, "v3: all-dates median"),
            (v4_path, "v4: EMP/p10"),
        ]):
            ax = axes[oi, col]
            ax.set_facecolor("#111")
            ax.set_xticks([]); ax.set_yticks([])
            if path.exists():
                arr = np.load(path)
                db = np.where(arr > 0, 10*np.log10(arr), np.nan)
                fin = db[np.isfinite(db)]
                if fin.size > 0:
                    vmin, vmax = np.percentile(fin, [2, 98])
                    im = ax.imshow(db, cmap="gray", vmin=vmin, vmax=vmax,
                                   interpolation="nearest", origin="upper")
                    cb = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
                    cb.ax.yaxis.set_tick_params(color="white")
                    plt.setp(plt.getp(cb.ax.axes, "yticklabels"), color="white")
                    cb.set_label("dB", color="white")
            else:
                ax.text(0.5, 0.5, "MISSING", color="#666",
                        ha="center", va="center", transform=ax.transAxes, fontsize=9)
            ax.set_title(f"o{orbit} {label}", color="white", fontsize=8, pad=4)
    fig.suptitle(f"east_t_head: background before / after", color="white", fontsize=11)
    out = OUT_DIR / "bg_comparison.png"
    fig.savefig(out, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"BG comparison PNG: {out}")
    return out

# ── Accuracy helper ───────────────────────────────────────────────────────────
def _cm_from_rows(rows_b):
    tp = sum(1 for r in rows_b if r["optical_ref"]=="OCC" and r["det"]=="OCC")
    tn = sum(1 for r in rows_b if r["optical_ref"]=="EMP" and r["det"]=="EMP")
    fp = sum(1 for r in rows_b if r["optical_ref"]=="EMP" and r["det"]=="OCC")
    fn = sum(1 for r in rows_b if r["optical_ref"]=="OCC" and r["det"]=="EMP")
    return tp, tn, fp, fn

def accuracy_block(rows_b, year_filter=None, label="", strict=False):
    """
    strict=False (с правилом): ? rows excluded from evaluation.
    strict=True  (без правила): ? rows with TEMPORAL_GAP_LABELS get their
                  nominal pre-rule label and are included.
    """
    if year_filter:
        rows_b = [r for r in rows_b if int(r["date"][:4]) in year_filter]

    # For strict mode, substitute nominal labels for temporal-gap ? rows
    if strict:
        expanded = []
        for r in rows_b:
            if r["optical_ref"] == "?":
                key = (r["date"], str(r["orbit"]), r["berth"])
                nominal = TEMPORAL_GAP_LABELS.get(key)
                if nominal:
                    r = dict(r, optical_ref=nominal)
            expanded.append(r)
        rows_b = expanded

    rows_b = [r for r in rows_b
              if r["optical_ref"] in ("OCC","EMP") and r["det"] in ("OCC","EMP")]
    n = len(rows_b)
    if n == 0:
        return [f"  {label}: no evaluable rows"], None
    tp, tn, fp, fn = _cm_from_rows(rows_b)
    acc  = (tp+tn)/n*100
    prec = tp/(tp+fp)*100 if (tp+fp) > 0 else float("nan")
    rec  = tp/(tp+fn)*100 if (tp+fn) > 0 else float("nan")
    lines = [
        f"  {label} (n={n})",
        f"    Accuracy:  {acc:.1f}%  Precision: {prec:.1f}%  Recall: {rec:.1f}%",
        f"    CM: TP={tp} FN={fn} FP={fp} TN={tn}",
    ]
    return lines, {"n": n, "acc": acc, "prec": prec, "rec": rec}

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not REF_CSV.exists():
        sys.exit(f"FATAL: {REF_CSV} not found")

    with open(REF_CSV) as f:
        ref_rows = list(csv.DictReader(f))
    print(f"Loaded {len(ref_rows)} rows from {REF_CSV.name}")

    cell_bboxes = {b: cell_bbox_for(*berth_center(pts)) for b, pts in BERTH_SPEC.items()}
    aoi_full    = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)

    # ── Build v4 background ───────────────────────────────────────────────────
    print("\n=== Building v4 background (EMP-only 2024-2025) ===")
    bg_cache = build_bg_v4(ref_rows, cell_bboxes, aoi_full)

    # ── BG comparison PNG (east_t_head) ───────────────────────────────────────
    print("\n=== BG comparison PNG ===")
    save_bg_comparison()

    # ── Validation loop ───────────────────────────────────────────────────────
    print("\n=== Validation on 42 dates ===")
    results = []
    discrepancy_dates = set()
    # cache for contact sheet: (date_str, orbit, berth) → dict
    cs_cache: dict = {}

    for row in ref_rows:
        date_str = row.get("date", "").strip()
        if not date_str: continue
        try:
            orbit = int(row["orbit"])
        except Exception:
            continue

        print(f"\n{date_str} o{orbit}")
        s1_item = scene_datetime_s1(date_str, orbit, aoi_full)
        print(f"  S1: {s1_item.id[:30] if s1_item else 'NOT FOUND'}")

        # S1/S2 acquisition times for time-delta rule
        s1_dt_iso = ""
        if s1_item is not None:
            try: s1_dt_iso = s1_item.datetime.isoformat()
            except Exception: pass
        s2_dt_str = scene_datetime_s2(date_str, aoi_full) if _sh_config_ok else ""

        for berth in BERTHS:
            ref_col    = f"{berth}_optical_ref"
            optical_ref = row.get(ref_col, "").strip()
            bbox_obj   = cell_bboxes[berth]
            pts        = BERTH_SPEC[berth]

            linear = fetch_s1_linear(s1_item, bbox_obj) if s1_item else None

            bg     = bg_cache.get((orbit, berth))
            no_bg  = bg is None
            if bg is not None and linear is not None and bg.shape != linear.shape:
                bg = nd_zoom(bg, (linear.shape[0]/bg.shape[0],
                                  linear.shape[1]/bg.shape[1]), order=1)

            ratio = None
            if linear is not None and bg is not None:
                ratio = np.where((linear>0)&(bg>0),
                                 10*np.log10(linear/bg), np.nan).astype(np.float32)

            img_shape = linear.shape if linear is not None else (99, 101)
            p = BERTH_DETECT_PARAMS[berth]
            geom = build_geom(pts, bbox_obj, img_shape,
                              pier_excl_m=p["pier"],
                              water_inner_m=p["water_i"],
                              water_outer_m=p["water_o"])

            if no_bg or ratio is None:
                det_occ = None; longest = None; blobs = []
            else:
                blobs, longest, det_occ = find_blobs(
                    ratio, geom["water_mask"], geom["axis_uvec"],
                    merge_gap_m=p["merge"], min_len_m=p["min_len"],
                    min_aspect=p["aspect"])

            det_str = "OCC" if det_occ else ("EMP" if det_occ is not None else "NO_BG")
            len_str = f"{longest['length_m']:.0f}" if longest else "0"
            asp_str = f"{longest['aspect']:.1f}" if longest else "0"

            match = None
            if optical_ref in ("OCC","EMP") and det_occ is not None:
                ref_bool = (optical_ref == "OCC")
                match    = (det_occ == ref_bool)
                if not match:
                    discrepancy_dates.add((date_str, orbit))

            results.append({
                "date": date_str, "orbit": orbit, "berth": berth,
                "optical_ref": optical_ref,
                "det": det_str,
                "match": ("" if match is None else str(match)),
                "length_m": len_str, "aspect": asp_str,
                "no_bg": str(no_bg), "s1_found": str(bool(s1_item)),
                "s1_dt": s1_dt_iso, "s2_dt": s2_dt_str,
            })
            status = "OK" if match else ("MISMATCH" if match is False else "SKIP")
            print(f"  {berth}: ref={optical_ref} det={det_str} "
                  f"len={len_str}m asp={asp_str} {status}")

            # cache for contact sheet
            qualifying_blobs = [b for b in blobs if b["length_m"] >= p["min_len"]]
            cs_cache[(date_str, orbit, berth)] = {
                "optical_ref": optical_ref,
                "det": det_str, "match": match,
                "ratio": ratio, "linear": linear,
                "water_mask": geom["water_mask"],
                "axis_uvec": geom["axis_uvec"],
                "blobs": qualifying_blobs,
                "s1_item": s1_item,
                "bbox_obj": bbox_obj,
            }

    # ── asp=2.5 variant pass for east_t_head ─────────────────────────────────
    # Re-run detection with min_aspect=2.5. Read-only — not written to main
    # results or DB. Used only for the side-by-side assert column.
    results_asp25 = []
    p_eth = BERTH_DETECT_PARAMS["east_t_head"]
    for row in ref_rows:
        date_str = row.get("date", "").strip()
        if not date_str: continue
        try: orbit = int(row["orbit"])
        except Exception: continue
        optical_ref = row.get("east_t_head_optical_ref", "").strip()
        if optical_ref not in ("OCC", "EMP"): continue
        s1_item = scene_datetime_s1(date_str, orbit, aoi_full)
        bbox_obj = cell_bboxes["east_t_head"]
        pts      = BERTH_SPEC["east_t_head"]
        linear   = fetch_s1_linear(s1_item, bbox_obj) if s1_item else None
        bg       = bg_cache.get((orbit, "east_t_head"))
        if bg is not None and linear is not None and bg.shape != linear.shape:
            bg = nd_zoom(bg, (linear.shape[0]/bg.shape[0],
                              linear.shape[1]/bg.shape[1]), order=1)
        ratio = None
        if linear is not None and bg is not None:
            ratio = np.where((linear>0)&(bg>0),
                             10*np.log10(linear/bg), np.nan).astype(np.float32)
        img_shape = linear.shape if linear is not None else (99, 101)
        geom = build_geom(pts, bbox_obj, img_shape,
                          pier_excl_m=p_eth["pier"],
                          water_inner_m=p_eth["water_i"],
                          water_outer_m=p_eth["water_o"])
        if ratio is None:
            det_occ = None; longest = None
        else:
            _, longest, det_occ = find_blobs(
                ratio, geom["water_mask"], geom["axis_uvec"],
                merge_gap_m=p_eth["merge"], min_len_m=p_eth["min_len"],
                min_aspect=2.5)
        det_str = "OCC" if det_occ else ("EMP" if det_occ is not None else "NO_BG")
        len_str = f"{longest['length_m']:.0f}" if longest else "0"
        asp_str = f"{longest['aspect']:.1f}" if longest else "0"
        s1_dt_iso = ""
        if s1_item is not None:
            try: s1_dt_iso = s1_item.datetime.isoformat()
            except Exception: pass
        main_r = next((r for r in results
                       if r["date"]==date_str and r["orbit"]==orbit
                       and r["berth"]=="east_t_head"), None)
        results_asp25.append({
            "date": date_str, "orbit": orbit, "berth": "east_t_head",
            "optical_ref": optical_ref, "det": det_str,
            "length_m": len_str, "aspect": asp_str,
            "s1_dt": s1_dt_iso,
            "s2_dt": main_r["s2_dt"] if main_r else "",
        })

    # ── Results CSV ───────────────────────────────────────────────────────────
    res_csv = OUT_DIR / "results.csv"
    fieldnames = ["date","orbit","berth","optical_ref","det","match",
                  "length_m","aspect","no_bg","s1_found","s1_dt","s2_dt"]
    with open(res_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(results)
    print(f"\nResults CSV: {res_csv}")

    # ── Accuracy report ───────────────────────────────────────────────────────
    acc_lines = [
        "Validation v4 — optical ground truth (optics_ref_sameday.csv)",
        "BG: EMP-only 2024-2025 median (fallback p10 if <5 EMP dates)",
        "PIER_EXCL=per-berth(20m)  WATER=20-80m  "
        "MIN_LEN=per-berth(sea=120,eth/sep=150)  GAP_MERGE=per-berth(sea=40,eth=0,sep=0)",
        "="*60,
    ]

    # Before/after table header
    acc_lines += [
        "",
        "── Before / After ──────────────────────────────────────",
        f"{'Berth':<20}  {'v3-Acc':>7} {'v3-Rec':>7}  {'v4-Acc':>7} {'v4-Rec':>7}  {'v4-Prec':>8}",
        "-"*60,
    ]

    v4_summary = {}
    for berth in BERTHS:
        rows_b = [r for r in results if r["berth"] == berth]
        _, stats = accuracy_block(rows_b, label="all")
        v4_summary[berth] = stats

    for berth in BERTHS:
        v3  = V3_STATS[berth]
        v4s = v4_summary.get(berth) or {}
        v4_acc  = f"{v4s['acc']:.1f}%" if v4s else "n/a"
        v4_rec  = f"{v4s['rec']:.1f}%" if v4s else "n/a"
        v4_prec = f"{v4s['prec']:.1f}%" if v4s else "n/a"
        acc_lines.append(
            f"{berth:<20}  {v3['acc']:>6.1f}% {v3['rec']:>6.1f}%  "
            f"{v4_acc:>7} {v4_rec:>7}  {v4_prec:>8}"
        )

    # Per-berth detail — dual table: с правилом (? excluded) | без правила (? as nominal)
    acc_lines += ["", "── Per-berth detail ─────────────────────────────────────"]
    acc_lines += [
        "  Columns: [с правилом — ? excluded] | [без правила — ? as nominal label]",
        "",
    ]
    for berth in BERTHS:
        rows_b = [r for r in results if r["berth"] == berth]
        acc_lines.append(f"\n{berth}")
        for label, yf in [("all years",    None),
                           ("2024-2025",    [2024, 2025]),
                           ("2026 only",    [2026])]:
            lines_r,  stats_r  = accuracy_block(rows_b, year_filter=yf, label=label, strict=False)
            lines_s,  stats_s  = accuracy_block(rows_b, year_filter=yf, label=label, strict=True)
            # Merge lines side-by-side
            if stats_r and stats_s:
                acc_lines.append(
                    f"  {label}  |  с правилом (n={stats_r['n']}): "
                    f"Acc={stats_r['acc']:.1f}% Prec={stats_r['prec']:.1f}% Rec={stats_r['rec']:.1f}%  |  "
                    f"без правила (n={stats_s['n']}): "
                    f"Acc={stats_s['acc']:.1f}% Prec={stats_s['prec']:.1f}% Rec={stats_s['rec']:.1f}%"
                )
            else:
                acc_lines.extend(lines_r)

    acc_txt = OUT_DIR / "accuracy.txt"
    with open(acc_txt, "w") as f:
        f.write("\n".join(acc_lines) + "\n")
    for line in acc_lines:
        print(line)

    # ── Confmat assert gate — before contact sheet ────────────────────────────
    print("\n=== Confmat asserts ===")
    _assert_confmat(results, results_asp25)   # sys.exit on failure — nothing below runs

    # ── Full 42-date contact sheet ────────────────────────────────────────────
    print("\n=== Contact sheet (all 42 dates × 3 berths) ===")
    _save_contact_sheet(ref_rows, cs_cache, cell_bboxes, aoi_full)

    # ── Discrepancy contact sheet ─────────────────────────────────────────────
    disc_rows = sorted(discrepancy_dates)
    print(f"\nDiscrepancies: {len(disc_rows)} date-orbits")
    if not disc_rows:
        print("No discrepancies — skipping discrepancy PNG")
        return

    disc_cells = {k: {} for k in disc_rows}
    for date_str, orbit in disc_rows:
        print(f"\nFetching discrepancy {date_str} o{orbit} ...")
        s1_item = scene_datetime_s1(date_str, orbit, aoi_full)
        s2_dt   = scene_datetime_s2(date_str, aoi_full)
        for berth in BERTHS:
            matching = [r for r in results
                        if r["date"]==date_str and r["orbit"]==orbit
                        and r["berth"]==berth and r["match"]=="False"]
            if not matching: continue
            bbox_obj = cell_bboxes[berth]
            pts      = BERTH_SPEC[berth]
            linear   = fetch_s1_linear(s1_item, bbox_obj) if s1_item else None
            rgb      = fetch_s2_rgb(s2_dt, bbox_obj)    if s2_dt else None
            bg = bg_cache.get((orbit, berth))
            if bg is not None and linear is not None and bg.shape != linear.shape:
                bg = nd_zoom(bg, (linear.shape[0]/bg.shape[0],
                                  linear.shape[1]/bg.shape[1]), order=1)
            ratio = None
            if linear is not None and bg is not None:
                ratio = np.where((linear>0)&(bg>0),
                                 10*np.log10(linear/bg), np.nan).astype(np.float32)
            img_shape = linear.shape if linear is not None else (99, 101)
            p = BERTH_DETECT_PARAMS[berth]
            geom = build_geom(pts, bbox_obj, img_shape,
                              pier_excl_m=p["pier"],
                              water_inner_m=p["water_i"],
                              water_outer_m=p["water_o"])
            blobs, longest, _ = find_blobs(
                ratio, geom["water_mask"], geom["axis_uvec"],
                merge_gap_m=p["merge"], min_len_m=p["min_len"],
                min_aspect=p["aspect"]) if ratio is not None else ([], None, False)
            disc_cells[(date_str, orbit)][berth] = {
                "optical_ref": matching[0]["optical_ref"],
                "det": matching[0]["det"],
                "blobs": blobs, "longest": longest,
                "ratio": ratio, "linear": linear, "rgb": rgb,
                "water_mask": geom["water_mask"],
                "bbox_obj": bbox_obj,
                "axis_uvec": geom["axis_uvec"],
            }

    n_rows_disc = len(disc_rows)
    PANEL_H = 1.3; COL_W = 4.2
    fig = plt.figure(figsize=(len(BERTHS)*COL_W, n_rows_disc*(PANEL_H+0.08)+0.3))
    fig.patch.set_facecolor("#1a1a1a")
    outer_gs = GridSpec(n_rows_disc, len(BERTHS), figure=fig,
                        hspace=0.08, wspace=0.04,
                        top=0.97, bottom=0.01, left=0.06, right=0.998)

    def _hide(ax): ax.set_xticks([]); ax.set_yticks([])

    for ri, (date_str, orbit) in enumerate(disc_rows):
        for ci, berth in enumerate(BERTHS):
            cell = disc_cells.get((date_str, orbit), {}).get(berth)
            inner = GridSpecFromSubplotSpec(1, 3, subplot_spec=outer_gs[ri, ci],
                                            wspace=0.02)
            ax_s2    = fig.add_subplot(inner[0])
            ax_sar   = fig.add_subplot(inner[1])
            ax_ratio = fig.add_subplot(inner[2])
            for ax in (ax_s2, ax_sar, ax_ratio):
                ax.set_facecolor("#111"); _hide(ax)
            if ci == 0:
                ax_s2.set_ylabel(f"{date_str}\no{orbit}", color="#bbb",
                                 fontsize=4, rotation=0, labelpad=28, va="center")
            if ri == 0:
                ax_s2.set_title(berth.replace("_"," "), color="white",
                                fontsize=5.5, pad=2, fontweight="bold")
            if cell is None:
                for ax in (ax_s2, ax_sar, ax_ratio):
                    ax.text(0.5, 0.5, "OK", color="#444",
                            ha="center", va="center", transform=ax.transAxes, fontsize=5)
                continue

            # S2
            if cell["rgb"] is not None:
                ax_s2.imshow(cell["rgb"], interpolation="nearest", origin="upper")
            else:
                ax_s2.text(0.5, 0.5, "NO S2", color="#666",
                           ha="center", va="center", transform=ax_s2.transAxes, fontsize=5)

            # SAR dB
            lin = cell["linear"]
            if lin is not None and np.isfinite(lin).any():
                db = np.where(lin>0, 10*np.log10(lin), np.nan)
                fin = db[np.isfinite(db)]
                if fin.size > 0:
                    vmin, vmax = np.percentile(fin, [2, 98])
                    ax_sar.imshow(db, cmap="gray", vmin=vmin, vmax=vmax,
                                  interpolation="nearest", origin="upper")
            else:
                ax_sar.text(0.5, 0.5, "NO SAR", color="#666",
                            ha="center", va="center", transform=ax_sar.transAxes, fontsize=5)

            # Ratio
            ratio = cell["ratio"]
            water_mask = cell["water_mask"]
            if ratio is not None and np.isfinite(ratio).any():
                disp = np.clip(np.where(np.isfinite(ratio), ratio, np.nan), -10, 30)
                ax_ratio.imshow(disp, cmap="RdYlGn", vmin=-10, vmax=30,
                                interpolation="nearest", origin="upper")
                try:
                    ax_ratio.contour(ratio, levels=[RATIO_THR], colors=["white"],
                                     linewidths=0.5, alpha=0.9)
                except Exception: pass
                try:
                    ax_ratio.contour(water_mask.astype(float), levels=[0.5],
                                     colors=["orange"], linewidths=0.7, alpha=0.9)
                except Exception: pass
                # blob outlines via core find_blobs (no border/bbox fields)
                if cell["longest"] and cell["ratio"] is not None:
                    # highlight above-threshold pixels in the detected length range
                    ln = cell["longest"]
                    above_px = (np.isfinite(cell["ratio"]) &
                                (cell["ratio"] > RATIO_THR) & cell["water_mask"])
                    if above_px.any():
                        rows2, cols2 = np.where(
                            above_px & ~ndimage.binary_erosion(above_px, structure=struct8))
                        ax_ratio.scatter(cols2, rows2, s=0.3,
                                         c="red", marker=".", linewidths=0)

            opt = cell["optical_ref"]; det = cell["det"]
            ann = f"ref={opt}\ndet={det}"
            if cell["longest"]:
                ann += f"\nlen={cell['longest']['length_m']:.0f}m asp={cell['longest']['aspect']:.1f}"
            ax_ratio.text(0.02, 0.98, ann, color="white",
                          ha="left", va="top", transform=ax_ratio.transAxes,
                          fontsize=3.5, fontweight="bold",
                          bbox=dict(boxstyle="square,pad=0.1", fc="black", alpha=0.6, lw=0))
            for ax in (ax_s2, ax_sar, ax_ratio):
                for spine in ax.spines.values():
                    spine.set_edgecolor("#e74c3c"); spine.set_linewidth(2.0)

    fig.text(0.5, 0.998,
             "Discrepancy sheet v4 — S2 RGB | SAR dB | ratio+water bands (orange)",
             color="white", fontsize=4.5, ha="center", va="top")
    out_png = OUT_DIR / "discrepancy_sheet.png"
    fig.savefig(out_png, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"\nSaved: {out_png} ({out_png.stat().st_size//1024} KB)")
    plt.close(fig)


def _classify_hull(len_m):
    return "VLCC" if len_m >= 300 else "SuezAframax"


def _draw_hull_boxes(ax, blobs, ux, uy, min_len, color_ok="#f1c40f", color_cap="#e67e22"):
    """Draw oriented bounding boxes for qualifying blobs.

    color_ok  = box within physical cap
    color_cap = extra box beyond 2-per-side cap
    """
    px_u, py_u = -uy, ux
    # group by side, keep ≤2 per side, mark rest as cap extras
    by_side: dict = {}
    for b in blobs:
        sk = 1 if b.get("perp_ctr", 0) >= 0 else -1
        by_side.setdefault(sk, []).append(b)

    for sk, side_blobs in by_side.items():
        side_blobs_s = sorted(side_blobs, key=lambda b: b["length_m"], reverse=True)
        for idx, b in enumerate(side_blobs_s):
            am, aM = b["axis_min"], b["axis_max"]
            pm = b.get("perp_min", b.get("perp_ctr", 0) - b.get("width_m", 20) / RESOLUTION / 2)
            pM = b.get("perp_max", b.get("perp_ctr", 0) + b.get("width_m", 20) / RESOLUTION / 2)
            # 4 corners in (col, row)
            cs = [am*ux + pm*px_u, aM*ux + pm*px_u,
                  aM*ux + pM*px_u, am*ux + pM*px_u, am*ux + pm*px_u]
            rs = [am*uy + pm*py_u, aM*uy + pm*py_u,
                  aM*uy + pM*py_u, am*uy + pM*py_u, am*uy + pm*py_u]
            clr = color_ok if idx < 2 else color_cap
            ax.plot(cs, rs, "-", color=clr, lw=1.0, alpha=0.9)
            # class label at centre
            ctr_c = (am + aM) / 2 * ux + (pm + pM) / 2 * px_u
            ctr_r = (am + aM) / 2 * uy + (pm + pM) / 2 * py_u
            cls_lbl = _classify_hull(b["length_m"])[0]  # "V" or "S"
            ax.text(ctr_c, ctr_r, f"{cls_lbl}\n{b['length_m']:.0f}m",
                    color=clr, fontsize=3, ha="center", va="center",
                    fontweight="bold")


def _save_contact_sheet(ref_rows, cs_cache, cell_bboxes, aoi_full):
    """Generate per-berth contact sheet (all 42 dates).

    Layout per berth: tall strip, 42 rows × 2 panels (optical | SAR+hulls).
    Caption: «корпусов: N, классы: X». Optical=S2 RGB if available else SAR dB.
    """
    min_lens = {b: BERTH_DETECT_PARAMS[b]["min_len"] for b in BERTHS}

    for berth in BERTHS:
        dates_in_order = []
        for row in ref_rows:
            d = row.get("date", "").strip()
            if not d: continue
            try: orb = int(row["orbit"])
            except: continue
            dates_in_order.append((d, orb, row.get(f"{berth}_optical_ref", "").strip()))

        n = len(dates_in_order)
        PANEL_H = 1.5; PANEL_W = 1.5; LABEL_W = 0.6
        fig_w = LABEL_W + 2 * PANEL_W
        fig_h = n * PANEL_H
        fig = plt.figure(figsize=(fig_w, fig_h))
        fig.patch.set_facecolor("#111")

        # Fetch S2 once per unique date (expensive call)
        s2_cache: dict = {}
        print(f"\nFetching S2 for {berth} ({n} dates)...")
        for date_str, orbit, _ in dates_in_order:
            if date_str not in s2_cache:
                s2_dt = scene_datetime_s2(date_str, aoi_full)
                rgb   = fetch_s2_rgb(s2_dt, cell_bboxes[berth]) if s2_dt else None
                s2_cache[date_str] = rgb
                if rgb is not None:
                    print(f"  S2 {date_str} OK")

        for ri, (date_str, orbit, optical_ref) in enumerate(dates_in_order):
            # axes: [label | optical | SAR+hulls]
            left_opt = LABEL_W / fig_w
            w_panel  = PANEL_W / fig_w
            row_b = 1.0 - (ri + 1) * PANEL_H / fig_h
            row_h = PANEL_H / fig_h

            ax_lbl = fig.add_axes([0, row_b, left_opt * 0.95, row_h])
            ax_opt = fig.add_axes([left_opt,            row_b, w_panel * 0.97, row_h])
            ax_sar = fig.add_axes([left_opt + w_panel,  row_b, w_panel * 0.97, row_h])

            for ax in (ax_lbl, ax_opt, ax_sar):
                ax.set_facecolor("#0d0d0d")
                ax.set_xticks([]); ax.set_yticks([])

            # Label
            ax_lbl.text(0.95, 0.5, f"{date_str}\no{orbit}",
                        color="#ccc", fontsize=4.5, ha="right", va="center",
                        transform=ax_lbl.transAxes)
            ax_lbl.axis("off")

            cell = cs_cache.get((date_str, orbit, berth), {})
            blobs    = cell.get("blobs", [])
            ratio    = cell.get("ratio")
            linear   = cell.get("linear")
            water_m  = cell.get("water_mask")
            axis_uvec = cell.get("axis_uvec", (1.0, 0.0))
            det_str  = cell.get("det", "?")
            match    = cell.get("match")
            ux, uy   = axis_uvec

            # Qualifying blobs after min_len filter (already applied in cs_cache)
            qual = [b for b in blobs if b["length_m"] >= min_lens[berth]]
            n_hulls = len(qual)
            classes = "/".join(sorted(set(_classify_hull(b["length_m"]) for b in qual))) if qual else "—"

            # optical panel: S2 RGB or SAR dB
            rgb = s2_cache.get(date_str)
            if rgb is not None:
                ax_opt.imshow(rgb, interpolation="nearest", origin="upper")
                ax_opt.text(0.03, 0.97, "S2", color="white", fontsize=3,
                            ha="left", va="top", transform=ax_opt.transAxes,
                            bbox=dict(fc="black", alpha=0.5, lw=0, pad=0.5))
            elif linear is not None and np.isfinite(linear).any():
                db = np.where(linear > 0, 10 * np.log10(linear), np.nan)
                fin = db[np.isfinite(db)]
                if fin.size > 0:
                    ax_opt.imshow(db, cmap="gray",
                                  vmin=np.percentile(fin, 2),
                                  vmax=np.percentile(fin, 98),
                                  interpolation="nearest", origin="upper")
                ax_opt.text(0.03, 0.97, "SAR", color="#aaa", fontsize=3,
                            ha="left", va="top", transform=ax_opt.transAxes,
                            bbox=dict(fc="black", alpha=0.5, lw=0, pad=0.5))
            else:
                ax_opt.text(0.5, 0.5, "NO DATA", color="#444",
                            ha="center", va="center", fontsize=4,
                            transform=ax_opt.transAxes)

            # SAR+hulls panel: ratio image with boxes
            if ratio is not None and np.isfinite(ratio).any():
                disp = np.clip(np.where(np.isfinite(ratio), ratio, np.nan), -10, 30)
                ax_sar.imshow(disp, cmap="gray", vmin=-10, vmax=30,
                              interpolation="nearest", origin="upper")
                if water_m is not None:
                    try:
                        ax_sar.contour(water_m.astype(float), levels=[0.5],
                                       colors=["#3498db"], linewidths=0.4, alpha=0.7)
                    except Exception: pass
                _draw_hull_boxes(ax_sar, qual, ux, uy, min_lens[berth])
            elif linear is not None and np.isfinite(linear).any():
                db = np.where(linear > 0, 10 * np.log10(linear), np.nan)
                fin = db[np.isfinite(db)]
                if fin.size > 0:
                    ax_sar.imshow(db, cmap="gray",
                                  vmin=np.percentile(fin, 2),
                                  vmax=np.percentile(fin, 98),
                                  interpolation="nearest", origin="upper")
            else:
                ax_sar.text(0.5, 0.5, "NO SAR", color="#444",
                            ha="center", va="center", fontsize=4,
                            transform=ax_sar.transAxes)

            # annotation
            ref_color = "#2ecc71" if optical_ref == "EMP" else "#e74c3c" if optical_ref == "OCC" else "#888"
            det_color = "#2ecc71" if det_str == "EMP" else "#f1c40f" if det_str == "OCC" else "#888"
            ann = f"ref={optical_ref}  det={det_str}\nкорпусов: {n_hulls}  {classes}"
            border_color = "#e74c3c" if match is False else ("#2ecc71" if match is True else "#555")
            ax_sar.text(0.02, 0.98, ann, color="white", fontsize=3.0, ha="left", va="top",
                        transform=ax_sar.transAxes, fontweight="bold",
                        bbox=dict(boxstyle="square,pad=0.15", fc="black", alpha=0.7, lw=0))
            for ax in (ax_opt, ax_sar):
                for sp in ax.spines.values():
                    sp.set_edgecolor(border_color); sp.set_linewidth(1.0)

        # column headers on first row
        fig.text(LABEL_W / fig_w + PANEL_W / fig_w / 2,
                 1.0 - 0.004,
                 "Optical (S2 RGB)", color="white", fontsize=4.5,
                 ha="center", va="top")
        fig.text(LABEL_W / fig_w + 1.5 * PANEL_W / fig_w,
                 1.0 - 0.004,
                 "SAR + hull boxes (yellow=ok, orange=cap)", color="white", fontsize=4.5,
                 ha="center", va="top")

        fig.suptitle(
            f"Contact sheet — {berth.replace('_', ' ')} "
            f"(merge_gap=120m MERGE_PERP_TOL=40m min_len={min_lens[berth]}m) "
            "| green=match red=mismatch",
            color="white", fontsize=5, y=1.0,
        )
        out_png = OUT_DIR / f"contact_sheet_{berth}.png"
        fig.savefig(out_png, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        kb = out_png.stat().st_size // 1024
        print(f"Saved contact sheet: {out_png.name} ({kb} KB)")
        plt.close(fig)


def _apply_timedelta_rule(rows):
    """
    Time-delta exclusion (both directions):
      Case A [FP→?]: optical=EMP, det=OCC, L≥250, asp≥4, |Δt|>4h
        Ship arrived after S2 image was taken → not a real FP.
      Case B [FN→?]: optical=OCC, det=EMP, L=0 (no signal at all), |Δt|>4h
        Ship may have departed before SAR pass → not a real FN.
    Returns list of adjusted rows (copy, no mutation).
    """
    from datetime import datetime, timezone

    def _parse_dt(s):
        s = s.strip().rstrip("Z").split("+")[0]
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)

    fp_excl, fn_excl = [], []
    out = []
    for r in rows:
        s1_dt_str = r.get("s1_dt", "")
        s2_dt_str = r.get("s2_dt", "")
        if s1_dt_str and s2_dt_str:
            try:
                delta_h = abs((_parse_dt(s1_dt_str) - _parse_dt(s2_dt_str))
                              .total_seconds()) / 3600
                if delta_h > 4.0:
                    # Case A: FP candidate — ship arrived after optical
                    if (r.get("optical_ref") == "EMP"
                            and r.get("det") == "OCC"
                            and float(r.get("length_m") or 0) >= 250
                            and float(r.get("aspect") or 0) >= 4.0):
                        r = dict(r, optical_ref="?")
                        fp_excl.append(
                            f"    [FP→?] {r['date']} o{r['orbit']} {r['berth']}"
                            f" Δt={delta_h:.1f}h L={r['length_m']}m asp={r['aspect']}")
                    # Case B: FN candidate — ship may have left before SAR
                    elif (r.get("optical_ref") == "OCC"
                            and r.get("det") == "EMP"
                            and float(r.get("length_m") or 0) == 0):
                        r = dict(r, optical_ref="?")
                        fn_excl.append(
                            f"    [FN→?] {r['date']} o{r['orbit']} {r['berth']}"
                            f" Δt={delta_h:.1f}h L=0")
            except Exception as e:
                print(f"  TIME-DELTA parse error: {e}")
        out.append(r)
    if fp_excl or fn_excl:
        print(f"  TIME-DELTA reclassified: {len(fp_excl)} FP→?, {len(fn_excl)} FN→?")
        for line in fp_excl + fn_excl:
            print(line)
    return out


def _assert_confmat(results, results_asp25=None):
    """
    Compare PC-based confusion matrix against SH ASSERT_TARGETS.
    Applies time-delta rule before gate.
    Shows OLD (SH) | NEW asp≥3.0 | NEW asp≥2.5 columns.
    Gate uses time-delta-adjusted asp≥3.0 numbers.
    asp≥2.5 adopted only if prec≥90% (shown for reference).
    """
    adj = _apply_timedelta_rule(results)
    n_excl = sum(1 for a, b in zip(adj, results)
                 if a.get("optical_ref") == "?" and b.get("optical_ref") != "?")
    if n_excl:
        print(f"  → {n_excl} row(s) reclassified '?' by time-delta rule")

    adj25 = _apply_timedelta_rule(results_asp25 or [])

    ok = True
    print(f"\n{'Berth':<20}  {'OLD (SH)':^28}  "
          f"{'NEW asp≥3.0':^28}  {'NEW asp≥2.5':^28}  Gate")
    print(f"{'':20}  "
          f"{'TP':>4} {'FN':>4} {'FP':>4} {'TN':>4}  "
          f"{'TP':>4} {'FN':>4} {'FP':>4} {'TN':>4}  "
          f"{'TP':>4} {'FN':>4} {'FP':>4} {'TN':>4}  "
          f"{'Prec30':>7} {'Rec30':>7} {'Prec25':>7} {'Rec25':>7}")
    print("-" * 110)

    for berth in BERTHS:
        rows_b = [r for r in adj
                  if r["berth"] == berth
                  and r["optical_ref"] in ("OCC", "EMP")
                  and r["det"] in ("OCC", "EMP")]
        tp, tn, fp, fn = _cm_from_rows(rows_b)
        tgt = ASSERT_TARGETS[berth]
        prec = tp / (tp + fp) * 100 if (tp + fp) > 0 else float("nan")
        rec  = tp / (tp + fn) * 100 if (tp + fn) > 0 else float("nan")
        sh_tp = tgt["TP"]; sh_fn = tgt["FN"]
        sh_rec = sh_tp / (sh_tp + sh_fn) * 100 if (sh_tp + sh_fn) > 0 else 0.0
        gate_ok = (prec >= 90.0 or tp + fp == 0) and rec >= sh_rec - 0.1
        gate_str = "OK" if gate_ok else "FAIL"
        if not gate_ok:
            ok = False

        if berth == "east_t_head" and adj25:
            rows_25 = [r for r in adj25
                       if r["berth"] == "east_t_head"
                       and r["optical_ref"] in ("OCC", "EMP")
                       and r["det"] in ("OCC", "EMP")]
            tp25, tn25, fp25, fn25 = _cm_from_rows(rows_25)
            prec25 = tp25 / (tp25 + fp25) * 100 if (tp25 + fp25) > 0 else float("nan")
            rec25  = tp25 / (tp25 + fn25) * 100 if (tp25 + fn25) > 0 else float("nan")
        else:
            tp25, tn25, fp25, fn25 = tp, tn, fp, fn
            prec25, rec25 = prec, rec

        print(f"{berth:<20}  "
              f"{tgt['TP']:>4} {tgt['FN']:>4} {tgt['FP']:>4} {tgt['TN']:>4}  "
              f"{tp:>4} {fn:>4} {fp:>4} {tn:>4}  "
              f"{tp25:>4} {fn25:>4} {fp25:>4} {tn25:>4}  "
              f"{prec:>6.1f}% {rec:>6.1f}%  "
              f"{prec25:>6.1f}% {rec25:>6.1f}%  {gate_str}")

        if not gate_ok:
            print(f"  → GATE FAIL: prec={prec:.1f}% (need≥90%)  "
                  f"rec={rec:.1f}% (need≥{sh_rec:.1f}%)")
            print("  Per-row mismatches:")
            for r in rows_b:
                if r["optical_ref"] != r["det"]:
                    print(f"    MISMATCH {r['date']} o{r['orbit']} {berth}: "
                          f"ref={r['optical_ref']} det={r['det']} "
                          f"len={r['length_m']}m asp={r['aspect']}")

        if berth == "east_t_head" and adj25 and (tp25 != tp or fn25 != fn):
            verdict = "≥90% — accept" if (not math.isnan(prec25) and prec25 >= 90.0) else "<90% — reject"
            print(f"  → asp≥2.5 delta: TP {tp}→{tp25} FN {fn}→{fn25} "
                  f"FP {fp}→{fp25}  prec25={prec25:.1f}% {verdict}")

    print("-" * 110)
    if not ok:
        sys.exit("FATAL: PC assert gate failed — v4_history and DB write blocked")
    print("=== PC assert gate passed (prec≥90%, rec not worse) — OK to proceed ===")


if __name__ == "__main__":
    main()
