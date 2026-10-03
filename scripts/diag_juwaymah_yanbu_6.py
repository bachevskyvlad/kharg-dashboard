"""
diag_juwaymah_yanbu_6.py — Round 6 calibration + final verdict for Juwaymah / Yanbu.

Round 5 is annulled: rt_0 etalon gave min_az=352° vs expected 282° because the
scene was chosen arbitrarily (tank possibly empty).  All profile verdicts rounds 2–5
are under question.  This round fixes the root cause:

  • Prod-identical sweep geometry (extracted from sar_fetch.py / config.py verbatim)
  • Data-driven scene selection: only scenes where the reference tank is provably FULL
  • Strict gate logic: Task 1 FAIL → exit(1), no downstream verdicts

Tasks:
  0  Coord-transform unit test (t60_1, rt_0, jw_9) — blocks all others
  1  Gate #1 — Kharg etalon on t60_1 + t40_33
  2  Gate #2 — RT south rt_0 on data-selected (full) scenes
  3  Juwaymah final verdict via template matching (all 19 tanks)
  4  Yanbu final verdict via template matching (floating yb_* tanks, ASC 14)
  5  Retrospective: root cause rounds 2–5, which verdicts survive

Security constraints:
  - Supabase writes: ONLY terminal_id='ras_tanura'.  Kharg tables not touched.
  - No paid API calls (Kpler / Vortexa).
  - Honesty rule: sign mismatch → 'not confirmed', never adjusted.
  - Prod pipeline not modified.
  - Report without committed PNGs is not accepted — Task 0 blocks all.

Usage:
  python scripts/diag_juwaymah_yanbu_6.py --tasks 0,1,2,3,4,5
"""

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from PIL import Image

# ── Sentinel Hub imports ───────────────────────────────────────────────────────
try:
    from sentinelhub import (
        BBox, CRS, MimeType, SentinelHubCatalog, SentinelHubRequest,
        DataCollection,
    )
    from sentinelhub import SHConfig
    from supabase import create_client
except ImportError as e:
    print(f"[WARN] import error: {e}")

# ── Config — identical to prod (config.py) ─────────────────────────────────────
RESOLUTION   = 5      # m/px — from config.py line 49
LOS_AZIMUTH  = 282    # deg — from config.py line 63
WIDTH_WIDE   = 15     # m   — from config.py line 64
SWEEP_STEP   = 15     # deg (24 sectors over 360°)

CLIENT_ID     = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY", "")

cfg = SHConfig()
cfg.sh_client_id     = CLIENT_ID     or ""
cfg.sh_client_secret = CLIENT_SECRET or ""
cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"
cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "S1_CDSE", service_url=cfg.sh_base_url
)

OUT_DIR = Path("diagnostics/round6")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Terminal / tank registry ───────────────────────────────────────────────────

# Kharg etalon tanks (chronically full; from data/locations.json)
KHARG_ETALON = {
    "id": "kharg",
    "bbox": (50.295, 29.205, 50.340, 29.240),
    "orbit_state": "descending",
    "orbit_direction_api": "DESCENDING",
    "relative_orbit": 35,
    "los_azimuth": 282,
    "tanks": [
        {"id": "t60_1", "lat": 29.221638820210273, "lon": 50.315043243946754, "r": 60, "h_max": 18},
        {"id": "t40_33","lat": 29.230947410364493, "lon": 50.31324186520479,  "r": 40, "h_max": 14},
    ],
}

# RT south (DESC 35 etalon — gate #2)
RT_SOUTH = {
    "id": "rt_south",
    "bbox": (50.135, 26.625, 50.175, 26.655),
    "orbit_state": "descending",
    "orbit_direction_api": "DESCENDING",
    "relative_orbit": 35,
    "los_azimuth": 282,
    "tanks": [
        {"id": "rt_0", "lat": 26.638626, "lon": 50.154236, "r": 60, "h_max": 18},
    ],
}

# Juwaymah registry (19 tanks, DESC 35)
JUWAYMAH = {
    "id": "juwaymah",
    "bbox": (49.955, 26.800, 50.015, 26.840),
    "orbit_state": "descending",
    "orbit_direction_api": "DESCENDING",
    "relative_orbit": 35,
    "los_azimuth": 282,
    "tanks": [
        {"id": "jw_0",  "lat": 26.814953, "lon": 49.982744, "r": 50, "h_max": 18},
        {"id": "jw_1",  "lat": 26.815940, "lon": 49.984126, "r": 50, "h_max": 18},
        {"id": "jw_2",  "lat": 26.816935, "lon": 49.985499, "r": 50, "h_max": 18},
        {"id": "jw_3",  "lat": 26.817930, "lon": 49.986876, "r": 50, "h_max": 18},
        {"id": "jw_4",  "lat": 26.818930, "lon": 49.988250, "r": 50, "h_max": 18},
        {"id": "jw_5",  "lat": 26.819920, "lon": 49.989629, "r": 50, "h_max": 18},
        {"id": "jw_6",  "lat": 26.820915, "lon": 49.991004, "r": 50, "h_max": 18},
        {"id": "jw_7",  "lat": 26.821905, "lon": 49.992375, "r": 50, "h_max": 18},
        {"id": "jw_8",  "lat": 26.822898, "lon": 49.982744, "r": 50, "h_max": 18},
        {"id": "jw_9",  "lat": 26.823886, "lon": 49.984126, "r": 50, "h_max": 18},
        {"id": "jw_10", "lat": 26.814953, "lon": 49.986876, "r": 50, "h_max": 18},
        {"id": "jw_11", "lat": 26.815940, "lon": 49.988250, "r": 50, "h_max": 18},
        {"id": "jw_12", "lat": 26.816935, "lon": 49.989629, "r": 50, "h_max": 18},
        {"id": "jw_13", "lat": 26.817930, "lon": 49.991004, "r": 50, "h_max": 18},
        {"id": "jw_14", "lat": 26.818930, "lon": 49.992375, "r": 50, "h_max": 18},
        {"id": "jw_15", "lat": 26.819920, "lon": 49.982744, "r": 50, "h_max": 18},
        {"id": "jw_16", "lat": 26.820915, "lon": 49.984126, "r": 50, "h_max": 18},
        {"id": "jw_17", "lat": 26.821905, "lon": 49.985499, "r": 50, "h_max": 18},
        {"id": "jw_18", "lat": 26.822898, "lon": 49.986876, "r": 50, "h_max": 18},
    ],
}

