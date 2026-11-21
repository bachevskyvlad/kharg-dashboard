"""
berth_detect_v410.py — ТЗ-7.10: Kharg berth occupancy + ship count.

Builds on berth_detect_v4 (rev2). Four changes:
  1. Background window: ±24 mo → ±48 mo fallback; <10 scenes → occupied=NaN, no_background=1.
  2. Static filter: blobs seen on ≥8 dates (by berth × round_50m_center) → structure=1; excluded.
  3. Merge gap <80m always; length brackets: ≤460→1, 460–820→2, >820→3.
  4. southeast_pier: experimental=1, excluded from metrics.

Output per row: date, orbit, platform, berth, occupied, n_ships, lengths_m,
                no_background, experimental, run_id

Steps:
  --step validation  Run 40 dates, compare vs labels_round1 + v3 blind; print accuracy.
  --step history     Full history 2017→today, both orbits, with checkpoint.
  --resume           Skip dates already in checkpoint_blobs.csv / checkpoint_results.csv.

Usage:
  python berth_detect_v410.py --step validation
  python berth_detect_v410.py --step history [--resume]
"""

import os, sys, csv, json, math, argparse, zipfile
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
    "s1iw_cdse_v410", service_url="https://sh.dataspace.copernicus.eu"
)

AOI_BBOX   = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)
RESOLUTION = 10
SIZE       = bbox_to_dimensions(AOI_BBOX, resolution=RESOLUTION)

BERTH_SPEC = {
    "sea_island":     [(50.28316, 29.23318), (50.28816, 29.22433)],
    "east_t_head":    [(50.33833, 29.23812), (50.34054, 29.22189)],
    "southeast_pier": [(50.33646, 29.21764), (50.33591, 29.21424), (50.33522, 29.21264)],
}
EXPERIMENTAL_BERTHS = {"southeast_pier"}

CORRIDOR_HALF_M  = 150
CORRIDOR_END_M   = 100

RATIO_THR          = 10.0
MIN_AREA_PX        = 15
KERNEL_RATIO_THR   = 12.0
MERGE_GAP_M        = 80.0       # merge blobs with gap < 80m (Change 3)
MIN_SHIP_GROUP_M   = 120.0      # groups < 120m → ignored
OCCUPIED_LEN_M     = 150.0
OCCUPIED_LEN_SP_M  = 100.0
WATER_RING_INNER_M = 300
WATER_RING_OUTER_M = 800

BG_PCT             = 20
BG_WINDOW_DAYS_1   = 365 * 2    # ±24 months (Change 1)
BG_WINDOW_DAYS_2   = 365 * 4    # ±48 months fallback
BG_MIN_DATES       = 10
BG_MAX_SCENES      = 25         # cap stack; evenly subsample if more
STATIC_MIN_DATES   = 8          # Change 2: static if seen ≥8 dates
STATIC_ROUND_M     = 50         # group by round-to-50m center

ROOT     = Path(__file__).parent.parent
OUT_DIR  = ROOT / "diagnostics" / "berth_v410"
HIST_DIR = ROOT / "diagnostics" / "berth_history"
BG_CACHE = ROOT / "diagnostics" / "berth_v3" / "bg_cache"

