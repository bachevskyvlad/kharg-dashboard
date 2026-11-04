"""
berth_detect_v2.py — Berth-level ship detector v2, Kharg Island.

Algorithm:
  1. Background model: 20th-pct σ0 VV linear, ±12 months window, min 20 dates,
     cached per orbit+YYYY-MM in diagnostics/berth_v2/bg_cache/
  2. Water whitelist: pixel is water if bg_linear < sea_thr
     where sea_thr = 70th_pct(bg) + 3·σ(bg)   [global bg stats]
  3. ratio_dB = 10·log10(scene_linear / bg_linear), clipped at ±30 dB
  4. Detection: ratio_dB > RATIO_THR AND scene_dB >= local_sea_dB + 6
     local_sea_dB = median(scene_dB) over ring 300–600 m around centroid
  5. 4 berth zones: east_head, east_stem, sea_island, south_pier
     (400 m buffer around line/point, intersected with water mask)
  6. Both orbits (35 DESC, 101 ASC) processed independently
  7. Output: diagnostics/berth_v2/
       <date>_<orbit>_tripanel.png  (scene_dB | bg_dB | ratio_dB)
       <date>_<orbit>_detections.csv
       ship_crops/  10 ship + 10 empty 600 m crops
       bg_cache/    GeoTIFF + fallback .npy backgrounds

ISOLATION: does NOT touch ship_backfill.py, update_anchorage.yml,
           data/anchorage.json, or any Supabase table.

Usage:
  python berth_detect_v2.py --dates 2021-06-20 2024-01-10
  python berth_detect_v2.py --dates 2021-06-20 --orbits 35
"""

import os, sys, json, csv, argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy import ndimage

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder
)
from shapely.geometry import Point, LineString, Polygon

# ── Config ────────────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID and CLIENT_SECRET env vars must be set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_berth_v2", service_url="https://sh.dataspace.copernicus.eu"
)

# ── AOI ───────────────────────────────────────────────────────────────────────
AOI_BBOX   = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)
RESOLUTION = 10
SIZE       = bbox_to_dimensions(AOI_BBOX, resolution=RESOLUTION)   # (W, H)

# ── Detection thresholds ──────────────────────────────────────────────────────
RATIO_THR     = 6.0   # dB above background
LOCAL_CONTRAST = 6.0  # dB above local sea
BG_PCT        = 20    # percentile for background model
SEA_THR_PCT   = 70    # percentile for sea_thr calculation
SEA_THR_SIGMA = 3.0   # σ multiplier for sea_thr
RING_IN_M     = 300   # inner radius of local-sea ring
RING_OUT_M    = 600   # outer radius

# ── Background window ─────────────────────────────────────────────────────────
BG_WINDOW_DAYS = 365  # ±12 months
BG_MIN_DATES   = 20   # require at least this many scenes

# ── Orbits ────────────────────────────────────────────────────────────────────
ORBIT_35  = 35   # descending
ORBIT_101 = 101  # ascending

# ── Berth zone line geometries (lat,lon in ТЗ → stored as lon,lat for shapely) ─
# Coordinates are (lon, lat)
BERTH_LINES = {
    "east_head":  LineString([(50.33837, 29.23799), (50.34062, 29.22201)]),
    "east_stem":  LineString([(50.33105, 29.23061), (50.33950, 29.23061)]),
    "sea_island": LineString([(50.28302, 29.23331), (50.28611, 29.22545)]),
    "south_pier": Point(50.33527, 29.21463),
}
BERTH_BUFFER_DEG = 400 / 111320  # ~400 m in degrees

# ── Kharg Island approximate coastline (inline, no network call) ──────────────
KHARG_COAST = Polygon([
    (50.287, 29.193), (50.294, 29.187), (50.312, 29.183), (50.332, 29.183),
    (50.350, 29.189), (50.368, 29.198), (50.378, 29.211), (50.382, 29.225),
    (50.379, 29.240), (50.372, 29.253), (50.360, 29.263), (50.344, 29.271),
    (50.325, 29.275), (50.305, 29.272), (50.290, 29.265), (50.281, 29.253),
    (50.278, 29.238), (50.280, 29.222), (50.284, 29.208), (50.287, 29.193),
])
LAND_BUFFER_DEG = 20 / 111320  # 20 m