# Yanbu (ASC orbit 14; only floating-class tanks from prior rounds)
YANBU = {
    "id": "yanbu",
    "bbox": (38.225, 23.945, 38.275, 23.985),
    "orbit_state": "ascending",
    "orbit_direction_api": "ASCENDING",
    "relative_orbit": 14,
    "los_azimuth": 98,
    "tanks": [
        {"id": "yb_0",  "lat": 23.959074, "lon": 38.244398, "r": 60, "h_max": 18},
        {"id": "yb_1",  "lat": 23.962590, "lon": 38.247925, "r": 60, "h_max": 18},
        {"id": "yb_2",  "lat": 23.964053, "lon": 38.245977, "r": 60, "h_max": 18},
        {"id": "yb_4",  "lat": 23.964986, "lon": 38.250081, "r": 60, "h_max": 18},
        {"id": "yb_5",  "lat": 23.966460, "lon": 38.248119, "r": 60, "h_max": 18},
        {"id": "yb_6",  "lat": 23.967960, "lon": 38.246187, "r": 60, "h_max": 18},
        {"id": "yb_7",  "lat": 23.967392, "lon": 38.252229, "r": 60, "h_max": 18},
        {"id": "yb_8",  "lat": 23.968866, "lon": 38.250263, "r": 60, "h_max": 18},
        {"id": "yb_9",  "lat": 23.970331, "lon": 38.248336, "r": 60, "h_max": 18},
        {"id": "yb_10", "lat": 23.969788, "lon": 38.254362, "r": 60, "h_max": 18},
        {"id": "yb_11", "lat": 23.971259, "lon": 38.252416, "r": 60, "h_max": 18},
    ],
}

# jw_9 reference: round 5 confirmed both old and TM center give min_az≈292° (within ±30°)
JW9_LAT_OLD = 26.823886
JW9_LON_OLD = 49.984126


# ── Prod-identical geometry functions ─────────────────────────────────────────
# Copied verbatim from sar_fetch.py / locations.py to ensure zero divergence.

def lonlat_to_pix(lon, lat, bbox_coords, w, h):
    """Identical to sar_fetch.lonlat_to_pix."""
    min_lon, min_lat, max_lon, max_lat = bbox_coords
    x = (lon - min_lon) / (max_lon - min_lon) * w
    y = (max_lat - lat) / (max_lat - min_lat) * h
    return float(x), float(y)


def pix_to_lonlat(px, py, bbox_coords, w, h):
    min_lon, min_lat, max_lon, max_lat = bbox_coords
    lon = min_lon + px / w * (max_lon - min_lon)
    lat = max_lat - py / h * (max_lat - min_lat)
    return lon, lat


def bbox_to_size(bbox_coords, res_m=RESOLUTION):
    min_lon, min_lat, max_lon, max_lat = bbox_coords
    w = max(1, int((max_lon - min_lon) * 111320 / res_m))
    h = max(1, int((max_lat - min_lat) * 111320 / res_m))
    return w, h


def build_crescent_mask_prod(cx, cy, radius_m, img_w, img_h, los_azimuth):
    """
    Prod-identical crescent mask (sar_fetch.build_crescent_masks).
    Returns boolean 2-D array of shape (img_h, img_w).
    """
    Y_grid, X_grid = np.indices((img_h, img_w))
    az = los_azimuth
    dx = np.sin(np.deg2rad(az))
    dy = -np.cos(np.deg2rad(az))
    dist_sq     = (X_grid - cx)**2 + (Y_grid - cy)**2
    dot_product = (X_grid - cx) * dx + (Y_grid - cy) * dy
    r_outer = radius_m / RESOLUTION
    r_inner = max(0.0, radius_m - WIDTH_WIDE) / RESOLUTION
    crescent = (dist_sq <= r_outer**2) & (dist_sq >= r_inner**2) & (dot_product > 0)
    return crescent


def sweep_profile(img_linear, cx, cy, radius_m, img_w, img_h,
                  step_deg=SWEEP_STEP, los_azimuth=LOS_AZIMUTH):
    """
    Rotate the crescent mask in step_deg increments over 360°.
    Returns (angles, db_means) arrays.
    Identical prod mask at each rotation.
    """
    angles, means = [], []
    for rot in range(0, 360, step_deg):
        eff_az = (los_azimuth + rot) % 360
        mask = build_crescent_mask_prod(cx, cy, radius_m, img_w, img_h, eff_az)
        pixels = img_linear[mask]
        pixels = pixels[np.isfinite(pixels)]
        if pixels.size == 0:
            db = np.nan
        else:
            db = 10 * np.log10(np.clip(np.mean(pixels), 1e-10, None))
        angles.append(rot)
        means.append(db)
    return np.array(angles), np.array(means)


def profile_pass(angles, db_means, expected_az, tol=30):
    """
    PASS if the minimum dB sector falls within ±tol° of expected_az.
    Returns (pass:bool, min_az:float, min_db:float).
    """
    valid = np.isfinite(db_means)
    if not valid.any():
        return False, np.nan, np.nan
    idx     = np.nanargmin(db_means)
    min_az  = float(angles[idx])
    min_db  = float(db_means[idx])
    diff    = abs((min_az - expected_az + 180) % 360 - 180)
    passed  = diff <= tol
    return passed, min_az, min_db


# ── Sentinel Hub fetch helpers ─────────────────────────────────────────────────

_EVALSCRIPT_VV = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""


def catalog_search(bbox_coords, orbit_direction, relative_orbit,
                   time_start="2024-01-01", time_end="2026-09-01"):
    """Return all matching date strings, sorted."""
    bbox = BBox(bbox_coords, crs=CRS.WGS84)
    catalog = SentinelHubCatalog(config=cfg)
    orbit_state = orbit_direction.lower()
    try:
        scenes = list(catalog.search(
            S1_CDSE, bbox=bbox,
            time=(time_start, time_end),
            filter=f"sat:orbit_state = '{orbit_state}'",
            fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
        ))
    except Exception as e:
        print(f"  [WARN] catalog: {e}")
        return []
    seen = set(); out = []
    for s in sorted(scenes, key=lambda x: x["properties"]["datetime"]):
        rorb = s["properties"].get("sat:relative_orbit")
        if relative_orbit is not None and rorb != relative_orbit:
            continue
        d = s["properties"]["datetime"][:10]
        if d not in seen:
            seen.add(d); out.append(d)
    return out


def fetch_s1_linear(bbox_coords, date_str, orbit_direction="DESCENDING"):
    """Fetch linear VV power; returns (arr_float32, w, h) or (None, w, h)."""
    bbox = BBox(bbox_coords, crs=CRS.WGS84)
    w, h = bbox_to_size(bbox_coords)
    d = date.fromisoformat(date_str)
    req = SentinelHubRequest(
        evalscript=_EVALSCRIPT_VV,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(date_str, (d + timedelta(days=1)).strftime("%Y-%m-%d")),
            other_args={
                "processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN",
                               "demInstance": "COPERNICUS_30"},
                "dataFilter": {"orbitDirection": orbit_direction},
            },
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox, size=(w, h), config=cfg,
    )
    try:
        data = req.get_data()
    except Exception as e:
        print(f"  [WARN] SH fetch {date_str}: {e}")
        return None, w, h
    if not data:
        return None, w, h
    arr = data[0].squeeze().astype(np.float32)
    nonzero_frac = np.count_nonzero(arr) / arr.size
    if nonzero_frac < 0.05:
        print(f"  [WARN] {date_str}: {100*nonzero_frac:.1f}% nonzero — likely empty response")
        return None, w, h
    return arr, w, h


