"""
berth_contact_sheet.py — enhanced contact sheet for berth validation.

Grid: 40 rows (date × orbit) × 3 berths = 120 "mega-cells".
Each mega-cell has 3 panels:
  top (scene):  SAR dB + real corridor polygon + red contours of all blobs
                above RATIO_THR + cyan bbox of largest ROI blob
  bottom-left:  background image (20th-percentile composite, dB scale)
  bottom-right: ratio map (scene/bg, dB) + threshold contour

Annotation per cell:
  det=OCC/EMP  ref=OCC/EMP  max_ratio=X dB  area=Y px (thr 15 px)

No detector parameters changed.
Output: diagnostics/berth_v4/contact_sheet/
"""

import os, sys, csv
from pathlib import Path
from datetime import datetime

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

# ── Config ───────────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID and CLIENT_SECRET env vars must be set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_cs2", service_url="https://sh.dataspace.copernicus.eu"
)

ROOT     = Path(__file__).parent.parent
OUT_DIR  = ROOT / "diagnostics" / "berth_v4" / "contact_sheet"
OUT_DIR.mkdir(parents=True, exist_ok=True)
BG_CACHE = ROOT / "diagnostics" / "berth_v3" / "bg_cache"

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
CELL_KM         = 1.0
RESOLUTION      = 10

# Full AOI (same as berth_detect_v410)
AOI_BBOX = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)

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

# ── Evalscript: return linear VV ──────────────────────────────────────────────
EVALSCRIPT_LINEAR = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  return [Math.max(s.VV, 1e-10)];
}
"""

# ── Geometry helpers ──────────────────────────────────────────────────────────
def metres_per_deg_lat(): return 111320.0
def metres_per_deg_lon(lat): return 111320.0 * np.cos(np.deg2rad(lat))

def berth_center(pts):
    return np.mean([p[0] for p in pts]), np.mean([p[1] for p in pts])

def cell_bbox(clon, clat, km=CELL_KM):
    half_lat = (km * 500) / metres_per_deg_lat()
    half_lon = (km * 500) / metres_per_deg_lon(clat)
    return BBox(bbox=[clon-half_lon, clat-half_lat,
                      clon+half_lon, clat+half_lat], crs=CRS.WGS84)

def _extend_line(pts, end_m):
    lat_mid = np.mean([p[1] for p in pts])
    mpdlat = metres_per_deg_lat(); mpdlon = metres_per_deg_lon(lat_mid)
    def ext(a, b, d):
        dx = (b[0]-a[0])*mpdlon; dy = (b[1]-a[1])*mpdlat
        L = np.hypot(dx, dy)
        if L < 1e-6: return b
        return (b[0]+dx/L*d/mpdlon, b[1]+dy/L*d/mpdlat)
    p = list(pts)
    return [ext(p[1],p[0],end_m)] + p[1:-1] + [ext(p[-2],p[-1],end_m)]

def build_corridor_polygon(pts_lonlat, half_m, end_m):
    """Return shapely polygon for corridor."""
    ext = _extend_line(pts_lonlat, end_m)
    line = LineString(ext)
    lat_mid = np.mean([p[1] for p in pts_lonlat])
    buf_deg = ((half_m/metres_per_deg_lat()) + (half_m/metres_per_deg_lon(lat_mid))) / 2
    return line.buffer(buf_deg)

def lonlat_to_px(lon, lat, bbox_obj, img_shape):
    """Convert lon/lat to pixel coords in image."""
    h, w = img_shape
    px = (lon - bbox_obj.min_x) / (bbox_obj.max_x - bbox_obj.min_x) * w
    py = (bbox_obj.max_y - lat) / (bbox_obj.max_y - bbox_obj.min_y) * h
    return px, py

def rasterize_poly(poly, bbox_obj, img_shape):
    """Rasterize shapely polygon to boolean mask in image coordinates."""
    h, w = img_shape
    lons = np.linspace(
        bbox_obj.min_x + 0.5*(bbox_obj.max_x-bbox_obj.min_x)/w,
        bbox_obj.max_x - 0.5*(bbox_obj.max_x-bbox_obj.min_x)/w, w)
    lats = np.linspace(
        bbox_obj.max_y - 0.5*(bbox_obj.max_y-bbox_obj.min_y)/h,
        bbox_obj.min_y + 0.5*(bbox_obj.max_y-bbox_obj.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    try:
        return shapely.contains_xy(poly, lon_g.ravel(), lat_g.ravel()).reshape(h, w)
    except AttributeError:
        from shapely.vectorized import contains
        return contains(poly, lon_g, lat_g)

# ── Background cache crop ─────────────────────────────────────────────────────
def crop_aoi_to_cell(aoi_arr, cell_bbox_obj):
    """Crop 2D full-AOI array to cell_bbox pixel region."""
    h_full, w_full = aoi_arr.shape[:2]
    alon = AOI_BBOX.max_x - AOI_BBOX.min_x
    alat = AOI_BBOX.max_y - AOI_BBOX.min_y
    x0 = int((cell_bbox_obj.min_x - AOI_BBOX.min_x) / alon * w_full)
    x1 = int((cell_bbox_obj.max_x - AOI_BBOX.min_x) / alon * w_full)
    y0 = int((AOI_BBOX.max_y - cell_bbox_obj.max_y) / alat * h_full)
    y1 = int((AOI_BBOX.max_y - cell_bbox_obj.min_y) / alat * h_full)
    x0 = max(0, x0); x1 = min(w_full, x1)
    y0 = max(0, y0); y1 = min(h_full, y1)
    if x1 <= x0 or y1 <= y0:
        return None
    return aoi_arr[y0:y1, x0:x1].copy()

def load_bg_crop(date_str, orbit, cell_bbox_obj):
    """Load bg npy cache (full AOI) and crop to cell. Returns None if unavailable."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    ym = f"{dt.year:04d}-{dt.month:02d}"
    for fname in [f"bg_o{orbit}_{ym}_w2.npy", f"bg_o{orbit}_{ym}.npy"]:
        p = BG_CACHE / fname
        if p.exists():
            bg_full = np.load(p)
            cropped = crop_aoi_to_cell(bg_full, cell_bbox_obj)
            if cropped is not None:
                return cropped
    return None