EVALSCRIPT_LINEAR = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  return [Math.max(s.VV, 1e-10)];
}
"""

CODE_VERSION = "v410"

# 40 validation dates (20 old + 20 new from v5)
VALIDATION_DATES_ORBITS = [
    # Old (20)
    ("2019-01-15", 35),  ("2019-03-08", 101), ("2019-06-24", 101),
    ("2020-01-10", 35),  ("2020-03-14", 101), ("2020-06-14", 35),
    ("2020-06-30", 101), ("2021-01-08", 101), ("2021-04-10", 35),
    ("2021-06-25", 101), ("2022-01-15", 101), ("2022-06-16", 35),
    ("2023-07-09", 101), ("2024-01-13", 35),  ("2024-06-29", 35),
    ("2024-07-03", 101), ("2025-01-07", 35),  ("2025-01-11", 101),
    ("2026-04-14", 35),  ("2026-04-18", 101),
    # New (20)
    ("2017-03-06", 101), ("2017-08-29", 35),  ("2018-01-12", 101),
    ("2018-05-20", 35),  ("2019-05-15", 35),  ("2019-10-22", 101),
    ("2020-04-03", 35),  ("2020-05-13", 101), ("2021-02-01", 101),
    ("2021-07-27", 35),  ("2022-02-04", 35),  ("2022-02-08", 101),
    ("2023-09-03", 35),  ("2023-10-25", 101), ("2024-05-16", 101),
    ("2024-12-26", 35),  ("2025-09-02", 101), ("2025-11-15", 35),
    ("2026-01-02", 35),  ("2026-09-28", 101),
]

HISTORY_START = "2017-01-01"
HISTORY_END   = datetime.utcnow().strftime("%Y-%m-%d")

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
# Water-ring wind normalisation
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
    return norm

# ═══════════════════════════════════════════════════════════════════════════════
# SentinelHub fetch
# ═══════════════════════════════════════════════════════════════════════════════

class PUExhaustedError(Exception):
    """Raised when Sentinel Hub returns 403 ACCESS_INSUFFICIENT_PROCESSING_UNITS."""

def catalog_search(date_from, date_to):
    catalog = SentinelHubCatalog(config=config)
    try:
        results = list(catalog.search(
            S1_CDSE, bbox=AOI_BBOX,
            time=(date_from + "T00:00:00Z", date_to + "T23:59:59Z"),
            fields={"include": ["id", "properties.datetime",
                                "properties.sat:relative_orbit",
                                "properties.sat:orbit_state",
                                "properties.platform"]},
        ))
    except Exception as e:
        msg = str(e)
        if "ACCESS_INSUFFICIENT_PROCESSING_UNITS" in msg or (
                "403" in msg and "Insufficient" in msg):
            raise PUExhaustedError("Processing units exhausted") from e
        raise
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
        msg = str(e)
        if "ACCESS_INSUFFICIENT_PROCESSING_UNITS" in msg or (
                "403" in msg and "Insufficient" in msg):
            global _PU_EXHAUSTED
            _PU_EXHAUSTED = True
            print(f"    fetch error: PU exhausted")
        else:
            print(f"    fetch error: {e}")
        return None
    if not data or data[0] is None:
        return None
    raw = np.array(data[0], dtype=np.float32)
    arr = raw[:, :, 0] if raw.ndim == 3 else raw
    return np.where(arr < 1e-10, 1e-10, arr)

# ═══════════════════════════════════════════════════════════════════════════════
# Background — Change 1: ±24 mo → ±48 mo fallback
# ═══════════════════════════════════════════════════════════════════════════════

def _bg_cache_path(date_str, orbit, window):
    ym = date_str[:7]
    tag = f"w{window//365}"
    return (BG_CACHE / f"bg_o{orbit}_{ym}_{tag}.npy",
            BG_CACHE / f"bg_o{orbit}_{ym}_{tag}_meta.json")

def _v3_cache_path(date_str, orbit):
    """v3 cache uses bg_o{orbit}_{YYYY-MM}.npy (no window suffix)."""
    ym = date_str[:7]
    return BG_CACHE / f"bg_o{orbit}_{ym}.npy"

_PU_EXHAUSTED = False  # set True on first 403; skip all further API calls


def _git_commit_cache(npy_path, meta_path):
    """In CI: commit and push a newly built bg cache so it survives runner eviction."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    import subprocess
    try:
        subprocess.run(["git", "config", "user.email", "actions@github.com"],
                       check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "GitHub Actions"],
                       check=True, capture_output=True)
        subprocess.run(["git", "add", str(npy_path), str(meta_path)],
                       check=True, capture_output=True)
        r = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if r.returncode == 0:
            return  # nothing staged
        subprocess.run(["git", "commit", "-m",
                        f"data: bg cache {npy_path.name} [skip ci]"],
                       check=True, capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
        print(f"    git: committed {npy_path.name}")
    except Exception as e:
        print(f"    WARNING: git commit cache failed: {e}")


def load_or_build_bg(date_str, orbit):
    """
    Try ±24 months; if < BG_MIN_DATES scenes → try ±48 months.
    Returns (bg_array, window_used) or (None, None) if still < BG_MIN_DATES.
    Never returns zero or False — returns None to signal no_background.
    Also checks v3 cache format (no _w2 suffix) before building.
    On PUExhaustedError: logs warning, marks global flag, returns (None, None).
    """
    global _PU_EXHAUSTED
    target = datetime.strptime(date_str, "%Y-%m-%d")
    for window_days in [BG_WINDOW_DAYS_1, BG_WINDOW_DAYS_2]:
        npy, meta_p = _bg_cache_path(date_str, orbit, window_days)
        if npy.exists():
            print(f"    bg cache hit: {npy.name}")
            return np.load(npy), window_days
        # fallback: v3 cache (no window suffix)
        v3 = _v3_cache_path(date_str, orbit)
        if v3.exists():
            print(f"    bg cache hit (v3): {v3.name}")
            return np.load(v3), window_days
        if _PU_EXHAUSTED:
            print(f"    skipping bg build (PU exhausted): {date_str} orbit {orbit}")
            return None, None
        d0 = (target - timedelta(days=window_days)).strftime("%Y-%m-%d")
        d1 = (target + timedelta(days=window_days)).strftime("%Y-%m-%d")
        try:
            scenes = catalog_search(d0, d1)
        except PUExhaustedError:
            print(f"    WARNING: Processing units exhausted on catalog_search — marking all remaining as no_background")
            _PU_EXHAUSTED = True
            return None, None
        orbit_sc = [s for s in scenes if s["rel_orbit"] == orbit]
        if len(orbit_sc) > BG_MAX_SCENES:
            step = len(orbit_sc) / BG_MAX_SCENES
            orbit_sc = [orbit_sc[int(i * step)] for i in range(BG_MAX_SCENES)]
        print(f"    bg build orbit {orbit} ±{window_days//365}yr: {len(orbit_sc)} scenes")
        if len(orbit_sc) < BG_MIN_DATES:
            print(f"    insufficient scenes ({len(orbit_sc)} < {BG_MIN_DATES}) — trying wider window")
            continue
        stack = []
        for s in orbit_sc:
            arr = fetch_linear(s["datetime"])
            if arr is not None:
                stack.append(arr)
        if len(stack) < BG_MIN_DATES:
            print(f"    fetched only {len(stack)} frames — trying wider window")
            continue
        bg = np.percentile(np.stack(stack, axis=0), BG_PCT, axis=0).astype(np.float32)
        BG_CACHE.mkdir(parents=True, exist_ok=True)
        np.save(npy, bg)
        with open(meta_p, "w") as f:
            json.dump({"orbit": orbit, "date": date_str, "n": len(stack),
                       "d0": d0, "d1": d1, "pct": BG_PCT, "window_days": window_days}, f)
        print(f"    bg saved: {npy.name}  ({len(stack)} scenes)")
        _git_commit_cache(npy, meta_p)
        return bg, window_days
    # Both windows failed
    print(f"    no_background: cannot build bg for {date_str} orbit {orbit}")
    return None, None

# ═══════════════════════════════════════════════════════════════════════════════
# Detection (v4 logic, with updated length brackets)
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

def _berth_axis_len_m(berth):
    pts = BERTH_SPEC[berth]
    lat_mid = np.mean([p[1] for p in pts])
    mpdlat = metres_per_deg_lat()
    mpdlon = metres_per_deg_lon(lat_mid)
    total = 0.0
    for i in range(len(pts) - 1):
        dx = (pts[i+1][0] - pts[i][0]) * mpdlon
        dy = (pts[i+1][1] - pts[i][1]) * mpdlat
        total += math.hypot(dx, dy)
    return total

def kernel_blobs(ratio_db, comp_union):
    kernel_mask = comp_union & (ratio_db > KERNEL_RATIO_THR)
    labeled, n = ndimage.label(kernel_mask)
    blobs = []
    for i in range(1, n + 1):
        m = labeled == i
        ys_k, xs_k = np.where(m)
        if len(xs_k) < 2:
            continue
        length_px, _, _ = pca_dims(ys_k, xs_k)
        blobs.append({"length_m": round(length_px * RESOLUTION, 1)})
    return blobs

def _n_ships_from_length(length_m):
    """Change 3: ≤460→1, 460-820→2, >820→3."""
    if length_m <= 460:
        return 1
    elif length_m <= 820:
        return 2
    else:
        return 3

def _blob_center_m(blob, axis_px):
    """Along-axis centroid of a blob in metres from berth start."""
    if axis_px is None:
        return None
    ux, uy, ox, oy = axis_px
    ys, xs = blob["_ys"], blob["_xs"]
    projs = ux * (xs - ox) + uy * (ys - oy)
    center_px = float(projs.mean())
    return round(center_px * RESOLUTION, 1)

def _round50(val):
    return round(val / STATIC_ROUND_M) * STATIC_ROUND_M

def detect_raw_blobs(ratio_db, corridor_masks, meta, date_str):
    """
    First-pass detection: return raw blob list for static-filter accumulation.
    Each blob: date, orbit, berth, center_m, length_m, _comp, _ys, _xs, _proj_min, _proj_max
    """
    gap_px = MERGE_GAP_M / RESOLUTION
    all_blobs = []

    for berth, cmask in corridor_masks.items():
        ratio_in = np.where(cmask, ratio_db, -999)
        blobs_bin = ratio_in > RATIO_THR
        labeled, num = ndimage.label(blobs_bin)
        axis_px = _berth_axis_px(berth)

        raw = []
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
            raw.append({
                "_comp": comp, "_ys": ys_c, "_xs": xs_c,
                "_proj_min": float(projs.min()), "_proj_max": float(projs.max()),
            })

        if not raw:
            continue

        # Merge blobs gap < 80m
        raw.sort(key=lambda b: b["_proj_min"])
        groups = []
        cur = [raw[0]]; cur_max = raw[0]["_proj_max"]
        for blob in raw[1:]:
            gap = blob["_proj_min"] - cur_max
            if gap < gap_px:
                cur.append(blob); cur_max = max(cur_max, blob["_proj_max"])
            else:
                groups.append(cur); cur = [blob]; cur_max = blob["_proj_max"]
        groups.append(cur)

        for group in groups:
            ys_all = np.concatenate([b["_ys"] for b in group])
            xs_all = np.concatenate([b["_xs"] for b in group])
            comp_union = np.zeros(ratio_db.shape, dtype=bool)
            for b in group:
                comp_union |= b["_comp"]
            group_len_px, _, _ = pca_dims(ys_all, xs_all)
            group_len_m = round(group_len_px * RESOLUTION, 1)

            center_m = None
            if axis_px is not None:
                ux, uy, ox, oy = axis_px
                projs = ux * (xs_all - ox) + uy * (ys_all - oy)
                center_m = round(float(projs.mean()) * RESOLUTION, 1)

            proj_min = min(b["_proj_min"] for b in group)
            proj_max = max(b["_proj_max"] for b in group)

            all_blobs.append({
                "date":        date_str,
                "orbit":       meta["rel_orbit"],
                "berth":       berth,
                "center_m":    center_m,
                "length_m":    group_len_m,
                "_comp":       comp_union,
                "_ys":         ys_all,
                "_xs":         xs_all,
                "_proj_min":   proj_min,
                "_proj_max":   proj_max,
            })

    return all_blobs


def compute_static_groups(all_blobs):
    """
    Change 2: group by (berth, round(center_m, 50m)).
    Groups seen on ≥ STATIC_MIN_DATES dates → static.
    Returns set of (berth, rounded_center) that are static.
    """
    group_dates = defaultdict(set)
    for b in all_blobs:
        if b["center_m"] is None:
            continue
        key = (b["berth"], _round50(b["center_m"]))
        group_dates[key].add(b["date"])
    static = {k for k, dates in group_dates.items() if len(dates) >= STATIC_MIN_DATES}
    print(f"[static_filter] {len(static)} static groups from {len(all_blobs)} blobs "
          f"across {len(set(b['date'] for b in all_blobs))} dates")
    return static


def finalize_results(all_blobs, static_groups, ratio_db_cache, meta_cache,
                     corridor_masks, date_str, orbit, run_id):
    """
    Second pass: given blobs for a single date+orbit, apply static filter,
    compute occupied / n_ships, return per-berth result rows.
    """
    rows = []
    gap_px = MERGE_GAP_M / RESOLUTION

    for berth in BERTH_SPEC:
        is_experimental = berth in EXPERIMENTAL_BERTHS
        berth_blobs = [b for b in all_blobs
                       if b["date"] == date_str and b["orbit"] == orbit and b["berth"] == berth]

        # Filter out static structures
        ship_blobs = []
        for b in berth_blobs:
            if b["center_m"] is None:
                ship_blobs.append(b)
                continue
            key = (b["berth"], _round50(b["center_m"]))
            if key not in static_groups:
                ship_blobs.append(b)
            else:
                print(f"    [{berth}] static blob at center={b['center_m']}m removed")

        # Compute occupied and n_ships from surviving blobs
        # (blobs already merged; re-sort by proj_min)
        ship_blobs.sort(key=lambda b: b["_proj_min"])

        lengths_m = []
        n_ships = 0
        any_occupied = False
        all_comp_union = np.zeros(ratio_db_cache.shape, dtype=bool) if ratio_db_cache is not None else None

        for b in ship_blobs:
            if b["length_m"] < MIN_SHIP_GROUP_M:
                continue
            n_ships_grp = _n_ships_from_length(b["length_m"])
            lengths_m.append(b["length_m"])
            n_ships += n_ships_grp

            if all_comp_union is not None:
                all_comp_union |= b["_comp"]

            if ratio_db_cache is not None:
                occ_thr = _occ_len(berth)
                kblobs = kernel_blobs(ratio_db_cache, b["_comp"])
                if any(kb["length_m"] >= occ_thr for kb in kblobs):
                    any_occupied = True

        rows.append({
            "date":         date_str,
            "orbit":        orbit,
            "platform":     meta_cache.get(orbit, {}).get("platform", ""),
            "berth":        berth,
            "occupied":     any_occupied if ship_blobs or berth_blobs else False,
            "n_ships":      n_ships,
            "lengths_m":    sorted(lengths_m, reverse=True),
            "no_background": 0,
            "experimental": 1 if is_experimental else 0,
            "run_id":       run_id,
        })

    return rows


def build_no_bg_rows(date_str, orbit, platform, run_id):
    """Return NaN rows when no background is available."""
    rows = []
    for berth in BERTH_SPEC:
        rows.append({
            "date":         date_str,
            "orbit":        orbit,
            "platform":     platform,
            "berth":        berth,
            "occupied":     None,   # NaN — never zero
            "n_ships":      None,
            "lengths_m":    [],
            "no_background": 1,
            "experimental": 1 if berth in EXPERIMENTAL_BERTHS else 0,
            "run_id":       run_id,
        })
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# PNG rendering (4-panel)
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

def render_png(scene_linear, bg_linear, ratio_db_norm,
               berth, corridor_mask, blobs, result_row,
               out_dir, static_groups):
    r0, r1, c0, c1 = _crop_bounds(corridor_mask, pad_m=300)
    if r1 <= r0 or c1 <= c0:
        return
    eps = 1e-10
    sc_db = 10 * np.log10(np.where(scene_linear[r0:r1,c0:c1] < eps, eps, scene_linear[r0:r1,c0:c1]))
    bg_db = 10 * np.log10(np.where(bg_linear[r0:r1,c0:c1]    < eps, eps, bg_linear[r0:r1,c0:c1]))
    rt_db = ratio_db_norm[r0:r1, c0:c1]
    cmask_crop = corridor_mask[r0:r1, c0:c1]

    date_str = result_row["date"]
    orbit    = result_row["orbit"]
    occupied = result_row["occupied"]
    n_ships  = result_row["n_ships"] or 0

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
            for b in blobs:
                if b["berth"] != berth:
                    continue
                is_static = (b.get("center_m") is not None and
                             (berth, _round50(b["center_m"])) in static_groups)
                ys2 = b["_ys"] - r0; xs2 = b["_xs"] - c0
                valid = (ys2 >= 0) & (ys2 < rt_db.shape[0]) & (xs2 >= 0) & (xs2 < rt_db.shape[1])
                if valid.any():
                    color = "#888888" if is_static else "#FF4444"
                    label = f"STRUCT {b['length_m']:.0f}m" if is_static else f"{b['length_m']:.0f}m"
                    bx0 = max(0, int(xs2[valid].min()) - 2)
                    bx1 = min(rt_db.shape[1], int(xs2[valid].max()) + 2)
                    by0 = max(0, int(ys2[valid].min()) - 2)
                    by1 = min(rt_db.shape[0], int(ys2[valid].max()) + 2)
                    rect = mpatches.Rectangle((bx0, by0), bx1-bx0, by1-by0,
                                              edgecolor=color, facecolor="none", lw=1.5)
                    ax.add_patch(rect)
                    ax.text(bx0, by0 - 2, label, color=color, fontsize=6, va="bottom")
        ax.set_title(title, fontsize=9)
        ax.axis("off")

    exp_tag = "  [EXPERIMENTAL]" if berth in EXPERIMENTAL_BERTHS else ""
    occ_str = "NaN" if occupied is None else ("OCCUPIED" if occupied else "clear")
    fig.suptitle(f"{berth}  {date_str}  orbit {orbit}  "
                 f"n_ships={n_ships}  {occ_str}{exp_tag}",
                 fontsize=11, y=0.99)
    plt.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = out_dir / f"{date_str}_{orbit}_{berth}.png"
    plt.savefig(fname, dpi=130, bbox_inches="tight")
    plt.close()
    return fname

# ═══════════════════════════════════════════════════════════════════════════════
# CSV I/O
# ═══════════════════════════════════════════════════════════════════════════════

RES_FIELDS = ["date", "orbit", "platform", "berth", "occupied",
              "n_ships", "lengths_m", "no_background", "experimental", "run_id"]

BLOB_FIELDS = ["date", "orbit", "berth", "center_m", "length_m"]


def write_results_csv(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RES_FIELDS)
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, "") for k in RES_FIELDS}
            row["lengths_m"] = str(row.get("lengths_m") or "[]")
            if row["occupied"] is None:
                row["occupied"] = ""   # NaN → empty string, never "0"
            w.writerow(row)
    print(f"results CSV: {path} ({len(rows)} rows)")


