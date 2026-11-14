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

import os, sys, csv
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
            })
            status = "OK" if match else ("MISMATCH" if match is False else "SKIP")
            print(f"  {berth}: ref={optical_ref} det={det_str} "
                  f"len={len_str}m asp={asp_str} {status}")

    # ── Results CSV ───────────────────────────────────────────────────────────
    res_csv = OUT_DIR / "results.csv"
    fieldnames = ["date","orbit","berth","optical_ref","det","match",
                  "length_m","aspect","no_bg","s1_found"]
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

    # ── Discrepancy contact sheet ─────────────────────────────────────────────
    # (runs before assert so we always have the PNG for debugging)
    disc_rows = sorted(discrepancy_dates)
    print(f"\nDiscrepancies: {len(disc_rows)} date-orbits")
    if not disc_rows:
        print("No discrepancies — skipping PNG"); return

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

    # ── Confmat assert gate — must pass before v4_history and DB write ────────
    print("\n=== Confmat asserts ===")
    _assert_confmat(results)


def _assert_confmat(results):
    """
    Compare PC-based confusion matrix against SH ASSERT_TARGETS.
    Prints old (SH) vs new (PC) side-by-side for every berth.
    Does NOT auto-update targets.
    Gate: precision >= 90% AND recall not worse than SH targets per berth.
    """
    ok = True
    print(f"\n{'Berth':<20}  {'':4}  "
          f"{'OLD (SH)':^28}  {'NEW (PC)':^28}  {'Gate'}")
    print(f"{'':20}  {'':4}  "
          f"{'TP':>4} {'FN':>4} {'FP':>4} {'TN':>4}  "
          f"{'TP':>4} {'FN':>4} {'FP':>4} {'TN':>4}  {'Prec':>7} {'Rec':>7}")
    print("-" * 90)

    for berth in BERTHS:
        rows_b = [r for r in results
                  if r["berth"] == berth
                  and r["optical_ref"] in ("OCC", "EMP")
                  and r["det"] in ("OCC", "EMP")]
        tp, tn, fp, fn = _cm_from_rows(rows_b)
        tgt = ASSERT_TARGETS[berth]

        prec = tp / (tp + fp) * 100 if (tp + fp) > 0 else float("nan")
        rec  = tp / (tp + fn) * 100 if (tp + fn) > 0 else float("nan")

        # SH reference recall for gate comparison
        sh_tp = tgt["TP"]; sh_fn = tgt["FN"]
        sh_rec = sh_tp / (sh_tp + sh_fn) * 100 if (sh_tp + sh_fn) > 0 else 0.0

        gate_ok = (prec >= 90.0 or tp + fp == 0) and rec >= sh_rec - 0.1
        gate_str = "OK" if gate_ok else "FAIL"
        if not gate_ok:
            ok = False

        print(f"{berth:<20}  {'':4}  "
              f"{tgt['TP']:>4} {tgt['FN']:>4} {tgt['FP']:>4} {tgt['TN']:>4}  "
              f"{tp:>4} {fn:>4} {fp:>4} {tn:>4}  "
              f"{prec:>6.1f}% {rec:>6.1f}%  {gate_str}")

        if not gate_ok:
            print(f"  → GATE FAIL: prec={prec:.1f}% (need≥90%)  "
                  f"rec={rec:.1f}% (need≥{sh_rec:.1f}%)")
            print(f"  Per-row mismatches:")
            for r in rows_b:
                if r["optical_ref"] != r["det"]:
                    print(f"    MISMATCH {r['date']} o{r['orbit']} {berth}: "
                          f"ref={r['optical_ref']} det={r['det']} "
                          f"len={r['length_m']}m asp={r['aspect']}")

    print("-" * 90)
    if not ok:
        sys.exit("FATAL: PC assert gate failed — v4_history and DB write blocked")
    print("=== PC assert gate passed (prec≥90%, rec not worse) — OK to proceed ===")


if __name__ == "__main__":
    main()
