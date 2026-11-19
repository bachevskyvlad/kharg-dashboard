"""
hormuz_corridor_detect.py — SAR ship detection in the Hormuz exit corridor.

Zone:   56.55–57.05 E, 25.90–26.60 N  (orbit 93, east of Hormuz)
Source: Planetary Computer sentinel-1-rtc VV
Detection:
  threshold = median_sea + max(10 dB, 5 × σ_sea)
  land mask = Natural Earth 10m + 1 km buffer
  hull 150–450 m, aspect ≥ 2
Classes:
  ≥ 300 m VLCC
  220–300 m Suezmax/Aframax/Panamax
  150–220 m small
Stationary: hull centroid within ±1.5 km on previous pass.

Output tables (Supabase, SUPABASE_SERVICE_KEY):
  hormuz_corridor_ships (date, orbit, vlcc, mid, small, stationary_vlcc, stationary_mid, scene_id)

Usage:
  python hormuz_corridor_detect.py --backfill 2025-01-01
  python hormuz_corridor_detect.py --incremental
  python hormuz_corridor_detect.py --write-db
"""

import argparse
import io
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import requests
from scipy.ndimage import label as ndlabel, uniform_filter

SUPABASE_URL         = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or ""
PC_KEY               = os.environ.get("PC_SDK_SUBSCRIPTION_KEY") or ""

if not SUPABASE_URL:
    sys.exit("FATAL: SUPABASE_URL missing")
if not SUPABASE_SERVICE_KEY:
    sys.exit("FATAL: SUPABASE_SERVICE_KEY missing")

ORBIT       = 93
RESOLUTION  = 10        # metres/pixel
BBOX_WGS84  = [56.55, 25.90, 57.05, 26.60]
TABLE       = "hormuz_corridor_ships"
CSV_PATH    = Path("data/hormuz_corridor_ships.csv")

# hull size thresholds (metres)
HULL_MIN_M   = 150
HULL_MAX_M   = 450
ASPECT_MIN   = 2.0
VLCC_MIN_M   = 300
MID_MIN_M    = 220        # Suezmax/Aframax/Panamax

# stationary = centroid within 1.5 km on previous pass
STATIONARY_KM = 1.5

# ── Planetary Computer fetch ───────────────────────────────────────────────

def _pc_client():
    import pystac_client, planetary_computer
    return pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )

def _fetch_scene_inner(date_str):
    """Fetches scene without timeout — called inside a thread with hard timeout."""
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS as RioCRS

    client = _pc_client()
    search = client.search(
        collections=["sentinel-1-rtc"],
        bbox=BBOX_WGS84,
        datetime=f"{date_str}/{date_str}",
        query={"sat:relative_orbit": {"eq": ORBIT}},
    )
    items = list(search.items())
    if not items:
        return None, None
    items.sort(key=lambda x: x.datetime or "", reverse=True)
    item = items[0]

    lat_mid  = (BBOX_WGS84[1] + BBOX_WGS84[3]) / 2
    width_m  = (BBOX_WGS84[2] - BBOX_WGS84[0]) * 111320 * np.cos(np.deg2rad(lat_mid))
    height_m = (BBOX_WGS84[3] - BBOX_WGS84[1]) * 111320
    tw       = max(1, round(width_m  / RESOLUTION))
    th       = max(1, round(height_m / RESOLUTION))

    asset = item.assets.get("vv") or item.assets.get("VV")
    if asset is None:
        print(f"  no vv asset for {item.id}")
        return None, item

    dst_crs       = RioCRS.from_epsg(4326)
    dst_transform = from_bounds(*BBOX_WGS84, tw, th)
    with rasterio.open(asset.href) as src:
        out = np.zeros((th, tw), dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1),
            destination=out,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
        )
    out = np.where(out <= 0, np.float32(1e-10), out)
    return out, item


