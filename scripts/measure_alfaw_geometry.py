"""
measure_alfaw_geometry.py — Al-Faw geometry v2 (ТЗ, строгий порядок)
=====================================================================
Steps:
  1. Fetch Esri World Imagery XYZ tiles (z=18, ~0.52 m/px) for Al-Faw bbox → mosaic PNG
  2. 16-ray circle measurement for all 24 tanks → r_measured, shift (dx, dy)
  3. Write v2 registry JSON (updated centres + radii, h_max=14 kept, h_source=assumed)
  4. Write geometry table CSV: tank | r_reg | r_measured | shift_m | dx_m | dy_m
  5. Write annotated mosaic PNG (tank circles + IDs)

Outputs (all in diagnostics/al_faw/geometry/):
  esri_mosaic_z18.png     — raw stitched mosaic
  mosaic_annotated.png    — mosaic with v2 circles overlaid
  geometry_v2.csv         — measurement table
  registry_v2.json        — v2 TANKS list for stage7 / ingest
  run.txt                 — log

Rules (ТЗ):
  - measurement is the ONLY source for registry edits
  - visual verdicts = analyst
  - "вероятно" prohibited
"""

import os, sys, math, io, json, time
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

# ── Config ────────────────────────────────────────────────────────────────────
ZOOM = 18          # ~0.52 m/px at lat 30°

# Al-Faw bbox with buffer (covers all 24 tanks)
LAT_MIN, LAT_MAX = 29.958, 29.980
LON_MIN, LON_MAX = 48.436, 48.462

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw" / "geometry"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
TILE_SIZE = 256   # px per tile
RAYS      = 16    # 16-ray measurement

