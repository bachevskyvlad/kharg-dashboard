"""
berth_detect_v3.py — Kharg Island tanker detector at three loading piers.

ISOLATION: research only. Does NOT touch ship_backfill.py, update_anchorage.yml,
           data/anchorage.json, or any Supabase table.
Output: diagnostics/berth_v3/

Algorithm:
  1. Three corridors (150 m buffer ± axis, +100 m at ends). Axis is first
     refined from the background image (line of stably-bright pixels); shift
     from spec geometry is printed in metres.
  2. Background: 20th-pct σ0 VV linear, per orbit, ±24 months, min 10 dates.
     Dates with <10 scenes → no_background flag, skipped silently.
  3. ratio_dB = 10·log10(scene / bg) on 10 m grid, inside corridors only.
     Candidate: connected blob ratio_dB > 6 dB, area ≥ 5 px.
  4. Azimuth ambiguity ghost filter (ТЗ-7.3 CC §2):
     S1 IW azimuth ambiguity spacing:
         Δaz = Va / PRF_az
       where Va  = effective satellite velocity ≈ 7116 m/s (S1A/B IW nominal),
             PRF_az = 1717 Hz (IW burst PRF, nominal)
       → Δaz ≈ 4143 m → 414 px at 10 m GRD spacing.
     Candidate at pixel (row, col) is flagged as ghost and discarded if:
       max(scene_dB[(row ± Δaz_px·[0.8, 1.2]), col-half_px:col+half_px])
         ≥ max_scene_dB(candidate) + GHOST_DB_DELTA (12 dB).
     The search range half-width in columns equals the candidate bounding-box
     half-width, minimum 5 px.
  5. Per candidate: date, scene_id, rel_orbit, platform, berth,
     lat, lon, length_m, width_m, orientation_deg, max_ratio_dB, area_px,
     ghost (bool — True = filtered out).
  6. Per date × berth: n_candidates, max_length_m, occupied (≥ 150 m non-ghost).
  7. Tripanel per date × berth (scene_dB | bg_dB | ratio_dB), corridor+300 m crop,
     candidates boxed with length_m labels.
  8. occupied_for_review.csv — one row per (date, berth): date, berth, occupied,
     n_candidates — ready for cell-by-cell comparison with K's manual annotation.
  9. GeoJSON corridors + PNG overlay per orbit.
 10. Summary table: summary.csv + per-berth occupancy.

Usage:
  python berth_detect_v3.py --dates 2021-06-09 2026-05-02
  python berth_detect_v3.py --dates 2021-06-09 --orbits 35
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
from shapely.geometry import Point, LineString, Polygon
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
    "s1iw_cdse_v3", service_url="https://sh.dataspace.copernicus.eu"
)

# ── AOI ───────────────────────────────────────────────────────────────────────
AOI_BBOX   = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)
RESOLUTION = 10
SIZE       = bbox_to_dimensions(AOI_BBOX, resolution=RESOLUTION)  # (W, H)

# ── Berth corridor spec (ТЗ coords are lat,lon → flip to lon,lat for shapely) ─
# Each entry: list of (lon, lat) waypoints
BERTH_SPEC = {
    "sea_island":     [(50.28316, 29.23318), (50.28816, 29.22433)],
    "east_t_head":    [(50.33833, 29.23812), (50.34054, 29.22189)],
    "southeast_pier": [(50.33646, 29.21764), (50.33591, 29.21424), (50.33522, 29.21264)],
}

CORRIDOR_HALF_M  = 150   # metres each side of axis
CORRIDOR_END_M   = 100   # extension beyond each end of axis

RATIO_THR            = 10.0  # dB  (rule 3: connected blob > 10 dB)
MIN_AREA_PX          = 15    #      (rule 3: area ≥ 15 px = 1500 m²)
OCCUPIED_LEN_M       = 150.0
OCCUPIED_MIN_RATIO   = 12.0  # dB  (rule 4: kernel must have max_ratio ≥ 12)
WATER_RING_INNER_M   = 300   # m   (rule 2: wind-norm ring inner radius)
WATER_RING_OUTER_M   = 800   # m   (rule 2: wind-norm ring outer radius)

# ── Azimuth ambiguity ghost filter (ТЗ-7.3 CC §2) ─────────────────────────────
# S1 IW nominal parameters (ESA Product Specification, Table 3-1):
#   PRF_az   = 1717.0 Hz   burst repetition frequency
#   Va       = 7116.0 m/s  effective platform velocity at target
# Ambiguity spacing: Δaz = Va / PRF_az ≈ 4143 m → 414 px @ 10 m GRD
# Tolerance band: [0.8 × Δaz_px, 1.2 × Δaz_px] in both azimuth directions.
GHOST_PRF_HZ     = 1717.0   # Hz
GHOST_VA_MS      = 7116.0   # m/s
GHOST_DB_DELTA   = 12.0     # dB; bright source must exceed candidate by this

BG_PCT           = 20
BG_WINDOW_DAYS   = 365 * 2  # ±24 months
BG_MIN_DATES     = 10

ORBIT_35         = 35
ORBIT_101        = 101

# ── Directories ───────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.parent
OUT_DIR   = ROOT / "diagnostics" / "berth_v3"
BG_CACHE  = OUT_DIR / "bg_cache"
CROP_DIR  = OUT_DIR / "tripanels"

# ── Evalscript ────────────────────────────────────────────────────────────────
EVALSCRIPT_LINEAR = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  return [Math.max(s.VV, 1e-10)];
}
"""

# ═══════════════════════════════════════════════════════════════════════════════
# Coordinate helpers
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

def haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000
    phi1, phi2 = np.deg2rad(lat1), np.deg2rad(lat2)
    dphi = np.deg2rad(lat2 - lat1)
    dlam = np.deg2rad(lon2 - lon1)
    a = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dlam/2)**2
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

def metres_to_deg(m):
    """Approximate degrees for a given distance at Kharg's latitude."""
    lat_mid = (AOI_BBOX.min_y + AOI_BBOX.max_y) / 2
    return m / metres_per_deg_lat(), m / metres_per_deg_lon(lat_mid)

# ── Rule 1: 3×3 boxcar smoothing ─────────────────────────────────────────────

def smooth3x3(arr: np.ndarray) -> np.ndarray:
    """Apply 3×3 mean (boxcar) filter to a 2-D linear-domain array."""
    return ndimage.uniform_filter(arr.astype(np.float32), size=3)


# ── Rule 2: water-ring wind normalisation ─────────────────────────────────────

def build_water_ring_masks(corridor_polys: dict) -> dict:
    """
    For each berth build a boolean mask of water pixels in the annulus
    [WATER_RING_INNER_M, WATER_RING_OUTER_M] around the corridor polygon.
    The ring excludes all corridor pixels (union of all corridor polygons).
    """
    lat_mid = (AOI_BBOX.min_y + AOI_BBOX.max_y) / 2
    mpdlat  = metres_per_deg_lat()
    mpdlon  = metres_per_deg_lon(lat_mid)
    buf_avg = lambda m: m / ((mpdlat + mpdlon) / 2)  # degrees

    all_corridor_poly = unary_union([p for p, _ in corridor_polys.values()])

    ring_masks = {}
    for berth, (poly, _) in corridor_polys.items():
        outer = poly.buffer(buf_avg(WATER_RING_OUTER_M))
        inner = poly.buffer(buf_avg(WATER_RING_INNER_M))
        ring  = outer.difference(inner).difference(all_corridor_poly)
        ring_masks[berth] = rasterise_poly(ring)
    return ring_masks


def wind_normalise(ratio_db: np.ndarray,
                   corridor_masks: dict,
                   water_ring_masks: dict) -> np.ndarray:
    """
    Rule 2: subtract the per-berth median ratio_dB over the water ring.
    Returns a full-image array where each corridor's pixels are shifted by
    its own water median (pixels outside any ring are shifted by 0).
    """
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
        print(f"  wind-norm [{berth}]: water-ring median = {bias:+.2f} dB → subtracted")
    return norm


# ═══════════════════════════════════════════════════════════════════════════════
# Corridor geometry
# ═══════════════════════════════════════════════════════════════════════════════

def _extend_line(pts_lonlat, end_m):
    """Extend a polyline end_m metres beyond both endpoints."""
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
    """
    Build shapely Polygon: half_m buffer around polyline, extended end_m at both ends.
    """
    ext_pts = _extend_line(pts_lonlat, end_m)
    line = LineString(ext_pts)
    # Buffer in degrees (approximate isotropic: use lat degrees)
    lat_mid = np.mean([p[1] for p in pts_lonlat])
    buf_deg_lat = half_m / metres_per_deg_lat()
    buf_deg_lon = half_m / metres_per_deg_lon(lat_mid)
    # Use average as approximation; anisotropy <5% at Kharg
    buf_deg = (buf_deg_lat + buf_deg_lon) / 2
    return line.buffer(buf_deg), ext_pts

# ── Rasterise a shapely polygon to a boolean mask (H, W) ─────────────────────