def _fetch_scene(date_str, _timeout=120):
    """Return (arr_linear, item) or (None, None) for the first orbit-93 scene on date_str.

    Uses shutdown(wait=False) to avoid blocking on the with-block __exit__
    when the underlying thread hangs on a network call.
    """
    for attempt in range(4):
        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_fetch_scene_inner, date_str)
        try:
            result = fut.result(timeout=_timeout)
            ex.shutdown(wait=False, cancel_futures=True)
            return result
        except FuturesTimeout:
            msg = f"PC API timed out after {_timeout}s"
        except Exception as e:
            msg = str(e)
        ex.shutdown(wait=False, cancel_futures=True)
        if attempt < 3:
            wait = 30 * (2 ** attempt)
            print(f"  {msg}, retry in {wait}s…")
            time.sleep(wait)
        else:
            print(f"  PC API failed after 4 attempts: {msg}")
            return None, None
    return None, None

# ── Land mask (Natural Earth 10m) ─────────────────────────────────────────

_LAND_MASK = None   # cached per process

def _build_land_mask(shape_hw):
    """Build a boolean land mask (True = land) on the corridor grid."""
    from shapely.geometry import shape, box
    from shapely.ops import unary_union
    import struct

    h, w = shape_hw
    corridor_box = box(*BBOX_WGS84)

    # Download NE 10m land polygons (small file, ~3 MB compressed)
    NE_URL = (
        "https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip"
    )
    print("  downloading Natural Earth 10m land...")
    r = requests.get(NE_URL, timeout=60)
    r.raise_for_status()

    # parse shapefile from zip in-memory
    import fiona, fiona.crs
    with fiona.BytesCollection(r.content) as fc:
        polys = [
            shape(f["geometry"])
            for f in fc
            if shape(f["geometry"]).intersects(corridor_box)
        ]

    if not polys:
        print("  no land polygons intersect corridor — mask all water")
        return np.zeros(shape_hw, dtype=bool)

    land   = unary_union(polys)
    # 1 km buffer (≈ 0.009 degrees at 25°N)
    buf_deg = 1000 / (111320 * np.cos(np.deg2rad((BBOX_WGS84[1]+BBOX_WGS84[3])/2)))
    land_buf = land.buffer(buf_deg)

    # rasterise: check cell centres
    lon_centers = np.linspace(BBOX_WGS84[0], BBOX_WGS84[2], w, endpoint=False) + (BBOX_WGS84[2]-BBOX_WGS84[0])/(2*w)
    lat_centers = np.linspace(BBOX_WGS84[3], BBOX_WGS84[1], h, endpoint=False) + (BBOX_WGS84[1]-BBOX_WGS84[3])/(2*h)

    mask = np.zeros(shape_hw, dtype=bool)
    for iy in range(h):
        for ix in range(w):
            from shapely.geometry import Point
            if land_buf.contains(Point(lon_centers[ix], lat_centers[iy])):
                mask[iy, ix] = True
    return mask

def _get_land_mask(shape_hw):
    global _LAND_MASK
    if _LAND_MASK is None or _LAND_MASK.shape != shape_hw:
        _LAND_MASK = _build_land_mask(shape_hw)
    return _LAND_MASK

# ── Detection ─────────────────────────────────────────────────────────────

def _linear_to_db(arr):
    return 10.0 * np.log10(np.maximum(arr, 1e-10))