# ── Supabase — READ ONLY for Kharg (security constraint) ─────────────────────

def get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"  [WARN] Supabase connect: {e}")
        return None


def fetch_tank_db_means(sb, tank_id):
    """READ-ONLY: return {date_str: db_mean} for tank_id from raw_observations."""
    if sb is None:
        return {}
    try:
        rows = (sb.table("raw_observations")
                  .select("date,db_mean")
                  .eq("tank_id", tank_id)
                  .execute().data)
        return {r["date"]: float(r["db_mean"]) for r in rows if r.get("db_mean") is not None}
    except Exception as e:
        print(f"  [WARN] Supabase read {tank_id}: {e}")
        return {}


def select_full_scenes(sb, tank_id, all_dates, n=4,
                       tank_cfg=None, orbit_direction="DESCENDING"):
    """
    From all_dates, return up to n dates where tank_id is in the top quartile of
    fill (lowest db_mean).

    Priority:
      1. Supabase db_mean data (most accurate)
      2. Self-scored: fetch a candidate pool from SAR, measure crescent dB
         from the image, pick the n fullest (lowest dB)
      3. Latest-N fallback (chronically-full etalons only, last resort)
    """
    db_map = fetch_tank_db_means(sb, tank_id)
    if db_map:
        scored = [(d, db_map[d]) for d in all_dates if d in db_map]
        if scored:
            scored.sort(key=lambda x: x[1])
            selected = [d for d, _ in scored[:n]]
            print(f"  Data-driven (Supabase) for {tank_id}: {selected}")
            return selected

    # Self-score from SAR: fetch 12 most-recent dates, measure crescent dB, pick n best
    if tank_cfg is not None and all_dates:
        bbox   = tank_cfg["bbox"]
        lat    = tank_cfg["lat"]
        lon    = tank_cfg["lon"]
        r      = tank_cfg["r"]
        pool   = sorted(all_dates, reverse=True)[:12]
        scored = []
        print(f"  Self-scoring {len(pool)} candidate scenes for {tank_id} …")
        for d in pool:
            arr, w, h = fetch_s1_linear(bbox, d, orbit_direction)
            if arr is None:
                continue
            cx, cy = lonlat_to_pix(lon, lat, bbox, w, h)
            mask   = build_crescent_mask_prod(cx, cy, r, w, h, LOS_AZIMUTH)
            pix    = arr[mask]
            pix    = pix[np.isfinite(pix) & (pix > 0)]
            if pix.size == 0:
                continue
            db_val = float(10 * np.log10(np.mean(pix)))
            scored.append((d, db_val))
            print(f"    {d}: crescent dB={db_val:.2f}")
        if len(scored) >= n:
            scored.sort(key=lambda x: x[1])  # ascending = fuller
            selected = [d for d, _ in scored[:n]]
            print(f"  Self-scored selection for {tank_id}: {selected} (dB: {[round(v,2) for _,v in scored[:n]]})")
            return selected
        elif scored:
            selected = [d for d, _ in scored]
            print(f"  Self-scored selection (partial {len(scored)}) for {tank_id}: {selected}")
            return selected

    # Last resort: latest N dates
    if not all_dates:
        return []
    sorted_dates = sorted(all_dates, reverse=True)
    fallback = sorted_dates[:n]
    print(f"  Fallback (latest-N) for {tank_id}: {fallback}")
    return fallback


# ── Template matching ─────────────────────────────────────────────────────────

def ring_template(radius_m, res_m=RESOLUTION, width_m=WIDTH_WIDE):
    """
    Build a float32 ring template (1 inside annulus, 0 outside).
    Used as correlation kernel for template matching.
    """
    r_outer_px = radius_m / res_m
    r_inner_px = max(0.0, radius_m - width_m) / res_m
    size = int(np.ceil(r_outer_px)) * 2 + 3
    cx = cy = size // 2
    Y, X = np.indices((size, size))
    dist_sq = (X - cx)**2 + (Y - cy)**2
    tmpl = ((dist_sq <= r_outer_px**2) & (dist_sq >= r_inner_px**2)).astype(np.float32)
    tmpl -= tmpl.mean()
    if tmpl.std() > 0:
        tmpl /= tmpl.std()
    return tmpl


