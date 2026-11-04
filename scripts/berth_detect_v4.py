"""
berth_detect_v4.py — ТЗ-7.4 rev2: Kharg berth detector.

Changes vs v3:
  a) Merge condition: gap < 80 m only (no total-length cap).
  b) Group ≥ 120 m = ship; groups < 120 m → lengths_small_m (not counted).
  c) n_ships per group by length: ≤420m→1, 420-780m→2, >780m→3.
  d) occupied unchanged: kernel blob ≥ 150 m (sea_island/east_t_head),
     ≥ 100 m (southeast_pier).

Output: diagnostics/berth_v4/
  results_table.csv (60 rows)
  <date>_<orbit>_<berth>.png  (60 files, 4-panel each)

Usage:
  python berth_detect_v4.py --dates 2021-06-25 2024-06-29
"""

import os, sys, json, csv, argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy import ndimage

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder,
)
from shapely.geometry import LineString
from shapely.ops import unary_union
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

AOI_BBOX   = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)
RESOLUTION = 10
SIZE       = bbox_to_dimensions(AOI_BBOX, resolution=RESOLUTION)

BERTH_SPEC = {
    "sea_island":     [(50.28316, 29.23318), (50.28816, 29.22433)],
    "east_t_head":    [(50.33833, 29.23812), (50.34054, 29.22189)],
    "southeast_pier": [(50.33646, 29.21764), (50.33591, 29.21424), (50.33522, 29.21264)],
}

CORRIDOR_HALF_M  = 150
CORRIDOR_END_M   = 100

RATIO_THR          = 10.0
MIN_AREA_PX        = 15
KERNEL_RATIO_THR   = 12.0   # dB — kernel threshold
MERGE_GAP_M        = 80.0
MIN_SHIP_GROUP_M   = 120.0   # groups < 120 m → lengths_small_m, not counted
OCCUPIED_LEN_M     = 150.0   # sea_island, east_t_head
OCCUPIED_LEN_SP_M  = 100.0   # southeast_pier
WATER_RING_INNER_M = 300
WATER_RING_OUTER_M = 800

BG_PCT          = 20
BG_WINDOW_DAYS  = 365 * 2
BG_MIN_DATES    = 10

ROOT     = Path(__file__).parent.parent
OUT_DIR  = ROOT / "diagnostics" / "berth_v4"
BG_CACHE = ROOT / "diagnostics" / "berth_v3" / "bg_cache"   # reuse v3 bg cache
PNG_DIR  = OUT_DIR