def rasterise_poly(poly) -> np.ndarray:
    h, w = SIZE[1], SIZE[0]
    lons = np.linspace(AOI_BBOX.min_x + 0.5*(AOI_BBOX.max_x-AOI_BBOX.min_x)/w,
                       AOI_BBOX.max_x - 0.5*(AOI_BBOX.max_x-AOI_BBOX.min_x)/w, w)
    lats = np.linspace(AOI_BBOX.max_y - 0.5*(AOI_BBOX.max_y-AOI_BBOX.min_y)/h,
                       AOI_BBOX.min_y + 0.5*(AOI_BBOX.max_y-AOI_BBOX.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    try:
        return shapely.contains_xy(poly, lon_g.ravel(), lat_g.ravel()).reshape(h, w)
    except AttributeError:
        # Shapely <2.0 fallback (slow)
        from shapely.vectorized import contains  # type: ignore
        return contains(poly, lon_g, lat_g)

# ── Axis refinement from background ──────────────────────────────────────────

def refine_axis(bg_linear: np.ndarray, corridor_mask: np.ndarray,
                spec_pts: list, berth_name: str) -> float:
    """
    Find the centroid of the stably-bright pixels (top-20th-pct σ0) inside
    the corridor. Project spec axis and refined centroid onto the perpendicular;
    return shift_m (positive = centroid is to the right of spec axis).
    Prints the shift.
    """
    db = 10 * np.log10(np.where(bg_linear < 1e-10, 1e-10, bg_linear))
    vals = db[corridor_mask]
    if len(vals) < 20:
        print(f"  [{berth_name}] axis refine: too few pixels ({len(vals)}), skip")
        return 0.0
    thr = np.percentile(vals, 80)
    bright = corridor_mask & (db >= thr)
    ys, xs = np.where(bright)
    if len(xs) == 0:
        print(f"  [{berth_name}] axis refine: no bright pixels, skip")
        return 0.0
    # Centroid in geo coords
    cx_px = float(xs.mean()); cy_px = float(ys.mean())
    clon, clat = px_to_lonlat(cx_px, cy_px)

    # Spec axis: project centroid onto axis
    lat_mid = np.mean([p[1] for p in spec_pts])
    mpdlat = metres_per_deg_lat(); mpdlon = metres_per_deg_lon(lat_mid)
    # Direction vector of spec axis (in metres)
    ax = (spec_pts[-1][0] - spec_pts[0][0]) * mpdlon
    ay = (spec_pts[-1][1] - spec_pts[0][1]) * mpdlat
    alen = np.hypot(ax, ay)
    if alen < 1:
        print(f"  [{berth_name}] axis refine: zero-length axis, skip")
        return 0.0
    ux, uy = ax / alen, ay / alen
    # Perpendicular component of centroid offset from spec start
    ox = (clon - spec_pts[0][0]) * mpdlon
    oy = (clat - spec_pts[0][1]) * mpdlat
    shift_m = float(-ux * oy + uy * ox)   # signed cross product → perp shift
    print(f"  [{berth_name}] axis refine: bright centroid ({clon:.5f},{clat:.5f}), "
          f"shift {shift_m:+.0f} m from spec axis")
    return shift_m

# ═══════════════════════════════════════════════════════════════════════════════
# SentinelHub helpers
# ═══════════════════════════════════════════════════════════════════════════════

def catalog_search(date_from: str, date_to: str) -> list:
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

def fetch_linear(scene_datetime: str) -> np.ndarray | None:
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
# Background
# ═══════════════════════════════════════════════════════════════════════════════

def bg_paths(date_str: str, orbit: int):
    ym = date_str[:7]
    return (BG_CACHE / f"bg_o{orbit}_{ym}.npy",
            BG_CACHE / f"bg_o{orbit}_{ym}_meta.json")

def load_or_build_bg(date_str: str, orbit: int) -> np.ndarray | None:
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
        print(f"    no_background: only {len(orbit_sc)} scenes (need {BG_MIN_DATES})")
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
# Detection
# ═══════════════════════════════════════════════════════════════════════════════

def pca_dims(ys, xs):
    pts = np.column_stack([xs - xs.mean(), ys - ys.mean()])
    if len(pts) < 2:
        return 1.0, 1.0, 0.0
    cov = np.cov(pts.T)
    evals, evecs = np.linalg.eigh(cov)
    idx = np.argsort(evals)[::-1]
    evecs = evecs[:, idx]
    p0 = pts @ evecs[:, 0]; p1 = pts @ evecs[:, 1]
    length = float(p0.max() - p0.min()) + 1
    width  = float(p1.max() - p1.min()) + 1
    angle  = float(np.degrees(np.arctan2(evecs[1, 0], evecs[0, 0]))) % 180
    return length, width, angle

def kernel_length_m(ratio_db: np.ndarray, comp: np.ndarray) -> float:
    """
    Rule 4: 'kernel' = pixels inside comp where ratio_db > OCCUPIED_MIN_RATIO.
    PCA major axis of those pixels → kernel_length in metres.
    Returns 0 if kernel is empty or too small for PCA.
    """
    kernel = comp & (ratio_db > OCCUPIED_MIN_RATIO)
    ys_k, xs_k = np.where(kernel)
    if len(xs_k) < 2:
        return 0.0
    length_px, _, _ = pca_dims(ys_k, xs_k)
    return round(length_px * RESOLUTION, 1)


def detect_in_corridors(ratio_db: np.ndarray,
                        corridor_masks: dict, meta: dict, date_str: str) -> list:
    """
    Rule 3: connected blob where ratio_db > RATIO_THR (10 dB), area ≥ MIN_AREA_PX (15 px).
    Rule 4: also computes kernel_length_m for each blob.
    ratio_db must be pre-smoothed and wind-normalised (rules 1 & 2 applied upstream).
    """
    detections = []
    for berth, cmask in corridor_masks.items():
        ratio_in = np.where(cmask, ratio_db, -999)
        blobs    = ratio_in > RATIO_THR
        labeled, num = ndimage.label(blobs)
        for i in range(1, num + 1):
            comp = (labeled == i)
            area_px = int(comp.sum())
            if area_px < MIN_AREA_PX:
                continue
            ys_c, xs_c = np.where(comp)
            cx_px = float(xs_c.mean()); cy_px = float(ys_c.mean())
            lon, lat = px_to_lonlat(cx_px, cy_px)
            length_px, width_px, orient = pca_dims(ys_c, xs_c)
            klen = kernel_length_m(ratio_db, comp)
            detections.append({
                "date":             date_str,
                "scene_id":         meta["scene_id"],
                "rel_orbit":        meta["rel_orbit"],
                "platform":         meta["platform"],
                "berth":            berth,
                "lat":              round(lat, 6),
                "lon":              round(lon, 6),
                "length_m":         round(length_px * RESOLUTION, 1),
                "width_m":          round(width_px  * RESOLUTION, 1),
                "orientation_deg":  round(orient, 1),
                "max_ratio_dB":     round(float(ratio_db[comp].max()), 2),
                "area_px":          area_px,
                "kernel_length_m":  klen,
                "_cx": int(round(cx_px)), "_cy": int(round(cy_px)),
                "_xs_min": int(xs_c.min()), "_xs_max": int(xs_c.max()),
                "_ys_min": int(ys_c.min()), "_ys_max": int(ys_c.max()),
            })
    return detections


# ═══════════════════════════════════════════════════════════════════════════════
# Azimuth ambiguity ghost filter (ТЗ-7.3 CC §2)
# ═══════════════════════════════════════════════════════════════════════════════

def filter_azimuth_ghosts(scene_linear: np.ndarray,
                          detections: list,
                          pixel_spacing_m: float = RESOLUTION) -> list:
    """
    Mark candidates whose scene_dB return is consistent with an azimuth ghost
    of a brighter target displaced by ±Δaz_px rows at the same range.

    Formula:
        Δaz = Va / PRF_az   (S1 IW nominal: 7116 / 1717 ≈ 4143 m)
        Δaz_px = Δaz / pixel_spacing_m          ≈ 414 px @ 10 m
        search band: row ± int(Δaz_px × [0.8, 1.2])

    A candidate is flagged ghost=True when any pixel in scene_dB within the
    search band and within the candidate's column half-width satisfies:
        scene_dB[search_row, col_window] ≥ max_scene_dB(candidate) + 12 dB.

    Returns the same list with 'ghost' field added (True/False).
    """
    eps = 1e-10
    scene_db = 10 * np.log10(np.where(scene_linear < eps, eps, scene_linear))
    H, W = scene_db.shape

    delta_az_m  = GHOST_VA_MS / GHOST_PRF_HZ          # ≈ 4143 m
    delta_az_px = delta_az_m / pixel_spacing_m         # ≈ 414 px
    band_lo = int(round(delta_az_px * 0.8))            # ≈ 331 px
    band_hi = int(round(delta_az_px * 1.2))            # ≈ 497 px

    for det in detections:
        cy   = det["_cy"]
        cx   = det["_cx"]
        xs0  = det["_xs_min"]
        xs1  = det["_xs_max"]
        # column window for the search (half-width of bounding box, min 5 px)
        col_half = max(5, (xs1 - xs0) // 2 + 2)
        c0 = max(0, cx - col_half)
        c1 = min(W, cx + col_half + 1)

        # max scene_dB inside the candidate bounding box
        ys0 = det["_ys_min"]; ys1 = det["_ys_max"]
        cand_max_db = float(scene_db[ys0:ys1+1, xs0:xs1+1].max())
        threshold   = cand_max_db + GHOST_DB_DELTA

        is_ghost = False
        for sign in (+1, -1):
            for offset in range(band_lo, band_hi + 1):
                row_check = cy + sign * offset
                if row_check < 0 or row_check >= H:
                    continue
                if float(scene_db[row_check, c0:c1].max()) >= threshold:
                    is_ghost = True
                    break
            if is_ghost:
                break

        det["ghost"] = is_ghost

    n_ghost = sum(1 for d in detections if d["ghost"])
    if n_ghost:
        print(f"    ghost filter: {n_ghost}/{len(detections)} candidates discarded "
              f"(Δaz={delta_az_m:.0f} m, band {band_lo}–{band_hi} px)")
    return detections


# ═══════════════════════════════════════════════════════════════════════════════
# Rendering
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

def render_tripanel(scene_linear, bg_linear, ratio_db_norm,
                    berth, corridor_mask, detections_berth,
                    date_str, orbit, out_dir):
    """
    4-panel figure: Scene dB | Background dB | Ratio dB (norm) | Candidate mask.
    Panel 4 shows ratio_db_norm pixels > OCCUPIED_MIN_RATIO (12 dB) in red
    over the ratio image — the 'kernel' pixels that drive the occupied decision.
    """
    r0, r1, c0, c1 = _crop_bounds(corridor_mask, pad_m=300)
    if r1 <= r0 or c1 <= c0:
        return None
    eps = 1e-10
    sc_db      = 10 * np.log10(np.where(scene_linear[r0:r1, c0:c1] < eps, eps,
                                        scene_linear[r0:r1, c0:c1]))
    bg_db      = 10 * np.log10(np.where(bg_linear[r0:r1, c0:c1]    < eps, eps,
                                        bg_linear[r0:r1, c0:c1]))
    rt_db      = ratio_db_norm[r0:r1, c0:c1]
    cmask_crop = corridor_mask[r0:r1, c0:c1]
    # Panel 4: candidate mask — pixels inside a blob AND ratio > 12 dB
    kernel_mask = np.zeros(rt_db.shape, dtype=bool)
    for det in detections_berth:
        bx0 = max(0, det["_xs_min"] - c0)
        bx1 = min(rt_db.shape[1], det["_xs_max"] - c0 + 1)
        by0 = max(0, det["_ys_min"] - r0)
        by1 = min(rt_db.shape[0], det["_ys_max"] - r0 + 1)
        kernel_mask[by0:by1, bx0:bx1] |= (rt_db[by0:by1, bx0:bx1] > OCCUPIED_MIN_RATIO)

    fig, axes = plt.subplots(1, 4, figsize=(22, 6))
    panels = [
        (sc_db,    "Scene dB",        "gray",   (-25, 5)),
        (bg_db,    "Background dB",   "gray",   (-25, 5)),
        (rt_db,    "Ratio dB (norm)", "RdBu_r", (-12, 12)),
        (rt_db,    "Kernel mask",     "RdBu_r", (-12, 12)),
    ]
    for idx, (ax, (data, title, cmap, vr)) in enumerate(zip(axes, panels)):
        im = ax.imshow(data, cmap=cmap, vmin=vr[0], vmax=vr[1],
                       origin="upper", aspect="equal")
        plt.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
        # Corridor outline
        outline = np.zeros_like(cmask_crop, dtype=float)
        outline[cmask_crop] = 1
        ax.contour(outline, levels=[0.5], colors=[BERTH_COLORS.get(berth, "#fff")],
                   linewidths=1.0)
        if idx == 3:
            # Overlay kernel pixels
            if kernel_mask.any():
                masked = np.ma.masked_where(~kernel_mask, np.ones(kernel_mask.shape))
                ax.imshow(masked, cmap="autumn", vmin=0, vmax=1,
                          origin="upper", aspect="equal", alpha=0.7)
        else:
            # Detection boxes on panels 0–2
            for det in detections_berth:
                x0 = det["_xs_min"] - c0 - 2; x1 = det["_xs_max"] - c0 + 2
                y0 = det["_ys_min"] - r0 - 2; y1 = det["_ys_max"] - r0 + 2
                occ_det = (det["max_ratio_dB"] >= OCCUPIED_MIN_RATIO and
                           det.get("kernel_length_m", 0) >= OCCUPIED_LEN_M)
                edge_col = "#FF4444" if occ_det else "#FFE000"
                rect = mpatches.Rectangle((x0, y0), x1-x0, y1-y0,
                                          edgecolor=edge_col, facecolor="none", lw=1.5)
                ax.add_patch(rect)
                ax.text(x1 + 2, y0,
                        f"{det['length_m']:.0f}m (k={det.get('kernel_length_m',0):.0f}m)",
                        color=edge_col, fontsize=5.5, va="top")
        ax.set_title(title, fontsize=9)
        ax.axis("off")

    occupied = any(d["max_ratio_dB"] >= OCCUPIED_MIN_RATIO and
                   d.get("kernel_length_m", 0) >= OCCUPIED_LEN_M
                   for d in detections_berth)
    n = len(detections_berth)
    fig.suptitle(f"{berth}  {date_str}  orbit {orbit}  "
                 f"({n} candidates, {'OCCUPIED' if occupied else 'clear'})",
                 fontsize=11, y=0.99)
    plt.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = out_dir / f"{date_str}_o{orbit}_{berth}_tripanel.png"
    plt.savefig(fname, dpi=130, bbox_inches="tight")
    plt.close()
    print(f"  saved: {fname.name}")
    return fname

EVALSCRIPT_S2_RGB = """
//VERSION=3
function setup() {
  return { input: ["B04","B03","B02"], output: { bands: 3, sampleType: "UINT8" } };
}
function evaluatePixel(s) {
  return [s.B04*3.5*255, s.B03*3.5*255, s.B02*3.5*255].map(v => Math.min(255,v));
}
"""

def render_southeast_optical(bg_linear_full: np.ndarray,
                              corridor_mask_sp: np.ndarray,
                              corridor_poly_sp,
                              out_dir: Path):
    """
    Rule 6: for southeast_pier, render two images:
      (a) Sentinel-2 true-color optical crop with corridor overlay
      (b) SAR background dB, same crop, same corridor overlay
    Uses a 600 m pad around the corridor. Sentinel-2 L2A, closest cloud-free
    scene within 60 days of 2024 (most recent full year with S1 coverage).
    """
    from sentinelhub import DataCollection as DC

    r0, r1, c0, c1 = _crop_bounds(corridor_mask_sp, pad_m=600)
    if r1 <= r0 or c1 <= c0:
        print("  [southeast_pier] optical: empty crop bounds, skip")
        return

    # Geographic bbox of the crop
    lon_min = AOI_BBOX.min_x + c0 / SIZE[0] * (AOI_BBOX.max_x - AOI_BBOX.min_x)
    lon_max = AOI_BBOX.min_x + c1 / SIZE[0] * (AOI_BBOX.max_x - AOI_BBOX.min_x)
    lat_max = AOI_BBOX.max_y - r0 / SIZE[1] * (AOI_BBOX.max_y - AOI_BBOX.min_y)
    lat_min = AOI_BBOX.max_y - r1 / SIZE[1] * (AOI_BBOX.max_y - AOI_BBOX.min_y)
    crop_bbox = BBox(bbox=[lon_min, lat_min, lon_max, lat_max], crs=CRS.WGS84)
    crop_size = bbox_to_dimensions(crop_bbox, resolution=10)

    # Fetch Sentinel-2 true color (pick a recent cloud-free date near 2024-06-29)
    S2_L2A = DataCollection.SENTINEL2_L2A.define_from(
        "s2l2a_cdse_sp",
        service_url="https://sh.dataspace.copernicus.eu"
    )
    rgb_img = None
    for time_win in [("2024-06-01", "2024-07-30"),
                     ("2024-04-01", "2024-09-30"),
                     ("2023-04-01", "2023-09-30")]:
        req = SentinelHubRequest(
            evalscript=EVALSCRIPT_S2_RGB,
            input_data=[SentinelHubRequest.input_data(
                data_collection=S2_L2A,
                time_interval=time_win,
                mosaicking_order=MosaickingOrder.LEAST_CC,
                other_args={"dataFilter": {"maxCloudCoverage": 10}},
            )],
            responses=[SentinelHubRequest.output_response("default", MimeType.PNG)],
            bbox=crop_bbox, size=crop_size, config=config,
        )
        try:
            data = req.get_data()
            if data and data[0] is not None:
                arr = np.array(data[0])
                if arr.ndim == 3 and arr.shape[2] >= 3 and arr.max() > 0:
                    rgb_img = arr[:, :, :3]
                    print(f"  [southeast_pier] S2 optical fetched: window {time_win}")
                    break
        except Exception as e:
            print(f"  [southeast_pier] S2 fetch attempt {time_win}: {e}")

    # Build corridor contour in crop coords
    cmask_crop = corridor_mask_sp[r0:r1, c0:c1]
    outline    = np.zeros_like(cmask_crop, dtype=float); outline[cmask_crop] = 1

    out_dir.mkdir(parents=True, exist_ok=True)

    # (b) SAR background — always available
    eps   = 1e-10
    bg_db = 10 * np.log10(np.where(bg_linear_full < eps, eps, bg_linear_full))
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.imshow(bg_db[r0:r1, c0:c1], cmap="gray", vmin=-25, vmax=5,
              origin="upper", aspect="equal")
    ax.contour(outline, levels=[0.5], colors=["#A0FF50"], linewidths=2.0)
    ax.set_title("southeast_pier — SAR background dB (600 m crop)\ncorridor overlay in green",
                 fontsize=9)
    ax.axis("off")
    fname_bg = out_dir / "southeast_pier_bg_crop.png"
    plt.savefig(fname_bg, dpi=160, bbox_inches="tight")
    plt.close()
    print(f"  saved: {fname_bg.name}")

    # (a) Optical — if S2 available
    if rgb_img is not None:
        fig, ax = plt.subplots(figsize=(8, 8))
        ax.imshow(rgb_img, origin="upper", aspect="equal")
        # Re-rasterise corridor in the smaller crop bbox
        cmask_opt = rasterise_poly(corridor_poly_sp)
        # Reuse corridor outline at crop resolution (same crop coords)
        ax.contour(outline, levels=[0.5], colors=["#FF5050"], linewidths=2.0)
        ax.set_title("southeast_pier — Sentinel-2 true color (10 m)\ncorridor overlay in red",
                     fontsize=9)
        ax.axis("off")
        fname_opt = out_dir / "southeast_pier_optical_crop.png"
        plt.savefig(fname_opt, dpi=160, bbox_inches="tight")
        plt.close()
        print(f"  saved: {fname_opt.name}")
    else:
        print("  [southeast_pier] optical: no cloud-free S2 found, skipping optical PNG")


def render_corridor_overlay(bg_linear, corridor_masks, corridor_polys,
                            orbit, out_dir):
    """PNG: background dB with corridor outlines for the given orbit."""
    eps = 1e-10
    bg_db = 10 * np.log10(np.where(bg_linear < eps, eps, bg_linear))
    fig, ax = plt.subplots(figsize=(8, 9))
    ax.imshow(bg_db, cmap="gray", vmin=-25, vmax=5, origin="upper", aspect="equal")
    for berth, cmask in corridor_masks.items():
        outline = np.zeros_like(cmask, dtype=float)
        outline[cmask] = 1
        ax.contour(outline, levels=[0.5], colors=[BERTH_COLORS.get(berth, "#fff")],
                   linewidths=2.0, linestyles="solid")
    ax.set_title(f"Kharg Island — corridors overlay, orbit {orbit}", fontsize=10)
    ax.axis("off")
    fname = out_dir / f"corridors_o{orbit}_overlay.png"
    plt.savefig(fname, dpi=130, bbox_inches="tight")
    plt.close()
    print(f"  saved: {fname.name}")
    return fname

# ── 5 ship + 5 empty crops with one-line reason ──────────────────────────────

def render_annotated_crops(scene_linear, bg_linear, ratio_db_full,
                           all_detections, corridor_masks, date_str, orbit, out_dir):
    """
    5 likely-ship crops and 5 likely-empty crops with text annotation.
    Ships: top-5 by max_ratio_dB among length_m >= 100 m candidates.
    Empty: berth centroids with no candidate >= 50 m.
    """
    eps = 1e-10
    sc_db = 10 * np.log10(np.where(scene_linear < eps, eps, scene_linear))
    bg_db = 10 * np.log10(np.where(bg_linear   < eps, eps, bg_linear))

    crops_dir = out_dir / "annotated"
    crops_dir.mkdir(parents=True, exist_ok=True)

    # Ship candidates ≥ 100 m
    ships = sorted(
        [d for d in all_detections if d["length_m"] >= 100],
        key=lambda d: d["max_ratio_dB"], reverse=True
    )[:5]

    for j, det in enumerate(ships, 1):
        cx, cy = det["_cx"], det["_cy"]
        half = 30  # 300 m in px
        r0 = max(0, cy - half); r1 = min(SIZE[1], cy + half)
        c0 = max(0, cx - half); c1 = min(SIZE[0], cx + half)
        fig, axes = plt.subplots(1, 2, figsize=(10, 5))
        axes[0].imshow(sc_db[r0:r1, c0:c1], cmap="gray", vmin=-25, vmax=5,
                       origin="upper", aspect="equal")
        axes[0].set_title("Scene dB", fontsize=8); axes[0].axis("off")
        axes[1].imshow(ratio_db_full[r0:r1, c0:c1], cmap="RdBu_r", vmin=-12, vmax=12,
                       origin="upper", aspect="equal")
        axes[1].set_title("Ratio dB", fontsize=8); axes[1].axis("off")
        reason = (f"ratio_dB={det['max_ratio_dB']:.1f}, length={det['length_m']:.0f}m "
                  f"in {det['berth']}")
        fig.suptitle(f"SHIP-CANDIDATE {j}: {reason}", fontsize=9)
        plt.tight_layout()
        plt.savefig(crops_dir / f"{date_str}_o{orbit}_ship_{j:02d}.png",
                    dpi=100, bbox_inches="tight")
        plt.close()

    # Empty berths: centroids with no long candidate
    busy_berths = {d["berth"] for d in all_detections if d["length_m"] >= 50}
    empty_count = 0
    for berth, cmask in corridor_masks.items():
        if berth in busy_berths or empty_count >= 5:
            continue
        rows, cols = np.where(cmask)
        if len(rows) == 0:
            continue
        cy = int(rows.mean()); cx = int(cols.mean())
        half = 30
        r0 = max(0, cy - half); r1 = min(SIZE[1], cy + half)
        c0 = max(0, cx - half); c1 = min(SIZE[0], cx + half)
        fig, axes = plt.subplots(1, 2, figsize=(10, 5))
        axes[0].imshow(sc_db[r0:r1, c0:c1], cmap="gray", vmin=-25, vmax=5,
                       origin="upper", aspect="equal")
        axes[0].set_title("Scene dB", fontsize=8); axes[0].axis("off")
        axes[1].imshow(ratio_db_full[r0:r1, c0:c1], cmap="RdBu_r", vmin=-12, vmax=12,
                       origin="upper", aspect="equal")
        axes[1].set_title("Ratio dB", fontsize=8); axes[1].axis("off")
        reason = f"no candidate ≥50 m at {berth} centroid"
        fig.suptitle(f"EMPTY-BERTH {empty_count+1}: {reason}", fontsize=9)
        plt.tight_layout()
        plt.savefig(crops_dir / f"{date_str}_o{orbit}_empty_{empty_count+1:02d}.png",
                    dpi=100, bbox_inches="tight")
        plt.close()
        empty_count += 1

# ═══════════════════════════════════════════════════════════════════════════════
# GeoJSON export
# ═══════════════════════════════════════════════════════════════════════════════

def save_corridors_geojson(corridor_polys: dict, corridor_ext_pts: dict, out_dir):
    features = []
    for berth, (poly, ext_pts) in corridor_polys.items():
        # Polygon exterior
        coords = list(poly.exterior.coords)
        features.append({
            "type": "Feature",
            "properties": {"berth": berth, "half_m": CORRIDOR_HALF_M,
                           "end_m": CORRIDOR_END_M},
            "geometry": {"type": "Polygon", "coordinates": [coords]}
        })
        # Refined axis line
        features.append({
            "type": "Feature",
            "properties": {"berth": berth, "type": "axis"},
            "geometry": {"type": "LineString", "coordinates": ext_pts}
        })
    fc = {"type": "FeatureCollection", "features": features}
    path = out_dir / "corridors.geojson"
    with open(path, "w") as f:
        json.dump(fc, f, indent=2)
    print(f"  saved: corridors.geojson")

# ═══════════════════════════════════════════════════════════════════════════════
# CSV / summary
# ═══════════════════════════════════════════════════════════════════════════════

CAND_FIELDS = ["date", "scene_id", "rel_orbit", "platform", "berth",
               "lat", "lon", "length_m", "width_m", "orientation_deg",
               "max_ratio_dB", "kernel_length_m", "area_px", "ghost"]

def append_candidates_csv(dets: list, path: Path):
    write_hdr = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAND_FIELDS, extrasaction="ignore")
        if write_hdr:
            w.writeheader()
        w.writerows(dets)

def append_occupied_review_csv(rows: list, path: Path):
    """
    occupied_for_review.csv — one row per (date, berth, rel_orbit).
    Columns: date, berth, rel_orbit, occupied, n_candidates
    Ready for cell-by-cell comparison with K's manual annotation.
    """
    fields = ["date", "berth", "rel_orbit", "occupied", "n_candidates"]
    write_hdr = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if write_hdr:
            w.writeheader()
        w.writerows(rows)


def append_summary_csv(rows: list, path: Path):
    fields = ["date", "rel_orbit", "no_background",
              "sea_island_n", "sea_island_occ", "sea_island_max_len",
              "east_t_head_n", "east_t_occ", "east_t_max_len",
              "southeast_pier_n", "southeast_occ", "southeast_max_len"]
    write_hdr = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if write_hdr:
            w.writeheader()
        w.writerows(rows)

# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates",  nargs="+", required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--orbits", nargs="+", type=int,
                        default=[ORBIT_35, ORBIT_101])
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BG_CACHE.mkdir(parents=True, exist_ok=True)
    CROP_DIR.mkdir(parents=True, exist_ok=True)

    # Build corridor polygons & raster masks
    print("Building corridor geometry ...")
    corridor_polys  = {}  # berth → (poly, ext_pts)
    corridor_masks  = {}  # berth → bool ndarray (H, W)
    for berth, pts in BERTH_SPEC.items():
        poly, ext_pts = build_corridor_polygon(pts, CORRIDOR_HALF_M, CORRIDOR_END_M)
        mask = rasterise_poly(poly)
        corridor_polys[berth] = (poly, ext_pts)
        corridor_masks[berth] = mask
        print(f"  {berth}: {mask.sum()} px in corridor")

    # Build water-ring masks for wind normalisation (once)
    print("Building water-ring masks for wind normalisation ...")
    water_ring_masks = build_water_ring_masks(corridor_polys)
    for berth, rm in water_ring_masks.items():
        print(f"  {berth}: {rm.sum()} px in water ring")

    save_corridors_geojson(corridor_polys, corridor_polys, OUT_DIR)

    # Track which orbits have already had overlay rendered
    overlay_done   = set()
    optical_done   = False   # Rule 6: southeast_pier optical rendered once
    cand_csv  = OUT_DIR / "candidates.csv"
    summ_csv  = OUT_DIR / "summary.csv"

    for date_str in args.dates:
        for orbit in args.orbits:
            print(f"\n[{date_str}  orbit={orbit}]")

            # Find scene
            scenes = catalog_search(date_str, date_str)
            orbit_sc = [s for s in scenes if s["rel_orbit"] == orbit]
            if not orbit_sc:
                print(f"  no scene for orbit {orbit}")
                append_summary_csv([{
                    "date": date_str, "rel_orbit": orbit, "no_background": "no_scene",
                    "sea_island_n": 0, "sea_island_occ": 0, "sea_island_max_len": 0,
                    "east_t_head_n": 0, "east_t_occ": 0, "east_t_max_len": 0,
                    "southeast_pier_n": 0, "southeast_occ": 0, "southeast_max_len": 0,
                }], summ_csv)
                append_occupied_review_csv([
                    {"date": date_str, "berth": b, "rel_orbit": orbit,
                     "occupied": "no_scene", "n_candidates": 0}
                    for b in BERTH_SPEC
                ], OUT_DIR / "occupied_for_review.csv")
                continue

            meta = orbit_sc[0]
            print(f"  scene_id={meta['scene_id']}  dir={meta['orbit_dir']}")

            # Fetch scene
            scene_linear = fetch_linear(meta["datetime"])
            if scene_linear is None:
                print("  fetch failed")
                continue

            # Background
            bg_linear = load_or_build_bg(date_str, orbit)
            if bg_linear is None:
                append_summary_csv([{
                    "date": date_str, "rel_orbit": orbit, "no_background": True,
                    "sea_island_n": 0, "sea_island_occ": 0, "sea_island_max_len": 0,
                    "east_t_head_n": 0, "east_t_occ": 0, "east_t_max_len": 0,
                    "southeast_pier_n": 0, "southeast_occ": 0, "southeast_max_len": 0,
                }], summ_csv)
                append_occupied_review_csv([
                    {"date": date_str, "berth": b, "rel_orbit": orbit,
                     "occupied": "no_bg", "n_candidates": 0}
                    for b in BERTH_SPEC
                ], OUT_DIR / "occupied_for_review.csv")
                continue

            # Axis refinement (once per bg build — use first date that triggers build)
            for berth, cmask in corridor_masks.items():
                refine_axis(bg_linear, cmask, BERTH_SPEC[berth], berth)

            # Corridor overlay (once per orbit)
            if orbit not in overlay_done:
                render_corridor_overlay(bg_linear, corridor_masks, corridor_polys,
                                        orbit, OUT_DIR)
                overlay_done.add(orbit)

            # Rule 6: southeast_pier optical crop (once per run)
            if not optical_done:
                sp_poly, _ = corridor_polys["southeast_pier"]
                render_southeast_optical(bg_linear,
                                         corridor_masks["southeast_pier"],
                                         sp_poly, OUT_DIR)
                optical_done = True

            # Rule 1: 3×3 boxcar smoothing before ratio
            sc_sm = smooth3x3(scene_linear)
            bg_sm = smooth3x3(bg_linear)

            # Ratio from smoothed arrays
            eps = 1e-10
            ratio_db_full = np.clip(
                10 * np.log10(np.where(sc_sm < eps, eps, sc_sm) /
                              np.where(bg_sm < eps, eps, bg_sm)),
                -30, 30)

            # Rule 2: wind normalisation — subtract water-ring median per berth
            ratio_db_norm = wind_normalise(ratio_db_full, corridor_masks, water_ring_masks)

            # Detect (rules 3 & 4)
            dets = detect_in_corridors(ratio_db_norm, corridor_masks, meta, date_str)
            print(f"  total candidates before ghost filter: {len(dets)}")

            # Azimuth ghost filter (ТЗ-7.3 CC §2)
            dets = filter_azimuth_ghosts(scene_linear, dets)
            real_dets = [d for d in dets if not d.get("ghost")]
            print(f"  total candidates after ghost filter:  {len(real_dets)}")

            # Per-berth stats — Rule 4: occupied = max_ratio_dB≥12 AND kernel_len≥150m
            review_csv  = OUT_DIR / "occupied_for_review.csv"
            sumrow = {"date": date_str, "rel_orbit": orbit, "no_background": False}
            review_rows = []
            for berth in BERTH_SPEC:
                bd   = [d for d in real_dets if d["berth"] == berth]
                occ  = int(any(d["max_ratio_dB"] >= OCCUPIED_MIN_RATIO and
                               d.get("kernel_length_m", 0) >= OCCUPIED_LEN_M
                               for d in bd))
                maxl = max((d["length_m"] for d in bd), default=0)
                sumrow[f"{berth}_n"]       = len(bd)
                sumrow[f"{berth}_occ"]     = occ
                sumrow[f"{berth}_max_len"] = maxl
                review_rows.append({
                    "date": date_str, "berth": berth, "rel_orbit": orbit,
                    "occupied": occ, "n_candidates": len(bd),
                })
                print(f"    {berth}: {len(bd)} candidates, max_len={maxl:.0f}m, "
                      f"{'OCCUPIED' if occ else 'clear'}")
            append_occupied_review_csv(review_rows, review_csv)

            # Tripanels — 4 panels now, using normalised ratio
            for berth, cmask in corridor_masks.items():
                bd = [d for d in real_dets if d["berth"] == berth]
                render_tripanel(scene_linear, bg_linear, ratio_db_norm,
                                berth, cmask, bd, date_str, orbit, CROP_DIR)

            # Annotated crops (non-ghost)
            render_annotated_crops(scene_linear, bg_linear, ratio_db_full,
                                   real_dets, corridor_masks, date_str, orbit, OUT_DIR)

            # Save all dets (ghost flag included) + summary
            if dets:
                append_candidates_csv(dets, cand_csv)
            append_summary_csv([sumrow], summ_csv)

    # Print final count tables
    review_csv = OUT_DIR / "occupied_for_review.csv"
    print("\n=== OCCUPIED FOR REVIEW ===")
    if review_csv.exists():
        with open(review_csv) as f:
            print(f.read())

    print("\n=== SUMMARY ===")
    if summ_csv.exists():
        with open(summ_csv) as f:
            print(f.read())
    n_cands = sum(1 for _ in open(cand_csv)) - 1 if cand_csv.exists() else 0
    n_ghost = 0
    if cand_csv.exists():
        import csv as _csv
        with open(cand_csv) as f:
            reader = _csv.DictReader(f)
            n_ghost = sum(1 for r in reader if r.get("ghost", "").lower() == "true")
    print(f"candidates CSV: {cand_csv} ({n_cands} rows, {n_ghost} ghost-filtered)")
    print(f"review CSV:     {review_csv}")


if __name__ == "__main__":
    main()
