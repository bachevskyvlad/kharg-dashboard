"""
ТЗ-7.8  Axial ship detector — Kharg Island berths
Outputs: diagnostics/berth_axis/
"""

import csv, json, math, os, random, sys, zipfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from scipy.ndimage import map_coordinates

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.colors import Normalize
except ImportError:
    print("FATAL: matplotlib missing"); sys.exit(1)

try:
    from sentinelhub import (
        SHConfig, BBox, CRS, DataCollection, SentinelHubCatalog,
        SentinelHubRequest, MimeType, MosaickingOrder,
        bbox_to_dimensions,
    )
except ImportError:
    print("FATAL: sentinelhub missing"); sys.exit(1)

try:
    from shapely.geometry import LineString, Point, mapping
    from shapely.ops import nearest_points
except ImportError:
    print("FATAL: shapely missing"); sys.exit(1)

# ─────────────────────────── config ──────────────────────────────────────────

CLIENT_ID     = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
if not CLIENT_ID or not CLIENT_SECRET:
    print("FATAL: CLIENT_ID / CLIENT_SECRET empty"); sys.exit(1)

OUT_DIR = Path("diagnostics/berth_axis")
OUT_DIR.mkdir(parents=True, exist_ok=True)

RESOLUTION_M = 10          # metres per pixel requested from SH
AXIS_BUFFER_M = 150        # AOI = axis bbox + this buffer
MIN_LEN_M = 150            # minimum ship length
MIN_LEN_SOUTHEAST = 100    # shorter threshold for southeast_pier
BIN_M = 10                 # along-axis bin width
GAP_FILL_M = 30            # merge gaps ≤ this
SIDE_A_D = (30, 110)       # d range side A (right of axis)
SIDE_B_D = (-110, -30)     # d range side B (left of axis)
SEA_D_MIN = 130            # |d| > this → sea pixels
SEA_EXCESS_DB = 8          # threshold = sea_median + this
AXIS_SHIFT_MAX = 20        # refinement search ±metres
AXIS_STRUCT_HALF = 25      # |d| < this = structure zone (excluded from sea, used for shift)
RANDOM_SEED = 17

# ─────────────────────────── berth definitions ───────────────────────────────

BERTHS = {
    "sea_island": {
        # axis from berth_detect_history.py BERTH_SPEC
        "axis_lonlat": [
            (50.28316, 29.23318),
            (50.28816, 29.22433),
        ],
        "min_len_m": MIN_LEN_M,
    },
    "east_t_head": {
        "axis_lonlat": [
            (50.33833, 29.23812),
            (50.34054, 29.22189),
        ],
        "min_len_m": MIN_LEN_M,
    },
    "southeast_pier": {
        "axis_lonlat": [
            (50.33646, 29.21764),
            (50.33591, 29.21424),
            (50.33522, 29.21264),
        ],
        "min_len_m": MIN_LEN_SOUTHEAST,
    },
}

# 20 previously-labelled (date, orbit) pairs that must NOT be reused
OLD_DATES_ORBITS = [
    ("2019-01-15", 35),  ("2019-03-08", 101), ("2019-06-24", 101),
    ("2020-01-10", 35),  ("2020-03-14", 101), ("2020-06-14", 35),
    ("2020-06-30", 101), ("2021-01-08", 101), ("2021-04-10", 35),
    ("2021-06-25", 101), ("2022-01-15", 101), ("2022-06-16", 35),
    ("2023-07-09", 101), ("2024-01-13", 35),  ("2024-06-29", 35),
    ("2024-07-03", 101), ("2025-01-07", 35),  ("2025-01-11", 101),
    ("2026-04-14", 35),  ("2026-04-18", 101),
]
OLD_SET = set(OLD_DATES_ORBITS)

KHARG_BBOX_LONLAT = (50.26, 29.17, 50.40, 29.31)   # catalog search AOI (Kharg Island)

# Custom DataCollection pointing at CDSE endpoint
S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_axisv5", service_url="https://sh.dataspace.copernicus.eu"
)

# ─────────────────────────── SH config ───────────────────────────────────────

def make_config():
    cfg = SHConfig()
    cfg.sh_client_id     = CLIENT_ID
    cfg.sh_client_secret = CLIENT_SECRET
    cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"
    cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    return cfg

# ─────────────────────────── geo helpers ─────────────────────────────────────

EARTH_R = 6_371_000.0

