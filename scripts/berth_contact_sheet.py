"""
berth_contact_sheet.py — contact sheet for berth validation.

Grid: 40 rows (date × orbit) × 3 columns (berths) = 120 cells.
Each cell: ~1×1 km SAR crop, ROI rectangle, annotation, green/red border.

Also writes legend.png with ROI coordinates, threshold, min area.

Output: diagnostics/berth_v4/contact_sheet/
"""

import os, sys, csv, ast, zipfile
from pathlib import Path
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
from matplotlib.gridspec import GridSpec

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder,
)
from shapely.geometry import LineString
import shapely

# ── Config ───────────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID and CLIENT_SECRET env vars must be set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_cs", service_url="https://sh.dataspace.copernicus.eu"
)

ROOT     = Path(__file__).parent.parent
OUT_DIR  = ROOT / "diagnostics" / "berth_v4" / "contact_sheet"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Constants (must match berth_detect_v410) ─────────────────────────────────
BERTH_SPEC = {
    "sea_island":     [(50.28316, 29.23318), (50.28816, 29.22433)],
    "east_t_head":    [(50.33833, 29.23812), (50.34054, 29.22189)],
    "southeast_pier": [(50.33646, 29.21764), (50.33591, 29.21424), (50.33522, 29.21264)],
}
EXPERIMENTAL_BERTHS = {"southeast_pier"}

CORRIDOR_HALF_M = 150
CORRIDOR_END_M  = 100
RATIO_THR       = 10.0
MIN_AREA_PX     = 15

# Cell image size per berth: ~1 km × 1 km at 10m resolution = 100×100 px
CELL_KM    = 1.0
RESOLUTION = 10

# Validation date-orbit list (matches VALIDATION_DATES_ORBITS in berth_detect_v410)
VALIDATION_DATES_ORBITS = [
    ("2019-01-15", 35),  ("2019-03-08", 101), ("2019-06-24", 101),
    ("2020-01-10", 35),  ("2020-03-14", 101), ("2020-06-14", 35),
    ("2020-06-30", 101), ("2021-01-08", 101), ("2021-04-10", 35),
    ("2021-06-25", 101), ("2022-01-15", 101), ("2022-06-16", 35),
    ("2023-07-09", 101), ("2024-01-13", 35),  ("2024-06-29", 35),
    ("2024-07-03", 101), ("2025-01-07", 35),  ("2025-01-11", 101),
    ("2026-04-14", 35),  ("2026-04-18", 101),
    ("2017-03-06", 101), ("2017-08-29", 35),  ("2018-01-12", 101),
    ("2018-05-20", 35),  ("2019-05-15", 35),  ("2019-10-22", 101),
    ("2020-04-03", 35),  ("2020-05-13", 101), ("2021-02-01", 101),
    ("2021-07-27", 35),  ("2022-02-04", 35),  ("2022-02-08", 101),
    ("2023-09-03", 35),  ("2023-10-25", 101), ("2024-05-16", 101),
    ("2024-12-26", 35),  ("2025-09-02", 101), ("2025-11-15", 35),
    ("2026-01-02", 35),  ("2026-09-28", 101),
]
BERTHS = ["sea_island", "east_t_head", "southeast_pier"]

# ── Geometry ─────────────────────────────────────────────────────────────────
def metres_per_deg_lat(): return 111320.0
def metres_per_deg_lon(lat): return 111320.0 * np.cos(np.deg2rad(lat))

def berth_center(pts):
    lons = [p[0] for p in pts]; lats = [p[1] for p in pts]
    return np.mean(lons), np.mean(lats)

def cell_bbox(center_lon, center_lat, km=CELL_KM):
    half_lat = (km * 500) / metres_per_deg_lat()
    half_lon = (km * 500) / metres_per_deg_lon(center_lat)
    return BBox(bbox=[center_lon - half_lon, center_lat - half_lat,
                      center_lon + half_lon, center_lat + half_lat],
                crs=CRS.WGS84)

