"""
jask_ship_detect.py — SAR ship detection near Jask storage terminal.

Zone:   57.10–57.75 E, 25.45–25.92 N  (orbit 57)
Subzones:
  terminal  57.20–57.42 / 25.74–25.92
  anchorage 57.45–57.75 / 25.48–25.68
  passing traffic outside subzones — NOT stored
Source: Planetary Computer sentinel-1-rtc VV
Detection:
  threshold = median_sea + max(10 dB, 5 × σ_sea)
  land mask = Natural Earth 10m + 1 km buffer
  hull 150–450 m, aspect ≥ 2
Classes:
  ≥ 300 m VLCC
  220–300 m Suezmax/Aframax/Panamax  → 'mid'
  150–220 m small
Stationary: hull centroid within ±1.5 km on previous pass.

Output table (Supabase, SUPABASE_SERVICE_KEY):
  jask_ship_obs (date, orbit, zone, vlcc, mid, small, stationary, scene_id)

Usage:
  python jask_ship_detect.py --backfill 2025-01-01
  python jask_ship_detect.py --incremental
  python jask_ship_detect.py --write-db
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import requests
from scipy.ndimage import label as ndlabel

SUPABASE_URL         = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or ""
PC_KEY               = os.environ.get("PC_SDK_SUBSCRIPTION_KEY") or ""

if not SUPABASE_URL:
    sys.exit("FATAL: SUPABASE_URL missing")
if not SUPABASE_SERVICE_KEY:
    sys.exit("FATAL: SUPABASE_SERVICE_KEY missing")

ORBIT      = 57
RESOLUTION = 10        # metres/pixel
BBOX_WGS84 = [57.10, 25.45, 57.75, 25.92]
TABLE      = "jask_ship_obs"
CSV_PATH   = Path("data/jask_ship_obs.csv")

# Subzone bounding boxes [min_lon, min_lat, max_lon, max_lat]
SUBZONES = {
    "terminal":  [57.20, 25.74, 57.42, 25.92],
    "anchorage": [57.45, 25.48, 57.75, 25.68],
}

HULL_MIN_M  = 150
HULL_MAX_M  = 450
ASPECT_MIN  = 2.0
VLCC_MIN_M  = 300
MID_MIN_M   = 220
STATIONARY_KM = 1.5


# ── PC fetch ───────────────────────────────────────────────────────────────

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
    """Wrapper with per-attempt hard timeout + retry with backoff.

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


# ── Land mask ─────────────────────────────────────────────────────────────

_LAND_MASK_CACHE = {}

def _build_land_mask(shape_hw, bbox):
    from shapely.geometry import shape, box, Point
    from shapely.ops import unary_union
    import fiona

    h, w = shape_hw
    zone_box = box(*bbox)
    NE_URL = "https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip"
    print("  downloading Natural Earth 10m land...")
    r = requests.get(NE_URL, timeout=60)
    r.raise_for_status()

    with fiona.BytesCollection(r.content) as fc:
        polys = [shape(f["geometry"]) for f in fc if shape(f["geometry"]).intersects(zone_box)]

    if not polys:
        return np.zeros(shape_hw, dtype=bool)

    land = unary_union(polys)
    lat_mid = (bbox[1] + bbox[3]) / 2
    buf_deg = 1000 / (111320 * np.cos(np.deg2rad(lat_mid)))
    land_buf = land.buffer(buf_deg)

    lon_step = (bbox[2] - bbox[0]) / w
    lat_step = (bbox[3] - bbox[1]) / h
    mask = np.zeros(shape_hw, dtype=bool)
    for iy in range(h):
        lat_c = bbox[3] - (iy + 0.5) * lat_step
        for ix in range(w):
            lon_c = bbox[0] + (ix + 0.5) * lon_step
            if land_buf.contains(Point(lon_c, lat_c)):
                mask[iy, ix] = True
    return mask

