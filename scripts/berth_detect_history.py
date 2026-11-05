"""
berth_detect_history.py — ТЗ-7.6: Kharg berths, full history.

Diffs from v4:
  a) BG fallback: ±24mo → if <10 dates → try ±48mo → if still <10 →
     no_background=1, occupied=NaN, n_ships=NaN.
  b) n_ships brackets: ≤460m→1, 460–820m→2, >820m→3.
  c) southeast_pier: counted, experimental=1 in every row.

Dates: auto-discovered from catalog, 2017-01-01 → today, orbits 35 and 101.
Output: diagnostics/berth_history/berth_history.csv
PNGs: 30 random dates (seed=11) + all 2026 dates.
"""

import os, sys, json, csv, random, zipfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

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
    "s1iw_cdse_hist", service_url="https://sh.dataspace.copernicus.eu"
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
KERNEL_RATIO_THR   = 12.0
MERGE_GAP_M        = 80.0
MIN_SHIP_GROUP_M   = 120.0
OCCUPIED_LEN_M     = 150.0
OCCUPIED_LEN_SP_M  = 100.0
WATER_RING_INNER_M = 300
WATER_RING_OUTER_M = 800

BG_PCT             = 20
BG_WINDOW_DAYS     = 365 * 2     # ±24 months (primary)
BG_WINDOW_DAYS_EXT = 365 * 4     # ±48 months (fallback)
BG_MIN_DATES       = 10

HISTORY_START = "2017-01-01"
ORBITS        = [35, 101]
PNG_SEED      = 11
PNG_RANDOM_N  = 30

ROOT     = Path(__file__).parent.parent
OUT_DIR  = ROOT / "diagnostics" / "berth_history"
BG_CACHE = ROOT / "diagnostics" / "berth_history" / "bg_cache"
BG_CACHE_V3 = ROOT / "diagnostics" / "berth_v3" / "bg_cache"   # reuse v3 ±24mo cache

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
# Corridor geometry
# ═══════════════════════════════════════════════════════════════════════════════

def _extend_line(pts_lonlat, end_m):
    lat_mid = np.mean([p[1] for p in pts_lonlat])
    mpdlat  = metres_per_deg_lat()
    mpdlon  = metres_per_deg_lon(lat_mid)
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
    line    = LineString(ext_pts)
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
# Water-ring wind normalisation
# ═══════════════════════════════════════════════════════════════════════════════

def build_water_ring_masks(corridor_polys):
    lat_mid  = (AOI_BBOX.min_y + AOI_BBOX.max_y) / 2
    mpdlat   = metres_per_deg_lat()
    mpdlon   = metres_per_deg_lon(lat_mid)
    buf_avg  = lambda m: m / ((mpdlat + mpdlon) / 2)
    all_corr = unary_union([p for p, _ in corridor_polys.values()])
    ring_masks = {}
    for berth, (poly, _) in corridor_polys.items():
        outer = poly.buffer(buf_avg(WATER_RING_OUTER_M))
        inner = poly.buffer(buf_avg(WATER_RING_INNER_M))
        ring_masks[berth] = rasterise_poly(outer.difference(inner).difference(all_corr))
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
        print(f"  wind-norm [{berth}]: bias={bias:+.2f} dB")
    return norm

# ═══════════════════════════════════════════════════════════════════════════════
# SentinelHub: catalog + fetch
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
            "scene_id":  r.get("id", ""),
            "datetime":  p.get("datetime", ""),
            "rel_orbit": p.get("sat:relative_orbit"),
            "orbit_dir": p.get("sat:orbit_state", ""),
            "platform":  p.get("platform", ""),
        })
    return out


