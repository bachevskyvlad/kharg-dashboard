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

# ── Config ────────────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID and CLIENT_SECRET env vars must be set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_v4", service_url="https://sh.dataspace.copernicus.eu"
)
S2_CDSE = DataCollection.SENTINEL2_L2A.define_from(
    "s2l2a_cdse_v4", service_url="https://sh.dataspace.copernicus.eu"
)

ROOT     = Path(__file__).parent.parent
BG_V3    = ROOT / "diagnostics" / "berth_v4" / "bg_v3"
BG_V4    = ROOT / "diagnostics" / "berth_v4" / "bg_v4"
OUT_DIR  = ROOT / "diagnostics" / "berth_v4" / "validation_optics_v4"
REF_CSV  = ROOT / "diagnostics" / "berth_v4" / "optics_ref_sameday.csv"
BG_V4.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Constants (v4) ────────────────────────────────────────────────────────────
BERTH_SPEC = {
    "sea_island":     [(50.28316, 29.23318), (50.28816, 29.22433)],
    "east_t_head":    [(50.33833, 29.23812), (50.34054, 29.22189)],
    "southeast_pier": [(50.33646, 29.21764), (50.33591, 29.21424), (50.33522, 29.21264)],
}
BERTHS          = ["sea_island", "east_t_head", "southeast_pier"]
CORRIDOR_HALF_M = 150
CORRIDOR_END_M  = 250
PIER_EXCL_M     = 12   # was 20
WATER_INNER_M   = 20   # kept at 20: 12 captured pier pixels → aspect ~1.9 < 3.0
WATER_OUTER_M   = 90   # was 80
SHIP_MIN_LEN_M  = 120  # was 150
SHIP_MIN_ASPECT = 3.0
BERTH_ASPECT = {
    "sea_island":     2.0,
    "east_t_head":    3.0,
    "southeast_pier": 3.0,
}
RATIO_THR       = 10.0
RESOLUTION      = 10
CELL_KM         = 1.0
GAP_MERGE_M     = 40   # new: merge blobs with axis gap <=40m

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

# ── Geometry ──────────────────────────────────────────────────────────────────
def metres_per_deg_lat(): return 111320.0
def metres_per_deg_lon(lat): return 111320.0 * np.cos(np.deg2rad(lat))

def berth_center(pts):
    return np.mean([p[0] for p in pts]), np.mean([p[1] for p in pts])

def cell_bbox_for(clon, clat, km=CELL_KM):
    half_lat = (km * 500) / metres_per_deg_lat()
    half_lon = (km * 500) / metres_per_deg_lon(clat)
    return BBox(bbox=[clon-half_lon, clat-half_lat,
                      clon+half_lon, clat+half_lat], crs=CRS.WGS84)

def _extend_line(pts, end_m):
    lat_mid = np.mean([p[1] for p in pts])
    mpdlat  = metres_per_deg_lat()
    mpdlon  = metres_per_deg_lon(lat_mid)
    def ext(a, b, d):
        dx = (b[0]-a[0])*mpdlon; dy = (b[1]-a[1])*mpdlat
        L = np.hypot(dx, dy)
        if L < 1e-6: return b
        return (b[0]+dx/L*d/mpdlon, b[1]+dy/L*d/mpdlat)
    p = list(pts)
    return [ext(p[1], p[0], end_m)] + p[1:-1] + [ext(p[-2], p[-1], end_m)]

def build_corridor_polygon(pts, half_m, end_m):
    ext = _extend_line(pts, end_m)
    line = LineString(ext)
    lat_mid = np.mean([p[1] for p in pts])
    buf_deg = ((half_m/metres_per_deg_lat()) + (half_m/metres_per_deg_lon(lat_mid))) / 2
    return line.buffer(buf_deg)

def lonlat_to_px(lon, lat, bbox_obj, img_shape):
    h, w = img_shape
    return ((lon-bbox_obj.min_x)/(bbox_obj.max_x-bbox_obj.min_x)*w,
            (bbox_obj.max_y-lat)/(bbox_obj.max_y-bbox_obj.min_y)*h)