def _get_land_mask(shape_hw):
    key = shape_hw
    if key not in _LAND_MASK_CACHE:
        _LAND_MASK_CACHE[key] = _build_land_mask(shape_hw, BBOX_WGS84)
    return _LAND_MASK_CACHE[key]


# ── Detection ─────────────────────────────────────────────────────────────

def _in_subzone(cx_lon, cy_lat, zone_name):
    bb = SUBZONES[zone_name]
    return bb[0] <= cx_lon <= bb[2] and bb[1] <= cy_lat <= bb[3]

def detect_ships(arr_linear, land_mask):
    arr_db = 10.0 * np.log10(np.maximum(arr_linear, 1e-10))
    arr_db[land_mask] = np.nan

    sea_px = arr_db[np.isfinite(arr_db)]
    if sea_px.size < 100:
        print("  too few sea pixels")
        return []

    sea_med       = float(np.median(sea_px))
    sea_std       = float(sea_px.std())
    threshold_db  = sea_med + max(10.0, 5.0 * sea_std)
    print(f"  sea median={sea_med:.1f} dB  std={sea_std:.1f} dB  threshold={threshold_db:.1f} dB")

    h, w        = arr_db.shape
    bright      = np.where(np.isfinite(arr_db), arr_db > threshold_db, False)
    labeled, n  = ndlabel(bright)

    lon_range    = BBOX_WGS84[2] - BBOX_WGS84[0]
    lat_range    = BBOX_WGS84[3] - BBOX_WGS84[1]
    lat_mid      = (BBOX_WGS84[1] + BBOX_WGS84[3]) / 2
    m_per_lon    = 111320 * np.cos(np.deg2rad(lat_mid))
    m_per_lat    = 111320

    ships = []
    for i in range(1, n + 1):
        comp = labeled == i
        ys, xs = np.where(comp)
        if comp.sum() < 2:
            continue

        lon_px = BBOX_WGS84[0] + (xs / w) * lon_range
        lat_px = BBOX_WGS84[3] - (ys / h) * lat_range
        cx_lon = float(lon_px.mean())
        cy_lat = float(lat_px.mean())

        span_lon_m = (lon_px.max() - lon_px.min()) * m_per_lon
        span_lat_m = (lat_px.max() - lat_px.min()) * m_per_lat
        length_m   = max(span_lon_m, span_lat_m) + RESOLUTION
        width_m    = max(min(span_lon_m, span_lat_m) + RESOLUTION, RESOLUTION)
        aspect     = length_m / width_m

        if length_m < HULL_MIN_M or length_m > HULL_MAX_M:
            continue
        if aspect < ASPECT_MIN:
            continue

        # assign subzone; skip if not in any subzone
        zone = None
        for zname in SUBZONES:
            if _in_subzone(cx_lon, cy_lat, zname):
                zone = zname
                break
        if zone is None:
            continue    # passing traffic — not stored

        if length_m >= VLCC_MIN_M:
            cls = "vlcc"
        elif length_m >= MID_MIN_M:
            cls = "mid"
        else:
            cls = "small"

        ships.append({
            "cx_lon": cx_lon, "cy_lat": cy_lat,
            "length_m": length_m, "width_m": width_m,
            "aspect": aspect, "cls": cls, "zone": zone,
        })
    return ships