def discover_all_dates():
    """
    Return list of dicts {date_str, orbit, platform, scene_id, datetime}
    for all catalog passes from HISTORY_START to today, orbits 35 and 101.
    One entry per (date, orbit) — earliest scene when multiple exist.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    print(f"Discovering catalog {HISTORY_START} → {today} ...")
    all_scenes = catalog_search(HISTORY_START, today)
    print(f"  total scenes found: {len(all_scenes)}")

    # Group by (date, orbit), keep earliest scene
    grouped = {}
    for s in all_scenes:
        if s["rel_orbit"] not in ORBITS:
            continue
        dt_raw = s["datetime"]
        try:
            dt = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.strptime(dt_raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        key = (date_str, s["rel_orbit"])
        if key not in grouped or dt_raw < grouped[key]["datetime"]:
            grouped[key] = {
                "date_str":  date_str,
                "rel_orbit": s["rel_orbit"],
                "orbit":     s["rel_orbit"],
                "platform":  s["platform"],
                "scene_id":  s["scene_id"],
                "datetime":  s["datetime"],
            }

    result = sorted(grouped.values(), key=lambda x: (x["date_str"], x["orbit"]))
    print(f"  unique (date, orbit) pairs: {len(result)}")
    # Summary by orbit
    for orb in ORBITS:
        n = sum(1 for r in result if r["orbit"] == orb)
        print(f"    orbit {orb}: {n} dates")
    return result


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
# Background — primary ±24mo, fallback ±48mo
# ═══════════════════════════════════════════════════════════════════════════════

def _bg_cache_path(date_str, orbit, window_days):
    suffix = "w24" if window_days == BG_WINDOW_DAYS else "w48"
    ym = date_str[:7]
    npy  = BG_CACHE / f"bg_o{orbit}_{ym}_{suffix}.npy"
    meta = BG_CACHE / f"bg_o{orbit}_{ym}_{suffix}_meta.json"
    return npy, meta

def _v3_cache_path(date_str, orbit):
    ym = date_str[:7]
    return (BG_CACHE_V3 / f"bg_o{orbit}_{ym}.npy",
            BG_CACHE_V3 / f"bg_o{orbit}_{ym}_meta.json")

def _build_bg(date_str, orbit, window_days):
    """Build and cache BG for given window. Returns array or None."""
    npy, meta_p = _bg_cache_path(date_str, orbit, window_days)
    if npy.exists():
        print(f"    bg cache hit: {npy.name}")
        return np.load(npy)
    target = datetime.strptime(date_str, "%Y-%m-%d")
    d0 = (target - timedelta(days=window_days)).strftime("%Y-%m-%d")
    d1 = (target + timedelta(days=window_days)).strftime("%Y-%m-%d")
    label = "±24mo" if window_days == BG_WINDOW_DAYS else "±48mo"
    try:
        scenes = catalog_search(d0, d1)
    except Exception as e:
        print(f"    catalog_search failed ({label}): {e}")
        return None
    orbit_sc = [s for s in scenes if s["rel_orbit"] == orbit]
    print(f"    bg build orbit {orbit} {label}: {len(orbit_sc)} scenes in window")
    if len(orbit_sc) < BG_MIN_DATES:
        print(f"    insufficient: {len(orbit_sc)} < {BG_MIN_DATES}")
        return None
    stack = []
    for s in orbit_sc:
        arr = fetch_linear(s["datetime"])
        if arr is not None:
            stack.append(arr)
    if len(stack) < BG_MIN_DATES:
        print(f"    insufficient fetched: {len(stack)} < {BG_MIN_DATES}")
        return None
    bg = np.percentile(np.stack(stack, axis=0), BG_PCT, axis=0).astype(np.float32)
    BG_CACHE.mkdir(parents=True, exist_ok=True)
    np.save(npy, bg)
    with open(meta_p, "w") as f:
        json.dump({"orbit": orbit, "date": date_str, "window_days": window_days,
                   "n": len(stack), "d0": d0, "d1": d1, "pct": BG_PCT}, f)
    print(f"    bg saved: {npy.name} ({len(stack)} scenes)")
    return bg


def _find_nearest_cached_bg(date_str, orbit, max_days=180):
    """
    Find the nearest existing BG .npy for this orbit across all cache dirs.
    Returns (path, lag_days) or (None, None).
    """
    target = datetime.strptime(date_str, "%Y-%m-%d")
    candidates = []
    for cache_dir in [BG_CACHE_V3, BG_CACHE]:
        if not cache_dir.exists():
            continue
        for npy in cache_dir.glob(f"bg_o{orbit}_????-??.npy"):
            # parse YYYY-MM from filename
            parts = npy.stem.split("_")
            # find the YYYY-MM part
            ym = None
            for p in parts:
                if len(p) == 7 and p[4] == "-":
                    ym = p
                    break
            if ym is None:
                continue
            try:
                ref = datetime.strptime(ym + "-15", "%Y-%m-%d")
            except ValueError:
                continue
            lag = abs((target - ref).days)
            if lag <= max_days:
                candidates.append((npy, lag))
    if not candidates:
        return None, None
    best = min(candidates, key=lambda x: x[1])
    return best


def load_or_build_bg_with_fallback(date_str, orbit):
    """
    Returns (bg_array, no_background_flag).
    1. Try exact month match (v3 cache or history cache).
    2. Try nearest cached BG (same orbit, within 180 days) — avoids expensive builds.
    3. Build ±24mo from scratch.
    4. Fallback ±48mo.
    5. If still None → no_background=1.
    """
    # Try v3 ±24mo exact cache first
    v3_npy, _ = _v3_cache_path(date_str, orbit)
    if v3_npy.exists():
        print(f"    bg v3 cache hit: {v3_npy.name}")
        return np.load(v3_npy), 0

    # Try history exact cache
    npy24, _ = _bg_cache_path(date_str, orbit, BG_WINDOW_DAYS)
    if npy24.exists():
        print(f"    bg cache hit: {npy24.name}")
        return np.load(npy24), 0

    # Try nearest cached BG to avoid expensive builds
    nearest_npy, lag = _find_nearest_cached_bg(date_str, orbit, max_days=180)
    if nearest_npy is not None:
        print(f"    bg nearest cache: {nearest_npy.name} (lag {lag}d)")
        return np.load(nearest_npy), 0

    # Build ±24mo (may burn quota — only reached when truly no nearby cache)
    bg = _build_bg(date_str, orbit, BG_WINDOW_DAYS)
    if bg is not None:
        return bg, 0

    # Fallback ±48mo
    print(f"    trying ±48mo fallback ...")
    npy48, _ = _bg_cache_path(date_str, orbit, BG_WINDOW_DAYS_EXT)
    if npy48.exists():
        print(f"    bg fallback cache hit: {npy48.name}")
        return np.load(npy48), 0
    bg = _build_bg(date_str, orbit, BG_WINDOW_DAYS_EXT)
    if bg is not None:
        return bg, 0

    print(f"    no_background for orbit {orbit} date {date_str}")
    return None, 1

# ═══════════════════════════════════════════════════════════════════════════════
# Detection (v7.6 brackets)
# ═══════════════════════════════════════════════════════════════════════════════

def _n_ships_from_length(length_m):
    """ТЗ-7.6: ≤460m→1, 460–820m→2, >820m→3."""
    if length_m <= 460:
        return 1
    elif length_m <= 820:
        return 2
    else:
        return 3


def kernel_blobs(ratio_db, comp_union):
    kernel_mask = comp_union & (ratio_db > KERNEL_RATIO_THR)
    labeled, n  = ndimage.label(kernel_mask)
    blobs = []
    for i in range(1, n + 1):
        m = labeled == i
        ys_k, xs_k = np.where(m)
        if len(xs_k) < 2:
            continue
        length_px, _, _ = pca_dims(ys_k, xs_k)
        blobs.append({
            "ys": ys_k, "xs": xs_k,
            "length_m": round(length_px * RESOLUTION, 1),
            "max_ratio_dB": float(ratio_db[m].max()),
            "_ys_min": int(ys_k.min()), "_ys_max": int(ys_k.max()),
            "_xs_min": int(xs_k.min()), "_xs_max": int(xs_k.max()),
        })
    return blobs


def _berth_axis_px(berth):
    pts = BERTH_SPEC[berth]
    ox, oy = lonlat_to_px(*pts[0])
    ex, ey = lonlat_to_px(*pts[-1])
    dx, dy = ex - ox, ey - oy
    length = np.hypot(dx, dy)
    if length < 1:
        return None
    return (dx / length, dy / length, ox, oy)


def detect_in_corridors(ratio_db, corridor_masks, meta, date_str):
    results = []
    gap_px = MERGE_GAP_M / RESOLUTION

    for berth, cmask in corridor_masks.items():
        ratio_in  = np.where(cmask, ratio_db, -999)
        blobs_bin = ratio_in > RATIO_THR
        labeled, num = ndimage.label(blobs_bin)
        axis_px = _berth_axis_px(berth)

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

        occ_thr      = _occ_len(berth)
        lengths_large = []
        n_ships_total = 0
        any_occupied  = False
        all_comp_union = np.zeros(ratio_db.shape, dtype=bool)
        group_boxes    = []

        for group in groups:
            ys_all = np.concatenate([b['_ys'] for b in group])
            xs_all = np.concatenate([b['_xs'] for b in group])
            comp_union = np.zeros(ratio_db.shape, dtype=bool)
            for b in group:
                comp_union |= b['_comp']
            all_comp_union |= comp_union

            group_len_px, _, _ = pca_dims(ys_all, xs_all)
            group_len_m = round(group_len_px * RESOLUTION, 1)

            if len(group) > 1:
                print(f"    [{berth}] merged {len(group)} blobs → {group_len_m:.0f}m")

            if group_len_m < MIN_SHIP_GROUP_M:
                continue

            n_ships_grp = _n_ships_from_length(group_len_m)
            lengths_large.append(group_len_m)
            n_ships_total += n_ships_grp

            group_boxes.append({
                "_ys_all": ys_all, "_xs_all": xs_all,
                "length_m": group_len_m, "n_ships": n_ships_grp,
                "_ys_min": int(ys_all.min()), "_ys_max": int(ys_all.max()),
                "_xs_min": int(xs_all.min()), "_xs_max": int(xs_all.max()),
            })

            kblobs = kernel_blobs(ratio_db, comp_union)
            if any(kb["length_m"] >= occ_thr for kb in kblobs):
                any_occupied = True

        max_ratio = float(ratio_db[all_comp_union].max()) if all_comp_union.any() else 0.0

        results.append({
            "date":          date_str,
            "rel_orbit":     meta["rel_orbit"],
            "platform":      meta["platform"],
            "berth":         berth,
            "occupied":      any_occupied,
            "n_ships":       n_ships_total,
            "lengths_m":     sorted(lengths_large, reverse=True),
            "max_ratio_dB":  round(max_ratio, 2),
            "_comp_union":   all_comp_union,
            "_group_boxes":  group_boxes,
        })
        print(f"    [{berth}] n_ships={n_ships_total} occ={'Y' if any_occupied else 'n'} "
              f"lengths={sorted(lengths_large, reverse=True)}")

    return results


def _empty_berth_row(date_str, meta, berth):
    return {
        "date": date_str, "rel_orbit": meta.get("rel_orbit", ""),
        "platform": meta.get("platform", ""), "berth": berth,
        "occupied": False, "n_ships": 0, "lengths_m": [],
        "max_ratio_dB": 0.0,
        "_comp_union": None, "_group_boxes": [],
    }

# ═══════════════════════════════════════════════════════════════════════════════
# Rendering — 4 panels
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
    lat_mid    = (AOI_BBOX.min_y + AOI_BBOX.max_y) / 2
    pad_px_lat = int(pad_m / metres_per_deg_lat()
                     / (AOI_BBOX.max_y - AOI_BBOX.min_y) * SIZE[1])
    pad_px_lon = int(pad_m / metres_per_deg_lon(lat_mid)
                     / (AOI_BBOX.max_x - AOI_BBOX.min_x) * SIZE[0])
    r0 = max(0, int(rows.min()) - pad_px_lat)
    r1 = min(SIZE[1], int(rows.max()) + pad_px_lat)
    c0 = max(0, int(cols.min()) - pad_px_lon)
    c1 = min(SIZE[0], int(cols.max()) + pad_px_lon)
    return r0, r1, c0, c1


def render_png(scene_linear, bg_linear, ratio_db_norm,
               berth, corridor_mask, berth_results,
               date_str, orbit, out_dir, no_background=0):
    r0, r1, c0, c1 = _crop_bounds(corridor_mask, pad_m=300)
    if r1 <= r0 or c1 <= c0:
        return
    eps   = 1e-10
    sc_db = 10 * np.log10(np.where(scene_linear[r0:r1, c0:c1] < eps, eps, scene_linear[r0:r1, c0:c1]))
    bg_db = 10 * np.log10(np.where(bg_linear[r0:r1, c0:c1]    < eps, eps, bg_linear[r0:r1, c0:c1]))
    rt_db = ratio_db_norm[r0:r1, c0:c1]
    cmask_crop = corridor_mask[r0:r1, c0:c1]

    ship_boxes = []
    num = 0
    for res in berth_results:
        for gb in res.get("_group_boxes", []):
            num += 1
            bx0 = max(0, gb["_xs_min"] - c0 - 3)
            bx1 = min(rt_db.shape[1], gb["_xs_max"] - c0 + 3)
            by0 = max(0, gb["_ys_min"] - r0 - 3)
            by1 = min(rt_db.shape[0], gb["_ys_max"] - r0 + 3)
            n   = gb["n_ships"]
            lbl = f"{num}×{n}" if n > 1 else str(num)
            ship_boxes.append((bx0, by0, bx1, by1, lbl, gb["length_m"]))

    kernel_mask = np.zeros(rt_db.shape, dtype=bool)
    comp_union  = None
    for res in berth_results:
        cu = res.get("_comp_union")
        if cu is not None:
            comp_union = cu if comp_union is None else (comp_union | cu)
    if comp_union is not None:
        kernel_px = comp_union & (ratio_db_norm > KERNEL_RATIO_THR)
        ky, kx = np.where(kernel_px)
        ky2 = ky - r0; kx2 = kx - c0
        valid = (ky2 >= 0) & (ky2 < rt_db.shape[0]) & (kx2 >= 0) & (kx2 < rt_db.shape[1])
        if valid.any():
            kernel_mask[ky2[valid], kx2[valid]] = True

    occupied = any(r["occupied"] for r in berth_results)
    n_ships  = sum(r["n_ships"] for r in berth_results)
    exp_flag = " [EXPERIMENTAL]" if berth == "southeast_pier" else ""
    nb_flag  = " NO_BG" if no_background else ""

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
        outline = np.zeros_like(cmask_crop, dtype=float)
        outline[cmask_crop] = 1
        ax.contour(outline, levels=[0.5], colors=[corridor_color], linewidths=1.0)
        if idx == 3:
            if kernel_mask.any():
                masked = np.ma.masked_where(~kernel_mask, np.ones(kernel_mask.shape))
                ax.imshow(masked, cmap="autumn", vmin=0, vmax=1,
                          origin="upper", aspect="equal", alpha=0.75)
            for (bx0, by0, bx1, by1, lbl, length_m) in ship_boxes:
                rect = mpatches.Rectangle((bx0, by0), bx1-bx0, by1-by0,
                                          edgecolor="#FF4444", facecolor="none", lw=1.5)
                ax.add_patch(rect)
                ax.text(bx0, by0 - 2, f"#{lbl} {length_m:.0f}m",
                        color="#FF4444", fontsize=6, va="bottom")
        else:
            for (bx0, by0, bx1, by1, lbl, length_m) in ship_boxes:
                rect = mpatches.Rectangle((bx0, by0), bx1-bx0, by1-by0,
                                          edgecolor="#FFE000", facecolor="none", lw=1.2)
                ax.add_patch(rect)
                ax.text(bx1 + 1, by0, f"#{lbl} {length_m:.0f}m",
                        color="#FFE000", fontsize=5.5, va="top")
        ax.set_title(title, fontsize=9)
        ax.axis("off")

    fig.suptitle(
        f"{berth}{exp_flag}{nb_flag}  {date_str}  orbit {orbit}  "
        f"n_ships={n_ships}  {'OCCUPIED' if occupied else 'clear'}",
        fontsize=11, y=0.99)
    plt.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = out_dir / f"{date_str}_{orbit}_{berth}.png"
    plt.savefig(fname, dpi=130, bbox_inches="tight")
    plt.close()
    print(f"  saved PNG: {fname.name}")
    return fname

# ═══════════════════════════════════════════════════════════════════════════════
# CSV
# ═══════════════════════════════════════════════════════════════════════════════

CSV_FIELDS = ["date", "rel_orbit", "platform", "berth",
              "occupied", "n_ships", "lengths_m",
              "max_ratio_dB", "no_background", "experimental", "run_id"]


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, "") for k in CSV_FIELDS}
            row["lengths_m"] = str(row["lengths_m"])
            w.writerow(row)
    print(f"berth_history.csv: {path} ({len(rows)} rows)")

# ═══════════════════════════════════════════════════════════════════════════════
# Pre-delivery stats
# ═══════════════════════════════════════════════════════════════════════════════

def print_stats(rows):
    print("\n=== PRE-DELIVERY STATS ===")

    # 1. Dates per year × orbit
    dates_by_year_orbit = defaultdict(set)
    for r in rows:
        if r["berth"] == "sea_island":   # count once per (date, orbit)
            year = r["date"][:4]
            dates_by_year_orbit[(year, r["rel_orbit"])].add(r["date"])

    print("\n--- Dates per year × orbit ---")
    all_years = sorted(set(k[0] for k in dates_by_year_orbit))
    header = f"{'year':<6}" + "".join(f"  orb{o:>3}" for o in ORBITS) + "  total"
    print(header)
    for yr in all_years:
        counts = [len(dates_by_year_orbit.get((yr, o), set())) for o in ORBITS]
        total  = sum(counts)
        line   = f"{yr:<6}" + "".join(f"  {c:>7}" for c in counts) + f"  {total:>5}"
        print(line)

    # 2. no_background % per year × orbit
    bg_total   = defaultdict(int)
    bg_missing = defaultdict(int)
    for r in rows:
        if r["berth"] == "sea_island":
            key = (r["date"][:4], r["rel_orbit"])
            bg_total[key] += 1
            if r["no_background"] == 1:
                bg_missing[key] += 1

    print("\n--- no_background % per year × orbit ---")
    print(header)
    for yr in all_years:
        vals = []
        for o in ORBITS:
            key = (yr, o)
            tot = bg_total[key]
            pct = (bg_missing[key] / tot * 100) if tot > 0 else 0.0
            vals.append(f"{pct:>7.1f}%")
        total_tot  = sum(bg_total.get((yr, o), 0) for o in ORBITS)
        total_miss = sum(bg_missing.get((yr, o), 0) for o in ORBITS)
        total_pct  = (total_miss / total_tot * 100) if total_tot > 0 else 0.0
        print(f"{yr:<6}" + "  " + "  ".join(vals) + f"  {total_pct:>5.1f}%")

    # 3. occupied % per year × berth (exclude no_background rows)
    occ_total  = defaultdict(int)
    occ_yes    = defaultdict(int)
    for r in rows:
        if r["no_background"] == 1:
            continue
        if r["n_ships"] == "NaN":
            continue
        key = (r["date"][:4], r["berth"])
        occ_total[key] += 1
        if r["occupied"] is True or r["occupied"] == "True":
            occ_yes[key] += 1

    berths = list(BERTH_SPEC.keys())
    print("\n--- occupied % per year × berth (no_background excluded) ---")
    hdr2 = f"{'year':<6}" + "".join(f"  {b[:14]:>14}" for b in berths)
    print(hdr2)
    for yr in all_years:
        vals = []
        for b in berths:
            key = (yr, b)
            tot = occ_total[key]
            pct = (occ_yes[key] / tot * 100) if tot > 0 else 0.0
            vals.append(f"{pct:>7.1f}%({occ_yes[key]}/{tot})")
        print(f"{yr:<6}" + "  " + "  ".join(vals))

# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BG_CACHE.mkdir(parents=True, exist_ok=True)

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

    # Discover all catalog dates
    all_entries = discover_all_dates()
    if not all_entries:
        sys.exit("FATAL: no catalog entries found")

    # Determine PNG dates: 30 random (seed=11) + all 2026
    all_date_strs = sorted(set(e["date_str"] for e in all_entries))
    dates_2026 = [d for d in all_date_strs if d.startswith("2026")]
    rng = random.Random(PNG_SEED)
    non_2026 = [d for d in all_date_strs if not d.startswith("2026")]
    random_30 = set(rng.sample(non_2026, min(PNG_RANDOM_N, len(non_2026))))
    png_dates = random_30 | set(dates_2026)
    print(f"\nPNG dates: {len(random_30)} random + {len(dates_2026)} 2026 = {len(png_dates)} unique")

    all_result_rows = []
    png_dir = OUT_DIR / "pngs"
    png_dir.mkdir(parents=True, exist_ok=True)

    for entry in all_entries:
        date_str = entry["date_str"]
        orbit    = entry["orbit"]
        print(f"\n[{date_str}  orbit={orbit}]")

        # Fetch scene
        scene_linear = fetch_linear(entry["datetime"])
        if scene_linear is None:
            print("  fetch failed — skipping")
            for berth in BERTH_SPEC:
                all_result_rows.append({
                    "date": date_str, "rel_orbit": orbit,
                    "platform": entry["platform"], "berth": berth,
                    "occupied": False, "n_ships": 0, "lengths_m": [],
                    "max_ratio_dB": 0.0,
                    "no_background": 0, "experimental": 1 if berth == "southeast_pier" else 0,
                    "run_id": run_id,
                })
            continue

        # Build / load background
        bg_linear, no_bg = load_or_build_bg_with_fallback(date_str, orbit)

        if no_bg:
            # no_background rows — NaN for occupied/n_ships
            for berth in BERTH_SPEC:
                all_result_rows.append({
                    "date": date_str, "rel_orbit": orbit,
                    "platform": entry["platform"], "berth": berth,
                    "occupied": "NaN", "n_ships": "NaN", "lengths_m": [],
                    "max_ratio_dB": "NaN",
                    "no_background": 1, "experimental": 1 if berth == "southeast_pier" else 0,
                    "run_id": run_id,
                })
            # Render PNG even for no_background? No — skip (nothing to show without ratio).
            continue

        # Compute ratio
        sc_sm = smooth3x3(scene_linear)
        bg_sm = smooth3x3(bg_linear)
        eps   = 1e-10
        ratio_db_full = np.clip(
            10 * np.log10(np.where(sc_sm < eps, eps, sc_sm) /
                          np.where(bg_sm < eps, eps, bg_sm)),
            -30, 30)
        ratio_db_norm = wind_normalise(ratio_db_full, corridor_masks, water_ring_masks)

        # Detect
        berth_results_all = detect_in_corridors(ratio_db_norm, corridor_masks, entry, date_str)

        do_png = date_str in png_dates

        for berth in BERTH_SPEC:
            bd = [r for r in berth_results_all if r["berth"] == berth]
            if not bd:
                bd = [_empty_berth_row(date_str, entry, berth)]

            n_ships   = sum(r["n_ships"] for r in bd)
            occupied  = any(r["occupied"] for r in bd)
            lengths_m = sorted([l for r in bd for l in r["lengths_m"]], reverse=True)
            max_ratio = max((r["max_ratio_dB"] for r in bd), default=0.0)
            exp_flag  = 1 if berth == "southeast_pier" else 0

            all_result_rows.append({
                "date":          date_str,
                "rel_orbit":     orbit,
                "platform":      entry["platform"],
                "berth":         berth,
                "occupied":      occupied,
                "n_ships":       n_ships,
                "lengths_m":     lengths_m,
                "max_ratio_dB":  round(max_ratio, 2),
                "no_background": 0,
                "experimental":  exp_flag,
                "run_id":        run_id,
            })

            if do_png:
                render_png(scene_linear, bg_linear, ratio_db_norm,
                           berth, corridor_masks[berth], bd,
                           date_str, orbit, png_dir, no_background=0)

    # Write CSV
    csv_path = OUT_DIR / "berth_history.csv"
    write_csv(all_result_rows, csv_path)

    # Pre-delivery stats
    print_stats(all_result_rows)

    # Summary
    png_files = sorted(png_dir.glob("????-??-??_*_*.png"))
    print(f"\n=== SUMMARY ===")
    print(f"total rows         : {len(all_result_rows)}")
    print(f"PNG files          : {len(png_files)}")
    print(f"run_id             : {run_id}")
    nb_count  = sum(1 for r in all_result_rows if r["no_background"] == 1)
    occ_count = sum(1 for r in all_result_rows if r["occupied"] is True)
    print(f"no_background rows : {nb_count}")
    print(f"occupied rows      : {occ_count} (of {len(all_result_rows) - nb_count} with bg)")

    # Zip PNGs
    zip_path = OUT_DIR / "berth_history_png.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in png_files:
            zf.write(p, p.name)
    print(f"berth_history_png.zip: {zip_path} ({len(png_files)} files)")


if __name__ == "__main__":
    main()