# ── Evalscript: return raw σ0 linear (no normalisation) ──────────────────────
EVALSCRIPT_LINEAR = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  return [Math.max(s.VV, 1e-10)];
}
"""

# ── Directories ───────────────────────────────────────────────────────────────
ROOT     = Path(__file__).parent.parent
OUT_DIR  = ROOT / "diagnostics" / "berth_v2"
BG_CACHE = OUT_DIR / "bg_cache"
CROP_DIR = OUT_DIR / "ship_crops"


# ═══════════════════════════════════════════════════════════════════════════════
# Coordinate helpers
# ═══════════════════════════════════════════════════════════════════════════════

def px_to_lonlat(px, py, bbox=AOI_BBOX, sz=SIZE):
    lon = bbox.min_x + (px + 0.5) / sz[0] * (bbox.max_x - bbox.min_x)
    lat = bbox.max_y - (py + 0.5) / sz[1] * (bbox.max_y - bbox.min_y)
    return lon, lat

def lonlat_to_px(lon, lat, bbox=AOI_BBOX, sz=SIZE):
    px = int((lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * sz[0])
    py = int((bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * sz[1])
    return px, py

def deg_to_px_radius(meters, lat):
    """Convert metres to pixel radius at given latitude."""
    deg_lon = meters / (111320 * np.cos(np.deg2rad(lat)))
    deg_lat = meters / 111320
    px_lon = deg_lon / (AOI_BBOX.max_x - AOI_BBOX.min_x) * SIZE[0]
    px_lat = deg_lat / (AOI_BBOX.max_y - AOI_BBOX.min_y) * SIZE[1]
    return int((px_lon + px_lat) / 2)


# ═══════════════════════════════════════════════════════════════════════════════
# Land / water masks
# ═══════════════════════════════════════════════════════════════════════════════

def build_land_mask():
    """True where pixel is land (Kharg Island + 20 m buffer)."""
    h, w = SIZE[1], SIZE[0]
    lons = np.linspace(AOI_BBOX.min_x + 0.5*(AOI_BBOX.max_x-AOI_BBOX.min_x)/w,
                       AOI_BBOX.max_x - 0.5*(AOI_BBOX.max_x-AOI_BBOX.min_x)/w, w)
    lats = np.linspace(AOI_BBOX.max_y - 0.5*(AOI_BBOX.max_y-AOI_BBOX.min_y)/h,
                       AOI_BBOX.min_y + 0.5*(AOI_BBOX.max_y-AOI_BBOX.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    buffered = KHARG_COAST.buffer(LAND_BUFFER_DEG)
    try:
        import shapely
        return shapely.contains_xy(buffered, lon_g.ravel(), lat_g.ravel()).reshape(h, w)
    except AttributeError:
        # Shapely < 2.0 fallback
        from shapely.vectorized import contains  # type: ignore
        return contains(buffered, lon_g, lat_g)

def build_berth_zones(land_mask):
    """
    Returns dict zone_name → bool mask (True = in berth zone + is water).
    400 m buffer around line geometries, intersected with ~water pixels.
    """
    h, w = SIZE[1], SIZE[0]
    lons = np.linspace(AOI_BBOX.min_x + 0.5*(AOI_BBOX.max_x-AOI_BBOX.min_x)/w,
                       AOI_BBOX.max_x - 0.5*(AOI_BBOX.max_x-AOI_BBOX.min_x)/w, w)
    lats = np.linspace(AOI_BBOX.max_y - 0.5*(AOI_BBOX.max_y-AOI_BBOX.min_y)/h,
                       AOI_BBOX.min_y + 0.5*(AOI_BBOX.max_y-AOI_BBOX.min_y)/h, h)
    lon_g, lat_g = np.meshgrid(lons, lats)

    zones = {}
    for zname, geom in BERTH_LINES.items():
        buffered = geom.buffer(BERTH_BUFFER_DEG)
        try:
            import shapely as _shp
            in_zone = _shp.contains_xy(buffered, lon_g.ravel(), lat_g.ravel()).reshape(h, w)
        except AttributeError:
            from shapely.vectorized import contains  # type: ignore
            in_zone = contains(buffered, lon_g, lat_g)
        zones[zname] = in_zone & ~land_mask
    return zones


# ═══════════════════════════════════════════════════════════════════════════════
# SentinelHub helpers
# ═══════════════════════════════════════════════════════════════════════════════

def catalog_search(date_from: str, date_to: str):
    """Return list of scene metadata dicts for AOI in date range."""
    catalog = SentinelHubCatalog(config=config)
    results = list(catalog.search(
        S1_CDSE,
        bbox=AOI_BBOX,
        time=(date_from + "T00:00:00Z", date_to + "T23:59:59Z"),
        fields={"include": ["id", "properties.datetime",
                            "properties.sat:relative_orbit",
                            "properties.sat:orbit_state",
                            "properties.platform"]},
    ))
    scenes = []
    for r in results:
        props = r.get("properties", {})
        dt_str = props.get("datetime", "")
        scenes.append({
            "scene_id":  r.get("id", ""),
            "datetime":  dt_str,
            "rel_orbit": props.get("sat:relative_orbit"),
            "orbit_dir": props.get("sat:orbit_state", ""),
            "platform":  props.get("platform", ""),
        })
    return scenes

def fetch_linear(scene_datetime: str) -> np.ndarray | None:
    """Fetch σ0 VV linear array for exact scene datetime. Returns (H,W) float32 or None."""
    # Use a ±30 min window around the scene datetime to pin the exact pass
    try:
        dt = datetime.fromisoformat(scene_datetime.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.strptime(scene_datetime[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    t0 = (dt - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t1 = (dt + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    request = SentinelHubRequest(
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
        data = request.get_data()
    except Exception as e:
        print(f"    Fetch error: {e}")
        return None
    if not data or data[0] is None:
        return None
    raw = np.array(data[0], dtype=np.float32)
    arr = raw[:, :, 0] if raw.ndim == 3 else raw
    arr = np.where(arr < 1e-10, 1e-10, arr)
    return arr


# ═══════════════════════════════════════════════════════════════════════════════
# Background model
# ═══════════════════════════════════════════════════════════════════════════════

def bg_cache_path(date_str: str, orbit: int) -> Path:
    """Cache key: orbit + year-month of target date."""
    ym = date_str[:7]  # YYYY-MM
    return BG_CACHE / f"bg_o{orbit}_{ym}.npy", BG_CACHE / f"bg_o{orbit}_{ym}_meta.json"

def load_or_build_background(date_str: str, orbit: int) -> np.ndarray | None:
    """
    Returns 20th-pct σ0 VV linear background for the given orbit.
    Builds and caches if not already present.
    """
    npy_path, meta_path = bg_cache_path(date_str, orbit)
    if npy_path.exists():
        print(f"    BG cache hit: {npy_path.name}")
        return np.load(npy_path)

    print(f"    Building background for orbit {orbit}, window ±12 months of {date_str} ...")
    target = datetime.strptime(date_str, "%Y-%m-%d")
    d0 = (target - timedelta(days=BG_WINDOW_DAYS)).strftime("%Y-%m-%d")
    d1 = (target + timedelta(days=BG_WINDOW_DAYS)).strftime("%Y-%m-%d")

    scenes = catalog_search(d0, d1)
    # Post-filter to target orbit (relativeOrbitNumber in dataFilter is forbidden)
    orbit_scenes = [s for s in scenes if s["rel_orbit"] == orbit]
    print(f"    Found {len(orbit_scenes)} scenes for orbit {orbit} in window")

    if len(orbit_scenes) < BG_MIN_DATES:
        print(f"    WARN: only {len(orbit_scenes)} scenes, need {BG_MIN_DATES} — skipping bg")
        return None

    stack = []
    for s in orbit_scenes:
        arr = fetch_linear(s["datetime"])
        if arr is not None:
            stack.append(arr)
        if len(stack) % 5 == 0:
            print(f"    Fetched {len(stack)}/{len(orbit_scenes)} ...")

    if len(stack) < BG_MIN_DATES:
        print(f"    WARN: only {len(stack)} fetched, need {BG_MIN_DATES}")
        return None

    cube = np.stack(stack, axis=0)
    bg = np.percentile(cube, BG_PCT, axis=0).astype(np.float32)
    BG_CACHE.mkdir(parents=True, exist_ok=True)
    np.save(npy_path, bg)
    with open(meta_path, "w") as f:
        json.dump({"orbit": orbit, "date": date_str, "n_scenes": len(stack),
                   "d0": d0, "d1": d1, "pct": BG_PCT}, f)
    print(f"    Background saved: {npy_path.name}  ({len(stack)} scenes)")
    return bg


# ═══════════════════════════════════════════════════════════════════════════════
# Water whitelist
# ═══════════════════════════════════════════════════════════════════════════════

def build_water_mask(bg: np.ndarray, land_mask: np.ndarray) -> np.ndarray:
    """
    True where pixel is water:
      bg_linear < sea_thr   AND   not land
    sea_thr = 70th_pct(bg[~land]) + 3·σ(bg[~land])
    """
    sea_vals = bg[~land_mask]
    sea_thr = np.percentile(sea_vals, SEA_THR_PCT) + SEA_THR_SIGMA * sea_vals.std()
    water = (bg < sea_thr) & ~land_mask
    return water


# ═══════════════════════════════════════════════════════════════════════════════
# Detection
# ═══════════════════════════════════════════════════════════════════════════════

def compute_local_sea_db(scene_db: np.ndarray, cx_px: int, cy_px: int,
                         water_mask: np.ndarray) -> float | None:
    """Median scene_dB in ring [RING_IN_M, RING_OUT_M] around centroid."""
    avg_lat = (AOI_BBOX.min_y + AOI_BBOX.max_y) / 2
    r_in  = deg_to_px_radius(RING_IN_M,  avg_lat)
    r_out = deg_to_px_radius(RING_OUT_M, avg_lat)
    h, w = scene_db.shape
    ys, xs = np.ogrid[:h, :w]
    dist2 = (xs - cx_px)**2 + (ys - cy_px)**2
    ring = (dist2 >= r_in**2) & (dist2 <= r_out**2) & water_mask
    vals = scene_db[ring]
    if len(vals) < 10:
        return None
    return float(np.median(vals))

def pca_dims(ys, xs):
    """Returns (length_px, width_px, orientation_deg) via PCA."""
    pts = np.column_stack([xs - xs.mean(), ys - ys.mean()])
    if len(pts) < 2:
        return 1.0, 1.0, 0.0
    cov = np.cov(pts.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    idx = np.argsort(eigvals)[::-1]
    eigvecs = eigvecs[:, idx]
    p0 = pts @ eigvecs[:, 0]; p1 = pts @ eigvecs[:, 1]
    length = float(p0.max() - p0.min()) + 1
    width  = float(p1.max() - p1.min()) + 1
    angle  = float(np.degrees(np.arctan2(eigvecs[1, 0], eigvecs[0, 0]))) % 180
    return length, width, angle

def detect_scene(scene_linear: np.ndarray, bg_linear: np.ndarray,
                 meta: dict, date_str: str,
                 land_mask: np.ndarray, water_mask: np.ndarray,
                 zone_masks: dict) -> list[dict]:
    """
    Run v2 detection on one scene.
    Returns list of detection dicts (includes _cx, _cy pixel coords).
    """
    eps = 1e-10
    scene_db = 10 * np.log10(np.where(scene_linear < eps, eps, scene_linear))
    bg_db    = 10 * np.log10(np.where(bg_linear   < eps, eps, bg_linear))
    ratio_db = np.clip(scene_db - bg_db, -30, 30)

    # Candidate mask: ratio > threshold AND water
    candidates = (ratio_db > RATIO_THR) & water_mask

    labeled, num = ndimage.label(candidates)
    detections = []

    for i in range(1, num + 1):
        comp = (labeled == i)
        area_px = int(comp.sum())
        if area_px < 3 or area_px > 800:
            continue

        ys_c, xs_c = np.where(comp)
        cx_px = int(round(float(xs_c.mean())))
        cy_px = int(round(float(ys_c.mean())))
        lon, lat = px_to_lonlat(cx_px, cy_px)

        # Local sea contrast check
        local_sea = compute_local_sea_db(scene_db, cx_px, cy_px, water_mask)
        peak_db   = float(scene_db[comp].max())
        if local_sea is None:
            continue  # not enough water pixels in ring — skip near-shore candidates
        if peak_db < local_sea + LOCAL_CONTRAST:
            continue  # fails local contrast test

        ratio_peak = float(ratio_db[comp].max())
        length_px, width_px, orientation = pca_dims(ys_c, xs_c)
        length_m = length_px * RESOLUTION
        width_m  = width_px  * RESOLUTION

        # Zone assignment (pixel mask lookup — fast, consistent with berth zone rasters)
        zone = "other"
        for zname, zmask in zone_masks.items():
            if zmask[cy_px, cx_px]:
                zone = zname
                break

        detections.append({
            "date":            date_str,
            "scene_id":        meta["scene_id"],
            "rel_orbit":       meta["rel_orbit"],
            "orbit_dir":       meta["orbit_dir"],
            "platform":        meta["platform"],
            "lat":             round(lat, 6),
            "lon":             round(lon, 6),
            "length_m":        round(length_m, 1),
            "width_m":         round(width_m, 1),
            "orientation_deg": round(orientation, 1),
            "peak_db":         round(peak_db, 2),
            "ratio_peak_db":   round(ratio_peak, 2),
            "local_sea_db":    round(local_sea, 2) if local_sea is not None else "",
            "area_px":         area_px,
            "class":           "large" if length_m >= 200 else "small",
            "zone":            zone,
            "_cx": cx_px, "_cy": cy_px,
            "_xs_min": int(xs_c.min()), "_xs_max": int(xs_c.max()),
            "_ys_min": int(ys_c.min()), "_ys_max": int(ys_c.max()),
        })

    return detections


# ═══════════════════════════════════════════════════════════════════════════════
# Rendering
# ═══════════════════════════════════════════════════════════════════════════════

ZONE_COLORS = {
    "east_head":  "#FF5050",
    "east_stem":  "#FF9900",
    "sea_island": "#50B4FF",
    "south_pier": "#A0FF50",
    "other":      "#FFFF50",
}

def render_tripanel(scene_linear, bg_linear, date_str, orbit, detections,
                    water_mask, zone_masks, out_dir):
    """Three-panel: scene_dB | bg_dB | ratio_dB. Saves PNG."""
    eps = 1e-10
    scene_db = 10 * np.log10(np.where(scene_linear < eps, eps, scene_linear))
    bg_db    = 10 * np.log10(np.where(bg_linear   < eps, eps, bg_linear))
    ratio_db = np.clip(scene_db - bg_db, -30, 30)

    fig, axes = plt.subplots(1, 3, figsize=(18, 7))
    panels = [
        (scene_db, "Scene dB", "gray", (-25, 5)),
        (bg_db,    "Background dB", "gray", (-25, 5)),
        (ratio_db, "Ratio dB", "RdBu_r", (-15, 15)),
    ]
    for ax, (data, title, cmap, vrange) in zip(axes, panels):
        im = ax.imshow(data, cmap=cmap, vmin=vrange[0], vmax=vrange[1],
                       origin="upper", aspect="equal")
        ax.set_title(f"{title}", fontsize=11)
        plt.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
        # Draw detections as boxes
        for det in detections:
            color = ZONE_COLORS.get(det["zone"], "#FFFF50")
            x0, y0 = det["_xs_min"] - 2, det["_ys_min"] - 2
            x1, y1 = det["_xs_max"] + 2, det["_ys_max"] + 2
            rect = plt.Rectangle((x0, y0), x1-x0, y1-y0,
                                  edgecolor=color, facecolor="none", linewidth=1.5)
            ax.add_patch(rect)
        # Draw zone outlines (approx bounding boxes)
        for zname, zmask in zone_masks.items():
            rows, cols = np.where(zmask)
            if len(rows):
                r0, r1, c0, c1 = rows.min(), rows.max(), cols.min(), cols.max()
                rect = plt.Rectangle((c0, r0), c1-c0, r1-r0,
                                     edgecolor=ZONE_COLORS.get(zname, "#fff"),
                                     facecolor="none", linewidth=1, linestyle="--")
                ax.add_patch(rect)
        ax.axis("off")

    n_ship = len(detections)
    title  = f"Kharg Island berth_v2 — {date_str}  orbit {orbit}  ({n_ship} detections)"
    fig.suptitle(title, fontsize=13, y=0.98)
    plt.tight_layout()
    fname = out_dir / f"{date_str}_o{orbit}_tripanel.png"
    plt.savefig(fname, dpi=120, bbox_inches="tight")
    plt.close()
    print(f"  Saved tripanel: {fname.name}")
    return fname


def render_crop(scene_linear, bg_linear, cx_px, cy_px, half_m, label, crop_dir):
    """600×600 m crop centred on centroid. Three-panel. Saves PNG."""
    avg_lat = (AOI_BBOX.min_y + AOI_BBOX.max_y) / 2
    half_px_lon = int(half_m / (111320 * np.cos(np.deg2rad(avg_lat)))
                      / (AOI_BBOX.max_x - AOI_BBOX.min_x) * SIZE[0])
    half_px_lat = int(half_m / 111320 / (AOI_BBOX.max_y - AOI_BBOX.min_y) * SIZE[1])
    half_px     = max(half_px_lon, half_px_lat)

    h, w = scene_linear.shape
    r0 = max(0, cy_px - half_px); r1 = min(h, cy_px + half_px)
    c0 = max(0, cx_px - half_px); c1 = min(w, cx_px + half_px)

    eps = 1e-10
    sc = scene_linear[r0:r1, c0:c1]
    bg = bg_linear[r0:r1, c0:c1]
    sc_db = 10 * np.log10(np.where(sc < eps, eps, sc))
    bg_db = 10 * np.log10(np.where(bg < eps, eps, bg))
    rt_db = np.clip(sc_db - bg_db, -30, 30)

    fig, axes = plt.subplots(1, 3, figsize=(12, 5))
    for ax, (data, title, cmap, vr) in zip(axes, [
        (sc_db, "Scene dB",     "gray",    (-25, 5)),
        (bg_db, "Background dB","gray",    (-25, 5)),
        (rt_db, "Ratio dB",     "RdBu_r",  (-15, 15)),
    ]):
        im = ax.imshow(data, cmap=cmap, vmin=vr[0], vmax=vr[1], origin="upper", aspect="equal")
        ax.set_title(title, fontsize=9)
        plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
        ax.axis("off")
    # Centre marker: detection centroid in crop-local coords
    axes[0].plot(cx_px - c0, cy_px - r0, "r+", markersize=10, markeredgewidth=2)
    fig.suptitle(label, fontsize=9, y=0.98)
    plt.tight_layout()
    crop_dir.mkdir(parents=True, exist_ok=True)
    fname = crop_dir / f"{label}.png"
    plt.savefig(fname, dpi=100, bbox_inches="tight")
    plt.close()
    return fname


def render_ship_crops(scene_linear, bg_linear, detections, date_str, orbit, crop_dir):
    """10 ship + 10 empty-berth 600 m crops."""
    # Ship crops: top-10 by ratio_peak_db
    ships = sorted(detections, key=lambda d: d["ratio_peak_db"], reverse=True)[:10]
    for j, det in enumerate(ships, 1):
        label = f"ship_{date_str}_o{orbit}_{det['zone']}_r{det['ratio_peak_db']:.1f}_{j:02d}"
        render_crop(scene_linear, bg_linear, det["_cx"], det["_cy"], 300, label, crop_dir)

    # Empty berth crops: zone centroids with no detections within 600 m
    from shapely.geometry import Point as SP
    shipped_pts = [SP(d["lon"], d["lat"]) for d in detections]

    empty_count = 0
    for zname, geom in BERTH_LINES.items():
        centroid = geom.centroid if hasattr(geom, "centroid") else geom
        near = any(centroid.distance(p) * 111320 < 600 for p in shipped_pts)
        if not near and empty_count < 10:
            cx, cy = lonlat_to_px(centroid.x, centroid.y)
            label = f"empty_{date_str}_o{orbit}_{zname}_{empty_count+1:02d}"
            render_crop(scene_linear, bg_linear, cx, cy, 300, label, crop_dir)
            empty_count += 1

    # If fewer than 10 empty from zones, add random water points
    if empty_count < 10:
        rng = np.random.default_rng(42)
        lons_try = rng.uniform(AOI_BBOX.min_x, AOI_BBOX.max_x, 50)
        lats_try = rng.uniform(AOI_BBOX.min_y, AOI_BBOX.max_y, 50)
        for lon_t, lat_t in zip(lons_try, lats_try):
            if empty_count >= 10:
                break
            if KHARG_COAST.contains(Point(lon_t, lat_t)):
                continue
            near = any(Point(lon_t, lat_t).distance(p) * 111320 < 600 for p in shipped_pts)
            if near:
                continue
            cx, cy = lonlat_to_px(lon_t, lat_t)
            label = f"empty_{date_str}_o{orbit}_sea_{empty_count+1:02d}"
            render_crop(scene_linear, bg_linear, cx, cy, 300, label, crop_dir)
            empty_count += 1


# ═══════════════════════════════════════════════════════════════════════════════
# CSV
# ═══════════════════════════════════════════════════════════════════════════════

FIELDS = ["date", "scene_id", "rel_orbit", "orbit_dir", "platform",
          "lat", "lon", "length_m", "width_m", "orientation_deg",
          "peak_db", "ratio_peak_db", "local_sea_db", "area_px", "class", "zone"]

def append_csv(detections: list, csv_path: Path):
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(detections)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def process_date_orbit(date_str: str, orbit: int,
                       land_mask, water_mask_fn, zone_masks,
                       out_dir: Path, crop_dir: Path):
    """
    Process one (date, orbit) pair.
    water_mask_fn: callable(bg) → ndarray  (applied after bg is built)
    """
    print(f"\n[{date_str}  orbit={orbit}]")

    # 1. Find scene for this date + orbit
    scenes = catalog_search(date_str, date_str)
    orbit_scenes = [s for s in scenes if s["rel_orbit"] == orbit]
    if not orbit_scenes:
        print(f"  No scene for orbit {orbit} on {date_str}")
        return []

    scene_meta = orbit_scenes[0]
    print(f"  scene_id={scene_meta['scene_id']}  dir={scene_meta['orbit_dir']}")

    # 2. Fetch scene
    scene_linear = fetch_linear(scene_meta["datetime"])
    if scene_linear is None:
        print("  Fetch failed")
        return []

    # 3. Background
    bg_linear = load_or_build_background(date_str, orbit)
    if bg_linear is None:
        print("  Background unavailable, skipping")
        return []

    # 4. Water mask from this background
    water_mask = water_mask_fn(bg_linear)
    print(f"  Water pixels: {water_mask.sum()} / {water_mask.size}")

    # 5. Detect
    detections = detect_scene(scene_linear, bg_linear, scene_meta, date_str,
                               land_mask, water_mask, zone_masks)
    print(f"  Detections: {len(detections)}")
    for zname in list(BERTH_LINES.keys()) + ["other"]:
        n = sum(1 for d in detections if d["zone"] == zname)
        if n:
            print(f"    {zname}: {n}")

    # 6. Tripanel
    render_tripanel(scene_linear, bg_linear, date_str, orbit, detections,
                    water_mask, zone_masks, out_dir)

    # 7. Ship + empty crops
    render_ship_crops(scene_linear, bg_linear, detections, date_str, orbit, crop_dir)

    # 8. CSV
    csv_path = out_dir / "detections.csv"
    append_csv(detections, csv_path)

    return detections


def main():
    parser = argparse.ArgumentParser(description="Kharg berth detector v2")
    parser.add_argument("--dates", nargs="+", required=True,
                        metavar="YYYY-MM-DD", help="Dates to process")
    parser.add_argument("--orbits", nargs="+", type=int,
                        default=[ORBIT_35, ORBIT_101],
                        help="Relative orbit numbers (default: 35 101)")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BG_CACHE.mkdir(parents=True, exist_ok=True)
    CROP_DIR.mkdir(parents=True, exist_ok=True)

    print("Building land mask ...")
    land_mask = build_land_mask()
    print(f"Land mask: {land_mask.sum()} px")

    print("Building berth zone masks ...")
    zone_masks = build_berth_zones(land_mask)
    for zname, zm in zone_masks.items():
        print(f"  {zname}: {zm.sum()} px")

    # water_mask is per-bg, so pass a callable
    def water_mask_fn(bg):
        return build_water_mask(bg, land_mask)

    all_detections = []
    for date_str in args.dates:
        for orbit in args.orbits:
            dets = process_date_orbit(date_str, orbit, land_mask,
                                      water_mask_fn, zone_masks,
                                      OUT_DIR, CROP_DIR)
            all_detections.extend(dets)

    # Summary
    print(f"\n{'='*60}")
    print(f"Total detections: {len(all_detections)}")
    from collections import Counter
    zone_cnt  = Counter(d["zone"]  for d in all_detections)
    orbit_cnt = Counter(d["rel_orbit"] for d in all_detections)
    class_cnt = Counter(d["class"] for d in all_detections)
    print(f"By zone:  {dict(zone_cnt)}")
    print(f"By orbit: {dict(orbit_cnt)}")
    print(f"By class: {dict(class_cnt)}")
    print(f"Output:   {OUT_DIR}")


if __name__ == "__main__":
    main()