# v1 registry (stage7 coords) — source of truth for initial window centres
TANKS_V1 = [
    {"id": "af_0",  "lat": 29.976037712391882, "lon": 48.44195552300743,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_1",  "lat": 29.97446023561727,  "lon": 48.44041073298363,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_2",  "lat": 29.973482237683964, "lon": 48.44170682926327,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_3",  "lat": 29.97506237979022,  "lon": 48.443256604287775, "radius_m": 40, "h_max_m": 14},
    {"id": "af_4",  "lat": 29.972052466075166, "lon": 48.44360540262179,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_5",  "lat": 29.973613605327138, "lon": 48.44517136039961,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_6",  "lat": 29.971043737749962, "lon": 48.44490239092673,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_7",  "lat": 29.972645241700267, "lon": 48.44646203210755,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_8",  "lat": 29.970602225788706, "lon": 48.44914349229505,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_9",  "lat": 29.96964506640903,  "lon": 48.45042448972437,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_10", "lat": 29.969053578374012, "lon": 48.44757323726147,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_11", "lat": 29.968074369587153, "lon": 48.44888119527686,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_12", "lat": 29.967562585491844, "lon": 48.44613477210609,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_13", "lat": 29.966616705980595, "lon": 48.44740303358956,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_14", "lat": 29.965160381102958, "lon": 48.449324515383225, "radius_m": 40, "h_max_m": 14},
    {"id": "af_15", "lat": 29.966613187227228, "lon": 48.45079062045803,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_16", "lat": 29.964164829146835, "lon": 48.450625111425616, "radius_m": 40, "h_max_m": 14},
    {"id": "af_17", "lat": 29.965658494437534, "lon": 48.45208349893114,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_18", "lat": 29.961779354173064, "lon": 48.45380166725049,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_19", "lat": 29.96274476143991,  "lon": 48.45253449582525,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_20", "lat": 29.964218037290507, "lon": 48.45399437056702,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_21", "lat": 29.96326674821542,  "lon": 48.45527466098349,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_22", "lat": 29.964822804270934, "lon": 48.45681522152186,  "radius_m": 40, "h_max_m": 14},
    {"id": "af_23", "lat": 29.96580178961507,  "lon": 48.45553712042206,  "radius_m": 40, "h_max_m": 14},
]

# ── Tile helpers ──────────────────────────────────────────────────────────────
def _lat_lon_to_tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return x, y

def _tile_nw_corner(tx, ty, z):
    """Return (lat, lon) of the NW corner of tile (tx,ty) at zoom z."""
    n = 2 ** z
    lon = tx / n * 360 - 180
    lat_r = math.atan(math.sinh(math.pi * (1 - 2 * ty / n)))
    lat = math.degrees(lat_r)
    return lat, lon

def _meters_per_px(lat, z):
    C = 2 * math.pi * 6378137  # Earth circumference
    return C * math.cos(math.radians(lat)) / (2 ** z * TILE_SIZE)

def _fetch_tile(tx, ty, z, session, retries=3):
    url = TILE_URL.format(z=z, y=ty, x=tx)
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return Image.open(io.BytesIO(r.content)).convert("RGB")
            print(f"    tile {tx},{ty} → HTTP {r.status_code}")
        except Exception as e:
            print(f"    tile {tx},{ty} attempt {attempt+1} error: {e}")
        time.sleep(1.5 ** attempt)
    return None


# ── Mosaic ────────────────────────────────────────────────────────────────────
def build_mosaic(lat_min, lat_max, lon_min, lon_max, z):
    """Download and stitch tiles. Returns (mosaic_img, mosaic_geo) where
    mosaic_geo = (lat_nw, lon_nw, m_per_px)."""
    tx0, ty0 = _lat_lon_to_tile(lat_max, lon_min, z)  # NW corner → smallest ty
    tx1, ty1 = _lat_lon_to_tile(lat_min, lon_max, z)  # SE corner → largest ty
    # Ensure correct ordering
    tx0, tx1 = min(tx0, tx1), max(tx0, tx1)
    ty0, ty1 = min(ty0, ty1), max(ty0, ty1)

    n_x = tx1 - tx0 + 1
    n_y = ty1 - ty0 + 1
    print(f"  tiles x: {tx0}–{tx1} ({n_x} tiles)")
    print(f"  tiles y: {ty0}–{ty1} ({n_y} tiles)")
    print(f"  total: {n_x * n_y} tiles")

    mosaic_w = n_x * TILE_SIZE
    mosaic_h = n_y * TILE_SIZE
    mosaic = Image.new("RGB", (mosaic_w, mosaic_h))

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 OilRadar geometry survey (research)"

    fetched, failed = 0, 0
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = _fetch_tile(tx, ty, z, session)
            if tile:
                px = (tx - tx0) * TILE_SIZE
                py = (ty - ty0) * TILE_SIZE
                mosaic.paste(tile, (px, py))
                fetched += 1
            else:
                failed += 1
            time.sleep(0.05)   # polite rate limit

    print(f"  fetched: {fetched}  failed: {failed}")

    # NW corner lat/lon of the stitched mosaic
    lat_nw, lon_nw = _tile_nw_corner(tx0, ty0, z)
    m_per_px = _meters_per_px((lat_min + lat_max) / 2, z)
    print(f"  mosaic size: {mosaic_w}×{mosaic_h} px")
    print(f"  NW corner: ({lat_nw:.6f}, {lon_nw:.6f})")
    print(f"  resolution: {m_per_px:.3f} m/px")
    return mosaic, (lat_nw, lon_nw, m_per_px)


# ── Geo ↔ pixel ───────────────────────────────────────────────────────────────
def geo_to_px(lat, lon, lat_nw, lon_nw, m_per_px):
    """Convert (lat, lon) to pixel (x, y) in the mosaic."""
    # Use Mercator projection consistent with tile scheme
    def merc_y(lat_deg):
        lat_r = math.radians(lat_deg)
        return math.log(math.tan(math.pi / 4 + lat_r / 2))

    m_nw = merc_y(lat_nw)
    m_pt = merc_y(lat)

    # 1 degree longitude = (m_per_px * TILE_SIZE * 2**z / 360) ← but we already have m_per_px
    # Simpler: use the fact that at zoom z, 1 tile = TILE_SIZE px = 2π*R*cos(lat)/(2**z) m
    # For x (longitude): linear
    # For y (Mercator): use ratio
    R = 6378137.0
    x_px = (lon - lon_nw) * math.cos(math.radians((lat + lat_nw) / 2)) * R / m_per_px * math.pi / 180
    y_px = (m_nw - m_pt) * R / m_per_px

    return float(x_px), float(y_px)


# ── 16-ray edge measurement ───────────────────────────────────────────────────
def _gradient_mag(arr_gray):
    """Simple Sobel-like gradient magnitude on float32 array."""
    from scipy.ndimage import sobel
    sx = sobel(arr_gray, axis=1)
    sy = sobel(arr_gray, axis=0)
    return np.sqrt(sx**2 + sy**2)


def measure_tank(mosaic_arr, cx_px, cy_px, r_guess_px, n_rays=16, search_frac=0.6):
    """
    16-ray measurement.  Cast n_rays from (cx_px, cy_px), search along each ray
    in [r_guess*(1-search_frac) .. r_guess*(1+search_frac)].
    Edge = peak gradient pixel.

    Returns:
      r_px_list  — measured radius per ray (px)
      cx_fit, cy_fit — fitted centre (from average of edge points)
      r_fit_px       — mean radius (px)
    """
    h, w = mosaic_arr.shape[:2]
    gray = np.mean(mosaic_arr, axis=2).astype(np.float32)

    r_inner = r_guess_px * (1 - search_frac)
    r_outer = r_guess_px * (1 + search_frac)
    r_inner = max(4, r_inner)
    r_outer = min(r_outer, min(h, w) / 2 - 2)

    angles = [2 * math.pi * i / n_rays for i in range(n_rays)]
    edge_xs, edge_ys, radii = [], [], []

    for ang in angles:
        dx = math.cos(ang)
        dy = math.sin(ang)
        # Sample gradient along ray
        t_vals = np.linspace(r_inner, r_outer, int((r_outer - r_inner) * 2) + 4)
        grads = []
        for t in t_vals:
            xi = int(round(cx_px + dx * t))
            yi = int(round(cy_px + dy * t))
            if 0 <= xi < w and 0 <= yi < h:
                # 3×3 local gradient
                x0, x1 = max(0, xi-1), min(w-1, xi+1)
                y0, y1 = max(0, yi-1), min(h-1, yi+1)
                patch = gray[y0:y1+1, x0:x1+1].astype(float)
                g = float(np.std(patch))
            else:
                g = 0.0
            grads.append(g)
        if not grads or max(grads) < 1.0:
            continue
        best_t = t_vals[int(np.argmax(grads))]
        ex = cx_px + dx * best_t
        ey = cy_px + dy * best_t
        edge_xs.append(ex)
        edge_ys.append(ey)
        radii.append(best_t)

    if len(radii) < 4:
        return radii, cx_px, cy_px, r_guess_px  # fallback

    # Fit centre as mean of edge points
    cx_fit = float(np.mean(edge_xs))
    cy_fit = float(np.mean(edge_ys))
    # Recompute radii from fitted centre
    radii_fit = [math.hypot(edge_xs[i] - cx_fit, edge_ys[i] - cy_fit)
                 for i in range(len(edge_xs))]
    r_fit_px = float(np.mean(radii_fit))

    return radii_fit, cx_fit, cy_fit, r_fit_px


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log_lines = []
    def log(msg):
        print(msg)
        log_lines.append(msg)

    log("=== Al-Faw geometry v2 ===")
    log(f"zoom={ZOOM}  bbox=lat[{LAT_MIN},{LAT_MAX}] lon[{LON_MIN},{LON_MAX}]")

    # ── Step 1: mosaic ────────────────────────────────────────────────────────
    log("\n[Step 1] Fetching Esri World Imagery tiles…")
    mosaic_path = OUT_DIR / "esri_mosaic_z18.png"
    geo_path    = OUT_DIR / "mosaic_geo.json"

    if mosaic_path.exists() and geo_path.exists():
        log("  mosaic cache hit — loading")
        mosaic = Image.open(mosaic_path).convert("RGB")
        with open(geo_path) as f:
            geo = json.load(f)
        lat_nw, lon_nw, m_per_px = geo["lat_nw"], geo["lon_nw"], geo["m_per_px"]
        log(f"  {mosaic.width}×{mosaic.height} px, {m_per_px:.3f} m/px")
    else:
        mosaic, (lat_nw, lon_nw, m_per_px) = build_mosaic(
            LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, ZOOM)
        mosaic.save(mosaic_path)
        with open(geo_path, "w") as f:
            json.dump({"lat_nw": lat_nw, "lon_nw": lon_nw, "m_per_px": m_per_px}, f)
        log(f"  saved {mosaic_path}")

    mosaic_arr = np.array(mosaic)

    # ── Step 2: 16-ray measurement ────────────────────────────────────────────
    log("\n[Step 2] 16-ray measurement (af_8, af_17, af_22 first, then rest)…")

    # Priority check tanks first
    priority = ["af_8", "af_17", "af_22"]
    all_ids   = priority + [t["id"] for t in TANKS_V1 if t["id"] not in priority]
    tank_by_id = {t["id"]: t for t in TANKS_V1}

    results = {}
    for tid in all_ids:
        t = tank_by_id[tid]
        cx_px, cy_px = geo_to_px(t["lat"], t["lon"], lat_nw, lon_nw, m_per_px)
        r_guess_px = t["radius_m"] / m_per_px

        h, w = mosaic_arr.shape[:2]
        if not (20 < cx_px < w - 20 and 20 < cy_px < h - 20):
            log(f"  {tid}: OUT OF MOSAIC (cx={cx_px:.0f}, cy={cy_px:.0f}) — skipping")
            results[tid] = {"skip": True, "cx_px": cx_px, "cy_px": cy_px}
            continue

        radii_fit, cx_fit, cy_fit, r_fit_px = measure_tank(
            mosaic_arr, cx_px, cy_px, r_guess_px, n_rays=RAYS)

        r_fit_m  = r_fit_px * m_per_px
        dx_px    = cx_fit - cx_px
        dy_px    = cy_fit - cy_px
        dx_m     = dx_px * m_per_px    # positive = east
        dy_m     = -dy_px * m_per_px   # positive = north (y inverted)
        shift_m  = math.hypot(dx_m, dy_m)

        # Convert fitted centre back to lat/lon
        # Inverse: lon = lon_nw + cx_fit * m_per_px / (R*cos(lat_mid)*pi/180)
        R = 6378137.0
        lat_mid = (lat_nw + LAT_MIN) / 2
        lon_v2 = t["lon"] + dx_m / (R * math.cos(math.radians(lat_mid)) * math.pi / 180)
        lat_v2 = t["lat"] + dy_m / (R * math.pi / 180)

        results[tid] = {
            "skip":     False,
            "r_reg_m":  t["radius_m"],
            "r_fit_m":  round(r_fit_m, 1),
            "dx_m":     round(dx_m, 1),
            "dy_m":     round(dy_m, 1),
            "shift_m":  round(shift_m, 1),
            "cx_px":    cx_px,
            "cy_px":    cy_px,
            "cx_fit":   cx_fit,
            "cy_fit":   cy_fit,
            "r_fit_px": r_fit_px,
            "lat_v2":   lat_v2,
            "lon_v2":   lon_v2,
        }
        log(f"  {tid}: r_reg={t['radius_m']}m  r_fit={r_fit_m:.1f}m  "
            f"shift={shift_m:.1f}m (dx={dx_m:+.1f}m, dy={dy_m:+.1f}m)  "
            f"n_rays={len(radii_fit)}")

    # ── Step 3: geometry table CSV ─────────────────────────────────────────────
    log("\n[Step 3] Writing geometry_v2.csv…")
    csv_lines = ["tank_id,r_reg_m,r_measured_m,shift_m,dx_m,dy_m,lat_v2,lon_v2,notes"]
    for tid in all_ids:
        t  = tank_by_id[tid]
        rv = results.get(tid, {})
        if rv.get("skip"):
            csv_lines.append(f"{tid},{t['radius_m']},NA,NA,NA,NA,{t['lat']:.8f},{t['lon']:.8f},out_of_mosaic")
        else:
            notes = ""
            if rv["shift_m"] > 10:
                notes = "large_shift"
            if abs(rv["r_fit_m"] - rv["r_reg_m"]) > 5:
                notes += ("+" if notes else "") + "r_mismatch"
            csv_lines.append(
                f"{tid},{rv['r_reg_m']},{rv['r_fit_m']},{rv['shift_m']},"
                f"{rv['dx_m']},{rv['dy_m']},"
                f"{rv['lat_v2']:.8f},{rv['lon_v2']:.8f},"
                f"{notes}")

    csv_path = OUT_DIR / "geometry_v2.csv"
    csv_path.write_text("\n".join(csv_lines) + "\n")
    log(f"  saved {csv_path}")

    # Print summary table
    log("\n  tank_id  r_reg  r_fit  shift_m   dx_m   dy_m")
    log("  " + "-"*55)
    for tid in all_ids:
        rv = results.get(tid, {})
        if rv.get("skip"):
            log(f"  {tid:<8}  {tank_by_id[tid]['radius_m']}     NA       NA       NA     NA")
        else:
            log(f"  {tid:<8}  {rv['r_reg_m']:<5}  {rv['r_fit_m']:<5.1f}  {rv['shift_m']:>6.1f}  {rv['dx_m']:>6.1f}  {rv['dy_m']:>6.1f}")

    # ── Step 4: registry v2 JSON ──────────────────────────────────────────────
    log("\n[Step 4] Writing registry_v2.json…")
    BARREL_PER_M3 = 6.2898
    tanks_v2 = []
    total_cap_mb = 0.0
    for t in TANKS_V1:
        tid = t["id"]
        rv  = results.get(tid, {})
        if rv.get("skip") or not rv:
            tanks_v2.append({**t, "h_source": "assumed"})
            cap = math.pi * t["radius_m"]**2 * t["h_max_m"] * BARREL_PER_M3 / 1e6
        else:
            r_v2 = round(rv["r_fit_m"])   # round to nearest metre
            lat_v2 = rv["lat_v2"]
            lon_v2 = rv["lon_v2"]
            tanks_v2.append({
                "id":       tid,
                "lat":      round(lat_v2, 8),
                "lon":      round(lon_v2, 8),
                "radius_m": r_v2,
                "h_max_m":  t["h_max_m"],
                "h_source": "assumed",
                "r_source": "esri_z18_16ray",
                "r_v1_m":   t["radius_m"],
                "shift_m":  rv["shift_m"],
            })
            cap = math.pi * r_v2**2 * t["h_max_m"] * BARREL_PER_M3 / 1e6
        total_cap_mb += cap

    reg_v2 = {
        "terminal_id":   "al_flaw",
        "version":       "v2",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source":        f"Esri World Imagery z={ZOOM} 16-ray measurement",
        "h_source_note": "h_max=14 assumed — no engineering specs available",
        "total_cap_mb":  round(total_cap_mb, 3),
        "tanks":         tanks_v2,
    }
    reg_path = OUT_DIR / "registry_v2.json"
    with open(reg_path, "w") as f:
        json.dump(reg_v2, f, indent=2)
    log(f"  total_cap_mb (v2) = {total_cap_mb:.3f}")
    log(f"  saved {reg_path}")

    # ── Step 5: annotated mosaic ──────────────────────────────────────────────
    log("\n[Step 5] Writing annotated mosaic…")
    ann = mosaic.copy()
    draw = ImageDraw.Draw(ann)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except Exception:
        font = ImageFont.load_default()
        font_sm = font

    for t in TANKS_V1:
        tid = t["id"]
        rv  = results.get(tid, {})
        if rv.get("skip"):
            continue
        # v1 circle (yellow, thin)
        cx0, cy0 = geo_to_px(t["lat"], t["lon"], lat_nw, lon_nw, m_per_px)
        r0 = t["radius_m"] / m_per_px
        draw.ellipse([cx0-r0, cy0-r0, cx0+r0, cy0+r0], outline=(255, 220, 0), width=1)
        # v2 circle (cyan, slightly thicker)
        cx2, cy2 = rv["cx_fit"], rv["cy_fit"]
        r2 = rv["r_fit_px"]
        draw.ellipse([cx2-r2, cy2-r2, cx2+r2, cy2+r2], outline=(0, 220, 255), width=2)
        # ID label
        draw.text((cx2+r2+3, cy2-7), tid, font=font, fill=(255,255,255))
        # shift arrow if >5m
        if rv["shift_m"] > 5:
            draw.line([cx0, cy0, cx2, cy2], fill=(255, 80, 80), width=2)

    # Legend
    draw.rectangle([8, 8, 200, 56], fill=(0, 0, 0, 180))
    draw.text((12, 10), "yellow: v1 registry", font=font_sm, fill=(255, 220, 0))
    draw.text((12, 26), "cyan:   v2 measured", font=font_sm, fill=(0, 220, 255))
    draw.text((12, 42), "red:    shift > 5m",  font=font_sm, fill=(255, 80, 80))

    ann_path = OUT_DIR / "mosaic_annotated.png"
    ann.save(ann_path)
    log(f"  saved {ann_path}")

    # ── Write log ─────────────────────────────────────────────────────────────
    (OUT_DIR / "run.txt").write_text("\n".join(log_lines) + "\n")
    log("\nDone.")


if __name__ == "__main__":
    main()
