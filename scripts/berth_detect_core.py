""" 
berth_detect_core.py — canonical geometry and ship detection.

Shared by berth_ablation.py, berth_v4_history.py, berth_validate_optics_v4.py.
No SHConfig initialisation — only data types from sentinelhub + pure numpy/shapely.
"""

import numpy as np
from scipy import ndimage
from sentinelhub import BBox, CRS
from shapely.geometry import LineString
import shapely

RESOLUTION       = 10        # metres per pixel
CELL_KM          = 1.0
CORRIDOR_HALF_M  = 150
CORRIDOR_END_M   = 250
RATIO_THR        = 10.0
MERGE_PERP_TOL_M = 40.0
ASPECT_MAX       = 15.0  # strips/specular reflections, not hulls

struct8 = ndimage.generate_binary_structure(2, 2)

BERTH_COORDS = {
    "sea_island":     [(50.28316, 29.23318), (50.28816, 29.22433)],
    "east_t_head":    [(50.33833, 29.23812), (50.34054, 29.22189)],
    "southeast_pier": [(50.33646, 29.21764), (50.33591, 29.21424), (50.33522, 29.21264)],
}

# Single authoritative detection params — matches ablation bg-mode and v4_history V4_CONFIG.
# bg_dir is NOT here (script-specific path).
BERTH_DETECT_PARAMS = {
    "sea_island":     dict(pier=20, water_i=20, water_o=80, min_len=120, max_len=420, aspect=2.0, merge=120),
    "east_t_head":    dict(pier=20, water_i=20, water_o=80, min_len=150, max_len=420, aspect=2.5, merge=120),
    "southeast_pier": dict(pier=20, water_i=20, water_o=80, min_len=150, max_len=420, aspect=3.0, merge=0),
}

# Hulls below this length are detected (OCC/EMP) but not tracked as visits/loadings
VISIT_MIN_LEN_M = 200.0


def metres_per_deg_lat():
    return 111320.0

def metres_per_deg_lon(lat):
    return 111320.0 * np.cos(np.deg2rad(lat))

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

def rasterize_poly(poly, bbox_obj, img_shape):
    h, w = img_shape
    lons = np.linspace(bbox_obj.min_x + 0.5*(bbox_obj.max_x-bbox_obj.min_x)/w,
                       bbox_obj.max_x - 0.5*(bbox_obj.max_x-bbox_obj.min_x)/w, w)
    lats = np.linspace(bbox_obj.max_y - 0.5*(bbox_obj.max_y-bbox_obj.min_y)/h,
                       bbox_obj.min_y + 0.5*(bbox_obj.max_y-bbox_obj.min_y)/h, h)
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
        sgn = np.where(dpx*rx + dpy*ry >= 0, 1.0, -1.0)
        update = dist < min_dist
        min_dist = np.where(update, dist, min_dist)
        sign = np.where(update, sgn, sign)
    return min_dist * sign