def _haversine_km(a, b):
    lat1, lon1 = np.deg2rad(a["cy_lat"]), np.deg2rad(a["cx_lon"])
    lat2, lon2 = np.deg2rad(b["cy_lat"]), np.deg2rad(b["cx_lon"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = np.sin(dlat/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin(dlon/2)**2
    return 6371 * 2 * np.arcsin(np.sqrt(h))

def mark_stationary(ships_now, ships_prev):
    for s in ships_now:
        s["is_stationary"] = False
        for p in ships_prev:
            if s["zone"] == p["zone"] and s["cls"] == p["cls"] and \
               _haversine_km(s, p) <= STATIONARY_KM:
                s["is_stationary"] = True
                break


# ── Supabase ───────────────────────────────────────────────────────────────

def _sb_headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

def _fetch_existing():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{TABLE}?select=date,zone&order=date.asc",
        headers={**_sb_headers(), "Prefer": ""},
        timeout=30,
    )
    if r.status_code == 200:
        return {(row["date"], row["zone"]) for row in r.json()}
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


# ── Discover scenes ────────────────────────────────────────────────────────

def discover_dates(date_from, date_to):
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


# ── Main ───────────────────────────────────────────────────────────────────

def run(dates_to_process, write_db=False):
    import csv as _csv
    fieldnames = ["date","orbit","zone","vlcc","mid","small","stationary","scene_id"]

    rows_csv   = []
    prev_ships = []

    for date_str in dates_to_process:
        print(f"\n── {date_str} ──")
        arr, item = _fetch_scene(date_str)
        if arr is None:
            print("  no data, skip")
            continue

        land_mask = _get_land_mask(arr.shape)
        ships     = detect_ships(arr, land_mask)
        mark_stationary(ships, prev_ships)
        prev_ships = ships

        scene_id = item.id if item else ""
        date_rows = []

        # aggregate per subzone
        for zone in SUBZONES:
            zships    = [s for s in ships if s["zone"] == zone]
            vlcc      = sum(1 for s in zships if s["cls"] == "vlcc")
            mid       = sum(1 for s in zships if s["cls"] == "mid")
            small     = sum(1 for s in zships if s["cls"] == "small")
            stationary= sum(1 for s in zships if s.get("is_stationary"))
            print(f"  {zone}: vlcc={vlcc} mid={mid} small={small} stationary={stationary}")
            date_rows.append({
                "date": date_str,
                "orbit": ORBIT,
                "zone": zone,
                "vlcc": vlcc,
                "mid": mid,
                "small": small,
                "stationary": stationary,
                "scene_id": scene_id,
            })

        rows_csv.extend(date_rows)

        # write incrementally so partial runs are not lost
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_header = not CSV_PATH.exists()
        with open(CSV_PATH, "a", newline="") as f:
            wtr = _csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                wtr.writeheader()
            wtr.writerows(date_rows)

    if not rows_csv:
        print("no rows produced")
        return

    print(f"\nCSV written: {CSV_PATH} ({len(rows_csv)} rows)")

    if write_db:
        _upsert_rows(rows_csv)


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--backfill",    metavar="DATE")
    mode.add_argument("--incremental", action="store_true")
    mode.add_argument("--write-db",    action="store_true")
    args = ap.parse_args()

    if args.write_db:
        import csv as _csv
        if not CSV_PATH.exists():
            sys.exit(f"FATAL: {CSV_PATH} not found — run --backfill first")
        rows = []
        with open(CSV_PATH, newline="") as f:
            for row in _csv.DictReader(f):
                row["orbit"]      = int(row["orbit"])
                row["vlcc"]       = int(row["vlcc"])
                row["mid"]        = int(row["mid"])
                row["small"]      = int(row["small"])
                row["stationary"] = int(row["stationary"])
                rows.append(row)
        _upsert_rows(rows)
        return

    today = date.today().isoformat()
    if args.backfill:
        date_from = args.backfill
        all_dates = discover_dates(date_from, today)
        print(f"discovered {len(all_dates)} orbit-{ORBIT} scenes {date_from} → {today}")
        run(all_dates, write_db=False)
    elif args.incremental:
        existing  = _fetch_existing()
        all_dates = discover_dates("2025-01-01", today)
        # filter dates where both subzones are already present
        def _all_zones_present(d):
            return all((d, z) in existing for z in SUBZONES)
        dates_new = [d for d in all_dates if not _all_zones_present(d)]
        print(f"incremental: {len(dates_new)} new dates")
        run(dates_new, write_db=True)
    else:
        date_from = (date.today() - timedelta(days=14)).isoformat()
        all_dates = discover_dates(date_from, today)
        run(all_dates, write_db=False)


if __name__ == "__main__":
    main()