def _extend_line(pts, end_m):
    lat_mid = np.mean([p[1] for p in pts])
    mpdlat = metres_per_deg_lat(); mpdlon = metres_per_deg_lon(lat_mid)
    def ext(a, b, d):
        dx = (b[0]-a[0])*mpdlon; dy = (b[1]-a[1])*mpdlat
        L = np.hypot(dx, dy)
        if L < 1e-6: return b
        return (b[0] + dx/L*d/mpdlon, b[1] + dy/L*d/mpdlat)
    p = list(pts)
    return [ext(p[1], p[0], end_m)] + p[1:-1] + [ext(p[-2], p[-1], end_m)]

def build_corridor_bbox(pts_lonlat, half_m, end_m):
    """Return axis-aligned bounding box of the corridor polygon."""
    ext = _extend_line(pts_lonlat, end_m)
    lat_mid = np.mean([p[1] for p in pts_lonlat])
    mpdlat = metres_per_deg_lat(); mpdlon = metres_per_deg_lon(lat_mid)
    buf_lat = half_m / mpdlat; buf_lon = half_m / mpdlon
    lons = [p[0] for p in ext]; lats = [p[1] for p in ext]
    return (min(lons)-buf_lon, min(lats)-buf_lat,
            max(lons)+buf_lon, max(lats)+buf_lat)

