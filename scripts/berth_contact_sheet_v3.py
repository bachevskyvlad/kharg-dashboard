"""
berth_contact_sheet_v3.py — contact sheet v3

Changes from v2:
  - Background: per-orbit per-berth median of >=10 same-orbit scenes (cached)
  - Ship zone: water bands 20-80m from pier axis (pier axis +-20m excluded)
  - Ship blob: length along pier axis >= 150m (not area_px)
  - NO BG dates: marked as unknown, not counted
  - Visualization: water bands (orange) + match frame (green/red) + length annotation
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
    "s1iw_cdse_v3", service_url="https://sh.dataspace.copernicus.eu"
)

ROOT    = Path(__file__).parent.parent
OUT_DIR = ROOT / "diagnostics" / "berth_v4" / "contact_sheet_v3"
BG_DIR  = ROOT / "diagnostics" / "berth_v4" / "bg_v3"
OUT_DIR.mkdir(parents=True, exist_ok=True)
BG_DIR.mkdir(parents=True, exist_ok=True)

# ── Constants ─────────────────────────────────────────────────────────────────
BERTH_SPEC = {
    "sea_island":     [(50.28316, 29.23318), (50.28816, 29.22433)],
    "east_t_head":    [(50.33833, 29.23812), (50.34054, 29.22189)],
    "southeast_pier": [(50.33646, 29.21764), (50.33591, 29.21424), (50.33522, 29.21264)],
}

CORRIDOR_HALF_M = 150   # outer ROI half-width from axis (m)
CORRIDOR_END_M  = 100   # extend axis endpoints (m)
PIER_EXCL_M     = 20    # exclude +-Xm from axis (pier body)
WATER_INNER_M   = 20    # water band inner edge (m)
WATER_OUTER_M   = 80    # water band outer edge (m)
SHIP_MIN_LEN_M  = 150   # min ship length along axis for OCC (m)
RATIO_THR       = 10.0  # dB threshold
CELL_KM         = 1.0
RESOLUTION      = 10    # m/pixel
N_BG_SCENES     = 10    # target background scenes

BERTHS = ["sea_island", "east_t_head", "southeast_pier"]

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

# ── Evalscript: linear VV ─────────────────────────────────────────────────────
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

def cell_bbox_for(clon, clat, km=CELL_KM):
    half_lat = (km * 500) / metres_per_deg_lat()
    half_lon = (km * 500) / metres_per_deg_lon(clat)
    return BBox(bbox=[clon-half_lon, clat-half_lat,
                      clon+half_lon, clat+half_lat], crs=CRS.WGS84)

def _extend_line(pts, end_m):
    lat_mid = np.mean([p[1] for p in pts])
    mpdlat = metres_per_deg_lat()
    mpdlon = metres_per_deg_lon(lat_mid)
    def ext(a, b, d):
        dx = (b[0]-a[0])*mpdlon; dy = (b[1]-a[1])*mpdlat
        L = np.hypot(dx, dy)
        if L < 1e-6: return b
        return (b[0]+dx/L*d/mpdlon, b[1]+dy/L*d/mpdlat)
    p = list(pts)
    return [ext(p[1], p[0], end_m)] + p[1:-1] + [ext(p[-2], p[-1], end_m)]

def build_corridor_polygon(pts_lonlat, half_m, end_m):
    ext = _extend_line(pts_lonlat, end_m)
    line = LineString(ext)
    lat_mid = np.mean([p[1] for p in pts_lonlat])
    buf_deg = ((half_m / metres_per_deg_lat()) + (half_m / metres_per_deg_lon(lat_mid))) / 2
    return line.buffer(buf_deg)

def lonlat_to_px(lon, lat, bbox_obj, img_shape):
    h, w = img_shape
    px = (lon - bbox_obj.min_x) / (bbox_obj.max_x - bbox_obj.min_x) * w
    py = (bbox_obj.max_y - lat) / (bbox_obj.max_y - bbox_obj.min_y) * h
    return px, py

def rasterize_poly(poly, bbox_obj, img_shape):
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

def compute_signed_dist_field(px_x, px_y, axis_m_pts):
    """
    Signed perpendicular distance (m) from multi-segment polyline.
    Positive = right side of forward direction (first->last point).
    px_x, px_y: 2D arrays in local metres (east, north).
    """
    min_dist = np.full(px_x.shape, np.inf)
    sign = np.ones(px_x.shape)

    for i in range(len(axis_m_pts) - 1):
        x1, y1 = axis_m_pts[i]
        x2, y2 = axis_m_pts[i+1]
        dx = x2 - x1; dy = y2 - y1
        L = np.hypot(dx, dy)
        if L < 1e-6:
            continue
        ux = dx / L; uy = dy / L
        # Right normal (90 deg clockwise from forward): (uy, -ux)
        rx = uy; ry = -ux

        vx = px_x - x1; vy = px_y - y1
        t = np.clip((vx * ux + vy * uy) / L, 0.0, 1.0)
        cx = x1 + t * dx; cy = y1 + t * dy
        dpx = px_x - cx; dpy = px_y - cy
        dist = np.hypot(dpx, dpy)
        sgn = np.where(dpx * rx + dpy * ry >= 0, 1.0, -1.0)

        update = dist < min_dist
        min_dist = np.where(update, dist, min_dist)
        sign = np.where(update, sgn, sign)

    return min_dist * sign


def build_water_masks(berth_pts, bbox_obj, img_shape):
    """
    Returns dict:
      corridor_mask  — full corridor polygon
      pier_mask      — +-PIER_EXCL_M from axis AND inside corridor
      water_mask     — WATER_INNER_M..WATER_OUTER_M from axis (both sides) AND corridor
      axis_uvec_img  — (ux, uy) unit vector along axis in image (col, row) coords
      corridor_poly  — shapely polygon
    """
    h, w = img_shape
    lat_mid = (bbox_obj.min_y + bbox_obj.max_y) / 2
    mpdlat = metres_per_deg_lat()
    mpdlon = metres_per_deg_lon(lat_mid)
    clon, clat = berth_center(berth_pts)

    ext_pts = _extend_line(berth_pts, CORRIDOR_END_M)

    # Axis points in local metres (east=x, north=y)
    axis_m = [((lon - clon) * mpdlon, (lat - clat) * mpdlat) for lon, lat in ext_pts]

    # Pixel grid in local metres
    lons = np.linspace(
        bbox_obj.min_x + 0.5*(bbox_obj.max_x-bbox_obj.min_x)/w,
        bbox_obj.max_x - 0.5*(bbox_obj.max_x-bbox_obj.min_x)/w, w)
    lats = np.linspace(
        bbox_obj.max_y - 0.5*(bbox_obj.max_y-bbox_obj.min_y)/h,
        bbox_obj.min_y + 0.5*(bbox_obj.max_y-bbox_obj.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    px_x = (lon_g - clon) * mpdlon   # east metres
    px_y = (lat_g - clat) * mpdlat   # north metres

    dist_arr = compute_signed_dist_field(px_x, px_y, axis_m)

    corridor_poly = build_corridor_polygon(berth_pts, CORRIDOR_HALF_M, CORRIDOR_END_M)
    corridor_mask = rasterize_poly(corridor_poly, bbox_obj, img_shape)

    abs_dist = np.abs(dist_arr)
    pier_mask  = (abs_dist <  PIER_EXCL_M) & corridor_mask
    water_mask = (abs_dist >= WATER_INNER_M) & (abs_dist <= WATER_OUTER_M) & corridor_mask

    # Axis unit vector in image (col, row) pixel coords
    pc_start = lonlat_to_px(ext_pts[0][0],  ext_pts[0][1],  bbox_obj, img_shape)
    pc_end   = lonlat_to_px(ext_pts[-1][0], ext_pts[-1][1], bbox_obj, img_shape)
    dcol = pc_end[0] - pc_start[0]
    drow = pc_end[1] - pc_start[1]
    L_px = np.hypot(dcol, drow)
    ux_img = dcol / L_px if L_px > 1e-6 else 0.0
    uy_img = drow / L_px if L_px > 1e-6 else 1.0

    return {
        "corridor_mask": corridor_mask,
        "pier_mask":     pier_mask,
        "water_mask":    water_mask,
        "axis_uvec_img": (ux_img, uy_img),
        "corridor_poly": corridor_poly,
    }


# ── Fetch helpers ─────────────────────────────────────────────────────────────
def fetch_linear_at(dt_str, bbox_obj):
    """Fetch linear VV for exact datetime string at given bbox."""
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
        if arr.ndim == 3: arr = arr[:, :, 0]
        return arr.astype(np.float32)
    except Exception as e:
        print(f"    fetch failed ({dt_str[:10]}): {e}")
        return None


def scene_datetime(date_str, orbit, bbox):
    """Return exact ISO datetime of S1 scene for given date+orbit, or ''."""
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


# ── Background: median of N scenes, cached per orbit+berth ───────────────────
def get_or_compute_bg(orbit, berth, bbox_obj, n_scenes=N_BG_SCENES):
    cache_path = BG_DIR / f"bg_o{orbit}_{berth}.npy"
    if cache_path.exists():
        arr = np.load(cache_path)
        print(f"  BG cache hit: {cache_path.name} shape={arr.shape}")
        return arr

    print(f"  Computing BG orbit={orbit} berth={berth} ...")
    catalog = SentinelHubCatalog(config=config)
    results = list(catalog.search(
        S1_CDSE, bbox=bbox_obj,
        time=("2017-01-01T00:00:00Z", "2026-12-31T23:59:59Z"),
        fields={"include": ["id","properties.datetime","properties.sat:relative_orbit"]},
    ))

    orbit_dts = sorted(set(
        r["properties"]["datetime"][:19]
        for r in results
        if int(r.get("properties",{}).get("sat:relative_orbit", 0)) == orbit
        and r.get("properties",{}).get("datetime","")
    ))
    print(f"    Found {len(orbit_dts)} scenes for orbit {orbit}")

    if len(orbit_dts) < 3:
        print(f"    WARNING: too few scenes ({len(orbit_dts)}), no BG")
        return None

    # Pick n_scenes evenly spaced
    if len(orbit_dts) <= n_scenes:
        selected = orbit_dts
    else:
        idxs = np.linspace(0, len(orbit_dts) - 1, n_scenes, dtype=int)
        selected = [orbit_dts[i] for i in idxs]

    print(f"    Fetching {len(selected)} scenes ...")
    stack = []
    for dt_str in selected:
        arr = fetch_linear_at(dt_str, bbox_obj)
        if arr is not None and arr.max() > 0:
            stack.append(arr)
            print(f"      ok {dt_str[:10]}")
        else:
            print(f"      skip {dt_str[:10]}")

    if len(stack) < 3:
        print(f"    WARNING: only {len(stack)} valid scenes, no BG")
        return None

    ref_shape = stack[0].shape
    aligned = []
    for a in stack:
        if a.shape == ref_shape:
            aligned.append(a)
        else:
            aligned.append(nd_zoom(a, (ref_shape[0]/a.shape[0], ref_shape[1]/a.shape[1]), order=1))

    bg = np.median(np.stack(aligned, axis=0), axis=0).astype(np.float32)
    np.save(cache_path, bg)
    print(f"    Saved {cache_path.name} from {len(aligned)} scenes, shape={bg.shape}")
    return bg


# ── Blob detection in water bands ────────────────────────────────────────────
struct8 = ndimage.generate_binary_structure(2, 2)

def find_blobs_water(ratio_arr, water_mask, axis_uvec_img):
    """
    Find blobs within water_mask where ratio > RATIO_THR.
    Returns (blobs, longest) where each blob has:
      length_m, border (bool mask), bbox (r0,r1,c0,c1)
    """
    ux, uy = axis_uvec_img
    above = np.isfinite(ratio_arr) & (ratio_arr > RATIO_THR) & water_mask
    labeled, n = ndimage.label(above, structure=struct8)

    blobs = []
    for bid in range(1, n + 1):
        mask = (labeled == bid)
        rows, cols = np.where(mask)
        if len(rows) == 0:
            continue
        # Project onto axis unit vector (pixel coords)
        # ux^2+uy^2=1, pixels are RESOLUTION m each => length in metres
        proj = cols.astype(float) * ux + rows.astype(float) * uy
        length_m = float(proj.max() - proj.min()) * RESOLUTION

        eroded = ndimage.binary_erosion(mask, structure=struct8)
        border = mask & ~eroded

        blobs.append({
            "length_m": length_m,
            "border":   border,
            "bbox":     (int(rows.min()), int(rows.max()),
                         int(cols.min()), int(cols.max())),
        })

    blobs.sort(key=lambda b: b["length_m"], reverse=True)
    return blobs, (blobs[0] if blobs else None)


# ── Reference loader ──────────────────────────────────────────────────────────
def load_reference(csv_path, col_map):
    ref = {}
    if not csv_path.exists():
        print(f"WARNING: {csv_path} not found")
        return ref
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        date = row.get("date","").strip()
        try:
            orbit = int(float(row.get("orbit", row.get("rel_orbit", 0))))
        except Exception:
            continue
        for berth, col in col_map.items():
            val = str(row.get(col,"")).strip().lower()
            if val in ("true","1","yes","occupied"):
                ref[(date, orbit, berth)] = True
            elif val in ("false","0","no","empty"):
                ref[(date, orbit, berth)] = False
    return ref


# ── Drawing ───────────────────────────────────────────────────────────────────
def _hide_ticks(ax):
    ax.set_xticks([]); ax.set_yticks([])


def draw_scene_v3(ax, linear, geom, blobs, longest, det_occ, ref_occ, no_bg):
    ax.set_facecolor("#111")
    _hide_ticks(ax)
    bbox_obj      = geom["bbox_obj"]
    corridor_poly = geom["corridor_poly"]
    pier_mask     = geom["pier_mask"]
    water_mask    = geom["water_mask"]

    if linear is not None and np.isfinite(linear).any():
        db = np.where(linear > 0, 10*np.log10(linear), np.nan)
        vmin, vmax = np.nanpercentile(db[np.isfinite(db)], [2, 98])
        ax.imshow(db, cmap="gray", vmin=vmin, vmax=vmax,
                  interpolation="nearest", origin="upper")

        # Corridor outline (yellow)
        try:
            coords = list(corridor_poly.exterior.coords)
            xs = [lonlat_to_px(c[0], c[1], bbox_obj, linear.shape)[0] for c in coords]
            ys = [lonlat_to_px(c[0], c[1], bbox_obj, linear.shape)[1] for c in coords]
            ax.plot(xs, ys, color='yellow', lw=0.8, alpha=0.8, zorder=3)
        except Exception:
            pass

        # Water band outline (orange)
        try:
            ax.contour(water_mask.astype(float), levels=[0.5],
                       colors=['orange'], linewidths=0.9, alpha=0.95, zorder=4)
        except Exception:
            pass

        # Pier exclusion outline (gray dashed)
        try:
            ax.contour(pier_mask.astype(float), levels=[0.5],
                       colors=['#999'], linewidths=0.5, linestyles='dashed',
                       alpha=0.7, zorder=3)
        except Exception:
            pass

        # Blob contours (red) in water bands
        for b in blobs:
            border = b["border"]
            if border.any():
                rows, cols = np.where(border)
                ax.scatter(cols, rows, s=0.3, c='red', marker='.', zorder=5, linewidths=0)

        # Longest blob bbox (cyan)
        if longest is not None:
            r0, r1, c0, c1 = longest["bbox"]
            ax.add_patch(mpatches.Rectangle(
                (c0-0.5, r0-0.5), c1-c0+1, r1-r0+1,
                lw=1.2, edgecolor='cyan', facecolor='none', zorder=6))
    else:
        ax.text(0.5, 0.5, "NO IMAGE", color="#888",
                ha="center", va="center", transform=ax.transAxes, fontsize=5)

    # Annotation
    if no_bg:
        label = "NO BG → unknown"; tc = "#aaa"
    elif det_occ is None:
        label = "NO DATA"; tc = "#aaa"
    else:
        d_s = "OCC" if det_occ else "EMP"
        r_s = "OCC" if ref_occ else ("EMP" if ref_occ is not None else "?")
        len_str = f"{longest['length_m']:.0f}m" if longest else "0m"
        label = f"d:{d_s} r:{r_s}\nlen={len_str}(>={SHIP_MIN_LEN_M}m)"; tc = "white"

    ax.text(0.02, 0.98, label, color=tc,
            ha="left", va="top", transform=ax.transAxes,
            fontsize=4.0, fontweight="bold",
            bbox=dict(boxstyle="square,pad=0.1", fc="black", alpha=0.6, lw=0))

    # Border
    if no_bg or ref_occ is None:
        bc = "#555"
    elif det_occ == ref_occ:
        bc = "#2ecc71"
    else:
        bc = "#e74c3c"
    for spine in ax.spines.values():
        spine.set_edgecolor(bc); spine.set_linewidth(2.0)


def draw_bg_v3(ax, bg_linear):
    ax.set_facecolor("#111"); _hide_ticks(ax)
    if bg_linear is not None and np.isfinite(bg_linear).any():
        db = np.where(bg_linear > 0, 10*np.log10(bg_linear), np.nan)
        vmin, vmax = np.nanpercentile(db[np.isfinite(db)], [2, 98])
        ax.imshow(db, cmap="gray", vmin=vmin, vmax=vmax,
                  interpolation="nearest", origin="upper")
        ax.set_xlabel("BG (median)", color="#aaa", fontsize=3.5, labelpad=1)
    else:
        ax.text(0.5, 0.5, "NO BG", color="#666",
                ha="center", va="center", transform=ax.transAxes, fontsize=4)


def draw_ratio_v3(ax, ratio_arr, water_mask):
    ax.set_facecolor("#111"); _hide_ticks(ax)
    if ratio_arr is not None and np.isfinite(ratio_arr).any():
        disp = np.clip(np.where(np.isfinite(ratio_arr), ratio_arr, np.nan), -10, 30)
        ax.imshow(disp, cmap="RdYlGn", vmin=-10, vmax=30,
                  interpolation="nearest", origin="upper", alpha=0.95)
        try:
            ax.contour(ratio_arr, levels=[RATIO_THR], colors=['white'],
                       linewidths=0.5, alpha=0.9)
        except Exception:
            pass
        try:
            ax.contour(water_mask.astype(float), levels=[0.5],
                       colors=['orange'], linewidths=0.5, alpha=0.8)
        except Exception:
            pass
        ax.set_xlabel("ratio dB", color="#aaa", fontsize=3.5, labelpad=1)
    else:
        ax.text(0.5, 0.5, "NO RATIO", color="#666",
                ha="center", va="center", transform=ax.transAxes, fontsize=4)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Reference labels
    ref1_csv = ROOT / "diagnostics" / "berth_v4" / "results_table.csv"
    ref2_csv = ROOT / "diagnostics" / "berth_v3" / "summary.csv"
    ref1 = load_reference(ref1_csv, {"sea_island": "occupied", "east_t_head": "occupied"})
    ref2 = load_reference(ref2_csv, {"sea_island": "sea_island_occ", "east_t_head": "east_t_occ"})
    ref = {}
    for key in set(list(ref1.keys()) + list(ref2.keys())):
        ref[key] = ref1[key] if key in ref1 else ref2[key]

    # Cell bboxes
    cell_bboxes = {}
    for berth, pts in BERTH_SPEC.items():
        clon, clat = berth_center(pts)
        cell_bboxes[berth] = cell_bbox_for(clon, clat)

    # Compute / load backgrounds
    print("Computing/loading backgrounds ...")
    bg_cache = {}
    for berth in BERTHS:
        for orbit in (35, 101):
            bg_cache[(orbit, berth)] = get_or_compute_bg(orbit, berth, cell_bboxes[berth])

    # Catalog search for exact datetimes
    aoi_full = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)
    dt_cache = {}
    n_rows = len(VALIDATION_DATES_ORBITS)
    print(f"Catalog search for {n_rows} date-orbits ...")
    for date_str, orbit in VALIDATION_DATES_ORBITS:
        key = (date_str, orbit)
        if key not in dt_cache:
            dt_cache[key] = scene_datetime(date_str, orbit, aoi_full)
            status = dt_cache[key][:19] if dt_cache[key] else "NOT FOUND"
            print(f"  {date_str} o{orbit}: {status}")

    # Figure layout
    n_cols   = len(BERTHS)
    SCENE_H  = 1.4
    BOTTOM_H = 0.7
    COL_W    = 1.4

    fig = plt.figure(figsize=(n_cols * COL_W, n_rows * (SCENE_H + BOTTOM_H + 0.08)))
    fig.patch.set_facecolor("#1a1a1a")
    outer_gs = GridSpec(
        n_rows, n_cols, figure=fig,
        hspace=0.12, wspace=0.03,
        top=0.985, bottom=0.005, left=0.07, right=0.995,
    )

    print(f"Rendering {n_rows} x {n_cols} cells ...")

    for ri, (date_str, orbit) in enumerate(VALIDATION_DATES_ORBITS):
        dt_str = dt_cache.get((date_str, orbit), "")

        for ci, berth in enumerate(BERTHS):
            bbox_obj = cell_bboxes[berth]
            pts      = BERTH_SPEC[berth]

            inner = GridSpecFromSubplotSpec(
                2, 2,
                subplot_spec=outer_gs[ri, ci],
                hspace=0.03, wspace=0.02,
                height_ratios=[SCENE_H, BOTTOM_H],
            )
            ax_scene = fig.add_subplot(inner[0, 0:2])
            ax_bg    = fig.add_subplot(inner[1, 0])
            ax_ratio = fig.add_subplot(inner[1, 1])

            if ci == 0:
                ax_scene.set_ylabel(
                    f"{date_str}\no{orbit}",
                    color="#bbb", fontsize=3.5, rotation=0,
                    labelpad=30, va="center")
            if ri == 0:
                ax_scene.set_title(
                    berth.replace("_"," "),
                    color="white", fontsize=5.5, pad=2, fontweight="bold")

            ref_occ = ref.get((date_str, orbit, berth))

            # Fetch scene
            linear = None
            if dt_str:
                linear = fetch_linear_at(dt_str, bbox_obj)
                print(f"  {date_str} o{orbit} {berth}: "
                      f"{'ok' if linear is not None else 'FAIL'} "
                      f"shape={linear.shape if linear is not None else '—'}")

            # Background
            bg = bg_cache.get((orbit, berth))
            no_bg = bg is None
            if bg is not None and linear is not None and bg.shape != linear.shape:
                bg = nd_zoom(bg, (linear.shape[0]/bg.shape[0],
                                  linear.shape[1]/bg.shape[1]), order=1)

            # Ratio
            ratio = None
            if linear is not None and bg is not None:
                ratio = np.where(
                    (linear > 0) & (bg > 0),
                    10 * np.log10(linear / bg),
                    np.nan
                ).astype(np.float32)

            # Water masks for actual image shape
            img_shape = linear.shape if linear is not None else (99, 101)
            geom = build_water_masks(pts, bbox_obj, img_shape)
            geom["bbox_obj"] = bbox_obj

            # Blobs in water bands
            blobs, longest = [], None
            if ratio is not None:
                blobs, longest = find_blobs_water(
                    ratio, geom["water_mask"], geom["axis_uvec_img"])

            # Detection decision
            if no_bg:
                det_occ = None
            elif longest is not None and longest["length_m"] >= SHIP_MIN_LEN_M:
                det_occ = True
            else:
                det_occ = False

            draw_scene_v3(ax_scene, linear, geom, blobs, longest,
                          det_occ, ref_occ, no_bg)
            draw_bg_v3(ax_bg, bg)
            draw_ratio_v3(ax_ratio, ratio, geom["water_mask"])

    fig.text(
        0.5, 0.998,
        (f"Berth v3 — Contact sheet v3  |  thr={RATIO_THR} dB  min_len={SHIP_MIN_LEN_M}m  "
         f"water={WATER_INNER_M}-{WATER_OUTER_M}m from axis  "
         f"Yellow=corridor  Orange=water bands  Cyan=longest blob  "
         f"Green=match  Red=mismatch  Gray=unknown"),
        color="white", fontsize=4.5, ha="center", va="top")

    out_png = OUT_DIR / "contact_sheet_v3.png"
    fig.savefig(out_png, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"Saved: {out_png} ({out_png.stat().st_size // 1024} KB)")
    plt.close(fig)


if __name__ == "__main__":
    main()