def match_shape(src, target_shape):
    """Resize src to target_shape using bilinear interpolation."""
    h_t, w_t = target_shape
    h_s, w_s = src.shape[:2]
    if (h_s, w_s) == (h_t, w_t):
        return src
    return nd_zoom(src, (h_t/h_s, w_t/w_s), order=1)

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
def fetch_linear_crop(dt_str, bbox_obj):
    """Fetch linear VV image at cell bbox. Returns float32 2D array or None."""
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_LINEAR,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(dt_str[:19].replace("T"," "), dt_str[:19].replace("T"," ")),
            other_args={"processing": {"backCoeff": "SIGMA0_ELLIPSOID",
                                       "orthorectify": True,
                                       "demInstance": "COPERNICUS"}},
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        data = req.get_data()
        arr = data[0]
        if arr.ndim == 3: arr = arr[:,:,0]
        return arr.astype(np.float32)
    except Exception as e:
        print(f"  fetch failed: {e}")
        return None

# ── Load detection results ────────────────────────────────────────────────────
def load_results(csv_path):
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
            if val in ("true","1","yes","occupied"):   ref[(date, orbit, berth)] = True
            elif val in ("false","0","no","empty"):    ref[(date, orbit, berth)] = False
    return ref

# ── Blob detection ────────────────────────────────────────────────────────────
struct8 = ndimage.generate_binary_structure(2, 2)

