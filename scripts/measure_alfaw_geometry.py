"""
measure_alfaw_geometry.py — Al-Faw geometry v2 (ТЗ, строгий порядок)
=====================================================================
Steps:
  1. Fetch Esri World Imagery XYZ tiles (z=18, ~0.52 m/px) → mosaic PNG
  2. 16-ray circle measurement for all 24 registry tanks:
       fit_quality: median ray residual (m) + edge energy; filter no_tank_visible
  3. Hough-circle census of the full mosaic → tanks visible but not in registry
  4. Write geometry_v2.csv (filtered), registry_v2.json, annotated PNG

Web Mercator projection (correct):
  global_px = (lon + 180) / 360 * 2^z * 256
  global_py = (1 - ln(tan(lat*π/180) + sec(lat*π/180)) / π) / 2 * 2^z * 256
  mosaic_px = global_px - tx0 * 256
  mosaic_py = global_py - ty0 * 256

Rules (ТЗ):
  - measurement is the ONLY source for registry edits
  - visual verdicts = analyst
  - "вероятно" prohibited
"""

import os, sys, math, io, json, time, csv
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import requests
from PIL import Image, ImageDraw

try:
    from scipy.ndimage import uniform_filter, gaussian_filter
    from scipy.signal import find_peaks
    SCIPY_OK = True
except ImportError:
    SCIPY_OK = False

# ── Config ────────────────────────────────────────────────────────────────────
ZOOM      = 18
TILE_SIZE = 256
RAYS      = 16

# Al-Faw bbox with generous buffer
LAT_MIN, LAT_MAX = 29.955, 29.983
LON_MIN, LON_MAX = 48.432, 48.466

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw" / "geometry"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

# Fit quality thresholds
EDGE_ENERGY_MIN  = 6.0   # gradient std at edge points; below → no_tank_visible
RESIDUAL_POOR_M  = 8.0   # median residual > this → poor fit

# Hough census
HOUGH_R_MIN_M = 25.0   # smallest expected tank radius (m)
HOUGH_R_MAX_M = 60.0   # largest
HOUGH_MIN_VOTES = 8    # out of 360 votes

# Registry tanks
TANKS_V1 = [
    {"id": "af_0",  "lat": 29.976037712391882, "lon": 48.44195552300743,  "radius_m": 40},
    {"id": "af_1",  "lat": 29.97446023561727,  "lon": 48.44041073298363,  "radius_m": 40},
    {"id": "af_2",  "lat": 29.973482237683964, "lon": 48.44170682926327,  "radius_m": 40},
    {"id": "af_3",  "lat": 29.97506237979022,  "lon": 48.443256604287775, "radius_m": 40},
    {"id": "af_4",  "lat": 29.972052466075166, "lon": 48.44360540262179,  "radius_m": 40},
    {"id": "af_5",  "lat": 29.973613605327138, "lon": 48.44517136039961,  "radius_m": 40},
    {"id": "af_6",  "lat": 29.971043737749962, "lon": 48.44490239092673,  "radius_m": 40},
    {"id": "af_7",  "lat": 29.972645241700267, "lon": 48.44646203210755,  "radius_m": 40},
    {"id": "af_8",  "lat": 29.970602225788706, "lon": 48.44914349229505,  "radius_m": 40},
    {"id": "af_9",  "lat": 29.96964506640903,  "lon": 48.45042448972437,  "radius_m": 40},
    {"id": "af_10", "lat": 29.969053578374012, "lon": 48.44757323726147,  "radius_m": 40},
    {"id": "af_11", "lat": 29.968074369587153, "lon": 48.44888119527686,  "radius_m": 40},
    {"id": "af_12", "lat": 29.967562585491844, "lon": 48.44613477210609,  "radius_m": 40},
    {"id": "af_13", "lat": 29.966616705980595, "lon": 48.44740303358956,  "radius_m": 40},
    {"id": "af_14", "lat": 29.965160381102958, "lon": 48.449324515383225, "radius_m": 40},
    {"id": "af_15", "lat": 29.966613187227228, "lon": 48.45079062045803,  "radius_m": 40},
    {"id": "af_16", "lat": 29.964164829146835, "lon": 48.450625111425616, "radius_m": 40},
    {"id": "af_17", "lat": 29.965658494437534, "lon": 48.45208349893114,  "radius_m": 40},
    {"id": "af_18", "lat": 29.961779354173064, "lon": 48.45380166725049,  "radius_m": 40},
    {"id": "af_19", "lat": 29.96274476143991,  "lon": 48.45253449582525,  "radius_m": 40},
    {"id": "af_20", "lat": 29.964218037290507, "lon": 48.45399437056702,  "radius_m": 40},
    {"id": "af_21", "lat": 29.96326674821542,  "lon": 48.45527466098349,  "radius_m": 40},
    {"id": "af_22", "lat": 29.964822804270934, "lon": 48.45681522152186,  "radius_m": 40},
    {"id": "af_23", "lat": 29.96580178961507,  "lon": 48.45553712042206,  "radius_m": 40},
]