def detect_ships(arr_linear, land_mask):
    """
    Returns list of dicts: {cx_lon, cy_lat, length_m, width_m, aspect, cls}
    cls: 'vlcc' | 'mid' | 'small'
    """
    arr_db = _linear_to_db(arr_linear)
    arr_db[land_mask] = np.nan

    sea_px = arr_db[np.isfinite(arr_db)]
    if sea_px.size < 100:
        print("  too few sea pixels")
        return []

    sea_med = float(np.median(sea_px))
    sea_std = float(sea_px.std())
    threshold_db = sea_med + max(10.0, 5.0 * sea_std)
    print(f"  sea median={sea_med:.1f} dB  std={sea_std:.1f} dB  threshold={threshold_db:.1f} dB")

    h, w = arr_db.shape
    bright = np.where(np.isfinite(arr_db), arr_db > threshold_db, False)
    labeled, num = ndlabel(bright)

    lon_range = BBOX_WGS84[2] - BBOX_WGS84[0]
    lat_range = BBOX_WGS84[3] - BBOX_WGS84[1]
    lat_mid   = (BBOX_WGS84[1] + BBOX_WGS84[3]) / 2
    m_per_deg_lon = 111320 * np.cos(np.deg2rad(lat_mid))
    m_per_deg_lat = 111320

    ships = []
    for i in range(1, num + 1):
        comp = labeled == i
        ys, xs = np.where(comp)
        area_px = int(comp.sum())
        if area_px < 2:
            continue

        lon_px = BBOX_WGS84[0] + (xs / w) * lon_range
        lat_px = BBOX_WGS84[3] - (ys / h) * lat_range

        cx_lon = float(lon_px.mean())
        cy_lat = float(lat_px.mean())

        # bounding box in metres
        span_lon_m = (lon_px.max() - lon_px.min()) * m_per_deg_lon
        span_lat_m = (lat_px.max() - lat_px.min()) * m_per_deg_lat

        length_m = max(span_lon_m, span_lat_m) + RESOLUTION   # add 1 pixel
        width_m  = min(span_lon_m, span_lat_m) + RESOLUTION
        if width_m < 1:
            width_m = RESOLUTION

        aspect = length_m / width_m

        if length_m < HULL_MIN_M or length_m > HULL_MAX_M:
            continue
        if aspect < ASPECT_MIN:
            continue

        if length_m >= VLCC_MIN_M:
            cls = "vlcc"
        elif length_m >= MID_MIN_M:
            cls = "mid"
        else:
            cls = "small"

        ships.append({
            "cx_lon": cx_lon, "cy_lat": cy_lat,
            "length_m": length_m, "width_m": width_m,
            "aspect": aspect, "cls": cls,
        })
    return ships

def _centroid_dist_km(a, b):
    """Haversine distance in km between two {cx_lon, cy_lat} dicts."""
    lat1, lon1 = np.deg2rad(a["cy_lat"]), np.deg2rad(a["cx_lon"])
    lat2, lon2 = np.deg2rad(b["cy_lat"]), np.deg2rad(b["cx_lon"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = np.sin(dlat/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin(dlon/2)**2
    return 6371 * 2 * np.arcsin(np.sqrt(h))

def mark_stationary(ships_now, ships_prev):
    """Add is_stationary=True where centroid ≤ STATIONARY_KM from prev pass."""
    for s in ships_now:
        s["is_stationary"] = False
        for p in ships_prev:
            if s["cls"] == p["cls"] and _centroid_dist_km(s, p) <= STATIONARY_KM:
                s["is_stationary"] = True
                break

# ── Supabase helpers ────────────────────────────────────────────────────────

def _sb_headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

def _fetch_existing_dates():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{TABLE}?select=date&order=date.asc",
        headers={**_sb_headers(), "Prefer": ""},
        timeout=30,
    )
    if r.status_code == 200:
        return {row["date"] for row in r.json()}
    return set()

def _upsert_rows(rows):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{TABLE}",
        headers=_sb_headers(),
        json=rows,
        timeout=30,
    )
    if r.status_code not in (200, 201):
        print(f"  DB upsert error {r.status_code}: {r.text[:200]}")
    else:
        print(f"  upserted {len(rows)} rows")

# ── Discover orbit-93 scene dates via PC STAC ──────────────────────────────

def _discover_dates_inner(date_from, date_to):
    client = _pc_client()
    search = client.search(
        collections=["sentinel-1-rtc"],
        bbox=BBOX_WGS84,
        datetime=f"{date_from}/{date_to}",
        query={"sat:relative_orbit": {"eq": ORBIT}},
    )
    seen = set()
    for item in search.items():
        d = (item.datetime or datetime.utcnow()).strftime("%Y-%m-%d")
        seen.add(d)
    return sorted(seen)


def discover_dates(date_from, date_to):
    """Discover pass dates via PC STAC with hard timeout to prevent indefinite hang."""
    for attempt in range(4):
        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_discover_dates_inner, date_from, date_to)
        try:
            result = fut.result(timeout=180)
            ex.shutdown(wait=False, cancel_futures=True)
            return result
        except FuturesTimeout:
            msg = "PC STAC discovery timed out after 180s"
        except Exception as e:
            msg = str(e)
        ex.shutdown(wait=False, cancel_futures=True)
        if attempt < 3:
            wait = 30 * (2 ** attempt)
            print(f"discover_dates: {msg}, retry in {wait}s…")
            time.sleep(wait)
        else:
            print(f"discover_dates: failed after 4 attempts: {msg}")
            os._exit(1)
    return []