def find_blobs(ratio_arr, corridor_mask, cell_bbox_obj):
    """
    Returns:
      labeled_all:   labeled array of all blobs above RATIO_THR in cell
      all_contours:  list of (row, col) arrays for each blob boundary
      largest_roi:   dict with keys area_px, length_m, r0, r1, c0, c1  (or None)
      max_ratio_roi: float, max ratio in corridor (or nan)
    """
    above_all = np.isfinite(ratio_arr) & (ratio_arr > RATIO_THR)
    labeled_all, _ = ndimage.label(above_all, structure=struct8)

    # contours: boundary pixels of each blob
    all_contours = []
    for bid in range(1, labeled_all.max()+1):
        mask = (labeled_all == bid)
        # erode and subtract to get border
        eroded = ndimage.binary_erosion(mask, structure=struct8)
        border = mask & ~eroded
        all_contours.append(border)

    # largest blob inside ROI
    above_roi = above_all & corridor_mask
    labeled_roi, _ = ndimage.label(above_roi, structure=struct8)
    max_ratio_roi = float(np.nanmax(ratio_arr[corridor_mask])) if corridor_mask.any() else float('nan')

    largest_roi = None
    if labeled_roi.max() > 0:
        h, w = ratio_arr.shape
        lat_mid = (cell_bbox_obj.min_y + cell_bbox_obj.max_y) / 2
        mpdlat = metres_per_deg_lat()
        mpdlon = metres_per_deg_lon(lat_mid)
        m_per_row = mpdlat * (cell_bbox_obj.max_y - cell_bbox_obj.min_y) / h
        m_per_col = mpdlon * (cell_bbox_obj.max_x - cell_bbox_obj.min_x) / w

        best_area = 0
        best_id = 0
        for bid in range(1, labeled_roi.max()+1):
            area = int((labeled_roi == bid).sum())
            if area > best_area:
                best_area = area
                best_id = bid

        if best_id > 0:
            mask = (labeled_roi == best_id)
            rows = np.where(mask.any(axis=1))[0]
            cols = np.where(mask.any(axis=0))[0]
            r0, r1 = int(rows[0]), int(rows[-1])
            c0, c1 = int(cols[0]), int(cols[-1])
            h_m = (r1 - r0) * m_per_row
            w_m = (c1 - c0) * m_per_col
            largest_roi = {
                "area_px": best_area,
                "length_m": max(h_m, w_m),
                "r0": r0, "r1": r1, "c0": c0, "c1": c1,
            }

    return labeled_all, all_contours, largest_roi, max_ratio_roi

# ── Draw panels ───────────────────────────────────────────────────────────────
def _hide_ticks(ax):
    ax.set_xticks([]); ax.set_yticks([])

def draw_scene_panel(ax, linear, corridor_poly, bbox_obj,
                     all_contours, largest_roi, det_occ, ref_occ,
                     max_ratio_roi, no_bg):
    """Top panel: SAR dB, ROI polygon, blob contours, largest bbox."""
    ax.set_facecolor("#111")
    _hide_ticks(ax)

    if linear is not None and np.isfinite(linear).any():
        db = np.where(linear > 0, 10*np.log10(linear), np.nan)
        vmin, vmax = np.nanpercentile(db[np.isfinite(db)], [2, 98])
        ax.imshow(db, cmap="gray", vmin=vmin, vmax=vmax,
                  interpolation="nearest", origin="upper")

        # ROI polygon (yellow)
        try:
            coords = list(corridor_poly.exterior.coords)
            xs = [lonlat_to_px(c[0], c[1], bbox_obj, linear.shape)[0] for c in coords]
            ys = [lonlat_to_px(c[0], c[1], bbox_obj, linear.shape)[1] for c in coords]
            ax.plot(xs, ys, color='yellow', linewidth=0.9, alpha=0.9, zorder=3)
        except Exception:
            pass

        # Red contours of all blobs above threshold
        for border in all_contours:
            if border.any():
                rows, cols = np.where(border)
                ax.scatter(cols, rows, s=0.3, c='red', marker='.', zorder=4, linewidths=0)

        # Cyan bbox of largest ROI blob
        if largest_roi is not None:
            r0, r1 = largest_roi["r0"], largest_roi["r1"]
            c0, c1 = largest_roi["c0"], largest_roi["c1"]
            ax.add_patch(mpatches.Rectangle(
                (c0-0.5, r0-0.5), c1-c0+1, r1-r0+1,
                linewidth=1.2, edgecolor='cyan', facecolor='none', zorder=5))
    else:
        ax.text(0.5, 0.5, "NO IMAGE", color="#888",
                ha="center", va="center", transform=ax.transAxes, fontsize=5)

    # Annotation
    if no_bg:
        label = "NO BG"; tc = "#aaa"
    elif det_occ is None:
        label = "NO DATA"; tc = "#aaa"
    else:
        d_s = "OCC" if det_occ else "EMP"
        r_s = "OCC" if ref_occ else ("EMP" if ref_occ is not None else "?")
        mr  = f"{max_ratio_roi:.0f}" if np.isfinite(max_ratio_roi) else "?"
        if largest_roi:
            a   = largest_roi["area_px"]
            ann_line2 = f"max={mr}dB  a={a}px(≥{MIN_AREA_PX})"
        else:
            ann_line2 = f"max={mr}dB  a=0px"
        label = f"d:{d_s} r:{r_s}\n{ann_line2}"; tc = "white"

    ax.text(0.02, 0.98, label, color=tc,
            ha="left", va="top", transform=ax.transAxes,
            fontsize=4.0, fontweight="bold",
            bbox=dict(boxstyle="square,pad=0.1", fc="black", alpha=0.6, lw=0))

    # Border
    if no_bg or det_occ is None or ref_occ is None:
        bc = "#666"
    elif det_occ == ref_occ:
        bc = "#2ecc71"
    else:
        bc = "#e74c3c"
    for spine in ax.spines.values():
        spine.set_edgecolor(bc); spine.set_linewidth(2.0)