# ── Web Mercator helpers ──────────────────────────────────────────────────────
def _global_px(lat, lon, z):
    """Tile-system global pixel coordinates (Web Mercator, origin at NW of world)."""
    n = 2 ** z * TILE_SIZE
    gx = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    gy = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return gx, gy

def _lat_lon_to_tile(lat, lon, z):
    gx, gy = _global_px(lat, lon, z)
    return int(gx / TILE_SIZE), int(gy / TILE_SIZE)

def _tile_nw_global_px(tx, ty, z):
    return tx * TILE_SIZE, ty * TILE_SIZE

def _m_per_px_at_lat(lat, z):
    """Web Mercator scale: constant in x, lat-dependent for geographic metres."""
    C = 2.0 * math.pi * 6378137.0
    return C * math.cos(math.radians(lat)) / (2 ** z * TILE_SIZE)

def geo_to_mosaic_px(lat, lon, z, tx0, ty0):
    """Convert (lat, lon) → (px, py) in the stitched mosaic."""
    gx, gy = _global_px(lat, lon, z)
    nw_gx, nw_gy = _tile_nw_global_px(tx0, ty0, z)
    return gx - nw_gx, gy - nw_gy


# ── Tile fetch ────────────────────────────────────────────────────────────────
def _fetch_tile(tx, ty, z, session, retries=3):
    url = TILE_URL.format(z=z, y=ty, x=tx)
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return Image.open(io.BytesIO(r.content)).convert("RGB")
            print(f"    tile {tx},{ty} → HTTP {r.status_code}")
        except Exception as e:
            print(f"    tile {tx},{ty} err {attempt+1}: {e}")
        time.sleep(1.5 ** attempt)
    return None

def build_mosaic():
    tx0, ty0 = _lat_lon_to_tile(LAT_MAX, LON_MIN, ZOOM)   # NW
    tx1, ty1 = _lat_lon_to_tile(LAT_MIN, LON_MAX, ZOOM)   # SE
    tx0, tx1 = min(tx0, tx1), max(tx0, tx1)
    ty0, ty1 = min(ty0, ty1), max(ty0, ty1)
    n_x = tx1 - tx0 + 1
    n_y = ty1 - ty0 + 1
    print(f"  tiles x:{tx0}–{tx1} ({n_x})  y:{ty0}–{ty1} ({n_y})  total:{n_x*n_y}")
    mosaic = Image.new("RGB", (n_x * TILE_SIZE, n_y * TILE_SIZE))
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 OilRadar geometry survey"
    ok = fail = 0
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = _fetch_tile(tx, ty, ZOOM, session)
            if tile:
                mosaic.paste(tile, ((tx - tx0) * TILE_SIZE, (ty - ty0) * TILE_SIZE))
                ok += 1
            else:
                fail += 1
            time.sleep(0.04)
    print(f"  fetched:{ok}  failed:{fail}  size:{mosaic.width}×{mosaic.height}")
    mpp = _m_per_px_at_lat((LAT_MIN + LAT_MAX) / 2, ZOOM)
    print(f"  m/px at lat_mid: {mpp:.4f}")
    return mosaic, tx0, ty0, mpp


