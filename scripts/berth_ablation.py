"""
berth_ablation.py — одна переменная за раз, всё остальное v3.

Режимы:
  v3   — baseline (bg_v3, pier=20, water=20-80, len≥150, no merge)
  bg   — только v4 BG (bg_v4, всё остальное v3)
  pier — только pier=12m (bg_v3, pier=12, water=20-80, len≥150, no merge)
  merge— только gap-merge+120m (bg_v3, pier=20, water=20-80, len≥120, merge=40m)

Выход: diagnostics/berth_v4/ablation/ablation_accuracy.txt + ablation.csv
"""

import os, sys, csv
from pathlib import Path
from collections import defaultdict

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).parent))

from berth_detect_core import (
    RESOLUTION, CELL_KM, CORRIDOR_HALF_M, CORRIDOR_END_M,
    RATIO_THR, MERGE_PERP_TOL_M, struct8,
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

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder,
)
from shapely.geometry import LineString
import shapely

config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url  = "https://sh.dataspace.copernicus.eu"
if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID / CLIENT_SECRET not set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_abl", service_url="https://sh.dataspace.copernicus.eu")

ROOT    = Path(__file__).parent.parent
BG_V3   = ROOT / "diagnostics" / "berth_v4" / "bg_v3"
BG_V4   = ROOT / "diagnostics" / "berth_v4" / "bg_v4"
CSV_REF = ROOT / "diagnostics" / "berth_v4" / "optics_ref_sameday.csv"
OUT_DIR = ROOT / "diagnostics" / "berth_v4" / "ablation"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BERTHS = BERTH_COORDS  # alias for local use
BERTH_COLS = {
    "sea_island": "sea_island_optical_ref",
    "east_t_head": "east_t_head_optical_ref",
    "southeast_pier": "southeast_pier_optical_ref",
}

MODES = {
    "v3":       dict(pier=20, water_i=20, water_o=80, min_len=150, merge=0,  bg_dir=BG_V3),
    "bg":       dict(pier=20, water_i=20, water_o=80, min_len=150, merge=0,  bg_dir=BG_V4),
    "pier":     dict(pier=12, water_i=20, water_o=80, min_len=150, merge=0,  bg_dir=BG_V3),
    "merge":    dict(pier=20, water_i=20, water_o=80, min_len=120, merge=40, bg_dir=BG_V3),
    "bg+merge": dict(pier=20, water_i=20, water_o=80, min_len=120, merge=40, bg_dir=BG_V4),
}

EVALSCRIPT_S1 = """
//VERSION=3
function setup() { return { input: ["VV"], output: { bands:1, sampleType:"FLOAT32" } }; }
function evaluatePixel(s) { return [Math.max(s.VV, 1e-10)]; }
"""

# geometry and find_blobs imported from berth_detect_core above

def scene_dt_s1(date_str, orbit, aoi_bbox):
    cat = SentinelHubCatalog(config=config)
    try:
        res = list(cat.search(S1_CDSE, bbox=aoi_bbox,
            time=(date_str+"T00:00:00Z", date_str+"T23:59:59Z"),
            fields={"include":["properties.datetime","properties.sat:relative_orbit"]}))
    except Exception as e:
        print(f"  catalog err {e}"); return ""
    for r in res:
        p = r.get("properties",{})
        if int(p.get("sat:relative_orbit",0)) == orbit:
            return p.get("datetime","")
    return ""

def fetch_s1_linear(dt_str, bbox_obj):
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S1,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(dt_str[:19].replace("T"," "), dt_str[:19].replace("T"," ")),
            other_args={"processing":{"backCoeff":"SIGMA0_ELLIPSOID","orthorectify":True,
                                      "demInstance":"COPERNICUS"}},
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        d=req.get_data(); a=d[0]
        if a.ndim==3: a=a[:,:,0]
        return a.astype(np.float32)
    except Exception as e:
        print(f"  S1 fetch err: {e}"); return None