def rasterize_poly(poly, bbox_obj, img_shape):
    h, w = img_shape
    lons = np.linspace(bbox_obj.min_x+0.5*(bbox_obj.max_x-bbox_obj.min_x)/w,
                       bbox_obj.max_x-0.5*(bbox_obj.max_x-bbox_obj.min_x)/w, w)
    lats = np.linspace(bbox_obj.max_y-0.5*(bbox_obj.max_y-bbox_obj.min_y)/h,
                       bbox_obj.min_y+0.5*(bbox_obj.max_y-bbox_obj.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    try:
        return shapely.contains_xy(poly, lon_g.ravel(), lat_g.ravel()).reshape(h, w)
    except AttributeError:
        from shapely.vectorized import contains
        return contains(poly, lon_g, lat_g)

def compute_signed_dist_field(px_x, px_y, axis_m_pts):
    min_dist = np.full(px_x.shape, np.inf)
    sign = np.ones(px_x.shape)
    for i in range(len(axis_m_pts) - 1):
        x1, y1 = axis_m_pts[i]; x2, y2 = axis_m_pts[i+1]
        dx = x2-x1; dy = y2-y1; L = np.hypot(dx, dy)
        if L < 1e-6: continue
        ux = dx/L; uy = dy/L; rx = uy; ry = -ux
        vx = px_x-x1; vy = px_y-y1
        t = np.clip((vx*ux+vy*uy)/L, 0, 1)
        cx = x1+t*dx; cy = y1+t*dy
        dpx = px_x-cx; dpy = px_y-cy
        dist = np.hypot(dpx, dpy)
        sgn = np.where(dpx*rx+dpy*ry >= 0, 1.0, -1.0)
        update = dist < min_dist
        min_dist = np.where(update, dist, min_dist)
        sign = np.where(update, sgn, sign)
    return min_dist * sign

def build_water_masks(berth_pts, bbox_obj, img_shape):
    h, w = img_shape
    lat_mid = (bbox_obj.min_y+bbox_obj.max_y)/2
    mpdlat = metres_per_deg_lat(); mpdlon = metres_per_deg_lon(lat_mid)
    clon, clat = berth_center(berth_pts)
    ext_pts = _extend_line(berth_pts, CORRIDOR_END_M)
    axis_m = [((lon-clon)*mpdlon, (lat-clat)*mpdlat) for lon, lat in ext_pts]
    lons = np.linspace(bbox_obj.min_x+0.5*(bbox_obj.max_x-bbox_obj.min_x)/w,
                       bbox_obj.max_x-0.5*(bbox_obj.max_x-bbox_obj.min_x)/w, w)
    lats = np.linspace(bbox_obj.max_y-0.5*(bbox_obj.max_y-bbox_obj.min_y)/h,
                       bbox_obj.min_y+0.5*(bbox_obj.max_y-bbox_obj.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    px_x = (lon_g-clon)*mpdlon; px_y = (lat_g-clat)*mpdlat
    dist_arr = compute_signed_dist_field(px_x, px_y, axis_m)
    corridor_poly = build_corridor_polygon(berth_pts, CORRIDOR_HALF_M, CORRIDOR_END_M)
    corridor_mask = rasterize_poly(corridor_poly, bbox_obj, img_shape)
    abs_dist = np.abs(dist_arr)
    pc_s = lonlat_to_px(ext_pts[0][0], ext_pts[0][1], bbox_obj, img_shape)
    pc_e = lonlat_to_px(ext_pts[-1][0], ext_pts[-1][1], bbox_obj, img_shape)
    dcol = pc_e[0]-pc_s[0]; drow = pc_e[1]-pc_s[1]
    L_px = np.hypot(dcol, drow)
    ux = dcol/L_px if L_px > 1e-6 else 0.0
    uy = drow/L_px if L_px > 1e-6 else 1.0
    return {
        "corridor_mask": corridor_mask,
        "pier_mask":     (abs_dist < PIER_EXCL_M) & corridor_mask,
        "water_mask":    (abs_dist >= WATER_INNER_M) & (abs_dist <= WATER_OUTER_M) & corridor_mask,
        "axis_uvec_img": (ux, uy),
        "corridor_poly": corridor_poly,
    }

struct8 = ndimage.generate_binary_structure(2, 2)

def find_blobs_water(ratio_arr, water_mask, axis_uvec_img):
    """Find blobs, merging axis gaps <=GAP_MERGE_M."""
    ux, uy = axis_uvec_img
    px_u, py_u = -uy, ux  # perpendicular unit vector

    above = np.isfinite(ratio_arr) & (ratio_arr > RATIO_THR) & water_mask
    labeled, n = ndimage.label(above, structure=struct8)
    if n == 0:
        return [], None

    raw_blobs = []
    for bid in range(1, n+1):
        mask = labeled == bid
        rows, cols = np.where(mask)
        if len(rows) == 0:
            continue
        fc = cols.astype(float); fr = rows.astype(float)
        axis_p = fc*ux + fr*uy
        perp_p = fc*px_u + fr*py_u
        eroded = ndimage.binary_erosion(mask, structure=struct8)
        raw_blobs.append({
            "axis_min": float(axis_p.min()), "axis_max": float(axis_p.max()),
            "perp_min": float(perp_p.min()), "perp_max": float(perp_p.max()),
            "row_min": int(rows.min()), "row_max": int(rows.max()),
            "col_min": int(cols.min()), "col_max": int(cols.max()),
            "area_px": int(mask.sum()),
            "border": mask & ~eroded,
        })

    if not raw_blobs:
        return [], None

    raw_blobs.sort(key=lambda b: b["axis_min"])

    # Merge blobs with axis gap <= GAP_MERGE_M / RESOLUTION pixels
    gap_px = GAP_MERGE_M / RESOLUTION
    groups = []
    cur = [raw_blobs[0]]
    for b in raw_blobs[1:]:
        cur_max = max(bb["axis_max"] for bb in cur)
        if b["axis_min"] - cur_max <= gap_px:
            cur.append(b)
        else:
            groups.append(cur)
            cur = [b]
    groups.append(cur)

    result_blobs = []
    for grp in groups:
        a_min = min(b["axis_min"] for b in grp)
        a_max = max(b["axis_max"] for b in grp)
        p_min = min(b["perp_min"] for b in grp)
        p_max = max(b["perp_max"] for b in grp)
        length_m = (a_max - a_min) * RESOLUTION
        width_m  = max((p_max - p_min) * RESOLUTION, 1.0)
        aspect   = length_m / width_m
        area_px  = sum(b["area_px"] for b in grp)
        r0 = min(b["row_min"] for b in grp)
        r1 = max(b["row_max"] for b in grp)
        c0 = min(b["col_min"] for b in grp)
        c1 = max(b["col_max"] for b in grp)
        h, w = ratio_arr.shape
        border = np.zeros((h, w), dtype=bool)
        for b in grp:
            border |= b["border"]
        result_blobs.append({
            "length_m": length_m, "width_m": width_m, "aspect": aspect,
            "area_px": area_px, "border": border,
            "bbox": (r0, r1, c0, c1),
        })

    result_blobs.sort(key=lambda b: b["length_m"], reverse=True)
    return result_blobs, result_blobs[0]

def detect(ratio_arr, water_mask, axis_uvec_img, min_aspect=SHIP_MIN_ASPECT):
    if ratio_arr is None:
        return None, None, None
    blobs, longest = find_blobs_water(ratio_arr, water_mask, axis_uvec_img)
    if longest and longest["length_m"] >= SHIP_MIN_LEN_M and longest["aspect"] >= min_aspect:
        return True, longest, blobs
    return False, longest, blobs

# ── Fetchers ──────────────────────────────────────────────────────────────────
def scene_datetime_s1(date_str, orbit, bbox):
    catalog = SentinelHubCatalog(config=config)
    try:
        results = list(catalog.search(
            S1_CDSE, bbox=bbox,
            time=(date_str+"T00:00:00Z", date_str+"T23:59:59Z"),
            fields={"include":["properties.datetime","properties.sat:relative_orbit"]},
        ))
    except Exception as e:
        print(f"  S1 catalog error ({date_str}): {e}"); return ""
    for r in results:
        p = r.get("properties", {})
        if int(p.get("sat:relative_orbit", 0)) == orbit:
            return p.get("datetime", "")
    return ""

def scene_datetime_s2(date_str, bbox):
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

def fetch_s1_linear(dt_str, bbox_obj):
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S1,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(dt_str[:19].replace("T"," "), dt_str[:19].replace("T"," ")),
            other_args={"processing":{"backCoeff":"SIGMA0_ELLIPSOID",
                                      "orthorectify":True,"demInstance":"COPERNICUS"}},
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        data = req.get_data(); arr = data[0]
        if arr.ndim == 3: arr = arr[:,:,0]
        return arr.astype(np.float32)
    except Exception as e:
        print(f"  S1 fetch failed ({dt_str[:10]}): {e}"); return None

def fetch_s2_rgb(dt_str, bbox_obj):
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
            if cache_path.exists():
                bg[(orbit, berth)] = np.load(cache_path)
                print(f"  BG v4 cached: {cache_path.name}")
                continue

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

            if len(emp_dates) >= 5:
                use_dates = emp_dates
                use_pct   = 50
                mode_str  = f"EMP-median ({len(emp_dates)} dates)"
            else:
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
                s1_dt = scene_datetime_s1(date_str, orbit, aoi_full)
                if not s1_dt:
                    print(f"    {date_str}: no S1"); continue
                arr = fetch_s1_linear(s1_dt, bbox_obj)
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
def accuracy_block(rows_b, year_filter=None, label=""):
    if year_filter:
        rows_b = [r for r in rows_b if int(r["date"][:4]) in year_filter]
    rows_b = [r for r in rows_b
              if r["optical_ref"] in ("OCC","EMP") and r["det"] in ("OCC","EMP")]
    n = len(rows_b)
    if n == 0:
        return [f"  {label}: no evaluable rows"], None
    tp = sum(1 for r in rows_b if r["optical_ref"]=="OCC" and r["det"]=="OCC")
    tn = sum(1 for r in rows_b if r["optical_ref"]=="EMP" and r["det"]=="EMP")
    fp = sum(1 for r in rows_b if r["optical_ref"]=="EMP" and r["det"]=="OCC")
    fn = sum(1 for r in rows_b if r["optical_ref"]=="OCC" and r["det"]=="EMP")
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
        s1_dt = scene_datetime_s1(date_str, orbit, aoi_full)
        print(f"  S1: {s1_dt[:19] if s1_dt else 'NOT FOUND'}")

        for berth in BERTHS:
            ref_col    = f"{berth}_optical_ref"
            optical_ref = row.get(ref_col, "").strip()
            bbox_obj   = cell_bboxes[berth]
            pts        = BERTH_SPEC[berth]

            linear = fetch_s1_linear(s1_dt, bbox_obj) if s1_dt else None

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
            geom = build_water_masks(pts, bbox_obj, img_shape)

            if no_bg:
                det_occ = None; longest = None; blobs = []
            else:
                det_occ, longest, blobs = detect(ratio, geom["water_mask"],
                                                  geom["axis_uvec_img"],
                                                  min_aspect=BERTH_ASPECT[berth])

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
                "no_bg": str(no_bg), "s1_found": str(bool(s1_dt)),
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
        f"PIER_EXCL={PIER_EXCL_M}m  WATER={WATER_INNER_M}-{WATER_OUTER_M}m  "
        f"MIN_LEN={SHIP_MIN_LEN_M}m  GAP_MERGE={GAP_MERGE_M}m",
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

    # Per-berth detail
    acc_lines += ["", "── Per-berth detail ─────────────────────────────────────"]
    for berth in BERTHS:
        rows_b = [r for r in results if r["berth"] == berth]
        acc_lines.append(f"\n{berth}")
        for label, yf in [("all years",    None),
                           ("2024-2025",    [2024, 2025]),
                           ("2026 only",    [2026])]:
            lines, _ = accuracy_block(rows_b, year_filter=yf, label=label)
            acc_lines.extend(lines)

    acc_txt = OUT_DIR / "accuracy.txt"
    with open(acc_txt, "w") as f:
        f.write("\n".join(acc_lines) + "\n")
    for line in acc_lines:
        print(line)

    # ── Discrepancy contact sheet ─────────────────────────────────────────────
    disc_rows = sorted(discrepancy_dates)
    print(f"\nDiscrepancies: {len(disc_rows)} date-orbits")
    if not disc_rows:
        print("No discrepancies — skipping PNG"); return

    disc_cells = {k: {} for k in disc_rows}
    for date_str, orbit in disc_rows:
        print(f"\nFetching discrepancy {date_str} o{orbit} ...")
        s1_dt = scene_datetime_s1(date_str, orbit, aoi_full)
        s2_dt = scene_datetime_s2(date_str, aoi_full)
        for berth in BERTHS:
            matching = [r for r in results
                        if r["date"]==date_str and r["orbit"]==orbit
                        and r["berth"]==berth and r["match"]=="False"]
            if not matching: continue
            bbox_obj = cell_bboxes[berth]
            pts      = BERTH_SPEC[berth]
            linear   = fetch_s1_linear(s1_dt, bbox_obj) if s1_dt else None
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
            geom = build_water_masks(pts, bbox_obj, img_shape)
            _, longest, blobs = detect(ratio, geom["water_mask"], geom["axis_uvec_img"],
                                       min_aspect=BERTH_ASPECT[berth])
            disc_cells[(date_str, orbit)][berth] = {
                "optical_ref": matching[0]["optical_ref"],
                "det": matching[0]["det"],
                "blobs": blobs, "longest": longest,
                "ratio": ratio, "linear": linear, "rgb": rgb,
                "water_mask": geom["water_mask"],
                "bbox_obj": bbox_obj,
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
                for b in cell["blobs"]:
                    rows2, cols2 = np.where(b["border"])
                    ax_ratio.scatter(cols2, rows2, s=0.3, c="red", marker=".", linewidths=0)
                if cell["longest"]:
                    r0,r1,c0,c1 = cell["longest"]["bbox"]
                    ax_ratio.add_patch(mpatches.Rectangle(
                        (c0-0.5, r0-0.5), c1-c0+1, r1-r0+1,
                        lw=1.0, edgecolor="cyan", facecolor="none", zorder=5))

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


if __name__ == "__main__":
    main()