# ── 16-ray measurement ────────────────────────────────────────────────────────
def measure_tank(gray, cx, cy, r_guess_px, n_rays=RAYS):
    """
    16-ray edge measurement.
    Returns dict with r_rays, cx_fit, cy_fit, r_fit_px,
    median_residual_px, edge_energies, quality_flag.
    """
    h, w = gray.shape
    r_in  = max(5.0, r_guess_px * 0.45)
    r_out = min(r_guess_px * 1.55, min(h, w) / 2.0 - 2)

    angles = [2 * math.pi * i / n_rays for i in range(n_rays)]
    edge_xs, edge_ys, edge_gs = [], [], []

    for ang in angles:
        dx, dy = math.cos(ang), math.sin(ang)
        # Sample gradient along ray at 0.5px steps
        n_samples = max(8, int((r_out - r_in) * 2))
        ts = np.linspace(r_in, r_out, n_samples)
        gs = []
        for t in ts:
            xi = int(round(cx + dx * t))
            yi = int(round(cy + dy * t))
            if not (1 <= xi < w - 1 and 1 <= yi < h - 1):
                gs.append(0.0)
                continue
            # 3×3 gradient (std as proxy for edge)
            patch = gray[yi-1:yi+2, xi-1:xi+2].astype(float)
            gs.append(float(np.std(patch)))

        if not gs or max(gs) < 1e-3:
            continue
        best_i = int(np.argmax(gs))
        best_t = ts[best_i]
        ex = cx + dx * best_t
        ey = cy + dy * best_t
        edge_xs.append(ex)
        edge_ys.append(ey)
        edge_gs.append(gs[best_i])

    n_valid = len(edge_xs)
    if n_valid < 4:
        return {"quality": "no_tank_visible", "n_rays": n_valid,
                "cx_fit": cx, "cy_fit": cy, "r_fit_px": r_guess_px,
                "edge_energy_mean": 0.0, "median_residual_px": 999.0}

    # Fit centre
    cx_fit = float(np.mean(edge_xs))
    cy_fit = float(np.mean(edge_ys))
    r_rays = [math.hypot(edge_xs[i] - cx_fit, edge_ys[i] - cy_fit)
              for i in range(n_valid)]
    r_fit_px = float(np.mean(r_rays))

    # Residuals from mean radius
    residuals = [abs(r - r_fit_px) for r in r_rays]
    med_res   = float(np.median(residuals))

    edge_energy_mean = float(np.mean(edge_gs))

    return {
        "quality":            "ok",   # updated below by caller
        "n_rays":             n_valid,
        "edge_xs":            edge_xs,
        "edge_ys":            edge_ys,
        "cx_fit":             cx_fit,
        "cy_fit":             cy_fit,
        "r_fit_px":           r_fit_px,
        "r_rays":             r_rays,
        "median_residual_px": med_res,
        "edge_energy_mean":   edge_energy_mean,
    }