def run_ablation():
    aoi = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)

    # Read CSV
    rows = []
    with open(CSV_REF, newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    print(f"CSV rows: {len(rows)}")

    # Cell bboxes
    cell_bboxes = {b: cell_bbox_for(*berth_center(pts)) for b, pts in BERTHS.items()}

    # BG cache per (berth, orbit, mode)
    bg_cache = {}
    for mode_name, cfg in MODES.items():
        bg_dir = cfg["bg_dir"]
        for berth in BERTHS:
            for orbit in (35, 101):
                key = (mode_name, berth, orbit)
                p = bg_dir / f"bg_o{orbit}_{berth}.npy"
                if p.exists():
                    bg_cache[key] = np.load(p)
                else:
                    bg_cache[key] = None
                    print(f"  MISSING BG: {mode_name} o{orbit} {berth}")

    # Result rows: mode, berth, date, orbit, optical_ref, detected
    results = []

    for row in rows:
        date_str = row["date"]
        orbit    = int(row["orbit"])
        # Find S1 scene datetime (cached across modes)
        dt_str_key = (date_str, orbit)
        s1_dt = scene_dt_s1(date_str, orbit, aoi)
        print(f"\n{date_str} o{orbit}  s1_dt={s1_dt[:16] if s1_dt else 'NOT FOUND'}")
        if not s1_dt:
            print("  skip — no scene")
            continue

        for berth, pts in BERTHS.items():
            optical_ref = row[BERTH_COLS[berth]]
            if optical_ref == "?":
                continue

            bbox_obj = cell_bboxes[berth]
            linear   = fetch_s1_linear(s1_dt, bbox_obj)
            if linear is None:
                continue

            for mode_name, cfg in MODES.items():
                bg = bg_cache.get((mode_name, berth, orbit))
                if bg is None:
                    det_str = "NO_BG"; det_bool = None
                else:
                    if bg.shape != linear.shape:
                        bg = nd_zoom(bg, (linear.shape[0]/bg.shape[0],
                                          linear.shape[1]/bg.shape[1]), order=1)
                    ratio = np.where((linear>0)&(bg>0),
                                     10*np.log10(linear/bg), np.nan).astype(np.float32)
                    geom = build_geom(pts, bbox_obj, linear.shape,
                                      cfg["pier"], cfg["water_i"], cfg["water_o"])
                    dbg = (berth=="east_t_head" and date_str=="2025-07-22"
                           and cfg["merge"] > 0)
                    if dbg:
                        print(f"  DEBUG {mode_name} {berth} {date_str}:")
                    _, best, detected = find_blobs(
                        ratio, geom["water_mask"], geom["axis_uvec"],
                        merge_gap_m=cfg["merge"], min_len_m=cfg["min_len"],
                        debug=dbg)
                    det_bool = detected
                    det_str  = "OCC" if detected else "EMP"

                results.append({
                    "mode": mode_name, "berth": berth,
                    "date": date_str, "orbit": orbit,
                    "optical_ref": optical_ref,
                    "det": det_str, "det_bool": det_bool,
                })
            # end modes

    # Save CSV
    csv_out = OUT_DIR / "ablation_rows.csv"
    with open(csv_out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["mode","berth","date","orbit","optical_ref","det"])
        w.writeheader()
        for r in results:
            w.writerow({k: r[k] for k in ["mode","berth","date","orbit","optical_ref","det"]})

    # Accuracy table
    lines = []
    lines.append("Ablation accuracy — one parameter change at a time vs v3 baseline")
    lines.append("")
    hdr = f"{'Mode':<8}  {'Change':<40}  {'Berth':<18}  {'Acc':>6} {'Rec':>6} {'Prec':>6}  "
    hdr += "TP  FN  FP  TN"
    lines.append(hdr)
    lines.append("-" * len(hdr))

    MODE_DESC = {
        "v3":       "baseline (pier=20, water=20-80, len≥150, no merge)",
        "bg":       "v4 BG only      (EMP median, rest=v3)",
        "pier":     "pier=12m only   (bg=v3, water=20-80, len≥150)",
        "merge":    "merge+120m only (bg=v3, pier=20, water=20-80, merge=40m)",
        "bg+merge": "bg+merge (v4 BG + merge=40m/len≥120, pier=20)",
    }

    per_mode_rows = defaultdict(list)
    for r in results:
        per_mode_rows[r["mode"]].append(r)

    summary = {}
    for mode_name in ["v3", "bg", "pier", "merge", "bg+merge"]:
        summary[mode_name] = {}
        mode_rows = per_mode_rows[mode_name]
        for berth in ["sea_island", "east_t_head", "southeast_pier"]:
            br = [r for r in mode_rows if r["berth"]==berth and r["det"]!="NO_BG"]
            if not br: continue
            TP = sum(1 for r in br if r["optical_ref"]=="OCC" and r["det"]=="OCC")
            FN = sum(1 for r in br if r["optical_ref"]=="OCC" and r["det"]=="EMP")
            FP = sum(1 for r in br if r["optical_ref"]=="EMP" and r["det"]=="OCC")
            TN = sum(1 for r in br if r["optical_ref"]=="EMP" and r["det"]=="EMP")
            n  = TP+FN+FP+TN
            acc  = (TP+TN)/n*100 if n else 0
            rec  = TP/(TP+FN)*100 if (TP+FN) else 0
            prec = TP/(TP+FP)*100 if (TP+FP) else float("nan")
            summary[mode_name][berth] = dict(TP=TP,FN=FN,FP=FP,TN=TN,acc=acc,rec=rec,prec=prec)
            desc = MODE_DESC[mode_name] if berth=="sea_island" else " "*40
            lines.append(
                f"{mode_name:<8}  {desc:<40}  {berth:<18}  "
                f"{acc:5.1f}% {rec:5.1f}% {prec:5.1f}%  "
                f"{TP:3d} {FN:3d} {FP:3d} {TN:3d}"
            )
        lines.append("")

    # Recall delta vs v3
    lines.append("=== Recall delta vs v3 baseline ===")
    hdr2 = f"{'Mode':<8}  {'sea_island':>12}  {'east_t_head':>13}  {'southeast_pier':>16}"
    lines.append(hdr2); lines.append("-" * len(hdr2))
    for mode_name in ["bg", "pier", "merge", "bg+merge"]:
        deltas = []
        for berth in ["sea_island", "east_t_head", "southeast_pier"]:
            rec_v3  = summary["v3"].get(berth,{}).get("rec", float("nan"))
            rec_m   = summary[mode_name].get(berth,{}).get("rec", float("nan"))
            d = rec_m - rec_v3
            deltas.append(f"{d:+.1f}%" if not (d != d) else "   n/a")
        lines.append(f"{mode_name:<8}  {deltas[0]:>12}  {deltas[1]:>13}  {deltas[2]:>16}")
    lines.append("")

    # 2024-25 vs 2026 split
    lines.append("=== Recall by period: 2024-25 vs 2026 ===")
    hdr3 = f"{'Mode':<8}  {'Period':<7}  {'Berth':<18}  {'Acc':>6} {'Rec':>6} {'Prec':>6}  TP  FN  FP  TN"
    lines.append(hdr3); lines.append("-" * len(hdr3))
    for mode_name in ["v3", "bg", "merge", "bg+merge"]:
        mode_rows = per_mode_rows[mode_name]
        for period, year_fn in [("2024-25", lambda y: y <= 2025),
                                 ("2026",    lambda y: y >= 2026)]:
            for berth in ["sea_island", "east_t_head", "southeast_pier"]:
                br = [r for r in mode_rows
                      if r["berth"]==berth and r["det"]!="NO_BG"
                      and year_fn(int(r["date"][:4]))]
                if not br: continue
                TP = sum(1 for r in br if r["optical_ref"]=="OCC" and r["det"]=="OCC")
                FN = sum(1 for r in br if r["optical_ref"]=="OCC" and r["det"]=="EMP")
                FP = sum(1 for r in br if r["optical_ref"]=="EMP" and r["det"]=="OCC")
                TN = sum(1 for r in br if r["optical_ref"]=="EMP" and r["det"]=="EMP")
                n  = TP+FN+FP+TN
                acc  = (TP+TN)/n*100 if n else 0
                rec  = TP/(TP+FN)*100 if (TP+FN) else 0
                prec = TP/(TP+FP)*100 if (TP+FP) else float("nan")
                lines.append(
                    f"{mode_name:<8}  {period:<7}  {berth:<18}  "
                    f"{acc:5.1f}% {rec:5.1f}% {prec:5.1f}%  "
                    f"{TP:3d} {FN:3d} {FP:3d} {TN:3d}"
                )
        lines.append("")
    lines.append("")

    txt = "\n".join(lines)
    out_txt = OUT_DIR / "ablation_accuracy.txt"
    out_txt.write_text(txt)
    print("\n" + txt)
    print(f"\nSaved: {out_txt}")
    print(f"Saved: {csv_out}")

    # Bar chart
    fig, axes = plt.subplots(1, 3, figsize=(11, 4))
    fig.patch.set_facecolor("#1a1a1a")
    berth_labels = ["sea_island", "east_t_head", "southeast_pier"]
    mode_colors  = {"v3":"#4a9eff", "bg":"#ff9944", "pier":"#44ff99", "merge":"#ff4499", "bg+merge":"#cc44ff"}
    mode_order   = ["v3", "bg", "pier", "merge", "bg+merge"]
    for ax, berth in zip(axes, berth_labels):
        ax.set_facecolor("#111")
        recalls = [summary.get(m,{}).get(berth,{}).get("rec",0) for m in mode_order]
        bars = ax.bar(mode_order, recalls,
                      color=[mode_colors[m] for m in mode_order], width=0.6)
        ax.set_ylim(0, 110)
        ax.set_title(berth.replace("_"," "), color="white", fontsize=8)
        ax.tick_params(colors="white", labelsize=7)
        ax.spines[:].set_color("#333")
        ax.set_ylabel("Recall (%)", color="white", fontsize=7)
        ax.tick_params(axis='x', labelrotation=25)
        for bar, v in zip(bars, recalls):
            ax.text(bar.get_x()+bar.get_width()/2, min(v+2, 103),
                    f"{v:.0f}%", ha="center", va="bottom", color="white", fontsize=7)
    fig.suptitle("Ablation: recall per berth — one parameter change at a time",
                 color="white", fontsize=9)
    fig.tight_layout(rect=[0,0,1,0.93])
    out_png = OUT_DIR / "ablation_recall.png"
    fig.savefig(out_png, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"Saved: {out_png}")


if __name__ == "__main__":
    run_ablation()