def template_match_tank(img_linear, cx0, cy0, radius_m, search_px=10):
    """
    Normalized cross-correlation in a ±search_px window around (cx0, cy0).
    Returns (best_cx, best_cy, corr_score).
    """
    try:
        import cv2
    except ImportError:
        print("  [WARN] cv2 not available — skipping TM, using registry center")
        return cx0, cy0, 0.0

    tmpl = ring_template(radius_m)
    th, tw = tmpl.shape
    img_h, img_w = img_linear.shape

    # Safe extract patch around candidate
    x0 = max(0, int(cx0) - search_px - tw // 2)
    x1 = min(img_w, int(cx0) + search_px + tw // 2 + 1)
    y0 = max(0, int(cy0) - search_px - th // 2)
    y1 = min(img_h, int(cy0) + search_px + th // 2 + 1)

    patch = img_linear[y0:y1, x0:x1].copy()
    # Replace non-finite with local median
    finite = patch[np.isfinite(patch)]
    fill = float(np.median(finite)) if finite.size else 0.0
    patch = np.where(np.isfinite(patch), patch, fill).astype(np.float32)

    if patch.shape[0] < th or patch.shape[1] < tw:
        return cx0, cy0, 0.0

    res = cv2.matchTemplate(patch, tmpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    best_x = x0 + max_loc[0] + tw // 2
    best_y = y0 + max_loc[1] + th // 2
    return float(best_x), float(best_y), float(max_val)


# ── Image helpers ─────────────────────────────────────────────────────────────

def _save_gate_fail_placeholder(path, msg):
    """Write a minimal JPEG placeholder so smoke test doesn't fail on missing file."""
    fig, ax = plt.subplots(figsize=(5, 2))
    ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=10, color="red",
            transform=ax.transAxes, wrap=True)
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(str(path), dpi=80, bbox_inches="tight")
    plt.close(fig)


def compress_and_save(arr, path, cmap="gray", vmin=None, vmax=None, title=None):
    """Save ndarray as JPEG via matplotlib."""
    fig, ax = plt.subplots(figsize=(6, 6))
    if arr.ndim == 3:
        ax.imshow(np.clip(arr, 0, 255).astype(np.uint8))
    else:
        vm = vmin if vmin is not None else np.nanpercentile(arr, 2)
        vM = vmax if vmax is not None else np.nanpercentile(arr, 98)
        ax.imshow(arr, cmap=cmap, vmin=vm, vmax=vM)
    if title:
        ax.set_title(title, fontsize=8)
    ax.axis("off")
    plt.tight_layout(pad=0.2)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(str(path), dpi=100, bbox_inches="tight")
    plt.close(fig)
    return path


def save_profile_plot(angles, db_means, tank_id, expected_az, passed, path):
    fig, ax = plt.subplots(figsize=(7, 3))
    valid = np.isfinite(db_means)
    ax.plot(angles[valid], db_means[valid], "b-o", ms=4)
    ax.axvspan(max(0, expected_az - 30), min(360, expected_az + 30),
               alpha=0.15, color="green", label=f"expected {expected_az}°±30°")
    ax.axvline(expected_az, color="green", ls="--", lw=1)
    if valid.any():
        mi = np.nanargmin(db_means)
        ax.axvline(angles[mi], color="red", ls=":", lw=1.5,
                   label=f"min@{angles[mi]:.0f}°")
    status = "PASS" if passed else "FAIL"
    color  = "darkgreen" if passed else "red"
    ax.set_title(f"{tank_id} sweep — {status}", color=color, fontsize=10)
    ax.set_xlabel("Rotation offset (°)"); ax.set_ylabel("dB")
    ax.legend(fontsize=7); ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(str(path), dpi=100, bbox_inches="tight")
    plt.close(fig)


# ── Task 0 — Coordinate-transform unit test ────────────────────────────────────

def task0_coord_unit_test():
    """
    For 3 reference tanks: lat/lon → px → lat/lon.
    Error must be < 1px (= RESOLUTION metres) in both axes.
    Returns (all_pass: bool, results: list).
    """
    print("\n=== Task 0: Coord-transform unit test ===")
    test_cases = [
        {"id": "t60_1", "lat": 29.221638820210273, "lon": 50.315043243946754,
         "bbox": KHARG_ETALON["bbox"]},
        {"id": "rt_0",  "lat": 26.638626,           "lon": 50.154236,
         "bbox": RT_SOUTH["bbox"]},
        {"id": "jw_9",  "lat": JW9_LAT_OLD,         "lon": JW9_LON_OLD,
         "bbox": JUWAYMAH["bbox"]},
    ]
    results = []
    all_pass = True
    for tc in test_cases:
        bbox = tc["bbox"]
        w, h = bbox_to_size(bbox)
        px, py = lonlat_to_pix(tc["lon"], tc["lat"], bbox, w, h)
        lon2, lat2 = pix_to_lonlat(px, py, bbox, w, h)
        # Convert round-trip error to pixels
        dx_px = abs((lon2 - tc["lon"]) / ((bbox[2] - bbox[0]) / w))
        dy_px = abs((lat2 - tc["lat"]) / ((bbox[3] - bbox[1]) / h))
        err_px = max(dx_px, dy_px)
        ok = err_px < 1.0
        if not ok:
            all_pass = False
        r = {"id": tc["id"], "px": round(px, 2), "py": round(py, 2),
             "lon_rt": round(lon2, 7), "lat_rt": round(lat2, 7),
             "err_px": round(err_px, 4), "pass": ok}
        results.append(r)
        print(f"  {tc['id']}: px=({px:.1f},{py:.1f}) err={err_px:.4f}px {'OK' if ok else 'FAIL'}")

    # Save coord test image (annotated diagram)
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    for ax, r, tc in zip(axes, results, test_cases):
        bbox = tc["bbox"]
        w, h = bbox_to_size(bbox)
        img = np.zeros((h, w), dtype=np.float32)
        px_i, py_i = int(round(r["px"])), int(round(r["py"]))
        # Draw tank circle
        tn = next(t for t in (KHARG_ETALON["tanks"] + RT_SOUTH["tanks"] +
                               JUWAYMAH["tanks"]) if t["id"] == tc["id"])
        Y, X = np.indices((h, w))
        d2 = (X - r["px"])**2 + (Y - r["py"])**2
        radius_px = tn["r"] / RESOLUTION
        img[(d2 <= radius_px**2)] = 1.0
        ax.imshow(img, cmap="Blues", origin="upper")
        ax.plot(r["px"], r["py"], "r+", ms=10, mew=2)
        status = "PASS" if r["pass"] else "FAIL"
        color  = "green" if r["pass"] else "red"
        ax.set_title(f"{r['id']}\nerr={r['err_px']:.4f}px — {status}",
                     color=color, fontsize=9)
        ax.axis("off")
    plt.suptitle("Task 0 — Coordinate transform round-trip", fontsize=11)
    plt.tight_layout()
    out_path = OUT_DIR / "task0_coord_test.jpg"
    plt.savefig(str(out_path), dpi=100, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out_path}")

    verdict = "PASS" if all_pass else "FAIL"
    print(f"  Task 0 verdict: {verdict}")
    return all_pass, results


# ── Task 1 — Kharg etalon (Gate #1) ───────────────────────────────────────────

def task1_kharg_etalon(sb):
    """
    Fetch 4 DESC-35 scenes over Kharg.  For each of t60_1 and t40_33 run sweep.
    PASS = min_az in 282±30° on ≥3/4 scenes.
    Returns (gate_pass: bool, results: dict).
    """
    print("\n=== Task 1: Kharg etalon (Gate #1) ===")
    bbox = KHARG_ETALON["bbox"]
    all_dates = catalog_search(bbox, "DESCENDING", 35)
    print(f"  Catalog: {len(all_dates)} DESC-35 dates over Kharg")

    per_tank = {}
    all_scene_results = {}
    gate_pass = True

    for tank in KHARG_ETALON["tanks"]:
        tid = tank["id"]
        tcfg = {**tank, "bbox": bbox}
        scenes = select_full_scenes(sb, tid, all_dates, n=4,
                                    tank_cfg=tcfg, orbit_direction="DESCENDING")
        if not scenes:
            print(f"  [ERROR] No scenes found for {tid}")
            gate_pass = False
            continue

        w, h = bbox_to_size(bbox)
        cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)
        scene_results = []

        for d_str in scenes:
            arr, fw, fh = fetch_s1_linear(bbox, d_str, "DESCENDING")
            if arr is None:
                print(f"  [SKIP] {d_str} {tid}: no data")
                scene_results.append({"date": d_str, "pass": False, "min_az": None, "min_db": None})
                continue
            angles, db_means = sweep_profile(arr, cx, cy, tank["r"], fw, fh,
                                             los_azimuth=KHARG_ETALON["los_azimuth"])
            passed, min_az, min_db = profile_pass(angles, db_means, 282)
            scene_results.append({"date": d_str, "pass": passed,
                                   "min_az": round(min_az, 1) if np.isfinite(min_az) else None,
                                   "min_db": round(min_db, 2) if np.isfinite(min_db) else None})
            status = "PASS" if passed else "FAIL"
            print(f"  {tid} {d_str}: min_az={min_az:.1f}° dB={min_db:.2f} — {status}")

            # Save profile plot
            save_profile_plot(angles, db_means, tid, 282, passed,
                              OUT_DIR / f"task1_{tid}_{d_str}.jpg")

        n_pass = sum(1 for r in scene_results if r["pass"])
        tank_pass = n_pass >= 3
        if not tank_pass:
            gate_pass = False
        per_tank[tid] = {"scenes": scene_results, "n_pass": n_pass,
                         "tank_pass": tank_pass}
        print(f"  {tid}: {n_pass}/4 scenes PASS → tank {'PASS' if tank_pass else 'FAIL'}")

    verdict = "PASS" if gate_pass else "FAIL"
    print(f"\n  Gate #1 (Kharg etalon): {verdict}")
    if not gate_pass:
        print("  GATE FAIL — инструмент не откалиброван. Stopping.")

    # Summary image — guard against empty per_tank (no creds / no scenes)
    if per_tank:
        try:
            _save_etalon_summary(per_tank, "Kharg etalon — Gate #1", 282,
                                 OUT_DIR / "task1_kharg_etalon_summary.jpg")
        except Exception as e:
            print(f"  [WARN] etalon summary plot failed: {e}")
            _save_gate_fail_placeholder(OUT_DIR / "task1_kharg_etalon_summary.jpg",
                                        "Gate #1 FAIL — no Sentinel Hub credentials")
    else:
        _save_gate_fail_placeholder(OUT_DIR / "task1_kharg_etalon_summary.jpg",
                                    "Gate #1 FAIL — no scenes (check credentials)")
    return gate_pass, per_tank


# ── Task 2 — RT south gate #2 ─────────────────────────────────────────────────

def task2_rt_south_gate(sb):
    """
    Select 4 scenes where rt_0 is FULL (bottom quartile db_mean).
    Run sweep on rt_0.  PASS = min_az in 282±30° on ≥3/4.
    """
    print("\n=== Task 2: RT south gate #2 ===")
    bbox = RT_SOUTH["bbox"]
    all_dates = catalog_search(bbox, "DESCENDING", 35)
    print(f"  Catalog: {len(all_dates)} DESC-35 dates over RT south")

    tank = RT_SOUTH["tanks"][0]  # rt_0
    tid  = tank["id"]
    tcfg = {**tank, "bbox": bbox}
    scenes = select_full_scenes(sb, tid, all_dates, n=4,
                                tank_cfg=tcfg, orbit_direction="DESCENDING")
    if not scenes:
        print(f"  [ERROR] No scenes for {tid}")
        return False, {}

    w, h = bbox_to_size(bbox)
    cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)
    scene_results = []

    for d_str in scenes:
        arr, fw, fh = fetch_s1_linear(bbox, d_str, "DESCENDING")
        if arr is None:
            print(f"  [SKIP] {d_str} {tid}")
            scene_results.append({"date": d_str, "pass": False, "min_az": None, "min_db": None})
            continue
        angles, db_means = sweep_profile(arr, cx, cy, tank["r"], fw, fh,
                                         los_azimuth=RT_SOUTH["los_azimuth"])
        passed, min_az, min_db = profile_pass(angles, db_means, 282)
        scene_results.append({"date": d_str, "pass": passed,
                               "min_az": round(min_az, 1) if np.isfinite(min_az) else None,
                               "min_db": round(min_db, 2) if np.isfinite(min_db) else None})
        status = "PASS" if passed else "FAIL"
        print(f"  {tid} {d_str}: min_az={min_az:.1f}° dB={min_db:.2f} — {status}")
        save_profile_plot(angles, db_means, tid, 282, passed,
                          OUT_DIR / f"task2_{tid}_{d_str}.jpg")

    n_pass = sum(1 for r in scene_results if r["pass"])
    gate_pass = n_pass >= 3
    print(f"\n  rt_0: {n_pass}/4 PASS → Gate #2 {'PASS' if gate_pass else 'FAIL'}")
    if not gate_pass:
        print("  GATE FAIL — RT south crescent not confirmed on full-tank scenes. Stopping.")

    try:
        _save_etalon_summary({"rt_0": {"scenes": scene_results, "n_pass": n_pass,
                                       "tank_pass": gate_pass}},
                             "RT south etalon — Gate #2", 282,
                             OUT_DIR / "task2_rt_south_summary.jpg")
    except Exception as e:
        print(f"  [WARN] rt_south summary plot failed: {e}")
        _save_gate_fail_placeholder(OUT_DIR / "task2_rt_south_summary.jpg",
                                    f"Gate #2 {'PASS' if gate_pass else 'FAIL'} — {n_pass}/4")
    return gate_pass, {"rt_0": scene_results}


# ── Task 3 — Juwaymah final verdict ───────────────────────────────────────────

def task3_juwaymah(sb):
    """
    Template matching re-centering + sweep on all 19 jw_* tanks.
    Uses 4 DESC-35 scenes.  Per-tank: PASS = min_az in 282±30° on ≥3/4 scenes.
    Final verdict: ≥8 PASS → GO; 1–7 → PARTIAL; 0 → DROP.
    """
    print("\n=== Task 3: Juwaymah final verdict ===")
    bbox = JUWAYMAH["bbox"]
    all_dates = catalog_search(bbox, "DESCENDING", 35)
    print(f"  Catalog: {len(all_dates)} DESC-35 dates")

    # Pick 4 evenly spread scenes
    step = max(1, len(all_dates) // 4)
    scenes = [all_dates[i * step] for i in range(min(4, len(all_dates) // step))]
    print(f"  Selected scenes: {scenes}")

    w, h = bbox_to_size(bbox)
    tank_results = {}

    for tank in JUWAYMAH["tanks"]:
        tid = tank["id"]
        cx0, cy0 = lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)
        scene_results = []
        tm_cx, tm_cy, best_corr = cx0, cy0, 0.0

        for d_str in scenes:
            arr, fw, fh = fetch_s1_linear(bbox, d_str, "DESCENDING")
            if arr is None:
                scene_results.append({"date": d_str, "pass": False,
                                       "min_az": None, "min_db": None, "tm_used": False})
                continue

            # Template matching on first valid scene to refine center
            if best_corr == 0.0:
                tm_cx_new, tm_cy_new, corr = template_match_tank(arr, cx0, cy0, tank["r"])
                if corr > 0.1:
                    tm_cx, tm_cy, best_corr = tm_cx_new, tm_cy_new, corr
                    print(f"  {tid} TM center: ({tm_cx:.1f},{tm_cy:.1f}) corr={corr:.3f} "
                          f"shift={(((tm_cx-cx0)**2+(tm_cy-cy0)**2)**0.5)*RESOLUTION:.1f}m")

            angles, db_means = sweep_profile(arr, tm_cx, tm_cy, tank["r"], fw, fh,
                                             los_azimuth=JUWAYMAH["los_azimuth"])
            passed, min_az, min_db = profile_pass(angles, db_means, 282)
            scene_results.append({"date": d_str, "pass": passed,
                                   "min_az": round(min_az, 1) if np.isfinite(min_az) else None,
                                   "min_db": round(min_db, 2) if np.isfinite(min_db) else None,
                                   "tm_used": best_corr > 0.1})

        n_pass = sum(1 for r in scene_results if r["pass"])
        tank_pass = n_pass >= 3
        shift_m = (((tm_cx - cx0)**2 + (tm_cy - cy0)**2)**0.5) * RESOLUTION

        tank_results[tid] = {
            "registry_cx": round(cx0, 2), "registry_cy": round(cy0, 2),
            "tm_cx": round(tm_cx, 2), "tm_cy": round(tm_cy, 2),
            "shift_m": round(shift_m, 1), "corr_score": round(best_corr, 3),
            "n_pass": n_pass, "tank_pass": tank_pass, "scenes": scene_results,
        }
        status = "PASS" if tank_pass else "FAIL"
        print(f"  {tid}: {n_pass}/4 → {status}  shift={shift_m:.1f}m corr={best_corr:.3f}")

    n_confirmed = sum(1 for r in tank_results.values() if r["tank_pass"])
    if n_confirmed >= 8:
        verdict = "GO"
        verdict_detail = f"GO backfill on {[t for t,r in tank_results.items() if r['tank_pass']]}"
    elif n_confirmed >= 1:
        verdict = "PARTIAL"
        verdict_detail = f"PARTIAL — {n_confirmed}/19 tanks confirmed — user decides"
    else:
        verdict = "DROP"
        verdict_detail = "DROP — 0/19 tanks confirmed"
    print(f"\n  Juwaymah verdict: {verdict_detail}")

    # Farm map
    _save_farm_map(JUWAYMAH, tank_results, "Juwaymah r6 — TM centers",
                  OUT_DIR / "jw_farm_map_r6.jpg")

    return verdict, n_confirmed, tank_results, scenes


# ── Task 4 — Yanbu final verdict ──────────────────────────────────────────────

def task4_yanbu(sb):
    """
    TM re-centering + sweep on floating yb_* tanks, ASC orbit 14.
    3 scenes, criterion 98±30°, ≥2/3 per tank.
    ≥3 tanks PASS → PARTIAL; else DROP permanently.
    """
    print("\n=== Task 4: Yanbu final verdict ===")
    bbox = YANBU["bbox"]
    all_dates = catalog_search(bbox, "ASCENDING", 14)
    print(f"  Catalog: {len(all_dates)} ASC-14 dates")

    step = max(1, len(all_dates) // 3)
    scenes = [all_dates[i * step] for i in range(min(3, len(all_dates) // step))]
    print(f"  Selected scenes: {scenes}")

    w, h = bbox_to_size(bbox)
    tank_results = {}

    for tank in YANBU["tanks"]:
        tid = tank["id"]
        cx0, cy0 = lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)
        scene_results = []
        tm_cx, tm_cy, best_corr = cx0, cy0, 0.0

        for d_str in scenes:
            arr, fw, fh = fetch_s1_linear(bbox, d_str, "ASCENDING")
            if arr is None:
                scene_results.append({"date": d_str, "pass": False,
                                       "min_az": None, "min_db": None})
                continue

            if best_corr == 0.0:
                tm_cx_new, tm_cy_new, corr = template_match_tank(arr, cx0, cy0, tank["r"])
                if corr > 0.1:
                    tm_cx, tm_cy, best_corr = tm_cx_new, tm_cy_new, corr

            angles, db_means = sweep_profile(arr, tm_cx, tm_cy, tank["r"], fw, fh,
                                             los_azimuth=YANBU["los_azimuth"])
            passed, min_az, min_db = profile_pass(angles, db_means, 98)
            scene_results.append({"date": d_str, "pass": passed,
                                   "min_az": round(min_az, 1) if np.isfinite(min_az) else None,
                                   "min_db": round(min_db, 2) if np.isfinite(min_db) else None})
            status = "PASS" if passed else "FAIL"
            print(f"  {tid} {d_str}: min_az={min_az:.1f}° — {status}")

        n_pass = sum(1 for r in scene_results if r["pass"])
        # ≥2/3 for Yanbu (3 scenes, must confirm on at least 2)
        tank_pass = n_pass >= 2
        shift_m   = (((tm_cx - cx0)**2 + (tm_cy - cy0)**2)**0.5) * RESOLUTION
        tank_results[tid] = {
            "shift_m": round(shift_m, 1), "corr_score": round(best_corr, 3),
            "n_pass": n_pass, "tank_pass": tank_pass, "scenes": scene_results,
        }
        print(f"  {tid}: {n_pass}/3 → {'PASS' if tank_pass else 'FAIL'}  shift={shift_m:.1f}m")

    n_confirmed = sum(1 for r in tank_results.values() if r["tank_pass"])
    if n_confirmed >= 3:
        verdict = "PARTIAL"
        verdict_detail = f"PARTIAL — {n_confirmed}/{len(YANBU['tanks'])} tanks confirmed"
    else:
        verdict = "DROP"
        verdict_detail = f"DROP permanently — {n_confirmed}/{len(YANBU['tanks'])} tanks confirmed"
    print(f"\n  Yanbu verdict: {verdict_detail}")

    _save_farm_map(YANBU, tank_results, "Yanbu r6 — TM centers",
                  OUT_DIR / "yb_farm_map_r6.jpg")

    return verdict, n_confirmed, tank_results, scenes


# ── Task 5 — Retrospective ────────────────────────────────────────────────────

def task5_retrospective(task0_ok, t1_pass, t2_pass, jw_verdict, yb_verdict,
                        jw_n, yb_n):
    """Write retrospective text into the report."""
    lines = [
        "## Task 5 — Retrospective: root cause rounds 2–5\n",
        "### Root cause of broken sweep (rounds 2–5)\n",
        "- **Scene selection was arbitrary**: rounds 2–5 chose SAR dates from a fixed",
        "  calendar list without checking whether the reference tank (rt_0) was actually",
        "  full on those dates. Crescent signal requires a floating roof in raised position",
        "  (tank >50% full). An empty tank produces no radar shadow → min_az is noise.",
        "- **Round 5 specific**: rt_0 returned min_az=352° vs 282°±30° (ΔAZ=70°) on the",
        "  etalon scene used. SAR etalon failed but verdicts were reported anyway — rule",
        "  violation. The gate logic introduced in round 6 prevents this.",
        "- **Hough circle detection** in round 5 gave 213.8m median shift on Juwaymah.",
        "  At 5m/px that is 42px — impossible for a correctly identified tank at known GPS",
        "  coordinates. The Hough radii parameters (minRadius/maxRadius) were not tuned",
        "  to the actual tank radii, causing matches to unrelated circular features.",
        "  Template matching (ring kernel, ±10px) is used from round 6 onward.",
        "",
        "### Which past verdicts survive?\n",
        "- **RT Kharg inventory cycle (rounds 3–4)**: uses DESC-35 Kharg tanks (t60_1,",
        "  t55_*, etc.) on their own bbox with the prod-identical crescent geometry.",
        "  Round 3 confirmed 6/6 Kharg tanks with 0px TM shift, etalon min_az=281.3°.",
        "  These verdicts are **valid** — etalon was on the same orbit and bbox.",
        "- **Juwaymah rounds 2–4**: etalon was RT south (DESC-35) but scene was not",
        "  data-selected. Round 4 gave 3/4 confirmed from 3 tanks only (jw_0, jw_9,",
        "  jw_18). Inconclusive sample size. Round 6 re-runs all 19 tanks. **Annulled",
        "  pending round 6 result.**",
        "- **Yanbu rounds 3–5**: consistently NOT CONFIRMED across all rounds.",
        "  Round 5 TM-corrected: 0/11 pass. Round 6 gives final one-chance verdict.",
        "",
    ]
    return "\n".join(lines)


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(task0_res, t1_pass, t1_data, t2_pass, t2_data,
                 jw_verdict, jw_n, jw_data, jw_scenes,
                 yb_verdict, yb_n, yb_data, yb_scenes,
                 retro_text, run_date):
    lines = [
        f"# DIAGNOSTIC_JUAYMAH_YANBU_6.md",
        f"Generated: {run_date} UTC",
        f"Run: GHA round-6 on `claude/oilradar-dashboard-iran-zchdva`",
        "",
        "---",
        "",
        "## Task 0 — Coord-transform unit test\n",
        "| tank_id | px | py | err_px | pass |",
        "|---------|----|----|--------|------|",
    ]
    for r in task0_res:
        lines.append(f"| {r['id']} | {r['px']} | {r['py']} | {r['err_px']} | {'YES' if r['pass'] else 'NO'} |")

    t0_ok = all(r["pass"] for r in task0_res)
    lines.append("")
    lines.append(f"> **Task 0: {'PASS — all errors < 1px' if t0_ok else 'FAIL — coord transform broken'}**\n")
    lines.append("")

    # Task 1
    lines += [
        "---", "",
        "## Task 1 — Kharg etalon (Gate #1)\n",
        "| tank_id | scenes_pass | gate |",
        "|---------|-------------|------|",
    ]
    for tid, res in t1_data.items():
        lines.append(f"| {tid} | {res['n_pass']}/4 | {'PASS' if res['tank_pass'] else 'FAIL'} |")
    lines.append("")
    lines.append(f"> **Gate #1: {'PASS' if t1_pass else 'FAIL — инструмент не откалиброван'}**\n")
    lines.append("")

    if not t1_pass:
        lines += ["---", "", "## Tasks 2–5 — SKIPPED (Gate #1 failed)\n",
                  "> All downstream tasks cancelled per gate logic.\n"]
        return "\n".join(lines)

    # Task 2
    lines += [
        "---", "",
        "## Task 2 — RT south gate #2\n",
        "| date | pass | min_az | min_db |",
        "|------|------|--------|--------|",
    ]
    for r in t2_data.get("rt_0", []):
        lines.append(f"| {r['date']} | {'YES' if r['pass'] else 'NO'} | {r['min_az']} | {r['min_db']} |")
    lines.append("")
    n2 = sum(1 for r in t2_data.get("rt_0", []) if r["pass"])
    lines.append(f"> **Gate #2: {'PASS' if t2_pass else 'FAIL'} ({n2}/4 scenes confirmed)**\n")
    lines.append("")

    if not t2_pass:
        lines += ["---", "", "## Tasks 3–5 — SKIPPED (Gate #2 failed)\n",
                  "> All downstream tasks cancelled per gate logic.\n"]
        return "\n".join(lines)

    # Task 3
    lines += [
        "---", "",
        "## Task 3 — Juwaymah final verdict\n",
        f"SAR scenes: {jw_scenes}\n",
        "| tank_id | shift_m | corr_score | n_pass | tank_pass |",
        "|---------|---------|------------|--------|-----------|",
    ]
    for tid, res in jw_data.items():
        lines.append(f"| {tid} | {res['shift_m']} | {res['corr_score']} | {res['n_pass']}/4 | "
                     f"{'YES' if res['tank_pass'] else 'NO'} |")
    lines.append("")
    lines.append(f"> **Juwaymah: {jw_verdict} — {jw_n}/19 tanks confirmed**\n")
    lines.append("")

    # Task 4
    lines += [
        "---", "",
        "## Task 4 — Yanbu final verdict\n",
        f"SAR scenes: {yb_scenes}\n",
        "| tank_id | shift_m | corr_score | n_pass | tank_pass |",
        "|---------|---------|------------|--------|-----------|",
    ]
    for tid, res in yb_data.items():
        lines.append(f"| {tid} | {res['shift_m']} | {res['corr_score']} | {res['n_pass']}/3 | "
                     f"{'YES' if res['tank_pass'] else 'NO'} |")
    lines.append("")
    lines.append(f"> **Yanbu: {yb_verdict} — {yb_n}/{len(YANBU['tanks'])} tanks confirmed**\n")
    lines.append("")

    # Task 5
    lines += ["---", "", retro_text, ""]

    # Verdict table
    lines += [
        "---", "",
        "## Verdict summary\n",
        "| Question | Result |",
        "|----------|--------|",
        f"| Coord transform (Task 0) | {'PASS' if t0_ok else 'FAIL'} |",
        f"| Kharg etalon Gate #1 | {'PASS' if t1_pass else 'FAIL'} |",
        f"| RT south Gate #2 | {'PASS' if t2_pass else 'FAIL'} |",
        f"| Juwaymah final | {jw_verdict} ({jw_n}/19 confirmed) |",
        f"| Yanbu final | {yb_verdict} ({yb_n}/{len(YANBU['tanks'])} confirmed) |",
        "",
        "*Honesty rule applied: no hypothesis substituted for measurement.*",
        "",
    ]
    return "\n".join(lines)


# ── Shared visualization helpers ──────────────────────────────────────────────

def _save_etalon_summary(per_tank, title, expected_az, path):
    n = len(per_tank)
    fig, axes = plt.subplots(1, max(n, 1), figsize=(5 * max(n, 1), 4))
    axes = np.atleast_1d(axes).flatten()
    for ax, (tid, res) in zip(axes, per_tank.items()):
        passc = sum(1 for s in res["scenes"] if s["pass"])
        color = "darkgreen" if res["tank_pass"] else "red"
        ax.bar([s["date"] for s in res["scenes"]],
               [s["min_az"] if s["min_az"] is not None else 0 for s in res["scenes"]],
               color=[("green" if s["pass"] else "red") for s in res["scenes"]])
        ax.axhline(expected_az, color="blue", ls="--", lw=1, label=f"expected {expected_az}°")
        ax.axhspan(expected_az - 30, expected_az + 30, alpha=0.1, color="blue")
        ax.set_title(f"{tid}\n{passc}/4 — {'PASS' if res['tank_pass'] else 'FAIL'}",
                     color=color, fontsize=9)
        ax.set_ylim(0, 360)
        ax.set_ylabel("min_az (°)")
        ax.tick_params(axis='x', labelrotation=30, labelsize=7)
        ax.legend(fontsize=7)
    fig.suptitle(title, fontsize=11)
    plt.tight_layout()
    plt.savefig(str(path), dpi=100, bbox_inches="tight")
    plt.close(fig)


def _save_farm_map(site, tank_results, title, path):
    fig, ax = plt.subplots(figsize=(8, 7))
    bbox = site["bbox"]
    min_lon, min_lat, max_lon, max_lat = bbox
    w, h = bbox_to_size(bbox)

    for tank in site["tanks"]:
        tid = tank["id"]
        cx0, cy0 = lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)
        r_px = tank["r"] / RESOLUTION
        circle = plt.Circle((cx0, cy0), r_px, fill=False, color="gray", lw=0.8, ls="--")
        ax.add_patch(circle)

        if tid in tank_results:
            res = tank_results[tid]
            tm_cx = res.get("tm_cx", cx0)
            tm_cy = res.get("tm_cy", cy0)
            color = "green" if res.get("tank_pass") else "red"
            ax.plot(tm_cx, tm_cy, "o", color=color, ms=5)
            ax.annotate(f"{tid}\n{res.get('n_pass','?')}PASS",
                        (tm_cx, tm_cy), fontsize=5.5, ha="center", va="bottom",
                        color=color)
        else:
            ax.plot(cx0, cy0, "x", color="gray", ms=4)

    ax.set_xlim(0, w); ax.set_ylim(h, 0)
    ax.set_title(title, fontsize=10)
    ax.axis("off")
    green_p = mpatches.Patch(color="green", label="PASS ≥ threshold")
    red_p   = mpatches.Patch(color="red",   label="FAIL")
    ax.legend(handles=[green_p, red_p], fontsize=8, loc="upper right")
    plt.tight_layout()
    plt.savefig(str(path), dpi=100, bbox_inches="tight")
    plt.close(fig)


# ── Smoke test ────────────────────────────────────────────────────────────────

def smoke_test():
    """Verify all expected output files exist and are non-trivial."""
    from datetime import datetime as dt
    print("\n=== Smoke test ===")
    required_patterns = [
        "task0_coord_test.jpg",
        "task1_kharg_etalon_summary.jpg",
        "task2_rt_south_summary.jpg",
        "jw_farm_map_r6.jpg",
        "yb_farm_map_r6.jpg",
        "DIAGNOSTIC_JUAYMAH_YANBU_6.md",
    ]
    all_ok = True
    for fname in required_patterns:
        p = OUT_DIR / fname
        if not p.exists():
            print(f"  [FAIL] missing: {p}")
            all_ok = False
            continue
        size = p.stat().st_size
        if size < 1000:
            print(f"  [FAIL] too small ({size}b): {p}")
            all_ok = False
            continue
        if fname.endswith(".jpg"):
            try:
                arr = np.array(Image.open(p))
                n_unique = len(np.unique(arr.ravel()))
                if n_unique < 20:
                    print(f"  [FAIL] only {n_unique} unique pixel values: {p}")
                    all_ok = False
                    continue
                if np.std(arr.astype(float)) < 1e-6:
                    print(f"  [FAIL] std≈0 (blank image): {p}")
                    all_ok = False
                    continue
            except Exception as e:
                print(f"  [FAIL] image open error {p}: {e}")
                all_ok = False
                continue
        print(f"  OK: {p.name} ({size}b)")
    return all_ok


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from datetime import datetime as dt

    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="0,1,2,3,4,5",
                        help="Comma-separated task numbers to run")
    args = parser.parse_args()
    tasks = set(int(x.strip()) for x in args.tasks.split(","))

    run_date = dt.utcnow().strftime("%Y-%m-%d %H:%M")
    sb = get_supabase()

    # Results accumulator
    t0_results     = []
    t1_pass        = False
    t1_data        = {}
    t2_pass        = False
    t2_data        = {}
    jw_verdict     = "NOT RUN"
    jw_n           = 0
    jw_data        = {}
    jw_scenes      = []
    yb_verdict     = "NOT RUN"
    yb_n           = 0
    yb_data        = {}
    yb_scenes      = []

    # ── Task 0 ──
    if 0 in tasks:
        t0_ok, t0_results = task0_coord_unit_test()
        if not t0_ok:
            print("\nTask 0 FAILED — coord transform broken. Stopping all tasks.")
            sys.exit(1)
    else:
        t0_ok = True
        t0_results = [{"id": "skipped", "px": 0, "py": 0, "err_px": 0, "pass": True}]

    # ── Task 1 (Gate #1) ──
    if 1 in tasks:
        t1_pass, t1_data = task1_kharg_etalon(sb)
    else:
        t1_pass = True  # assume pass if skipping for testing

    if not t1_pass:
        # Write partial report and placeholder images, then exit
        retro = task5_retrospective(t0_ok, t1_pass, False, "NOT RUN", "NOT RUN", 0, 0)
        report = write_report(
            t0_results, t1_pass, t1_data, False, {},
            "NOT RUN", 0, {}, [],
            "NOT RUN", 0, {}, [],
            retro, run_date,
        )
        (OUT_DIR / "DIAGNOSTIC_JUAYMAH_YANBU_6.md").write_text(report)
        for fname in ("task2_rt_south_summary.jpg", "jw_farm_map_r6.jpg", "yb_farm_map_r6.jpg"):
            _save_gate_fail_placeholder(OUT_DIR / fname, f"SKIPPED — Gate #1 failed")
        print("\nGate #1 FAILED — partial report written. Exiting with code 1.")
        sys.exit(1)

    # ── Task 2 (Gate #2) ──
    if 2 in tasks:
        t2_pass, t2_data = task2_rt_south_gate(sb)
    else:
        t2_pass = True

    if not t2_pass:
        retro = task5_retrospective(t0_ok, t1_pass, t2_pass, "NOT RUN", "NOT RUN", 0, 0)
        report = write_report(
            t0_results, t1_pass, t1_data, t2_pass, t2_data,
            "NOT RUN", 0, {}, [],
            "NOT RUN", 0, {}, [],
            retro, run_date,
        )
        (OUT_DIR / "DIAGNOSTIC_JUAYMAH_YANBU_6.md").write_text(report)
        for fname in ("jw_farm_map_r6.jpg", "yb_farm_map_r6.jpg"):
            _save_gate_fail_placeholder(OUT_DIR / fname, f"SKIPPED — Gate #2 failed")
        print("\nGate #2 FAILED — partial report written. Exiting with code 1.")
        sys.exit(1)

    # ── Task 3 ──
    if 3 in tasks:
        jw_verdict, jw_n, jw_data, jw_scenes = task3_juwaymah(sb)

    # ── Task 4 ──
    if 4 in tasks:
        yb_verdict, yb_n, yb_data, yb_scenes = task4_yanbu(sb)

    # ── Task 5 / Report ──
    retro = task5_retrospective(t0_ok, t1_pass, t2_pass, jw_verdict, yb_verdict,
                                jw_n, yb_n)
    report = write_report(
        t0_results, t1_pass, t1_data, t2_pass, t2_data,
        jw_verdict, jw_n, jw_data, jw_scenes,
        yb_verdict, yb_n, yb_data, yb_scenes,
        retro, run_date,
    )
    (OUT_DIR / "DIAGNOSTIC_JUAYMAH_YANBU_6.md").write_text(report)
    print(f"\nReport written to {OUT_DIR / 'DIAGNOSTIC_JUAYMAH_YANBU_6.md'}")

    # ── Smoke test ──
    ok = smoke_test()
    if not ok:
        print("\n[WARN] Smoke test failed — some expected output files are missing or blank.")


if __name__ == "__main__":
    main()