# ── Main ───────────────────────────────────────────────────────────────────

def run(dates_to_process, write_db=False):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)

    import csv as _csv
    fieldnames = ["date","orbit","vlcc","mid","small","stationary_vlcc","stationary_mid","scene_id"]

    rows_csv  = []
    prev_ships = []

    for date_str in dates_to_process:
        print(f"\n── {date_str} ──")
        arr, item = _fetch_scene(date_str)
        if arr is None:
            print("  no data, skip")
            continue

        land_mask = _get_land_mask(arr.shape)
        ships = detect_ships(arr, land_mask)
        mark_stationary(ships, prev_ships)
        prev_ships = ships

        vlcc      = sum(1 for s in ships if s["cls"] == "vlcc")
        mid       = sum(1 for s in ships if s["cls"] == "mid")
        small     = sum(1 for s in ships if s["cls"] == "small")
        stat_vlcc = sum(1 for s in ships if s["cls"] == "vlcc" and s.get("is_stationary"))
        stat_mid  = sum(1 for s in ships if s["cls"] == "mid"  and s.get("is_stationary"))

        scene_id = item.id if item else ""
        print(f"  vlcc={vlcc} mid={mid} small={small}  "
              f"stationary vlcc={stat_vlcc} mid={stat_mid}  scene={scene_id}")

        row = {
            "date": date_str,
            "orbit": ORBIT,
            "vlcc": vlcc,
            "mid": mid,
            "small": small,
            "stationary_vlcc": stat_vlcc,
            "stationary_mid": stat_mid,
            "scene_id": scene_id,
        }
        rows_csv.append(row)

        # write incrementally so partial runs are not lost
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_header = not CSV_PATH.exists()
        with open(CSV_PATH, "a", newline="") as f:
            wtr = _csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                wtr.writeheader()
            wtr.writerow(row)

    if not rows_csv:
        print("no rows produced")
        return

    print(f"\nCSV written: {CSV_PATH} ({len(rows_csv)} rows)")

    if write_db:
        _upsert_rows(rows_csv)



def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--backfill",    metavar="DATE", help="backfill from DATE to today (YYYY-MM-DD)")
    mode.add_argument("--incremental", action="store_true",  help="only dates not yet in DB")
    mode.add_argument("--write-db",    action="store_true",  help="upsert CSV rows already computed")
    args = ap.parse_args()

    if args.write_db:
        # read CSV and upsert
        import csv as _csv
        if not CSV_PATH.exists():
            sys.exit(f"FATAL: {CSV_PATH} not found — run --backfill first")
        rows = []
        with open(CSV_PATH, newline="") as f:
            for row in _csv.DictReader(f):
                row["orbit"]          = int(row["orbit"])
                row["vlcc"]           = int(row["vlcc"])
                row["mid"]            = int(row["mid"])
                row["small"]          = int(row["small"])
                row["stationary_vlcc"]= int(row["stationary_vlcc"])
                row["stationary_mid"] = int(row["stationary_mid"])
                rows.append(row)
        _upsert_rows(rows)
        return

    today = date.today().isoformat()
    if args.backfill:
        date_from = args.backfill
    elif args.incremental:
        existing = _fetch_existing_dates()
        # find dates not in DB from 2025-01 onward
        all_dates = discover_dates("2025-01-01", today)
        dates_to_process = [d for d in all_dates if d not in existing]
        print(f"incremental: {len(dates_to_process)} new dates")
        run(dates_to_process, write_db=True)
        return
    else:
        date_from = (date.today() - timedelta(days=14)).isoformat()

    all_dates = discover_dates(date_from, today)
    print(f"discovered {len(all_dates)} orbit-{ORBIT} scenes {date_from} → {today}")
    run(all_dates, write_db=False)
    os._exit(0)  # force-exit: abandoned ThreadPoolExecutor threads are non-daemon


if __name__ == "__main__":
    main()