def lonlat_to_metric(lon, lat, ref_lon, ref_lat):
    """Return (east_m, north_m) relative to ref."""
    east  = math.radians(lon - ref_lon) * EARTH_R * math.cos(math.radians(ref_lat))
    north = math.radians(lat - ref_lat) * EARTH_R
    return east, north

def metric_to_lonlat(east_m, north_m, ref_lon, ref_lat):
    lon = ref_lon + math.degrees(east_m / (EARTH_R * math.cos(math.radians(ref_lat))))
    lat = ref_lat + math.degrees(north_m / EARTH_R)
    return lon, lat

def axis_bbox_lonlat(axis_lonlat, buffer_m):
    lons = [p[0] for p in axis_lonlat]
    lats = [p[1] for p in axis_lonlat]
    mid_lat = (min(lats) + max(lats)) / 2
    dlon = buffer_m / (EARTH_R * math.cos(math.radians(mid_lat))) * 180 / math.pi
    dlat = buffer_m / EARTH_R * 180 / math.pi
    return (min(lons) - dlon, min(lats) - dlat,
            max(lons) + dlon, max(lats) + dlat)

# ─────────────────────────── AxisCoords ──────────────────────────────────────

class AxisCoords:
    """
    Polyline axis in metric space.
    Converts (east_m, north_m) relative to ref_lon/lat ↔ (s, d).
    s = arc-length from axis start (m), d = signed perp distance (m).
    d > 0 = right of travel direction (east/south depending on bearing).
    right_perp(ux, uy) = (uy, -ux)  in (east, north) space.
    """

    def __init__(self, axis_lonlat):
        self.axis_lonlat = list(axis_lonlat)
        # reference point = first axis point
        self.ref_lon = axis_lonlat[0][0]
        self.ref_lat = axis_lonlat[0][1]
        # build segments in metric
        pts_m = [lonlat_to_metric(lon, lat, self.ref_lon, self.ref_lat)
                 for lon, lat in axis_lonlat]
        self.segments = []   # (x0, y0, ux, uy, L)
        self.cumlen   = [0.0]
        for i in range(len(pts_m) - 1):
            x0, y0 = pts_m[i]
            x1, y1 = pts_m[i + 1]
            dx, dy = x1 - x0, y1 - y0
            L = math.sqrt(dx*dx + dy*dy)
            self.segments.append((x0, y0, dx/L, dy/L, L))
            self.cumlen.append(self.cumlen[-1] + L)
        self.total_len = self.cumlen[-1]

    def metric_to_sd(self, east_m, north_m):
        """Nearest-segment projection → (s, d)."""
        best_s, best_d, best_dist2 = None, None, 1e18
        for i, (x0, y0, ux, uy, L) in enumerate(self.segments):
            dx = east_m  - x0
            dy = north_m - y0
            t  = dx*ux + dy*uy          # projection onto segment
            t  = max(0.0, min(L, t))
            px = x0 + t*ux
            py = y0 + t*uy
            dist2 = (east_m - px)**2 + (north_m - py)**2
            if dist2 < best_dist2:
                best_dist2 = dist2
                best_s = self.cumlen[i] + t
                # signed perpendicular: d = cross(segment_dir, vector_to_point)
                # right_perp(ux,uy) = (uy,-ux); d = (uy,-ux)·(dx,dy) = uy*dx - ux*dy
                best_d = uy*(east_m - px) - ux*(north_m - py) + \
                         uy*(px - x0 - t*ux) - ux*(py - y0 - t*uy)
                # simplify: d = uy*(east_m-x0-t*ux) - ux*(north_m-y0-t*uy)
                # = uy*dx_full - ux*dy_full  where dx_full from (x0,y0)
                dx_full = east_m - x0
                dy_full = north_m - y0
                best_d = uy*dx_full - ux*dy_full
        return best_s, best_d

    def sd_to_metric(self, s, d):
        """Inverse: (s, d) → (east_m, north_m)."""
        s = max(0.0, min(self.total_len, s))
        for i, (x0, y0, ux, uy, L) in enumerate(self.segments):
            s0 = self.cumlen[i]
            s1 = self.cumlen[i + 1]
            if s <= s1 or i == len(self.segments) - 1:
                t = s - s0
                east_m  = x0 + t*ux + d*uy
                north_m = y0 + t*uy - d*ux
                return east_m, north_m
        # fallback
        x0, y0, ux, uy, L = self.segments[-1]
        t = s - self.cumlen[-1]
        return x0 + t*ux + d*uy, y0 + t*uy - d*ux

    def pixel_sd(self, pixel_lons, pixel_lats):
        """Vectorised: arrays of lons/lats → (s_arr, d_arr)."""
        s_arr = np.zeros(pixel_lons.shape)
        d_arr = np.zeros(pixel_lons.shape)
        flat_lons = pixel_lons.ravel()
        flat_lats = pixel_lats.ravel()
        for k in range(len(flat_lons)):
            em, nm = lonlat_to_metric(flat_lons[k], flat_lats[k],
                                      self.ref_lon, self.ref_lat)
            s_arr.ravel()[k], d_arr.ravel()[k] = self.metric_to_sd(em, nm)
        return s_arr, d_arr

    def shifted(self, delta_d_m):
        """Return new AxisCoords with axis shifted perpendicular by delta_d_m."""
        new_lonlat = []
        for i, (x0, y0, ux, uy, L) in enumerate(self.segments):
            if i == 0:
                east_m  = x0 + delta_d_m * uy
                north_m = y0 - delta_d_m * ux
                new_lonlat.append(metric_to_lonlat(east_m, north_m,
                                                   self.ref_lon, self.ref_lat))
            east_m  = x0 + L*ux + delta_d_m * uy
            north_m = y0 + L*uy - delta_d_m * ux
            new_lonlat.append(metric_to_lonlat(east_m, north_m,
                                               self.ref_lon, self.ref_lat))
        return AxisCoords(new_lonlat)