EVALSCRIPT_LINEAR = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  return [Math.max(s.VV, 1e-10)];
}
"""

CODE_VERSION = "v4"

# ═══════════════════════════════════════════════════════════════════════════════
# Coordinate helpers (identical to v3)
# ═══════════════════════════════════════════════════════════════════════════════

def px_to_lonlat(px, py):
    lon = AOI_BBOX.min_x + (px + 0.5) / SIZE[0] * (AOI_BBOX.max_x - AOI_BBOX.min_x)
    lat = AOI_BBOX.max_y - (py + 0.5) / SIZE[1] * (AOI_BBOX.max_y - AOI_BBOX.min_y)
    return lon, lat

def lonlat_to_px(lon, lat):
    px = (lon - AOI_BBOX.min_x) / (AOI_BBOX.max_x - AOI_BBOX.min_x) * SIZE[0]
    py = (AOI_BBOX.max_y - lat) / (AOI_BBOX.max_y - AOI_BBOX.min_y) * SIZE[1]
    return px, py

def metres_per_deg_lat():
    return 111320.0

def metres_per_deg_lon(lat):
    return 111320.0 * np.cos(np.deg2rad(lat))

def smooth3x3(arr):
    return ndimage.uniform_filter(arr.astype(np.float32), size=3)

def pca_dims(ys, xs):
    pts = np.column_stack([xs - xs.mean(), ys - ys.mean()])
    if len(pts) < 2:
        return 1.0, 1.0, 0.0
    cov = np.cov(pts.T)
    evals, evecs = np.linalg.eigh(cov)
    idx = np.argsort(evals)[::-1]
    evecs = evecs[:, idx]
    p0 = pts @ evecs[:, 0]
    length = float(p0.max() - p0.min()) + 1
    p1 = pts @ evecs[:, 1]
    width = float(p1.max() - p1.min()) + 1
    angle = float(np.degrees(np.arctan2(evecs[1, 0], evecs[0, 0]))) % 180
    return length, width, angle

def _occ_len(berth):
    return OCCUPIED_LEN_SP_M if berth == "southeast_pier" else OCCUPIED_LEN_M

# ═══════════════════════════════════════════════════════════════════════════════
# Corridor geometry (identical to v3)
# ═══════════════════════════════════════════════════════════════════════════════

def _extend_line(pts_lonlat, end_m):
    lat_mid = np.mean([p[1] for p in pts_lonlat])
    mpdlat = metres_per_deg_lat()
    mpdlon = metres_per_deg_lon(lat_mid)
    def extend_end(a, b, dist_m):
        dx = (b[0] - a[0]) * mpdlon
        dy = (b[1] - a[1]) * mpdlat
        length = np.hypot(dx, dy)
        if length < 1e-6:
            return b
        ux, uy = dx / length, dy / length
        return (b[0] + ux * dist_m / mpdlon, b[1] + uy * dist_m / mpdlat)
    pts = list(pts_lonlat)
    start_ext = extend_end(pts[1], pts[0], end_m)
    end_ext   = extend_end(pts[-2], pts[-1], end_m)
    return [start_ext] + pts[1:-1] + [end_ext]

def build_corridor_polygon(pts_lonlat, half_m, end_m):
    ext_pts = _extend_line(pts_lonlat, end_m)
    line = LineString(ext_pts)
    lat_mid = np.mean([p[1] for p in pts_lonlat])
    buf_deg = ((half_m / metres_per_deg_lat()) + (half_m / metres_per_deg_lon(lat_mid))) / 2
    return line.buffer(buf_deg), ext_pts

def rasterise_poly(poly):
    h, w = SIZE[1], SIZE[0]
    lons = np.linspace(AOI_BBOX.min_x + 0.5*(AOI_BBOX.max_x-AOI_BBOX.min_x)/w,
                       AOI_BBOX.max_x - 0.5*(AOI_BBOX.max_x-AOI_BBOX.min_x)/w, w)
    lats = np.linspace(AOI_BBOX.max_y - 0.5*(AOI_BBOX.max_y-AOI_BBOX.min_y)/h,
                       AOI_BBOX.min_y + 0.5*(AOI_BBOX.max_y-AOI_BBOX.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    try:
        return shapely.contains_xy(poly, lon_g.ravel(), lat_g.ravel()).reshape(h, w)
    except AttributeError:
        from shapely.vectorized import contains
        return contains(poly, lon_g, lat_g)

# ═══════════════════════════════════════════════════════════════════════════════
# Water-ring wind normalisation (identical to v3)
# ═══════════════════════════════════════════════════════════════════════════════

def build_water_ring_masks(corridor_polys):
    lat_mid = (AOI_BBOX.min_y + AOI_BBOX.max_y) / 2
    mpdlat = metres_per_deg_lat(); mpdlon = metres_per_deg_lon(lat_mid)
    buf_avg = lambda m: m / ((mpdlat + mpdlon) / 2)
    all_corridor_poly = unary_union([p for p, _ in corridor_polys.values()])
    ring_masks = {}
    for berth, (poly, _) in corridor_polys.items():
        outer = poly.buffer(buf_avg(WATER_RING_OUTER_M))
        inner = poly.buffer(buf_avg(WATER_RING_INNER_M))
        ring_masks[berth] = rasterise_poly(outer.difference(inner).difference(all_corridor_poly))
    return ring_masks

def wind_normalise(ratio_db, corridor_masks, water_ring_masks):
    norm = ratio_db.copy()
    for berth, cmask in corridor_masks.items():
        ring = water_ring_masks.get(berth)
        if ring is None or ring.sum() == 0:
            continue
        water_vals = ratio_db[ring]
        water_vals = water_vals[np.isfinite(water_vals)]
        if water_vals.size == 0:
            continue
        bias = float(np.median(water_vals))
        norm[cmask] = ratio_db[cmask] - bias
        print(f"  wind-norm [{berth}]: water-ring median = {bias:+.2f} dB")
    return norm

# ═══════════════════════════════════════════════════════════════════════════════
# SentinelHub fetch (identical to v3)
# ═══════════════════════════════════════════════════════════════════════════════

def catalog_search(date_from, date_to):
    catalog = SentinelHubCatalog(config=config)
    results = list(catalog.search(
        S1_CDSE, bbox=AOI_BBOX,
        time=(date_from + "T00:00:00Z", date_to + "T23:59:59Z"),
        fields={"include": ["id", "properties.datetime",
                            "properties.sat:relative_orbit",
                            "properties.sat:orbit_state",
                            "properties.platform"]},
    ))
    out = []
    for r in results:
        p = r.get("properties", {})
        out.append({
            "scene_id": r.get("id", ""),
            "datetime": p.get("datetime", ""),
            "rel_orbit": p.get("sat:relative_orbit"),
            "orbit_dir": p.get("sat:orbit_state", ""),
            "platform":  p.get("platform", ""),
        })
    return out

def fetch_linear(scene_datetime):
    try:
        dt = datetime.fromisoformat(scene_datetime.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.strptime(scene_datetime[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    t0 = (dt - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t1 = (dt + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_LINEAR,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(t0, t1),
            mosaicking_order=MosaickingOrder.MOST_RECENT,
            other_args={"dataFilter": {"polarization": "DV"}},
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=AOI_BBOX, size=SIZE, config=config,
    )
    try:
        data = req.get_data()
    except Exception as e:
        print(f"    fetch error: {e}")
        return None
    if not data or data[0] is None:
        return None
    raw = np.array(data[0], dtype=np.float32)
    arr = raw[:, :, 0] if raw.ndim == 3 else raw
    return np.where(arr < 1e-10, 1e-10, arr)

# ═══════════════════════════════════════════════════════════════════════════════
# Background (reuses v3 cache)
# ═══════════════════════════════════════════════════════════════════════════════

def bg_paths(date_str, orbit):
    ym = date_str[:7]
    return (BG_CACHE / f"bg_o{orbit}_{ym}.npy",
            BG_CACHE / f"bg_o{orbit}_{ym}_meta.json")

def load_or_build_bg(date_str, orbit):
    npy, meta_p = bg_paths(date_str, orbit)
    if npy.exists():
        print(f"    bg cache hit: {npy.name}")
        return np.load(npy)
    target = datetime.strptime(date_str, "%Y-%m-%d")
    d0 = (target - timedelta(days=BG_WINDOW_DAYS)).strftime("%Y-%m-%d")
    d1 = (target + timedelta(days=BG_WINDOW_DAYS)).strftime("%Y-%m-%d")
    scenes = catalog_search(d0, d1)
    orbit_sc = [s for s in scenes if s["rel_orbit"] == orbit]
    print(f"    bg build orbit {orbit}: {len(orbit_sc)} scenes in ±24 mo")
    if len(orbit_sc) < BG_MIN_DATES:
        print(f"    no_background: only {len(orbit_sc)} scenes")
        return None
    stack = []
    for s in orbit_sc:
        arr = fetch_linear(s["datetime"])
        if arr is not None:
            stack.append(arr)
    if len(stack) < BG_MIN_DATES:
        print(f"    no_background: fetched only {len(stack)}")
        return None
    bg = np.percentile(np.stack(stack, axis=0), BG_PCT, axis=0).astype(np.float32)
    BG_CACHE.mkdir(parents=True, exist_ok=True)
    np.save(npy, bg)
    with open(meta_p, "w") as f:
        json.dump({"orbit": orbit, "date": date_str, "n": len(stack),
                   "d0": d0, "d1": d1, "pct": BG_PCT}, f)
    print(f"    bg saved: {npy.name}  ({len(stack)} scenes)")
    return bg

# ═══════════════════════════════════════════════════════════════════════════════
# v4 Detection
# ═══════════════════════════════════════════════════════════════════════════════

def _berth_axis_px(berth):
    pts = BERTH_SPEC[berth]
    ox, oy = lonlat_to_px(*pts[0])
    ex, ey = lonlat_to_px(*pts[-1])
    dx, dy = ex - ox, ey - oy
    length = np.hypot(dx, dy)
    if length < 1:
        return None
    return (dx / length, dy / length, ox, oy)

def kernel_blobs(ratio_db, comp_union):
    """
    Find connected components of (ratio_db > KERNEL_RATIO_THR) inside comp_union.
    Used for occupied detection only (not for n_ships counting).
    """
    kernel_mask = comp_union & (ratio_db > KERNEL_RATIO_THR)
    labeled, n = ndimage.label(kernel_mask)
    blobs = []
    for i in range(1, n + 1):
        m = labeled == i
        ys_k, xs_k = np.where(m)
        if len(xs_k) < 2:
            continue
        length_px, _, _ = pca_dims(ys_k, xs_k)
        length_m = round(length_px * RESOLUTION, 1)
        blobs.append({
            "ys": ys_k, "xs": xs_k,
            "length_m": length_m,
            "max_ratio_dB": float(ratio_db[m].max()),
            "_ys_min": int(ys_k.min()), "_ys_max": int(ys_k.max()),
            "_xs_min": int(xs_k.min()), "_xs_max": int(xs_k.max()),
        })
    return blobs


def _n_ships_from_length(length_m):
    """Rev2: n_ships per merged group by PCA length."""
    if length_m <= 420:
        return 1
    elif length_m <= 780:
        return 2
    else:
        return 3


def detect_in_corridors_v4(ratio_db, corridor_masks, meta, date_str):
    """
    Rev2 detection:
      a) merge: gap < 80 m (no length cap)
      b) group < 120 m → lengths_small_m (not counted)
      c) n_ships per group: ≤420m→1, 420-780m→2, >780m→3
      d) occupied: kernel blob ≥ occ_threshold
    Returns list of per-berth result dicts.
    """
    results = []
    gap_px = MERGE_GAP_M / RESOLUTION

    for berth, cmask in corridor_masks.items():
        ratio_in = np.where(cmask, ratio_db, -999)
        blobs_bin = ratio_in > RATIO_THR
        labeled, num = ndimage.label(blobs_bin)

        axis_px = _berth_axis_px(berth)

        # Collect raw blobs above area threshold
        raw_blobs = []
        for i in range(1, num + 1):
            comp = (labeled == i)
            if comp.sum() < MIN_AREA_PX:
                continue
            ys_c, xs_c = np.where(comp)
            if axis_px is not None:
                ux, uy, ox, oy = axis_px
                projs = ux * (xs_c - ox) + uy * (ys_c - oy)
            else:
                projs = ys_c.astype(float)
            raw_blobs.append({
                '_comp': comp, '_ys': ys_c, '_xs': xs_c,
                '_proj_min': float(projs.min()), '_proj_max': float(projs.max()),
            })

        if not raw_blobs:
            results.append(_empty_berth_row(date_str, meta, berth))
            continue

        # Sort by projection; merge if gap < 80m (no length cap)
        raw_blobs.sort(key=lambda b: b['_proj_min'])
        groups = []
        cur = [raw_blobs[0]]
        cur_max = raw_blobs[0]['_proj_max']
        for blob in raw_blobs[1:]:
            gap = blob['_proj_min'] - cur_max
            if gap < gap_px:
                cur.append(blob)
                cur_max = max(cur_max, blob['_proj_max'])
            else:
                groups.append(cur)
                cur = [blob]
                cur_max = blob['_proj_max']
        groups.append(cur)

        # Per group: group-length → n_ships; kernel blobs → occupied
        occ_thr = _occ_len(berth)
        lengths_large = []   # groups ≥ 120 m
        lengths_small = []   # groups < 120 m
        n_ships_total = 0
        split_any = 0
        any_occupied = False
        all_comp_union = np.zeros(ratio_db.shape, dtype=bool)
        group_boxes = []  # for rendering: (ys_all, xs_all, group_len_m, n_ships_grp)

        for group in groups:
            n_parts = len(group)
            ys_all  = np.concatenate([b['_ys'] for b in group])
            xs_all  = np.concatenate([b['_xs'] for b in group])
            comp_union = np.zeros(ratio_db.shape, dtype=bool)
            for b in group:
                comp_union |= b['_comp']
            all_comp_union |= comp_union

            group_len_px, _, _ = pca_dims(ys_all, xs_all)
            group_len_m = round(group_len_px * RESOLUTION, 1)

            if n_parts > 1:
                print(f"    [{berth}] merged {n_parts} blobs → group {group_len_m:.0f}m")

            if group_len_m < MIN_SHIP_GROUP_M:
                lengths_small.append(group_len_m)
                continue

            # Count ships from length
            n_ships_grp = _n_ships_from_length(group_len_m)
            split = n_ships_grp > 1
            lengths_large.append(group_len_m)
            n_ships_total += n_ships_grp
            if split:
                split_any = 1

            group_boxes.append({
                "_ys_all": ys_all, "_xs_all": xs_all,
                "length_m": group_len_m,
                "n_ships": n_ships_grp,
                "split": split,
                "_ys_min": int(ys_all.min()), "_ys_max": int(ys_all.max()),
                "_xs_min": int(xs_all.min()), "_xs_max": int(xs_all.max()),
            })

            # Occupied: any kernel blob inside this group ≥ occ_threshold
            kblobs = kernel_blobs(ratio_db, comp_union)
            if any(kb["length_m"] >= occ_thr for kb in kblobs):
                any_occupied = True

        max_ratio = float(ratio_db[all_comp_union].max()) if all_comp_union.any() else 0.0

        results.append({
            "date":            date_str,
            "rel_orbit":       meta["rel_orbit"],
            "platform":        meta["platform"],
            "berth":           berth,
            "occupied":        any_occupied,
            "n_ships":         n_ships_total,
            "lengths_m":       sorted(lengths_large, reverse=True),
            "lengths_small_m": sorted(lengths_small, reverse=True),
            "n_parts":         sum(len(g) for g in groups),
            "max_ratio_dB":    round(max_ratio, 2),
            "split_by_length": split_any,
            "_comp_union":     all_comp_union,
            "_group_boxes":    group_boxes,
        })
        print(f"    [{berth}] n_ships={n_ships_total} occ={'YES' if any_occupied else 'no'} "
              f"lengths={sorted(lengths_large, reverse=True)} small={sorted(lengths_small, reverse=True)}")

    return results


def _empty_berth_row(date_str, meta, berth):
    return {
        "date": date_str, "rel_orbit": meta["rel_orbit"],
        "platform": meta["platform"], "berth": berth,
        "occupied": False, "n_ships": 0, "lengths_m": [], "lengths_small_m": [], "lengths_small_m": [],
        "n_parts": 0, "max_ratio_dB": 0.0, "split_by_length": 0,
        "_comp_union": None, "_group_boxes": [],
    }

# ═══════════════════════════════════════════════════════════════════════════════
# Azimuth ghost filter (identical to v3)
# ═══════════════════════════════════════════════════════════════════════════════
# (not needed for per-berth results, kept for safety — not applied in v4
#  since we work per-berth not per-candidate)

# ═══════════════════════════════════════════════════════════════════════════════
# Rendering — 4 panels, 60 PNGs
# ═══════════════════════════════════════════════════════════════════════════════

BERTH_COLORS = {
    "sea_island":     "#4DD9FF",
    "east_t_head":    "#FF5050",
    "southeast_pier": "#A0FF50",
}


def _crop_bounds(corridor_mask, pad_m=300):
    rows, cols = np.where(corridor_mask)
    if len(rows) == 0:
        return 0, SIZE[1], 0, SIZE[0]
    lat_mid = (AOI_BBOX.min_y + AOI_BBOX.max_y) / 2
    pad_px_lat = int(pad_m / metres_per_deg_lat()
                     / (AOI_BBOX.max_y - AOI_BBOX.min_y) * SIZE[1])
    pad_px_lon = int(pad_m / metres_per_deg_lon(lat_mid)
                     / (AOI_BBOX.max_x - AOI_BBOX.min_x) * SIZE[0])
    r0 = max(0, int(rows.min()) - pad_px_lat)
    r1 = min(SIZE[1], int(rows.max()) + pad_px_lat)
    c0 = max(0, int(cols.min()) - pad_px_lon)
    c1 = min(SIZE[0], int(cols.max()) + pad_px_lon)
    return r0, r1, c0, c1


def render_v4(scene_linear, bg_linear, ratio_db_norm,
              berth, corridor_mask, berth_results,
              date_str, orbit, out_dir):
    """
    4-panel PNG: Scene dB | Background dB | Ratio dB | Final ship mask.
    Panel 4: kernel pixels overlaid; each ship boxed and labelled with number + length.
    File name: <date>_<orbit>_<berth>.png
    """
    r0, r1, c0, c1 = _crop_bounds(corridor_mask, pad_m=300)
    if r1 <= r0 or c1 <= c0:
        return
    eps = 1e-10
    sc_db = 10 * np.log10(np.where(scene_linear[r0:r1, c0:c1] < eps, eps, scene_linear[r0:r1, c0:c1]))
    bg_db = 10 * np.log10(np.where(bg_linear[r0:r1, c0:c1]    < eps, eps, bg_linear[r0:r1, c0:c1]))
    rt_db = ratio_db_norm[r0:r1, c0:c1]
    cmask_crop = corridor_mask[r0:r1, c0:c1]

    # Build group-based ship boxes for panel 4
    ship_boxes  = []  # (x0,y0,x1,y1, label, length_m)
    ship_num_start = 0
    for res in berth_results:
        for gb in res.get("_group_boxes", []):
            ship_num_start += 1
            bx0 = max(0, gb["_xs_min"] - c0 - 3)
            bx1 = min(rt_db.shape[1], gb["_xs_max"] - c0 + 3)
            by0 = max(0, gb["_ys_min"] - r0 - 3)
            by1 = min(rt_db.shape[0], gb["_ys_max"] - r0 + 3)
            n = gb["n_ships"]
            if n > 1:
                label = f"{ship_num_start}×{n}"
            else:
                label = str(ship_num_start)
            ship_boxes.append((bx0, by0, bx1, by1, label, gb["length_m"]))

    # Also paint kernel pixels for visual reference on panel 4
    kernel_mask = np.zeros(rt_db.shape, dtype=bool)
    comp_union = None
    for res in berth_results:
        cu = res.get("_comp_union")
        if cu is not None:
            if comp_union is None:
                comp_union = cu
            else:
                comp_union |= cu
    if comp_union is not None:
        kernel_px = comp_union & (ratio_db_norm > KERNEL_RATIO_THR)
        ky, kx = np.where(kernel_px)
        ky2 = ky - r0; kx2 = kx - c0
        valid = (ky2 >= 0) & (ky2 < rt_db.shape[0]) & (kx2 >= 0) & (kx2 < rt_db.shape[1])
        if valid.any():
            kernel_mask[ky2[valid], kx2[valid]] = True

    occupied = any(r["occupied"] for r in berth_results)
    n_ships  = sum(r["n_ships"] for r in berth_results)

    fig, axes = plt.subplots(1, 4, figsize=(22, 6))
    panels = [
        (sc_db, "Scene dB",        "gray",   (-25, 5)),
        (bg_db, "Background dB",   "gray",   (-25, 5)),
        (rt_db, "Ratio dB (norm)", "RdBu_r", (-12, 12)),
        (rt_db, "Ship mask",       "RdBu_r", (-12, 12)),
    ]
    corridor_color = BERTH_COLORS.get(berth, "#ffffff")
    for idx, (ax, (data, title, cmap, vr)) in enumerate(zip(axes, panels)):
        im = ax.imshow(data, cmap=cmap, vmin=vr[0], vmax=vr[1], origin="upper", aspect="equal")
        plt.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
        outline = np.zeros_like(cmask_crop, dtype=float); outline[cmask_crop] = 1
        ax.contour(outline, levels=[0.5], colors=[corridor_color], linewidths=1.0)

        if idx == 3:
            # Kernel pixels overlay
            if kernel_mask.any():
                masked = np.ma.masked_where(~kernel_mask, np.ones(kernel_mask.shape))
                ax.imshow(masked, cmap="autumn", vmin=0, vmax=1,
                          origin="upper", aspect="equal", alpha=0.75)
            # Ship boxes with labels
            for (bx0, by0, bx1, by1, label, length_m) in ship_boxes:
                rect = mpatches.Rectangle((bx0, by0), bx1-bx0, by1-by0,
                                          edgecolor="#FF4444", facecolor="none", lw=1.5)
                ax.add_patch(rect)
                ax.text(bx0, by0 - 2, f"#{label} {length_m:.0f}m",
                        color="#FF4444", fontsize=6, va="bottom")
        else:
            # Boxes on panels 0-2 too
            for (bx0, by0, bx1, by1, label, length_m) in ship_boxes:
                rect = mpatches.Rectangle((bx0, by0), bx1-bx0, by1-by0,
                                          edgecolor="#FFE000", facecolor="none", lw=1.2)
                ax.add_patch(rect)
                ax.text(bx1 + 1, by0, f"#{label} {length_m:.0f}m",
                        color="#FFE000", fontsize=5.5, va="top")
        ax.set_title(title, fontsize=9)
        ax.axis("off")

    fig.suptitle(f"{berth}  {date_str}  orbit {orbit}  "
                 f"n_ships={n_ships}  {'OCCUPIED' if occupied else 'clear'}",
                 fontsize=11, y=0.99)
    plt.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = out_dir / f"{date_str}_{orbit}_{berth}.png"
    plt.savefig(fname, dpi=130, bbox_inches="tight")
    plt.close()
    print(f"  saved: {fname.name}")
    return fname

# ═══════════════════════════════════════════════════════════════════════════════
# CSV
# ═══════════════════════════════════════════════════════════════════════════════

RES_FIELDS = ["date", "rel_orbit", "platform", "berth",
              "occupied", "n_ships", "lengths_m", "lengths_small_m",
              "n_parts", "max_ratio_dB", "split_by_length", "run_id"]


def write_results_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RES_FIELDS)
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, "") for k in RES_FIELDS}
            row["lengths_m"] = str(row["lengths_m"])
            row["lengths_small_m"] = str(row["lengths_small_m"])
            w.writerow(row)
    print(f"results_table.csv: {path} ({len(rows)} rows)")

# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates",  nargs="+", metavar="YYYY-MM-DD", required=True)
    parser.add_argument("--orbits", nargs="+", type=int, default=[35, 101])
    args = parser.parse_args()

    run_id = os.environ.get("GITHUB_RUN_ID", "local")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build corridor geometry
    print("Building corridor geometry ...")
    corridor_polys = {}
    corridor_masks = {}
    for berth, pts in BERTH_SPEC.items():
        poly, ext_pts = build_corridor_polygon(pts, CORRIDOR_HALF_M, CORRIDOR_END_M)
        mask = rasterise_poly(poly)
        corridor_polys[berth] = (poly, ext_pts)
        corridor_masks[berth] = mask
        print(f"  {berth}: {mask.sum()} px")

    print("Building water-ring masks ...")
    water_ring_masks = build_water_ring_masks(corridor_polys)

    all_result_rows = []

    for date_str in args.dates:
        for orbit in args.orbits:
            print(f"\n[{date_str}  orbit={orbit}]")

            scenes = catalog_search(date_str, date_str)
            orbit_sc = [s for s in scenes if s["rel_orbit"] == orbit]
            if not orbit_sc:
                print(f"  no scene for orbit {orbit} on {date_str}")
                for berth in BERTH_SPEC:
                    all_result_rows.append({
                        "date": date_str, "rel_orbit": orbit,
                        "platform": "", "berth": berth,
                        "occupied": False, "n_ships": 0, "lengths_m": [], "lengths_small_m": [],
                        "n_parts": 0, "max_ratio_dB": 0.0, "split_by_length": 0,
                        "run_id": run_id,
                    })
                continue

            meta = orbit_sc[0]
            print(f"  scene_id={meta['scene_id']}")

            scene_linear = fetch_linear(meta["datetime"])
            if scene_linear is None:
                print("  fetch failed")
                for berth in BERTH_SPEC:
                    all_result_rows.append({
                        "date": date_str, "rel_orbit": orbit,
                        "platform": meta["platform"], "berth": berth,
                        "occupied": False, "n_ships": 0, "lengths_m": [], "lengths_small_m": [],
                        "n_parts": 0, "max_ratio_dB": 0.0, "split_by_length": 0,
                        "run_id": run_id,
                    })
                continue

            bg_linear = load_or_build_bg(date_str, orbit)
            if bg_linear is None:
                print("  no background")
                for berth in BERTH_SPEC:
                    all_result_rows.append({
                        "date": date_str, "rel_orbit": orbit,
                        "platform": meta["platform"], "berth": berth,
                        "occupied": False, "n_ships": 0, "lengths_m": [], "lengths_small_m": [],
                        "n_parts": 0, "max_ratio_dB": 0.0, "split_by_length": 0,
                        "run_id": run_id,
                    })
                continue

            sc_sm = smooth3x3(scene_linear)
            bg_sm = smooth3x3(bg_linear)
            eps = 1e-10
            ratio_db_full = np.clip(
                10 * np.log10(np.where(sc_sm < eps, eps, sc_sm) /
                              np.where(bg_sm < eps, eps, bg_sm)),
                -30, 30)
            ratio_db_norm = wind_normalise(ratio_db_full, corridor_masks, water_ring_masks)

            berth_results_all = detect_in_corridors_v4(ratio_db_norm, corridor_masks, meta, date_str)

            for berth in BERTH_SPEC:
                bd = [r for r in berth_results_all if r["berth"] == berth]
                if not bd:
                    bd = [_empty_berth_row(date_str, meta, berth)]

                # Merge multiple groups per berth into one result row
                n_ships      = sum(r["n_ships"] for r in bd)
                occupied     = any(r["occupied"] for r in bd)
                lengths_m    = sorted([l for r in bd for l in r["lengths_m"]], reverse=True)
                lengths_sm   = sorted([l for r in bd for l in r.get("lengths_small_m", [])], reverse=True)
                n_parts      = sum(r["n_parts"] for r in bd)
                max_ratio    = max((r["max_ratio_dB"] for r in bd), default=0.0)
                split_any    = int(any(r["split_by_length"] for r in bd))

                all_result_rows.append({
                    "date":            date_str,
                    "rel_orbit":       orbit,
                    "platform":        meta["platform"],
                    "berth":           berth,
                    "occupied":        occupied,
                    "n_ships":         n_ships,
                    "lengths_m":       lengths_m,
                    "lengths_small_m": lengths_sm,
                    "n_parts":         n_parts,
                    "max_ratio_dB":    round(max_ratio, 2),
                    "split_by_length": split_any,
                    "run_id":          run_id,
                })

                # Render PNG (bd already has _group_boxes / _comp_union)
                render_v4(scene_linear, bg_linear, ratio_db_norm,
                          berth, corridor_masks[berth], bd,
                          date_str, orbit, OUT_DIR)

    # Write results
    res_csv = OUT_DIR / "results_table.csv"
    write_results_csv(all_result_rows, res_csv)

    # Pre-delivery check
    png_count = len(list(OUT_DIR.glob("????-??-??_*_*.png")))
    print(f"\n=== PRE-DELIVERY CHECK ===")
    print(f"results_table rows : {len(all_result_rows)}")
    print(f"PNG files          : {png_count}")
    print(f"run_id             : {run_id}")
    occupied_count = sum(1 for r in all_result_rows if r["occupied"])
    print(f"occupied cells     : {occupied_count} / {len(all_result_rows)}")

    # Print table
    print(f"\n{'date':<12} {'orbit':>5} {'berth':<18} {'occ':>4} {'n_ships':>7} {'lengths_m':<35} {'small_m':<25} {'split':>5}")
    print("-" * 100)
    for r in all_result_rows:
        print(f"{r['date']:<12} {r['rel_orbit']:>5} {r['berth']:<18} "
              f"{'Y' if r['occupied'] else 'n':>4} {r['n_ships']:>7} "
              f"{str(r['lengths_m']):<35} {str(r.get('lengths_small_m',[])):<25} {r['split_by_length']:>5}")

    # Create labels_template.csv
    labels_path = OUT_DIR / "labels_template.csv"
    with open(labels_path, "w", newline="") as f:
        import csv as _csv
        w = _csv.DictWriter(f, fieldnames=["date", "rel_orbit", "berth", "png_file"])
        w.writeheader()
        for r in all_result_rows:
            png_name = f"{r['date']}_{r['rel_orbit']}_{r['berth']}.png"
            w.writerow({"date": r["date"], "rel_orbit": r["rel_orbit"],
                        "berth": r["berth"], "png_file": png_name})
    print(f"labels_template.csv: {labels_path}")

    # Create berth_v4_png.zip
    import zipfile
    zip_path = OUT_DIR / "berth_v4_png.zip"
    pngs = sorted(OUT_DIR.glob("????-??-??_*_*.png"))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in pngs:
            zf.write(p, p.name)
    print(f"berth_v4_png.zip: {zip_path} ({len(pngs)} files)")


if __name__ == "__main__":
    main()