# ── Hough circle census ───────────────────────────────────────────────────────
def hough_census(gray, mpp):
    """
    Simple accumulator-based Hough circle detection on down-sampled image.
    Returns list of (cx_px, cy_px, r_px) in full-resolution coords.
    """
    # Down-sample 4× for speed
    DSAMP = 4
    h, w = gray.shape
    sm_h, sm_w = h // DSAMP, w // DSAMP
    small = gray[::DSAMP, ::DSAMP].astype(np.float32)

    # Gradient edges
    gy = np.diff(small, axis=0, prepend=small[:1])
    gx = np.diff(small, axis=1, prepend=small[:, :1])
    gmag = np.sqrt(gx**2 + gy**2)

    # Threshold top 5% edges
    thr = np.percentile(gmag, 95)
    edge_ys, edge_xs = np.where(gmag > thr)

    if len(edge_xs) == 0:
        return []

    r_min_px = max(2, int(HOUGH_R_MIN_M / mpp / DSAMP))
    r_max_px = max(r_min_px + 1, int(HOUGH_R_MAX_M / mpp / DSAMP))

    # Limit edge points for speed
    if len(edge_xs) > 5000:
        idx = np.random.choice(len(edge_xs), 5000, replace=False)
        edge_xs = edge_xs[idx]
        edge_ys = edge_ys[idx]

    # Accumulator
    acc = {}
    for r in range(r_min_px, r_max_px + 1):
        for i in range(len(edge_xs)):
            ex, ey = int(edge_xs[i]), int(edge_ys[i])
            for ang_deg in range(0, 360, 5):
                ang = math.radians(ang_deg)
                cx = int(round(ex - r * math.cos(ang)))
                cy = int(round(ey - r * math.sin(ang)))
                if 0 <= cx < sm_w and 0 <= cy < sm_h:
                    key = (cx, cy, r)
                    acc[key] = acc.get(key, 0) + 1

    # Collect peaks
    candidates = [(v, k) for k, v in acc.items() if v >= HOUGH_MIN_VOTES]
    candidates.sort(reverse=True)

    # NMS: suppress within 2r
    detected = []
    suppressed = set()
    for votes, (cx, cy, r) in candidates[:2000]:
        if (cx, cy, r) in suppressed:
            continue
        detected.append((cx * DSAMP, cy * DSAMP, r * DSAMP, votes))
        # suppress nearby
        for dr in range(-2, 3):
            for dcx in range(-r*2, r*2+1, max(1, r//2)):
                for dcy in range(-r*2, r*2+1, max(1, r//2)):
                    suppressed.add((cx + dcx // DSAMP, cy + dcy // DSAMP, r + dr))

    # Convert radius back to metres
    result = []
    for cx_full, cy_full, r_full, v in detected[:80]:
        r_m = r_full * mpp
        result.append({"cx_px": cx_full, "cy_px": cy_full,
                        "r_px": r_full, "r_m": round(r_m, 1), "votes": v})
    return result


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log_lines = []
    def log(msg):
        print(msg); log_lines.append(msg)

    log("=== Al-Faw geometry v2 — Web Mercator + fit_quality + census ===")

    # ── Step 1: mosaic ────────────────────────────────────────────────────────
    mosaic_path = OUT_DIR / "esri_mosaic_z18.png"
    geo_path    = OUT_DIR / "mosaic_geo.json"
    log(f"\n[Step 1] Esri z{ZOOM} mosaic…")

    if mosaic_path.exists() and geo_path.exists():
        log("  cache hit")
        mosaic = Image.open(mosaic_path).convert("RGB")
        with open(geo_path) as f:
            g = json.load(f)
        tx0, ty0, mpp = g["tx0"], g["ty0"], g["mpp"]
    else:
        mosaic, tx0, ty0, mpp = build_mosaic()
        mosaic.save(mosaic_path)
        with open(geo_path, "w") as f:
            json.dump({"tx0": tx0, "ty0": ty0, "mpp": mpp}, f)
        log(f"  saved {mosaic_path}")

    log(f"  {mosaic.width}×{mosaic.height} px  mpp={mpp:.4f} m/px  tx0={tx0} ty0={ty0}")

    gray_arr = np.mean(np.array(mosaic), axis=2).astype(np.float32)

    # ── Step 2: 16-ray measurement ────────────────────────────────────────────
    log(f"\n[Step 2] 16-ray measurement with fit_quality…")
    log(f"  edge_energy threshold: >{EDGE_ENERGY_MIN}  residual poor: >{RESIDUAL_POOR_M}m")

    priority = ["af_8", "af_17", "af_22"]
    all_ids  = priority + [t["id"] for t in TANKS_V1 if t["id"] not in priority]
    tank_map = {t["id"]: t for t in TANKS_V1}

    results = {}
    for tid in all_ids:
        t = tank_map[tid]
        cx_px, cy_px = geo_to_mosaic_px(t["lat"], t["lon"], ZOOM, tx0, ty0)
        r_guess_px   = t["radius_m"] / mpp

        h, w = gray_arr.shape
        margin = r_guess_px * 1.7
        if not (margin < cx_px < w - margin and margin < cy_px < h - margin):
            log(f"  {tid}: out_of_mosaic  cx={cx_px:.0f} cy={cy_px:.0f}")
            results[tid] = {"quality": "out_of_mosaic"}
            continue

        m = measure_tank(gray_arr, cx_px, cy_px, r_guess_px)

        if m["quality"] == "no_tank_visible":
            log(f"  {tid}: no_tank_visible  energy={m['edge_energy_mean']:.2f}  n_rays={m['n_rays']}")
            results[tid] = {**m, "cx_px": cx_px, "cy_px": cy_px}
            continue

        # Classify quality
        r_fit_m         = m["r_fit_px"] * mpp
        med_res_m       = m["median_residual_px"] * mpp
        dx_px           = m["cx_fit"] - cx_px
        dy_px           = m["cy_fit"] - cy_px
        dx_m            = dx_px * mpp
        dy_m            = -dy_px * mpp   # y↑ = north
        shift_m         = math.hypot(dx_m, dy_m)

        if m["edge_energy_mean"] < EDGE_ENERGY_MIN:
            quality = "no_tank_visible"
        elif med_res_m > RESIDUAL_POOR_M:
            quality = "poor"
        else:
            quality = "good"

        # Lat/lon of fitted centre
        # Invert: global_px = tx0*256 + cx_fit
        # lon = global_px / (2^z * 256) * 360 - 180
        n_full = 2 ** ZOOM * TILE_SIZE
        gx_fit = tx0 * TILE_SIZE + m["cx_fit"]
        gy_fit = ty0 * TILE_SIZE + m["cy_fit"]
        lon_v2 = gx_fit / n_full * 360.0 - 180.0
        lat_v2 = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * gy_fit / n_full))))

        results[tid] = {
            "quality":            quality,
            "r_reg_m":            t["radius_m"],
            "r_fit_m":            round(r_fit_m, 1),
            "median_residual_m":  round(med_res_m, 1),
            "edge_energy_mean":   round(m["edge_energy_mean"], 2),
            "dx_m":               round(dx_m, 1),
            "dy_m":               round(dy_m, 1),
            "shift_m":            round(shift_m, 1),
            "n_rays":             m["n_rays"],
            "cx_px":              cx_px,
            "cy_px":              cy_px,
            "cx_fit":             m["cx_fit"],
            "cy_fit":             m["cy_fit"],
            "r_fit_px":           m["r_fit_px"],
            "lat_v2":             lat_v2,
            "lon_v2":             lon_v2,
        }

        flag = "" if quality == "good" else f"  [{quality}]"
        log(f"  {tid}: r_fit={r_fit_m:.1f}m  res={med_res_m:.1f}m  "
            f"energy={m['edge_energy_mean']:.2f}  shift={shift_m:.1f}m  "
            f"({dx_m:+.1f},{dy_m:+.1f})  q={quality}{flag}")

    # ── Step 3: Hough circle census ───────────────────────────────────────────
    log(f"\n[Step 3] Hough circle census (r={HOUGH_R_MIN_M}–{HOUGH_R_MAX_M}m)…")
    np.random.seed(42)
    detected = hough_census(gray_arr, mpp)
    log(f"  Hough detected: {len(detected)} circles")

    # Match detected circles to registry tanks (tolerance 30m)
    MATCH_TOL_PX = 30.0 / mpp
    registry_pxs = []
    for t in TANKS_V1:
        px, py = geo_to_mosaic_px(t["lat"], t["lon"], ZOOM, tx0, ty0)
        registry_pxs.append((px, py, t["id"]))

    n_full = 2 ** ZOOM * TILE_SIZE
    unmatched = []
    for d in detected:
        cx_d, cy_d = d["cx_px"], d["cy_px"]
        matched = False
        for rx, ry, rid in registry_pxs:
            if math.hypot(cx_d - rx, cy_d - ry) < MATCH_TOL_PX:
                matched = True
                break
        if not matched:
            # Convert to lat/lon
            gx = tx0 * TILE_SIZE + cx_d
            gy = ty0 * TILE_SIZE + cy_d
            lon_d = gx / n_full * 360.0 - 180.0
            lat_d = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * gy / n_full))))
            unmatched.append({**d, "lat": round(lat_d, 6), "lon": round(lon_d, 6)})

    log(f"  Matched to registry: {len(detected) - len(unmatched)}")
    log(f"  Visible but NOT in registry: {len(unmatched)}")
    if unmatched:
        log(f"\n  {'lat':>10}  {'lon':>10}  {'r_m':>6}  votes")
        for u in sorted(unmatched, key=lambda x: -x["votes"])[:50]:
            log(f"  {u['lat']:>10.6f}  {u['lon']:>10.6f}  {u['r_m']:>6.1f}  {u['votes']}")

    # ── Step 4: geometry table CSV ────────────────────────────────────────────
    log("\n[Step 4] geometry_v2.csv (quality-filtered)…")
    good_count = sum(1 for v in results.values() if v.get("quality") == "good")
    poor_count = sum(1 for v in results.values() if v.get("quality") == "poor")
    skip_count = sum(1 for v in results.values()
                     if v.get("quality") in ("no_tank_visible", "out_of_mosaic"))
    log(f"  good: {good_count}  poor: {poor_count}  excluded: {skip_count}")

    rows_csv = []
    for tid in all_ids:
        t  = tank_map[tid]
        rv = results.get(tid, {})
        q  = rv.get("quality", "unknown")
        if q in ("no_tank_visible", "out_of_mosaic"):
            rows_csv.append({
                "tank_id": tid, "quality": q,
                "r_reg_m": t["radius_m"], "r_fit_m": "", "median_residual_m": "",
                "edge_energy": "", "shift_m": "", "dx_m": "", "dy_m": "",
                "lat_v2": t["lat"], "lon_v2": t["lon"], "notes": q
            })
        else:
            notes = ""
            if rv["shift_m"] > 10: notes += "large_shift "
            if rv["r_fit_m"] > 50 or rv["r_fit_m"] < 28: notes += "r_outlier "
            notes = notes.strip()
            rows_csv.append({
                "tank_id": tid, "quality": q,
                "r_reg_m": rv["r_reg_m"], "r_fit_m": rv["r_fit_m"],
                "median_residual_m": rv["median_residual_m"],
                "edge_energy": rv["edge_energy_mean"],
                "shift_m": rv["shift_m"],
                "dx_m": rv["dx_m"], "dy_m": rv["dy_m"],
                "lat_v2": round(rv["lat_v2"], 8), "lon_v2": round(rv["lon_v2"], 8),
                "notes": notes
            })

    csv_path = OUT_DIR / "geometry_v2.csv"
    fields = ["tank_id","quality","r_reg_m","r_fit_m","median_residual_m",
              "edge_energy","shift_m","dx_m","dy_m","lat_v2","lon_v2","notes"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows_csv)
    log(f"  saved {csv_path}")

    # ── Step 5: registry_v2.json (good + poor only, no_tank_visible excluded) ─
    log("\n[Step 5] registry_v2.json…")
    BPERM3 = 6.2898
    tanks_v2 = []
    total_cap_mb = 0.0
    for t in TANKS_V1:
        rv = results.get(t["id"], {})
        q  = rv.get("quality", "unknown")
        if q in ("good", "poor"):
            r_v2 = max(25, min(60, round(rv["r_fit_m"])))
            tanks_v2.append({
                "id": t["id"],
                "lat": round(rv["lat_v2"], 8),
                "lon": round(rv["lon_v2"], 8),
                "radius_m": r_v2,
                "h_max_m": 14, "h_source": "assumed",
                "r_source": f"esri_z{ZOOM}_16ray",
                "fit_quality": q,
                "median_residual_m": rv["median_residual_m"],
                "shift_m": rv["shift_m"],
            })
        else:
            # Keep v1 values, mark excluded
            tanks_v2.append({**t, "h_max_m": 14, "h_source": "assumed",
                             "r_source": "v1_kept", "fit_quality": q})
        r_use = tanks_v2[-1]["radius_m"]
        total_cap_mb += math.pi * r_use**2 * 14 * BPERM3 / 1e6

    reg = {
        "terminal_id": "al_flaw", "version": "v2",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": f"Esri World Imagery z={ZOOM} 16-ray Web-Mercator",
        "total_cap_mb": round(total_cap_mb, 3),
        "tanks": tanks_v2,
    }
    reg_path = OUT_DIR / "registry_v2.json"
    with open(reg_path, "w") as f:
        json.dump(reg, f, indent=2)
    log(f"  total_cap_mb={total_cap_mb:.3f}  saved {reg_path}")

    # ── Step 6: annotated mosaic ──────────────────────────────────────────────
    log("\n[Step 6] Annotated mosaic…")
    ann = mosaic.copy()
    draw = ImageDraw.Draw(ann)

    # Draw Hough-detected unmatched circles (magenta, thin)
    for u in unmatched[:60]:
        cx_, cy_, rp_ = u["cx_px"], u["cy_px"], u["r_px"]
        draw.ellipse([cx_-rp_, cy_-rp_, cx_+rp_, cy_+rp_],
                     outline=(220, 0, 220), width=1)

    # Draw registry tanks
    COLORS = {"good": (0, 220, 255), "poor": (255, 165, 0),
              "no_tank_visible": (255, 80, 80), "out_of_mosaic": (128, 128, 128)}
    for t in TANKS_V1:
        rv = results.get(t["id"], {})
        q  = rv.get("quality", "unknown")
        # v1 circle (yellow, thin)
        cx0, cy0 = geo_to_mosaic_px(t["lat"], t["lon"], ZOOM, tx0, ty0)
        r0 = t["radius_m"] / mpp
        draw.ellipse([cx0-r0, cy0-r0, cx0+r0, cy0+r0], outline=(255, 220, 0), width=1)
        # v2 fitted circle
        col = COLORS.get(q, (180, 180, 180))
        lw = 2 if q == "good" else 1
        if q in ("good", "poor") and "cx_fit" in rv:
            cx2, cy2, r2 = rv["cx_fit"], rv["cy_fit"], rv["r_fit_px"]
            draw.ellipse([cx2-r2, cy2-r2, cx2+r2, cy2+r2], outline=col, width=lw)
            if rv["shift_m"] > 5:
                draw.line([cx0, cy0, cx2, cy2], fill=(255, 80, 80), width=2)
        # Label
        label_x = cx0 + r0 * 0.7
        label_y = cy0 - r0 * 0.7
        draw.text((label_x, label_y), t["id"].replace("af_", ""), fill=(255, 255, 255))

    # Legend
    draw.rectangle([6, 6, 240, 92], fill=(0, 0, 0))
    items = [("yellow: v1 registry", (255,220,0)),
             ("cyan:   v2 good fit", (0,220,255)),
             ("orange: v2 poor fit", (255,165,0)),
             ("red:    no_tank / shift>5m", (255,80,80)),
             ("magenta: Hough unregistered", (220,0,220))]
    for i, (txt, col) in enumerate(items):
        draw.text((10, 10 + i * 16), txt, fill=col)

    ann_path = OUT_DIR / "mosaic_annotated.png"
    ann.save(ann_path)
    log(f"  saved {ann_path}")

    # ── Step 7: unmatched census CSV ──────────────────────────────────────────
    census_path = OUT_DIR / "hough_unregistered.csv"
    with open(census_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["lat","lon","r_m","votes"])
        w.writeheader()
        for u in sorted(unmatched, key=lambda x: -x["votes"]):
            w.writerow({"lat": u["lat"], "lon": u["lon"],
                        "r_m": u["r_m"], "votes": u["votes"]})
    log(f"  unregistered census → {census_path}")

    (OUT_DIR / "run.txt").write_text("\n".join(log_lines) + "\n")
    log("\nDone.")


if __name__ == "__main__":
    main()