# ─────────────────────────── SH catalog helpers ───────────────────────────────

def catalog_search(cfg, bbox_lonlat, date_from, date_to, orbit):
    """Search catalog for a given orbit number; filter post-hoc on sat:relative_orbit."""
    catalog = SentinelHubCatalog(config=cfg)
    bbox = BBox(bbox=bbox_lonlat, crs=CRS.WGS84)
    results = list(catalog.search(
        S1_CDSE,
        bbox=bbox,
        time=(date_from + "T00:00:00Z", date_to + "T23:59:59Z"),
        fields={"include": ["id", "properties.datetime",
                             "properties.sat:relative_orbit",
                             "properties.sat:orbit_state",
                             "properties.platform"]},
    ))
    out = []
    seen = {}
    for item in results:
        p = item.get("properties", {})
        rel_orb = p.get("sat:relative_orbit")
        if rel_orb != orbit:
            continue
        dt_str = p.get("datetime", "")[:10]
        plat   = p.get("platform", "unknown")
        if dt_str and dt_str not in seen:
            seen[dt_str] = True
            out.append((dt_str, orbit, plat))
    return sorted(out)

def select_new_dates(cfg):
    """
    For each (orbit, year) ∈ {35,101} × 2017..2026:
        pick 1 date at random (seed=17) that is NOT in OLD_SET.
    Returns list of (date_str, orbit, platform).
    """
    rng = random.Random(RANDOM_SEED)
    selected = []
    all_scenes = {}
    # search full range per orbit
    for orbit in (35, 101):
        scenes = catalog_search(cfg, KHARG_BBOX_LONLAT,
                                "2017-01-01", "2026-12-31", orbit)
        all_scenes[orbit] = scenes
        print(f"  orbit {orbit}: {len(scenes)} catalog dates")

    for orbit in (35, 101):
        by_year = {}
        for dt_str, orb, plat in all_scenes[orbit]:
            yr = int(dt_str[:4])
            by_year.setdefault(yr, []).append((dt_str, orb, plat))
        for yr in range(2017, 2027):
            candidates = [t for t in by_year.get(yr, [])
                          if (t[0], t[1]) not in OLD_SET]
            if not candidates:
                print(f"  WARNING: no new candidate for orbit={orbit} year={yr}")
                continue
            rng.shuffle(candidates)
            chosen = candidates[0]
            selected.append(chosen)
            print(f"  selected orbit={orbit} year={yr}: {chosen[0]}")

    return selected

# ─────────────────────────── scene fetch ─────────────────────────────────────

EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input:  [{bands:["VV"]}],
    output: {bands:1, sampleType:"FLOAT32"}
  };
}
function evaluatePixel(s) { return [Math.max(s.VV, 1e-10)]; }
"""

def fetch_scene(cfg, bbox_lonlat, date_str, orbit):
    """Return (arr_linear, transform_dict) or (None, None) on failure."""
    bbox = BBox(bbox=bbox_lonlat, crs=CRS.WGS84)
    size = bbox_to_dimensions(bbox, resolution=RESOLUTION_M)
    t0 = date_str
    t1 = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(t0, t1),
            other_args={
                "dataFilter": {
                    "relativeOrbitNumber": orbit,
                    "orbitDirection": "DESCENDING",
                    "mosaickingOrder": "mostRecent",
                },
            },
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox,
        size=size,
        config=cfg,
    )
    try:
        data = req.get_data()
    except Exception as e:
        print(f"  fetch error {date_str} orbit={orbit}: {e}")
        return None, None
    arr = data[0].squeeze()
    if arr.ndim != 2 or arr.max() == 0:
        print(f"  empty scene {date_str} orbit={orbit}")
        return None, None
    # build pixel→lonlat transform
    min_lon, min_lat, max_lon, max_lat = bbox_lonlat
    h, w = arr.shape
    transform = dict(min_lon=min_lon, min_lat=min_lat,
                     max_lon=max_lon, max_lat=max_lat,
                     h=h, w=w)
    return arr, transform

def pixel_lonlat(transform):
    """Return (lon_arr, lat_arr) for all pixels (shape h×w)."""
    t = transform
    rows = np.arange(t["h"])
    cols = np.arange(t["w"])
    # SH returns top-left origin, lat decreases with row
    lats = t["max_lat"] - (rows + 0.5) / t["h"] * (t["max_lat"] - t["min_lat"])
    lons = t["min_lon"] + (cols + 0.5) / t["w"] * (t["max_lon"] - t["min_lon"])
    lon_arr, lat_arr = np.meshgrid(lons, lats)
    return lon_arr, lat_arr

# ─────────────────────────── linear → dB ─────────────────────────────────────

def to_db(arr_linear):
    out = np.full_like(arr_linear, np.nan, dtype=np.float32)
    pos = arr_linear > 0
    out[pos] = 10 * np.log10(arr_linear[pos])
    return out

# ─────────────────────────── axis refinement ─────────────────────────────────

def refine_axis(axis_coords, all_db_scenes):
    """
    Scan delta_d ∈ [-AXIS_SHIFT_MAX, +AXIS_SHIFT_MAX] step 1m.
    score = mean(db[|d - delta_d| < AXIS_STRUCT_HALF]).
    Return best delta_d.
    """
    scores = {}
    for delta_d in range(-AXIS_SHIFT_MAX, AXIS_SHIFT_MAX + 1):
        vals = []
        for (arr_db, transform) in all_db_scenes:
            lon_arr, lat_arr = pixel_lonlat(transform)
            s_arr, d_arr = axis_coords.pixel_sd(lon_arr, lat_arr)
            mask = np.abs(d_arr - delta_d) < AXIS_STRUCT_HALF
            mask &= np.isfinite(arr_db)
            if mask.any():
                vals.append(arr_db[mask].mean())
        scores[delta_d] = np.mean(vals) if vals else -99.0

    best_delta = max(scores, key=scores.get)
    print(f"    axis refinement: best delta_d={best_delta:+.0f}m "
          f"(score={scores[best_delta]:.2f} dB)")
    return best_delta

# ─────────────────────────── detection ───────────────────────────────────────

def classify_ship(length_m):
    if length_m < 200:
        return "Panamax"
    elif length_m < 270:
        return "AfraSuez"
    elif length_m < 360:
        return "VLCC"
    else:
        return "two_or_ULCC"

def detect_on_profile(P, s_bins_m, threshold, min_len_m, side_label,
                       date_str, orbit, platform, berth):
    """
    P[j] = mean dB in bin j; s_bins_m[j] = bin centre.
    Returns list of ship dicts.
    """
    above = np.where(np.isfinite(P) & (P > threshold))[0]
    if len(above) == 0:
        return []

    # merge gaps ≤ GAP_FILL_M / BIN_M bins
    gap_bins = int(GAP_FILL_M / BIN_M)
    groups = []
    cur = [above[0]]
    for idx in above[1:]:
        if idx - cur[-1] <= gap_bins + 1:
            cur.append(idx)
        else:
            groups.append(cur)
            cur = [idx]
    groups.append(cur)

    ships = []
    for g in groups:
        s_start = s_bins_m[g[0]]  - BIN_M / 2
        s_end   = s_bins_m[g[-1]] + BIN_M / 2
        length  = s_end - s_start
        if length < min_len_m:
            continue
        mean_exc = float(np.nanmean(P[g]) - threshold)
        ships.append({
            "date":          date_str,
            "orbit":         orbit,
            "platform":      platform,
            "berth":         berth,
            "side":          side_label,
            "s_start_m":     round(s_start, 1),
            "s_end_m":       round(s_end,   1),
            "s_center_m":    round((s_start + s_end) / 2, 1),
            "length_m":      round(length, 1),
            "mean_excess_dB": round(mean_exc, 2),
            "threshold_dB":  round(threshold, 2),
            "class":         classify_ship(length),
        })
    return ships

def detect_ships(arr_db, s_arr, d_arr, min_len_m, date_str, orbit, platform, berth):
    """Full detection pipeline for one scene × berth."""
    # sea mask
    sea_mask = (np.abs(d_arr) > SEA_D_MIN) & np.isfinite(arr_db)
    if sea_mask.sum() < 10:
        print(f"    WARNING: too few sea pixels for {berth} on {date_str}")
        return [], None, None, None

    sea_median = float(np.nanmedian(arr_db[sea_mask]))
    threshold  = sea_median + SEA_EXCESS_DB

    # build s bins
    s_min  = float(np.nanmin(s_arr))
    s_max  = float(np.nanmax(s_arr))
    n_bins = max(1, int((s_max - s_min) / BIN_M))
    s_edges = np.linspace(s_min, s_max, n_bins + 1)
    s_centres = (s_edges[:-1] + s_edges[1:]) / 2

    P_A = np.full(n_bins, np.nan)
    P_B = np.full(n_bins, np.nan)

    for j in range(n_bins):
        in_bin = (d_arr >= -SEA_D_MIN) & (s_arr >= s_edges[j]) & (s_arr < s_edges[j+1])
        for P, (d_lo, d_hi) in [(P_A, SIDE_A_D), (P_B, SIDE_B_D)]:
            m = in_bin & (d_arr >= d_lo) & (d_arr <= d_hi) & np.isfinite(arr_db)
            if m.any():
                P[j] = float(np.nanmean(arr_db[m]))

    ships  = detect_on_profile(P_A, s_centres, threshold, min_len_m,
                                "A", date_str, orbit, platform, berth)
    ships += detect_on_profile(P_B, s_centres, threshold, min_len_m,
                                "B", date_str, orbit, platform, berth)
    return ships, P_A, P_B, (s_centres, threshold, sea_median)

# ─────────────────────────── warp to (s,d) space ─────────────────────────────

def warp_to_sd(arr_db, s_arr, d_arr, s_res=10, d_res=10,
               d_lo=-150, d_hi=150):
    """
    Warp arr_db to a regular (s, d) grid via inverse map.
    Returns warped image (H_out × W_out) and extent tuple.
    """
    s_min = float(np.nanmin(s_arr)); s_max = float(np.nanmax(s_arr))
    n_s = max(1, int((s_max - s_min) / s_res))
    n_d = max(1, int((d_hi - d_lo)   / d_res))

    s_grid = np.linspace(s_min, s_max, n_s)
    d_grid = np.linspace(d_lo,  d_hi,  n_d)
    SS, DD = np.meshgrid(s_grid, d_grid)

    # For each output pixel, find source pixel via nearest-neighbour in (s,d) space.
    # Cheap implementation: bin s_arr/d_arr into output grid coords then map.
    h, w = arr_db.shape
    s_arr_flat = s_arr.ravel()
    d_arr_flat = d_arr.ravel()

    # source pixel coords in output grid space
    s_src = (s_arr_flat - s_min) / (s_max - s_min + 1e-9) * (n_s - 1)
    d_src = (d_arr_flat - d_lo)  / (d_hi  - d_lo  + 1e-9) * (n_d - 1)

    # output grid coords → source image coords via inverse scatter
    out_s = ((SS - s_min) / (s_max - s_min + 1e-9) * (n_s - 1)).ravel()
    out_d = ((DD - d_lo)  / (d_hi  - d_lo  + 1e-9) * (n_d - 1)).ravel()

    # row,col in source image corresponding to output (s,d) via nearest bin
    # Build a lookup by accumulating source pixel locs into output bins
    out_img = np.full((n_d, n_s), np.nan, dtype=np.float32)

    # bin each source pixel into nearest output cell
    si_idx = np.clip(np.round(s_src).astype(int), 0, n_s - 1)
    di_idx = np.clip(np.round(d_src).astype(int), 0, n_d - 1)
    rows_src = np.arange(h * w) // w
    cols_src = np.arange(h * w) %  w
    # write (last write wins — doesn't matter for display)
    valid = np.isfinite(arr_db.ravel())
    out_img[di_idx[valid], si_idx[valid]] = arr_db.ravel()[valid]

    return out_img, (s_min, s_max, d_lo, d_hi)

# ─────────────────────────── PNG rendering ───────────────────────────────────

def render_png(arr_db, s_arr, d_arr, P_A, P_B, stats,
               ships, date_str, orbit, berth, out_path):
    s_centres, threshold, sea_median = stats
    warped, (s_min, s_max, d_lo, d_hi) = warp_to_sd(arr_db, s_arr, d_arr)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(f"{berth}  |  {date_str}  orbit={orbit}", fontsize=11)

    # --- left: warped SAR ---
    ax = axes[0]
    vmin = np.nanpercentile(arr_db, 2)
    vmax = np.nanpercentile(arr_db, 98)
    ax.imshow(warped, extent=[s_min, s_max, d_lo, d_hi],
              origin="lower", aspect="auto", cmap="gray",
              vmin=vmin, vmax=vmax, interpolation="nearest")
    ax.axhline(SIDE_A_D[0], color="cyan", lw=0.7, ls="--")
    ax.axhline(SIDE_A_D[1], color="cyan", lw=0.7, ls="--")
    ax.axhline(SIDE_B_D[0], color="orange", lw=0.7, ls="--")
    ax.axhline(SIDE_B_D[1], color="orange", lw=0.7, ls="--")
    ax.axhline(0,            color="white",  lw=0.7)
    ax.axhline(SEA_D_MIN,    color="lime",   lw=0.5, ls=":")
    ax.axhline(-SEA_D_MIN,   color="lime",   lw=0.5, ls=":")
    for i, sh in enumerate(ships):
        col  = "cyan" if sh["side"] == "A" else "orange"
        d_c  = (SIDE_A_D[0] + SIDE_A_D[1]) / 2 if sh["side"] == "A" \
               else (SIDE_B_D[0] + SIDE_B_D[1]) / 2
        ax.annotate(f"{i+1}", xy=(sh["s_center_m"], d_c),
                    color=col, fontsize=7, ha="center", va="center",
                    bbox=dict(boxstyle="round,pad=0.1", fc="black", alpha=0.5))
        ax.hlines(d_c, sh["s_start_m"], sh["s_end_m"], colors=col, lw=2)
    ax.set_xlabel("s (m along axis)")
    ax.set_ylabel("d (m perp, right=+)")
    ax.set_title("SAR warped to (s,d)")

    # --- right: profiles ---
    ax2 = axes[1]
    ax2.plot(s_centres, P_A, color="cyan",   lw=1.2, label="Side A")
    ax2.plot(s_centres, P_B, color="orange", lw=1.2, label="Side B")
    ax2.axhline(threshold,   color="red",   lw=0.8, ls="--",
                label=f"T={threshold:.1f} dB")
    ax2.axhline(sea_median,  color="gray",  lw=0.6, ls=":",
                label=f"sea={sea_median:.1f} dB")
    for i, sh in enumerate(ships):
        col = "cyan" if sh["side"] == "A" else "orange"
        ax2.axvspan(sh["s_start_m"], sh["s_end_m"],
                    alpha=0.2, color=col)
        ax2.text(sh["s_center_m"], threshold + 0.3, str(i+1),
                 color=col, fontsize=7, ha="center")
    ax2.set_xlabel("s (m along axis)")
    ax2.set_ylabel("mean dB")
    ax2.set_title(f"Axial profiles  |  {len(ships)} ship(s)")
    ax2.legend(fontsize=7, loc="upper right")
    ax2.grid(True, lw=0.3, alpha=0.5)

    plt.tight_layout()
    plt.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)

# ─────────────────────────── geojson ─────────────────────────────────────────

def write_axes_geojson(berth_axes, path):
    features = []
    for berth, axis_coords in berth_axes.items():
        coords = [[lon, lat] for lon, lat in axis_coords.axis_lonlat]
        features.append({
            "type": "Feature",
            "properties": {"berth": berth,
                           "total_len_m": round(axis_coords.total_len, 1)},
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    gj = {"type": "FeatureCollection", "features": features}
    with open(path, "w") as f:
        json.dump(gj, f, indent=2)
    print(f"axes.geojson: {path}")

def render_axes_png(berth_axes, path):
    fig, ax = plt.subplots(figsize=(8, 6))
    colors = {"sea_island": "cyan", "east_t_head": "orange",
              "southeast_pier": "lime"}
    for berth, ac in berth_axes.items():
        lons = [p[0] for p in ac.axis_lonlat]
        lats = [p[1] for p in ac.axis_lonlat]
        col  = colors.get(berth, "white")
        ax.plot(lons, lats, color=col, lw=2, label=berth)
        ax.scatter(lons, lats, color=col, s=20, zorder=5)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_title("Kharg berth axes (refined)")
    ax.legend(fontsize=8)
    ax.set_aspect("equal")
    ax.grid(True, lw=0.3, alpha=0.5)
    plt.tight_layout()
    plt.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"axes.png: {path}")

# ─────────────────────────── summary outputs ─────────────────────────────────

SHIP_FIELDS = [
    "date", "orbit", "platform", "berth", "side",
    "s_start_m", "s_end_m", "s_center_m", "length_m",
    "mean_excess_dB", "threshold_dB", "class",
]

def write_ships_csv(ships, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHIP_FIELDS)
        w.writeheader()
        for sh in ships:
            w.writerow({k: sh.get(k, "") for k in SHIP_FIELDS})
    print(f"ships.csv: {path} ({len(ships)} rows)")

OCC_FIELDS = ["date", "orbit", "platform", "berth",
              "ships_A", "ships_B", "ships_total", "occupied"]

def write_occupancy_csv(occ_rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OCC_FIELDS)
        w.writeheader()
        for r in occ_rows:
            w.writerow({k: r.get(k, "") for k in OCC_FIELDS})
    print(f"occupancy.csv: {path} ({len(occ_rows)} rows)")

def write_comparison_table(occ_rows, old_set, path):
    """
    Table: date | orbit | berth | n_ships_v5 | in_old_set
    Sorted by date.
    """
    rows = sorted(occ_rows, key=lambda r: (r["date"], r["orbit"], r["berth"]))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "orbit", "berth",
                                           "n_ships_v5", "in_old_set"])
        w.writeheader()
        for r in rows:
            w.writerow({
                "date":       r["date"],
                "orbit":      r["orbit"],
                "berth":      r["berth"],
                "n_ships_v5": r["ships_total"],
                "in_old_set": "YES" if (r["date"], r["orbit"]) in old_set else "NO",
            })
    print(f"comparison_table.csv: {path}")

def write_labels_template(selected_dates, path):
    """
    Template CSV for round-2 labelling: 20 NEW dates × 3 berths.
    Columns: date, orbit, platform, berth, label_A (blank), label_B (blank), notes.
    """
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "orbit", "platform",
                                           "berth", "label_A", "label_B", "notes"])
        w.writeheader()
        for dt_str, orbit, platform in selected_dates:
            for berth in BERTHS:
                w.writerow({"date": dt_str, "orbit": orbit,
                             "platform": platform, "berth": berth,
                             "label_A": "", "label_B": "", "notes": ""})
    print(f"labels_round2_template.csv: {path}")

# ─────────────────────────── main ────────────────────────────────────────────

def main():
    cfg = make_config()

    print("=== Step 1: select new dates ===")
    selected_dates = select_new_dates(cfg)
    print(f"  total selected: {len(selected_dates)} scenes")

    # write selected dates manifest
    manifest_path = OUT_DIR / "selected_dates.csv"
    with open(manifest_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "orbit", "platform"])
        for row in selected_dates:
            w.writerow(row)
    print(f"  manifest: {manifest_path}")

    print("\n=== Step 2: build axes ===")
    berth_axes = {bname: AxisCoords(bdef["axis_lonlat"])
                  for bname, bdef in BERTHS.items()}

    print("\n=== Step 3: fetch ALL scenes (for axis refinement) ===")
    # group by (date, orbit) → fetch once per scene, then split by berth
    unique_scenes = {}
    for dt_str, orbit, platform in selected_dates:
        key = (dt_str, orbit)
        if key not in unique_scenes:
            unique_scenes[key] = platform

    # For each berth, accumulate (arr_db, transform) pairs for refinement
    berth_scene_data = {b: [] for b in BERTHS}

    # cache fetched arrays  key=(date,orbit,berth) → (arr_db, transform)
    scene_cache = {}

    for (dt_str, orbit), platform in sorted(unique_scenes.items()):
        for berth, bdef in BERTHS.items():
            bbox_lonlat = axis_bbox_lonlat(bdef["axis_lonlat"], AXIS_BUFFER_M)
            arr_lin, transform = fetch_scene(cfg, bbox_lonlat, dt_str, orbit)
            if arr_lin is None:
                scene_cache[(dt_str, orbit, berth)] = None
                continue
            arr_db = to_db(arr_lin)
            scene_cache[(dt_str, orbit, berth)] = (arr_db, transform)
            berth_scene_data[berth].append((arr_db, transform))
            print(f"  fetched {berth} {dt_str} orbit={orbit} shape={arr_db.shape}")

    print("\n=== Step 4: axis refinement ===")
    for berth, ac in berth_axes.items():
        scenes = berth_scene_data[berth]
        if not scenes:
            print(f"  {berth}: no scenes, skipping refinement")
            continue
        print(f"  refining {berth} ({len(scenes)} scenes)…")
        # compute s,d for each scene using initial axis
        all_db_sd = []
        for arr_db, transform in scenes:
            lon_arr, lat_arr = pixel_lonlat(transform)
            all_db_sd.append((arr_db, transform))
        delta_d = refine_axis(ac, all_db_sd)
        if delta_d != 0:
            berth_axes[berth] = ac.shifted(delta_d)
            print(f"  {berth}: axis shifted {delta_d:+.0f}m perp")
        else:
            print(f"  {berth}: no shift needed")

    print("\n=== Step 5: detection + PNG rendering ===")
    all_ships   = []
    occ_rows    = []

    for dt_str, orbit, platform in sorted(selected_dates, key=lambda x: x[0]):
        for berth, bdef in BERTHS.items():
            cached = scene_cache.get((dt_str, orbit, berth))
            if cached is None:
                print(f"  SKIP {berth} {dt_str} orbit={orbit} (no data)")
                continue
            arr_db, transform = cached
            ac = berth_axes[berth]

            lon_arr, lat_arr = pixel_lonlat(transform)
            s_arr, d_arr     = ac.pixel_sd(lon_arr, lat_arr)

            ships, P_A, P_B, stats = detect_ships(
                arr_db, s_arr, d_arr,
                bdef["min_len_m"], dt_str, orbit, platform, berth
            )
            all_ships.extend(ships)

            n_A   = sum(1 for s in ships if s["side"] == "A")
            n_B   = sum(1 for s in ships if s["side"] == "B")
            total = n_A + n_B
            occ_rows.append({
                "date": dt_str, "orbit": orbit, "platform": platform,
                "berth": berth,
                "ships_A": n_A, "ships_B": n_B,
                "ships_total": total,
                "occupied": "YES" if total > 0 else "NO",
            })
            print(f"  {berth} {dt_str} orbit={orbit}: {total} ship(s) "
                  f"(A={n_A}, B={n_B})")

            if P_A is not None:
                png_name = f"{dt_str}_orbit{orbit}_{berth}.png"
                render_png(arr_db, s_arr, d_arr, P_A, P_B, stats,
                           ships, dt_str, orbit, berth,
                           OUT_DIR / png_name)

    print("\n=== Step 6: write outputs ===")
    write_ships_csv    (all_ships, OUT_DIR / "ships.csv")
    write_occupancy_csv(occ_rows,  OUT_DIR / "occupancy.csv")
    write_comparison_table(occ_rows, OLD_SET, OUT_DIR / "comparison_table.csv")
    write_labels_template(selected_dates, OUT_DIR / "labels_round2_template.csv")
    write_axes_geojson(berth_axes, OUT_DIR / "axes.geojson")
    render_axes_png   (berth_axes, OUT_DIR / "axes.png")

    print(f"\nDone. Output: {OUT_DIR}/")
    print(f"  ships:     {len(all_ships)}")
    print(f"  occupancy: {len(occ_rows)} rows")

if __name__ == "__main__":
    main()