def build_geom(pts, bbox_obj, img_shape, pier_excl_m, water_inner_m, water_outer_m):
    """Return water_mask, corridor_mask, abs_dist, axis_uvec for the berth corridor."""
    h, w = img_shape
    lat_mid = (bbox_obj.min_y + bbox_obj.max_y) / 2
    mpdlat  = metres_per_deg_lat()
    mpdlon  = metres_per_deg_lon(lat_mid)
    clon, clat = berth_center(pts)
    ext_pts = _extend_line(pts, CORRIDOR_END_M)
    axis_m = [((lon-clon)*mpdlon, (lat-clat)*mpdlat) for lon, lat in ext_pts]
    lons = np.linspace(bbox_obj.min_x + 0.5*(bbox_obj.max_x-bbox_obj.min_x)/w,
                       bbox_obj.max_x - 0.5*(bbox_obj.max_x-bbox_obj.min_x)/w, w)
    lats = np.linspace(bbox_obj.max_y - 0.5*(bbox_obj.max_y-bbox_obj.min_y)/h,
                       bbox_obj.min_y + 0.5*(bbox_obj.max_y-bbox_obj.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    px_x = (lon_g-clon)*mpdlon
    px_y = (lat_g-clat)*mpdlat
    dist_arr = compute_signed_dist_field(px_x, px_y, axis_m)
    corridor = LineString(ext_pts).buffer(
        ((CORRIDOR_HALF_M/mpdlat) + (CORRIDOR_HALF_M/mpdlon)) / 2)
    corridor_mask = rasterize_poly(corridor, bbox_obj, img_shape)
    abs_dist = np.abs(dist_arr)
    pc_s = ((ext_pts[0][0]-bbox_obj.min_x)/(bbox_obj.max_x-bbox_obj.min_x)*w,
            (bbox_obj.max_y-ext_pts[0][1])/(bbox_obj.max_y-bbox_obj.min_y)*h)
    pc_e = ((ext_pts[-1][0]-bbox_obj.min_x)/(bbox_obj.max_x-bbox_obj.min_x)*w,
            (bbox_obj.max_y-ext_pts[-1][1])/(bbox_obj.max_y-bbox_obj.min_y)*h)
    dcol = pc_e[0]-pc_s[0]; drow = pc_e[1]-pc_s[1]
    L_px = np.hypot(dcol, drow)
    ux = dcol/L_px if L_px > 1e-6 else 0.0
    uy = drow/L_px if L_px > 1e-6 else 1.0
    return {
        "water_mask":    (abs_dist >= water_inner_m) & (abs_dist <= water_outer_m) & corridor_mask,
        "pier_mask":     (abs_dist < pier_excl_m) & corridor_mask,
        "corridor_mask": corridor_mask,
        "abs_dist":      abs_dist,
        "axis_uvec":     (ux, uy),
    }

def find_blobs(ratio_arr, water_mask, axis_uvec, merge_gap_m, min_len_m,
               min_aspect=3.0, debug=False, return_all=False):
    """
    Canonical ship detection.  Returns (blobs, best_blob, detected: bool).
    - best_aspect per group = max over individual blob aspects (not merged bbox).
    - width per group       = median of individual blob widths.
    """
    ux, uy = axis_uvec
    px_u, py_u = -uy, ux
    above = np.isfinite(ratio_arr) & (ratio_arr > RATIO_THR) & water_mask
    labeled, n = ndimage.label(above, structure=struct8)
    if n == 0:
        return [], None, False
    raw = []
    for bid in range(1, n+1):
        mask = labeled == bid
        rows, cols = np.where(mask)
        if len(rows) == 0:
            continue
        fc = cols.astype(float); fr = rows.astype(float)
        axis_p = fc*ux + fr*uy
        perp_p = fc*px_u + fr*py_u
        a_min = float(axis_p.min()); a_max = float(axis_p.max())
        p_min = float(perp_p.min()); p_max = float(perp_p.max())
        len_m = (a_max - a_min) * RESOLUTION
        wid_m = max((p_max - p_min) * RESOLUTION, 10.0)  # floor=1px=10m
        raw.append({
            "axis_min": a_min, "axis_max": a_max,
            "perp_min": p_min, "perp_max": p_max,
            "perp_ctr": (p_min + p_max) / 2,
            "length_m": len_m, "width_m": wid_m,
            "aspect": len_m / wid_m,
            "area_px": int(mask.sum()),
        })
    raw.sort(key=lambda b: b["axis_min"])
    if debug:
        print(f"    raw blobs ({len(raw)}): " +
              ", ".join(f"len={b['length_m']:.0f}m asp={b['aspect']:.1f}" for b in raw))
    if merge_gap_m <= 0:
        groups = [[b] for b in raw]
    else:
        gap_px   = merge_gap_m / RESOLUTION
        perp_tol = MERGE_PERP_TOL_M / RESOLUTION
        groups = []; cur = [raw[0]]
        for b in raw[1:]:
            cur_max  = max(bb["axis_max"] for bb in cur)
            cur_perp = float(np.mean([bb["perp_ctr"] for bb in cur]))
            if (b["axis_min"] - cur_max <= gap_px and
                    abs(b["perp_ctr"] - cur_perp) <= perp_tol):
                cur.append(b)
            else:
                groups.append(cur); cur = [b]
        groups.append(cur)
    blobs = []
    for grp in groups:
        a_min = min(b["axis_min"] for b in grp)
        a_max = max(b["axis_max"] for b in grp)
        length_m    = (a_max - a_min) * RESOLUTION
        # median width (not union) to avoid noise inflation
        width_m     = max(float(np.median([b["width_m"] for b in grp])), 1.0)
        # aspect of the MERGED result — length/width of the unified bounding box.
        # Previously used max(sub_aspect) which leaked sub-blob ratios into merged
        # hulls, causing identical aspect values across hulls of different sizes.
        best_aspect = length_m / width_m
        area_px     = sum(b["area_px"] for b in grp)
        # area-weighted perp centre, and union perp extent, of merged group
        perp_ctr_m  = float(np.average([b["perp_ctr"] for b in grp],
                                        weights=[max(b["area_px"], 1) for b in grp]))
        perp_min_m  = min(b["perp_min"] for b in grp)
        perp_max_m  = max(b["perp_max"] for b in grp)
        blobs.append({
            "length_m": length_m, "width_m": width_m,
            "aspect": best_aspect, "area_px": area_px,
            "n_merged": len(grp),
            "axis_min": a_min, "axis_max": a_max,
            "perp_ctr": perp_ctr_m,
            "perp_min": perp_min_m,
            "perp_max": perp_max_m,
        })
        if debug and len(grp) > 1:
            print(f"    merged {len(grp)} blobs → len={length_m:.0f}m "
                  f"asp={best_aspect:.1f} wid={width_m:.0f}m")
    blobs = [b for b in blobs if b["aspect"] <= ASPECT_MAX]
    blobs.sort(key=lambda b: b["length_m"], reverse=True)
    best = blobs[0] if blobs else None
    detected = bool(best and best["length_m"] >= min_len_m and best["aspect"] >= min_aspect)
    return blobs, best, detected