EVALSCRIPT_DB = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  var v = Math.max(s.VV, 1e-10);
  return [10 * Math.log10(v)];
}
"""

# ── Catalog search ────────────────────────────────────────────────────────────
def scene_datetime(date_str, orbit, bbox):
    catalog = SentinelHubCatalog(config=config)
    results = list(catalog.search(
        S1_CDSE, bbox=bbox,
        time=(date_str+"T00:00:00Z", date_str+"T23:59:59Z"),
        fields={"include": ["id","properties.datetime","properties.sat:relative_orbit"]},
    ))
    for r in results:
        p = r.get("properties", {})
        if int(p.get("sat:relative_orbit", 0)) == orbit:
            return p.get("datetime", "")
    return ""

# ── Image fetch ───────────────────────────────────────────────────────────────
def fetch_db_crop(dt_str, bbox):
    """Return dB image for bbox at given datetime. None if fetch fails."""
    sz = bbox_to_dimensions(bbox, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_DB,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(dt_str[:19].replace("T"," "), dt_str[:19].replace("T"," ")),
            other_args={"processing": {"backCoeff": "SIGMA0_ELLIPSOID",
                                       "orthorectify": True,
                                       "demInstance": "COPERNICUS"}},
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox, size=sz, config=config,
    )
    try:
        data = req.get_data()
        arr = data[0]
        if arr.ndim == 3: arr = arr[:,:,0]
        return arr.astype(np.float32)
    except Exception as e:
        print(f"  fetch failed: {e}")
        return None

# ── Load results CSV ──────────────────────────────────────────────────────────
def load_results(csv_path):
    """Returns dict keyed by (date, orbit, berth) → {occupied, max_ratio_dB, ...}."""
    result = {}
    if not csv_path.exists():
        print(f"WARNING: {csv_path} not found")
        return result
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            key = (row["date"], int(row["orbit"]), row["berth"])
            result[key] = {
                "occupied":      row.get("occupied", ""),
                "no_background": row.get("no_background", ""),
            }
    return result

def load_reference(csv_path, col_map):
    """col_map: {berth: col_name_in_csv}. Returns dict (date, orbit, berth) → occupied."""
    ref = {}
    if not csv_path.exists():
        print(f"WARNING: {csv_path} not found")
        return ref
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        date = row.get("date","").strip()
        try: orbit = int(float(row.get("orbit", row.get("rel_orbit", 0))))
        except: continue
        for berth, col in col_map.items():
            val = str(row.get(col,"")).strip().lower()
            if val in ("true","1","yes","occupied"): ref[(date, orbit, berth)] = True
            elif val in ("false","0","no","empty"):  ref[(date, orbit, berth)] = False
    return ref

# ── ROI rectangle in image coordinates ───────────────────────────────────────
def corridor_rect_in_cell(pts_lonlat, cell_bbox_obj, img_shape):
    """Return (x0, y0, width, height) of corridor bbox in image pixel coords."""
    h, w = img_shape
    cmin_lon = cell_bbox_obj.min_x; cmax_lon = cell_bbox_obj.max_x
    cmin_lat = cell_bbox_obj.min_y; cmax_lat = cell_bbox_obj.max_y
    clon = cmax_lon - cmin_lon; clat = cmax_lat - cmin_lat

    r_lon_min, r_lat_min, r_lon_max, r_lat_max = build_corridor_bbox(
        pts_lonlat, CORRIDOR_HALF_M, CORRIDOR_END_M)

    def to_px(lon, lat):
        px = (lon - cmin_lon) / clon * w
        py = (cmax_lat - lat) / clat * h
        return px, py

    x0, y0 = to_px(r_lon_min, r_lat_max)  # top-left
    x1, y1 = to_px(r_lon_max, r_lat_min)  # bottom-right
    return x0, y0, x1-x0, y1-y0           # x, y, w, h

# ── Compute max_ratio in ROI from raw image ───────────────────────────────────
def compute_max_ratio_in_roi(db_img, pts_lonlat, cell_bbox_obj):
    """Very rough: max dB in the axis-aligned corridor bbox."""
    h, w = db_img.shape
    cmin_lon = cell_bbox_obj.min_x; cmax_lon = cell_bbox_obj.max_x
    cmin_lat = cell_bbox_obj.min_y; cmax_lat = cell_bbox_obj.max_y
    clon = cmax_lon - cmin_lon; clat = cmax_lat - cmin_lat

    r_lon_min, r_lat_min, r_lon_max, r_lat_max = build_corridor_bbox(
        pts_lonlat, CORRIDOR_HALF_M, CORRIDOR_END_M)

    def to_px_int(lon, lat):
        px = int((lon - cmin_lon) / clon * w)
        py = int((cmax_lat - lat) / clat * h)
        return np.clip(px, 0, w-1), np.clip(py, 0, h-1)

    x0, y0 = to_px_int(r_lon_min, r_lat_max)
    x1, y1 = to_px_int(r_lon_max, r_lat_min)
    xs, xe = min(x0,x1), max(x0,x1)+1
    ys, ye = min(y0,y1), max(y0,y1)+1
    roi = db_img[ys:ye, xs:xe]
    finite = roi[np.isfinite(roi)]
    if finite.size == 0: return float('nan')
    return float(np.nanmax(finite))

# ── Draw one cell ─────────────────────────────────────────────────────────────
def draw_cell(ax, db_img, pts_lonlat, cell_bbox_obj,
              det_occ, ref_occ, max_db, no_bg):
    """Render SAR image with ROI box and annotation into ax."""
    if db_img is not None:
        vmin, vmax = np.nanpercentile(db_img, [2, 98])
        ax.imshow(db_img, cmap="gray", vmin=vmin, vmax=vmax,
                  interpolation="nearest", origin="upper")
    else:
        ax.set_facecolor("#111")
        ax.text(0.5, 0.5, "NO IMAGE", color="gray",
                ha="center", va="center", transform=ax.transAxes, fontsize=5)

    ax.set_xticks([]); ax.set_yticks([])

    # ROI rectangle
    if db_img is not None:
        rx, ry, rw, rh = corridor_rect_in_cell(pts_lonlat, cell_bbox_obj, db_img.shape)
        roi_rect = mpatches.Rectangle((rx, ry), rw, rh,
            linewidth=1.0, edgecolor="yellow", facecolor="none", alpha=0.85)
        ax.add_patch(roi_rect)

    # Annotation text
    if no_bg:
        label = "NO BG"
        txt_color = "#aaa"
    elif det_occ is None:
        label = "?"
        txt_color = "#aaa"
    else:
        d_str = "OCC" if det_occ else "EMP"
        r_str = "OCC" if ref_occ else ("EMP" if ref_occ is not None else "?")
        db_str = f"{max_db:.0f}dB" if np.isfinite(max_db) else "?"
        label = f"d:{d_str} r:{r_str}\n{db_str}/thr{RATIO_THR:.0f}"
        txt_color = "white"

    ax.text(0.03, 0.97, label, color=txt_color,
            ha="left", va="top", transform=ax.transAxes,
            fontsize=4.5, fontweight="bold",
            bbox=dict(boxstyle="square,pad=0.1", fc="black", alpha=0.55, lw=0))

    # Border colour
    if no_bg or det_occ is None or ref_occ is None:
        bcolor = "#666"
    elif det_occ == ref_occ:
        bcolor = "#2ecc71"   # green
    else:
        bcolor = "#e74c3c"   # red

    for spine in ax.spines.values():
        spine.set_edgecolor(bcolor)
        spine.set_linewidth(2.5)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Load detection results
    det_csv   = ROOT / "diagnostics" / "berth_v410" / "results_table.csv"
    det_data  = load_results(det_csv)

    # Load reference labels
    ref1_csv  = ROOT / "diagnostics" / "berth_v4" / "results_table.csv"
    ref1      = load_reference(ref1_csv, {
        "sea_island": "occupied", "east_t_head": "occupied"})

    ref2_csv  = ROOT / "diagnostics" / "berth_v3" / "summary.csv"
    ref2      = load_reference(ref2_csv, {
        "sea_island": "sea_island_occ", "east_t_head": "east_t_occ"})

    # Merge references: prefer ref1 if present, else ref2
    ref = {}
    for key in set(list(ref1.keys()) + list(ref2.keys())):
        if key in ref1: ref[key] = ref1[key]
        elif key in ref2: ref[key] = ref2[key]

    n_rows = len(VALIDATION_DATES_ORBITS)  # 40
    n_cols = len(BERTHS)                   # 3

    # Pre-compute cell bboxes per berth
    cell_bboxes = {}
    for berth, pts in BERTH_SPEC.items():
        clon, clat = berth_center(pts)
        cell_bboxes[berth] = cell_bbox(clon, clat)

    print(f"Building contact sheet: {n_rows} rows × {n_cols} cols")
    print(f"Fetching {n_rows} scenes × {n_cols} crops ...")

    # Collect unique (date, orbit) → datetime string
    dt_cache = {}
    for date_str, orbit in VALIDATION_DATES_ORBITS:
        key = (date_str, orbit)
        if key not in dt_cache:
            # Use any berth's bbox to search (AOI covers all)
            from sentinelhub import BBox as _BBox
            aoi = _BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)
            dt_cache[key] = scene_datetime(date_str, orbit, aoi)
            print(f"  {date_str} o{orbit}: {dt_cache[key][:19] if dt_cache[key] else 'NOT FOUND'}")

    # Figure
    cell_px   = 100
    cell_in   = cell_px / 72.0        # 1 inch = 72 pt; each cell ~1.4"
    fig_w     = n_cols * cell_in * 1.5
    fig_h     = n_rows * cell_in * 1.3
    fig, axes = plt.subplots(n_rows, n_cols,
                             figsize=(fig_w, fig_h),
                             gridspec_kw={"hspace": 0.05, "wspace": 0.02})

    fig.patch.set_facecolor("#1a1a1a")

    # Column headers
    for ci, berth in enumerate(BERTHS):
        axes[0, ci].set_title(
            berth.replace("_"," "),
            color="white", fontsize=7, pad=3, fontweight="bold")

    for ri, (date_str, orbit) in enumerate(VALIDATION_DATES_ORBITS):
        dt_str = dt_cache.get((date_str, orbit), "")
        # Row label
        axes[ri, 0].set_ylabel(
            f"{date_str}\no{orbit}",
            color="#ccc", fontsize=4.5, rotation=0,
            labelpad=36, va="center")

        for ci, berth in enumerate(BERTHS):
            ax = axes[ri, ci]
            pts = BERTH_SPEC[berth]
            bbox_obj = cell_bboxes[berth]

            # Fetch image
            db_img = None
            if dt_str:
                db_img = fetch_db_crop(dt_str, bbox_obj)

            # Detection result
            det_key = (date_str, orbit, berth)
            det_row = det_data.get(det_key, {})
            no_bg = str(det_row.get("no_background","")).strip() == "1"
            det_occ_str = str(det_row.get("occupied","")).strip().lower()
            if det_occ_str in ("true","1"): det_occ = True
            elif det_occ_str in ("false","0"): det_occ = False
            else: det_occ = None

            # Reference
            ref_occ = ref.get(det_key)

            # max dB in ROI from image
            max_db = float('nan')
            if db_img is not None:
                max_db = compute_max_ratio_in_roi(db_img, pts, bbox_obj)

            draw_cell(ax, db_img, pts, bbox_obj, det_occ, ref_occ, max_db, no_bg)

    # Supertitle
    fig.suptitle(
        f"Berth v410 — Validation contact sheet  |  threshold={RATIO_THR} dB  |  "
        f"Yellow box = ROI  |  Green border = match  |  Red = mismatch",
        color="white", fontsize=7, y=0.995)

    out_png = OUT_DIR / "contact_sheet.png"
    fig.savefig(out_png, dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"Saved: {out_png}")

    # ── Legend PNG ────────────────────────────────────────────────────────────
    fig_l, ax_l = plt.subplots(figsize=(8, 4))
    fig_l.patch.set_facecolor("#1a1a1a")
    ax_l.set_facecolor("#1a1a1a")
    ax_l.axis("off")

    lines = [
        "BERTH ROI PARAMETERS (berth_detect_v410.py)",
        "",
        f"RATIO_THR      = {RATIO_THR} dB   (10×log10(scene/background) threshold to flag a pixel as ship)",
        f"MIN_AREA_PX    = {MIN_AREA_PX} px  (minimum blob area in ROI)",
        f"CORRIDOR_HALF_M = {CORRIDOR_HALF_M} m  (ROI half-width perpendicular to berth axis)",
        f"CORRIDOR_END_M  = {CORRIDOR_END_M} m   (ROI extension beyond berth endpoints)",
        "",
        "ROI CENTRELINES (lon, lat pairs):",
    ]
    for berth, pts in BERTH_SPEC.items():
        exp = " [EXPERIMENTAL]" if berth in EXPERIMENTAL_BERTHS else ""
        lines.append(f"  {berth}{exp}:")
        for p in pts:
            lines.append(f"    ({p[0]:.5f}, {p[1]:.5f})")
        clon, clat = berth_center(pts)
        lines.append(f"    → cell centre: ({clon:.5f}, {clat:.5f}), "
                     f"crop {CELL_KM:.1f}×{CELL_KM:.1f} km at {RESOLUTION} m/px")

    ax_l.text(0.03, 0.97, "\n".join(lines),
              color="white", ha="left", va="top",
              transform=ax_l.transAxes, fontsize=8.5,
              fontfamily="monospace")

    leg_png = OUT_DIR / "legend.png"
    fig_l.savefig(leg_png, dpi=120, bbox_inches="tight",
                  facecolor=fig_l.get_facecolor())
    plt.close(fig_l)
    print(f"Saved: {leg_png}")


if __name__ == "__main__":
    main()