def append_blobs_csv(blobs, path):
    """Append raw blob data (without numpy arrays) to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if path.exists() else "w"
    with open(path, mode, newline="") as f:
        w = csv.DictWriter(f, fieldnames=BLOB_FIELDS)
        if mode == "w":
            w.writeheader()
        for b in blobs:
            w.writerow({k: b.get(k, "") for k in BLOB_FIELDS})


def load_blobs_csv(path):
    """Load blob CSV back (center_m and length_m as float)."""
    if not path.exists():
        return []
    blobs = []
    with open(path) as f:
        for r in csv.DictReader(f):
            blobs.append({
                "date":     r["date"],
                "orbit":    int(r["orbit"]) if r["orbit"] else None,
                "berth":    r["berth"],
                "center_m": float(r["center_m"]) if r["center_m"] else None,
                "length_m": float(r["length_m"]) if r["length_m"] else 0.0,
                "_comp":    None, "_ys": None, "_xs": None,
                "_proj_min": 0, "_proj_max": 0,
            })
    return blobs


def load_checkpoint(checkpoint_path):
    if not checkpoint_path.exists():
        return set()
    done = set()
    with open(checkpoint_path) as f:
        for r in csv.DictReader(f):
            done.add((r["date"], int(r["orbit"])))
    return done

# ═══════════════════════════════════════════════════════════════════════════════
# Validation comparison
# ═══════════════════════════════════════════════════════════════════════════════

def _occ_bool(val):
    if val is None or val == "" or str(val).lower() == "nan":
        return None
    return str(val).strip().lower() in ("true", "1", "yes", "occupied")

def compare_vs_reference(results, ref_path, label_col_map):
    """
    Compare occupied for sea_island and east_t_head against a reference CSV.
    ref_path: path to reference CSV
    label_col_map: {berth: col_name_in_ref}
    Returns (matches, total, mismatches_list)
    """
    if not ref_path.exists():
        print(f"  [validation] reference not found: {ref_path}")
        return 0, 0, []

    ref_by_key = {}
    with open(ref_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = row.get("date", "")
            orbit = row.get("rel_orbit") or row.get("orbit") or ""
            if not orbit:
                continue
            key = (date, int(orbit))
            ref_by_key[key] = row

    matches = 0; total = 0; mismatches = []
    for r in results:
        if r["berth"] not in label_col_map:
            continue
        if r["berth"] in EXPERIMENTAL_BERTHS:
            continue
        key = (r["date"], int(r["orbit"]))
        if key not in ref_by_key:
            continue
        ref_row = ref_by_key[key]
        col = label_col_map[r["berth"]]
        ref_occ = _occ_bool(ref_row.get(col))
        det_occ = _occ_bool(r.get("occupied"))
        if ref_occ is None or det_occ is None:
            continue
        total += 1
        if ref_occ == det_occ:
            matches += 1
        else:
            mismatches.append({
                "date": r["date"], "orbit": r["orbit"], "berth": r["berth"],
                "detected": det_occ, "reference": ref_occ,
            })
    return matches, total, mismatches


def run_validation(all_results, out_dir, run_id):
    """Compare against v4 results_table and v3 summary."""
    # Reference 1: berth_v4 results_table (labels_round1 fallback)
    labels_round1 = ROOT / "diagnostics" / "berth_v4" / "labels_round1.csv"
    if not labels_round1.exists():
        labels_round1 = ROOT / "diagnostics" / "berth_v4" / "results_table.csv"
    m1, t1, miss1 = compare_vs_reference(all_results, labels_round1, {
        "sea_island": "occupied", "east_t_head": "occupied"
    })

    # Reference 2: v3 blind summary
    v3_summary = ROOT / "diagnostics" / "berth_v3" / "summary.csv"
    m2, t2, miss2 = compare_vs_reference(all_results, v3_summary, {
        "sea_island": "sea_island_occ", "east_t_head": "east_t_occ"
    })

    print(f"\n=== VALIDATION ===")
    print(f"vs labels_round1 ({labels_round1.name}): {m1}/{t1} match")
    print(f"vs v3_blind ({v3_summary.name}):         {m2}/{t2} match")

    combined = m1 + m2
    total    = t1 + t2
    pct = 100.0 * combined / total if total > 0 else 0.0
    print(f"Combined: {combined}/{total} = {pct:.1f}%")

    if miss1 or miss2:
        print("\nMismatches:")
        for m in miss1:
            print(f"  [round1] {m['date']} orbit={m['orbit']} {m['berth']}: "
                  f"det={m['detected']} ref={m['reference']}")
        for m in miss2:
            print(f"  [v3]     {m['date']} orbit={m['orbit']} {m['berth']}: "
                  f"det={m['detected']} ref={m['reference']}")

    # Save mismatch PNGs into a review zip
    mismatch_dates = set((m["date"], m["orbit"]) for m in miss1 + miss2)
    review_pngs = []
    for date_str, orbit in mismatch_dates:
        for berth in BERTH_SPEC:
            if berth in EXPERIMENTAL_BERTHS:
                continue
            p = out_dir / f"{date_str}_{orbit}_{berth}.png"
            if p.exists():
                review_pngs.append(p)

    if review_pngs:
        zip_path = out_dir / "validation_mismatches.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in review_pngs:
                zf.write(p, p.name)
        print(f"Mismatch PNGs: {zip_path} ({len(review_pngs)} files)")

    return pct, total

# ═══════════════════════════════════════════════════════════════════════════════
# Main pipeline — per date+orbit
# ═══════════════════════════════════════════════════════════════════════════════

def process_date_orbit(date_str, orbit, corridor_masks, water_ring_masks,
                       run_id, render=True, out_dir=None):
    """
    Returns (raw_blobs, result_rows, no_scene).
    raw_blobs: list of blob dicts (with numpy arrays for static filter)
    result_rows: list WITHOUT static filter applied yet (placeholder)
    no_scene: True if scene not found
    """
    global _PU_EXHAUSTED
    print(f"\n[{date_str}  orbit={orbit}]")

    if _PU_EXHAUSTED:
        print(f"  PU exhausted — skipping")
        rows = build_no_bg_rows(date_str, orbit, None, run_id)
        return [], rows, False

    try:
        scenes = catalog_search(date_str, date_str)
    except PUExhaustedError:
        _PU_EXHAUSTED = True
        print(f"  PU exhausted on catalog — skipping remaining")
        rows = build_no_bg_rows(date_str, orbit, None, run_id)
        return [], rows, False
    orbit_sc = [s for s in scenes if s["rel_orbit"] == orbit]
    if not orbit_sc:
        print(f"  no scene for orbit {orbit} on {date_str}")
        return [], None, True

    meta = orbit_sc[0]
    print(f"  scene_id={meta['scene_id']}")

    scene_linear = fetch_linear(meta["datetime"])
    if scene_linear is None:
        print("  fetch failed")
        return [], None, False

    bg_linear, window_used = load_or_build_bg(date_str, orbit)
    if bg_linear is None:
        rows = build_no_bg_rows(date_str, orbit, meta["platform"], run_id)
        return [], rows, False

    sc_sm = smooth3x3(scene_linear)
    bg_sm = smooth3x3(bg_linear)
    eps = 1e-10
    ratio_db_full = np.clip(
        10 * np.log10(np.where(sc_sm < eps, eps, sc_sm) /
                      np.where(bg_sm < eps, eps, bg_sm)),
        -30, 30)
    ratio_db_norm = wind_normalise(ratio_db_full, corridor_masks, water_ring_masks)

    # First-pass: collect raw blobs for static filter
    raw_blobs = detect_raw_blobs(ratio_db_norm, corridor_masks, meta, date_str)

    return raw_blobs, (scene_linear, bg_linear, ratio_db_norm, meta), False


def finalize_date_orbit(date_str, orbit, all_run_blobs, static_groups,
                        fetch_data, corridor_masks, run_id, render=True, out_dir=None):
    """
    Given all_run_blobs (complete) and static_groups, produce final result rows.
    fetch_data: (scene_linear, bg_linear, ratio_db_norm, meta) or None for no_bg rows
    """
    if fetch_data is None:
        # Already have no_bg rows from process_date_orbit
        return build_no_bg_rows(date_str, orbit, "", run_id)

    scene_linear, bg_linear, ratio_db_norm, meta = fetch_data

    rows = finalize_results(
        all_run_blobs, static_groups,
        ratio_db_norm, {orbit: meta},
        corridor_masks, date_str, orbit, run_id
    )

    if render and out_dir:
        date_blobs = [b for b in all_run_blobs if b["date"] == date_str and b["orbit"] == orbit]
        for r in rows:
            berth = r["berth"]
            render_png(scene_linear, bg_linear, ratio_db_norm,
                       berth, corridor_masks[berth],
                       date_blobs, r, out_dir, static_groups)

    return rows

# ═══════════════════════════════════════════════════════════════════════════════
# Step 1: Validation run (40 dates)
# ═══════════════════════════════════════════════════════════════════════════════

def step_validation(run_id, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Building corridor geometry ...")
    corridor_polys = {}
    corridor_masks = {}
    for berth, pts in BERTH_SPEC.items():
        poly, ext_pts = build_corridor_polygon(pts, CORRIDOR_HALF_M, CORRIDOR_END_M)
        mask = rasterise_poly(poly)
        corridor_polys[berth] = (poly, ext_pts)
        corridor_masks[berth] = mask
        print(f"  {berth}: {mask.sum()} px")
    water_ring_masks = build_water_ring_masks(corridor_polys)

    print(f"\nPhase 1/3: Collecting raw blobs for {len(VALIDATION_DATES_ORBITS)} date-orbits ...")
    all_blobs = []
    fetch_cache = {}   # (date, orbit) → fetch_data or "no_bg" or "no_scene"
    no_bg_rows_cache = {}

    for date_str, orbit in VALIDATION_DATES_ORBITS:
        raw_blobs, fetch_data_or_rows, no_scene = process_date_orbit(
            date_str, orbit, corridor_masks, water_ring_masks, run_id,
            render=False, out_dir=out_dir
        )
        if no_scene:
            fetch_cache[(date_str, orbit)] = "no_scene"
        elif fetch_data_or_rows is None:
            fetch_cache[(date_str, orbit)] = "no_scene"
        elif isinstance(fetch_data_or_rows, list):
            # no_background rows
            fetch_cache[(date_str, orbit)] = "no_bg"
            no_bg_rows_cache[(date_str, orbit)] = fetch_data_or_rows
        else:
            fetch_cache[(date_str, orbit)] = fetch_data_or_rows
            all_blobs.extend(raw_blobs)

    print(f"\nPhase 2/3: Computing static groups ...")
    static_groups = compute_static_groups(all_blobs)

    # Save static_groups.csv
    static_csv = out_dir / "static_groups.csv"
    with open(static_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["berth", "center_m_rounded", "n_dates"])
        from collections import defaultdict as dd
        gd = dd(set)
        for b in all_blobs:
            if b["center_m"] is None:
                continue
            key = (b["berth"], _round50(b["center_m"]))
            gd[key].add(b["date"])
        for key, dates in sorted(gd.items()):
            w.writerow([key[0], key[1], len(dates)])
    print(f"static_groups.csv: {static_csv}")

    print(f"\nPhase 3/3: Finalizing results + rendering PNGs ...")
    all_results = []
    for date_str, orbit in VALIDATION_DATES_ORBITS:
        fd = fetch_cache.get((date_str, orbit))
        if fd == "no_scene":
            continue
        if fd == "no_bg":
            all_results.extend(no_bg_rows_cache[(date_str, orbit)])
            continue
        rows = finalize_date_orbit(
            date_str, orbit, all_blobs, static_groups,
            fd, corridor_masks, run_id, render=True, out_dir=out_dir
        )
        all_results.extend(rows)

    # Write results CSV
    write_results_csv(all_results, out_dir / "results_table.csv")

    # Accuracy check
    pct, total = run_validation(all_results, out_dir, run_id)

    # ZIP PNGs
    pngs = sorted(out_dir.glob("????-??-??_*_*.png"))
    zip_path = out_dir / "berth_v410_validation.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in pngs:
            zf.write(p, p.name)
    print(f"PNG zip: {zip_path} ({len(pngs)} files)")

    print(f"\n=== STEP 1 DONE: {pct:.1f}% accuracy on {total} comparable cells ===")
    return pct, total

# ═══════════════════════════════════════════════════════════════════════════════
# Step 2: Full history run with checkpoint
# ═══════════════════════════════════════════════════════════════════════════════

def get_all_history_dates():
    """Get all Sentinel-1 scene dates for Kharg from catalog, both orbits."""
    catalog = SentinelHubCatalog(config=config)
    results = list(catalog.search(
        S1_CDSE, bbox=AOI_BBOX,
        time=(HISTORY_START + "T00:00:00Z", HISTORY_END + "T23:59:59Z"),
        fields={"include": ["id", "properties.datetime",
                            "properties.sat:relative_orbit",
                            "properties.sat:orbit_state",
                            "properties.platform"]},
    ))
    seen = set()
    pairs = []
    for r in results:
        p = r.get("properties", {})
        date_str = p.get("datetime", "")[:10]
        orbit = p.get("sat:relative_orbit")
        if orbit in (35, 101) and (date_str, orbit) not in seen:
            seen.add((date_str, orbit))
            pairs.append((date_str, orbit))
    pairs.sort()
    print(f"History: {len(pairs)} unique date-orbit pairs from catalog")
    return pairs


def step_history(run_id, hist_dir, resume=False, commit_every=20):
    hist_dir.mkdir(parents=True, exist_ok=True)

    blob_csv      = hist_dir / "checkpoint_blobs.csv"
    result_csv    = hist_dir / "checkpoint_results.csv"
    checkpoint_csv = hist_dir / "checkpoint_done.csv"

    print("Building corridor geometry ...")
    corridor_polys = {}
    corridor_masks = {}
    for berth, pts in BERTH_SPEC.items():
        poly, ext_pts = build_corridor_polygon(pts, CORRIDOR_HALF_M, CORRIDOR_END_M)
        mask = rasterise_poly(poly)
        corridor_polys[berth] = (poly, ext_pts)
        corridor_masks[berth] = mask
    water_ring_masks = build_water_ring_masks(corridor_polys)

    # Load existing checkpoint
    done_set = load_checkpoint(checkpoint_csv) if resume else set()
    if done_set:
        print(f"Resuming: {len(done_set)} date-orbit pairs already done")

    # Load existing blobs (for static filter from prior runs)
    existing_blobs = load_blobs_csv(blob_csv) if resume else []
    print(f"Existing blobs loaded: {len(existing_blobs)}")

    # Get all history date-orbit pairs
    all_pairs = get_all_history_dates()
    todo = [p for p in all_pairs if p not in done_set]
    print(f"Total pairs: {len(all_pairs)}, todo: {len(todo)}")

    all_blobs = list(existing_blobs)
    all_result_rows = []

    # We do a two-pass approach:
    # Pass 1: collect blobs from all todo dates
    # Then compute static groups from ALL blobs (existing + new)
    # Pass 2: finalize results

    print(f"\nPass 1: Collecting blobs for {len(todo)} date-orbit pairs ...")
    fetch_cache = {}   # (date, orbit) → fetch_data or "no_bg"/"no_scene"
    no_bg_rows_cache = {}
    new_blobs = []
    new_done = []

    for idx, (date_str, orbit) in enumerate(todo):
        raw_blobs, fetch_data_or_rows, no_scene = process_date_orbit(
            date_str, orbit, corridor_masks, water_ring_masks, run_id,
            render=False
        )
        if no_scene:
            fetch_cache[(date_str, orbit)] = "no_scene"
        elif fetch_data_or_rows is None:
            fetch_cache[(date_str, orbit)] = "no_scene"
        elif isinstance(fetch_data_or_rows, list):
            fetch_cache[(date_str, orbit)] = "no_bg"
            no_bg_rows_cache[(date_str, orbit)] = fetch_data_or_rows
        else:
            fetch_cache[(date_str, orbit)] = fetch_data_or_rows
            new_blobs.extend(raw_blobs)

        new_done.append((date_str, orbit))

        # Checkpoint every commit_every dates
        if len(new_done) % commit_every == 0:
            append_blobs_csv(new_blobs[-len(raw_blobs)*len(BERTH_SPEC)*2:], blob_csv)
            with open(checkpoint_csv, "a", newline="") as f:
                w = csv.writer(f)
                if not checkpoint_csv.stat().st_size if checkpoint_csv.exists() else True:
                    w.writerow(["date", "orbit"])
                for d, o in new_done[-commit_every:]:
                    w.writerow([d, o])
            print(f"  checkpoint: {len(new_done)} pairs processed")

    # Save any remaining blobs
    append_blobs_csv(new_blobs, blob_csv)
    all_blobs.extend(new_blobs)

    print(f"\nTotal blobs collected: {len(all_blobs)}")

    # Compute static groups from ALL blobs
    print("\nComputing static groups (all history) ...")
    static_groups = compute_static_groups(all_blobs)

    # Save static_groups.csv
    static_csv = hist_dir / "static_groups.csv"
    from collections import defaultdict as dd
    gd = dd(set)
    for b in all_blobs:
        if b["center_m"] is None:
            continue
        key = (b["berth"], _round50(b["center_m"]))
        gd[key].add(b["date"])
    with open(static_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["berth", "center_m_rounded", "n_dates"])
        for key, dates in sorted(gd.items()):
            w.writerow([key[0], key[1], len(dates)])
    print(f"static_groups.csv: {static_csv}")

    print(f"\nPass 2: Finalizing results for {len(todo)} date-orbit pairs ...")
    png_dates_2026 = set()
    for date_str, orbit in todo:
        fd = fetch_cache.get((date_str, orbit))
        if fd == "no_scene":
            continue
        if fd == "no_bg":
            all_result_rows.extend(no_bg_rows_cache[(date_str, orbit)])
            continue
        render = date_str.startswith("2026")
        rows = finalize_date_orbit(
            date_str, orbit, all_blobs, static_groups,
            fd, corridor_masks, run_id,
            render=render, out_dir=hist_dir if render else None
        )
        all_result_rows.extend(rows)
        if render:
            png_dates_2026.add(date_str)

    # Also render 30 random dates (seed=11) from non-2026
    import random
    rng = random.Random(11)
    non_2026 = [(d, o) for d, o in todo if not d.startswith("2026")]
    sample30 = rng.sample(non_2026, min(30, len(non_2026)))
    for date_str, orbit in sample30:
        fd = fetch_cache.get((date_str, orbit))
        if fd not in ("no_scene", "no_bg", None) and fd is not None:
            finalize_date_orbit(
                date_str, orbit, all_blobs, static_groups,
                fd, corridor_masks, run_id,
                render=True, out_dir=hist_dir
            )

    # Write final results
    final_csv = hist_dir / "berth_history.csv"
    write_results_csv(all_result_rows, final_csv)

    # Print summary: dates by year × orbit, no_background fraction, occupied fraction
    _print_history_summary(all_result_rows)

    # ZIP PNGs
    pngs = sorted(hist_dir.glob("????-??-??_*_*.png"))
    zip_path = hist_dir / "berth_history_pngs.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in pngs:
            zf.write(p, p.name)
    print(f"PNG zip: {zip_path} ({len(pngs)} files)")
    print(f"\n=== STEP 2 DONE: {len(all_result_rows)} result rows ===")


def _print_history_summary(rows):
    from collections import defaultdict
    year_orbit = defaultdict(int)
    year_orbit_nobg = defaultdict(int)
    berth_year_occ = defaultdict(lambda: [0, 0])  # [occupied, total]

    for r in rows:
        if r.get("experimental"):
            continue
        year = r["date"][:4]
        orbit = r["orbit"]
        yok = (year, orbit)
        year_orbit[yok] += 1
        if r.get("no_background"):
            year_orbit_nobg[yok] += 1
        occ = r.get("occupied")
        if occ is not None and occ != "":
            by = (r["berth"], year)
            berth_year_occ[by][1] += 1
            if str(occ).lower() == "true" or occ is True:
                berth_year_occ[by][0] += 1

    print("\n=== SUMMARY: dates by year × orbit ===")
    print(f"{'year':>5} {'orbit':>6} {'n_rows':>7} {'no_bg':>7} {'no_bg%':>7}")
    for key in sorted(year_orbit.keys()):
        n = year_orbit[key]
        nb = year_orbit_nobg[key]
        print(f"{key[0]:>5} {key[1]:>6} {n:>7} {nb:>7} {100*nb/n:.1f}%")

    print("\n=== SUMMARY: occupied fraction by berth × year ===")
    print(f"{'berth':<20} {'year':>5} {'occ/total':>12} {'occ%':>7}")
    for key in sorted(berth_year_occ.keys()):
        occ, tot = berth_year_occ[key]
        pct = 100.0 * occ / tot if tot > 0 else 0.0
        print(f"{key[0]:<20} {key[1]:>5} {occ:>5}/{tot:<5} {pct:>7.1f}%")

# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=["validation", "history"], default="validation")
    parser.add_argument("--resume", action="store_true",
                        help="Skip date-orbit pairs in checkpoint_done.csv")
    parser.add_argument("--commit-every", type=int, default=20,
                        help="Commit checkpoint every N dates (history step)")
    args = parser.parse_args()

    run_id = os.environ.get("GITHUB_RUN_ID", "local")

    if args.step == "validation":
        pct, total = step_validation(run_id, OUT_DIR)
        if total == 0:
            print("WARNING: no comparable cells — cannot determine accuracy")
        elif pct >= 90.0:
            print(f"\n✓ Accuracy {pct:.1f}% ≥ 90% — PASS: proceed to history run")
            sys.exit(0)
        else:
            print(f"\n✗ Accuracy {pct:.1f}% < 90% — STOP")
            sys.exit(2)

    elif args.step == "history":
        step_history(run_id, HIST_DIR, resume=args.resume, commit_every=args.commit_every)


if __name__ == "__main__":
    main()