def draw_bg_panel(ax, bg_linear):
    """Bottom-left: background image in dB."""
    ax.set_facecolor("#111")
    _hide_ticks(ax)
    if bg_linear is not None and np.isfinite(bg_linear).any():
        db = np.where(bg_linear > 0, 10*np.log10(bg_linear), np.nan)
        vmin, vmax = np.nanpercentile(db[np.isfinite(db)], [2, 98])
        ax.imshow(db, cmap="gray", vmin=vmin, vmax=vmax,
                  interpolation="nearest", origin="upper")
        ax.set_xlabel("BG", color="#aaa", fontsize=3.5, labelpad=1)
    else:
        ax.text(0.5, 0.5, "NO BG", color="#666",
                ha="center", va="center", transform=ax.transAxes, fontsize=4)


def draw_ratio_panel(ax, ratio_arr, corridor_mask):
    """Bottom-right: ratio dB map with threshold contour."""
    ax.set_facecolor("#111")
    _hide_ticks(ax)
    if ratio_arr is not None and np.isfinite(ratio_arr).any():
        # clip for display
        disp = np.clip(ratio_arr, -10, 30)
        disp = np.where(np.isfinite(ratio_arr), disp, np.nan)
        ax.imshow(disp, cmap="RdYlGn", vmin=-10, vmax=30,
                  interpolation="nearest", origin="upper", alpha=0.95)
        # threshold contour
        try:
            ax.contour(ratio_arr, levels=[RATIO_THR], colors=['white'],
                       linewidths=0.5, alpha=0.9)
        except Exception:
            pass
        # corridor outline
        if corridor_mask is not None:
            try:
                ax.contour(corridor_mask.astype(float), levels=[0.5],
                           colors=['yellow'], linewidths=0.5, alpha=0.7)
            except Exception:
                pass
        ax.set_xlabel("ratio dB", color="#aaa", fontsize=3.5, labelpad=1)
    else:
        ax.text(0.5, 0.5, "NO RATIO", color="#666",
                ha="center", va="center", transform=ax.transAxes, fontsize=4)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Load detection results
    det_csv  = ROOT / "diagnostics" / "berth_v410" / "results_table.csv"
    det_data = load_results(det_csv)

    # Load reference labels
    ref1_csv = ROOT / "diagnostics" / "berth_v4" / "results_table.csv"
    ref1     = load_reference(ref1_csv, {
        "sea_island": "occupied", "east_t_head": "occupied"})
    ref2_csv = ROOT / "diagnostics" / "berth_v3" / "summary.csv"
    ref2     = load_reference(ref2_csv, {
        "sea_island": "sea_island_occ", "east_t_head": "east_t_occ"})
    ref = {}
    for key in set(list(ref1.keys()) + list(ref2.keys())):
        ref[key] = ref1[key] if key in ref1 else ref2[key]

    # Cell bboxes per berth
    cell_bboxes = {}
    for berth, pts in BERTH_SPEC.items():
        clon, clat = berth_center(pts)
        cell_bboxes[berth] = cell_bbox(clon, clat)

    # Corridor polygons per berth
    corridor_polys = {}
    for berth, pts in BERTH_SPEC.items():
        corridor_polys[berth] = build_corridor_polygon(pts, CORRIDOR_HALF_M, CORRIDOR_END_M)

    n_rows = len(VALIDATION_DATES_ORBITS)
    n_cols = len(BERTHS)

    # Pre-fetch scene datetimes (catalog)
    aoi_full = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)
    dt_cache = {}
    print(f"Catalog search for {n_rows} date-orbits ...")
    for date_str, orbit in VALIDATION_DATES_ORBITS:
        key = (date_str, orbit)
        if key not in dt_cache:
            dt_cache[key] = scene_datetime(date_str, orbit, aoi_full)
            status = dt_cache[key][:19] if dt_cache[key] else "NOT FOUND"
            print(f"  {date_str} o{orbit}: {status}")

    # Figure layout:
    # outer GridSpec: n_rows × n_cols
    # each cell: GridSpecFromSubplotSpec 2×2 → top row spans 2 (scene), bottom: bg | ratio
    SCENE_H = 1.4   # scene panel height (inches)
    BOTTOM_H = 0.7  # bg/ratio panels height (inches)
    COL_W   = 1.4   # berth column width (inches)

    fig = plt.figure(figsize=(n_cols * COL_W,
                               n_rows * (SCENE_H + BOTTOM_H + 0.08)))
    fig.patch.set_facecolor("#1a1a1a")

    outer_gs = GridSpec(
        n_rows, n_cols, figure=fig,
        hspace=0.12, wspace=0.03,
        top=0.985, bottom=0.005, left=0.07, right=0.995
    )

    print(f"Rendering {n_rows} × {n_cols} mega-cells ...")

    for ri, (date_str, orbit) in enumerate(VALIDATION_DATES_ORBITS):
        dt_str = dt_cache.get((date_str, orbit), "")

        for ci, berth in enumerate(BERTHS):
            bbox_obj  = cell_bboxes[berth]
            corridor  = corridor_polys[berth]
            pts       = BERTH_SPEC[berth]

            inner = GridSpecFromSubplotSpec(
                2, 2,
                subplot_spec=outer_gs[ri, ci],
                hspace=0.03, wspace=0.02,
                height_ratios=[SCENE_H, BOTTOM_H],
            )
            ax_scene = fig.add_subplot(inner[0, 0:2])
            ax_bg    = fig.add_subplot(inner[1, 0])
            ax_ratio = fig.add_subplot(inner[1, 1])

            # Row / column labels
            if ci == 0:
                ax_scene.set_ylabel(
                    f"{date_str}\no{orbit}",
                    color="#bbb", fontsize=3.5, rotation=0,
                    labelpad=30, va="center")
            if ri == 0:
                ax_scene.set_title(
                    berth.replace("_"," "),
                    color="white", fontsize=5.5, pad=2, fontweight="bold")

            # Detection result
            det_key = (date_str, orbit, berth)
            det_row = det_data.get(det_key, {})
            no_bg_flag = str(det_row.get("no_background","")).strip() == "1"
            det_str    = str(det_row.get("occupied","")).strip().lower()
            if det_str in ("true","1"):    det_occ = True
            elif det_str in ("false","0"): det_occ = False
            else:                          det_occ = None
            ref_occ = ref.get(det_key)

            # Fetch scene
            linear = None
            if dt_str:
                linear = fetch_linear_crop(dt_str, bbox_obj)
                print(f"  {date_str} o{orbit} {berth}: "
                      f"{'fetched' if linear is not None else 'FAIL'} "
                      f"shape={linear.shape if linear is not None else '—'}")

            # Load bg
            bg = load_bg_crop(date_str, orbit, bbox_obj)
            if bg is not None and linear is not None:
                bg = match_shape(bg, linear.shape)

            # Compute ratio
            ratio = None
            if linear is not None and bg is not None:
                ratio = np.where(
                    (linear > 0) & (bg > 0),
                    10 * np.log10(linear / bg),
                    np.nan
                ).astype(np.float32)

            # Rasterize corridor mask
            img_shape = linear.shape if linear is not None else (100, 100)
            corridor_mask = rasterize_poly(corridor, bbox_obj, img_shape)

            # Find blobs
            all_contours = []; largest_roi = None; max_ratio_roi = float('nan')
            if ratio is not None:
                _, all_contours, largest_roi, max_ratio_roi = find_blobs(
                    ratio, corridor_mask, bbox_obj)

            # Draw
            draw_scene_panel(ax_scene, linear, corridor, bbox_obj,
                             all_contours, largest_roi, det_occ, ref_occ,
                             max_ratio_roi, no_bg_flag)
            draw_bg_panel(ax_bg, bg)
            draw_ratio_panel(ax_ratio, ratio, corridor_mask)

    # Supertitle
    fig.text(
        0.5, 0.998,
        f"Berth v410 — Contact sheet v2  |  thr={RATIO_THR} dB  |  "
        f"Yellow=ROI  Red=blobs  Cyan=largest ROI blob  |  "
        f"Green border=match  Red=mismatch",
        color="white", fontsize=5, ha="center", va="top")

    out_png = OUT_DIR / "contact_sheet.png"
    fig.savefig(out_png, dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"Saved: {out_png} ({out_png.stat().st_size//1024} KB)")

    # ── Legend PNG ────────────────────────────────────────────────────────────
    fig_l, ax_l = plt.subplots(figsize=(9, 5))
    fig_l.patch.set_facecolor("#1a1a1a")
    ax_l.set_facecolor("#1a1a1a"); ax_l.axis("off")

    lines = [
        "BERTH ROI PARAMETERS (berth_detect_v410.py)   —   contact sheet v2",
        "",
        f"RATIO_THR        = {RATIO_THR} dB   (10×log10(scene/background))",
        f"MIN_AREA_PX      = {MIN_AREA_PX} px  (minimum blob area in ROI)",
        f"CORRIDOR_HALF_M  = {CORRIDOR_HALF_M} m  (ROI half-width perpendicular to berth axis)",
        f"CORRIDOR_END_M   = {CORRIDOR_END_M} m   (ROI extension beyond berth endpoints)",
        "",
        "PANELS per cell:",
        "  top:          SAR scene (dB, gray)  +  yellow=ROI polygon  +  red=blob contours  +  cyan=largest ROI bbox",
        "  bottom-left:  background (20th-pct composite, dB, gray)",
        "  bottom-right: ratio map (scene/bg, dB, RdYlGn)  +  white contour = threshold  +  yellow contour = ROI",
        "",
        "ANNOTATION: d:det  r:ref  max_ratio dB  area_px(≥MIN_AREA_PX) of largest ROI blob",
        "BORDER: green=match  red=mismatch  grey=no reference or no background",
        "",
        "ROI CENTRELINES (lon, lat):",
    ]
    for berth, pts in BERTH_SPEC.items():
        exp = " [EXPERIMENTAL]" if berth in EXPERIMENTAL_BERTHS else ""
        lines.append(f"  {berth}{exp}:")
        for p in pts:
            lines.append(f"    ({p[0]:.5f}, {p[1]:.5f})")

    ax_l.text(0.03, 0.97, "\n".join(lines),
              color="white", ha="left", va="top",
              transform=ax_l.transAxes, fontsize=8.5, fontfamily="monospace")

    leg_png = OUT_DIR / "legend.png"
    fig_l.savefig(leg_png, dpi=120, bbox_inches="tight",
                  facecolor=fig_l.get_facecolor())
    plt.close(fig_l)
    print(f"Saved: {leg_png}")


if __name__ == "__main__":
    main()
