"""
hormuz_corridor_detect.py — SAR ship detection in the Hormuz exit corridor, orbit 93.

Zone: 56.55–57.05 E, 25.90–26.60 N

Usage:
  python hormuz_corridor_detect.py --backfill 2025-01-01
  python hormuz_corridor_detect.py --incremental
  python hormuz_corridor_detect.py --write-db
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from math import cos, radians
from multiprocessing import Pool, TimeoutError as MPTimeout
from pathlib import Path

import numpy as np
import requests
from scipy.ndimage import label as ndlabel

SUPABASE_URL         = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or ""

ORBIT      = 93
BBOX       = [56.55, 25.90, 57.05, 26.60]
RESOLUTION = 25  # meters

HULL_MIN_M     = 150
HULL_MAX_M     = 450
VLCC_MIN_M     = 300
MID_MIN_M      = 220
ASPECT_MIN     = 2.0
STATIONARY_KM  = 1.5
SCENE_TIMEOUT  = 120

TABLE      = "hormuz_corridor_ships"
REPO_ROOT  = Path(__file__).parent.parent
DIAG_DIR   = REPO_ROOT / "diagnostics"
DATES_JSON = DIAG_DIR / "dates_corridor.json"
JSONL_PATH = DIAG_DIR / "hormuz_corridor_ships.jsonl"
CSV_PATH   = DIAG_DIR / "hormuz_corridor_ships.csv"

# ── Grid dimensions ────────────────────────────────────────────────────────────

def _grid_dims(bbox, res_m):
    lat_mid  = (bbox[1] + bbox[3]) / 2
    width_m  = (bbox[2] - bbox[0]) * 111320 * cos(radians(lat_mid))
    height_m = (bbox[3] - bbox[1]) * 111320
    return max(1, round(width_m / res_m)), max(1, round(height_m / res_m))

W, H = _grid_dims(BBOX, RESOLUTION)

# ── Land mask ─────────────────────────────────────────────────────────────────

_LAND_MASK_CACHE = {}

def _get_land_mask():
    if "mask" in _LAND_MASK_CACHE:
        return _LAND_MASK_CACHE["mask"]

    from shapely.geometry import shape, box as sbox
    from shapely.ops import unary_union
    import fiona
    import rasterio.features
    from rasterio.transform import from_bounds

    bbox_poly = sbox(*BBOX)
    NE_URL = "https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip"
    r = requests.get(NE_URL, timeout=60)
    r.raise_for_status()

    polys = []
    with fiona.BytesCollection(r.content) as fc:
        for feat in fc:
            g = shape(feat["geometry"])
            if g.intersects(bbox_poly):
                polys.append(g)

    lat_mid = (BBOX[1] + BBOX[3]) / 2
    buf_deg = 1000 / (111320 * cos(radians(lat_mid)))

    if polys:
        land = unary_union(polys).buffer(buf_deg)
        transform = from_bounds(*BBOX, W, H)
        mask = rasterio.features.rasterize(
            [(land.__geo_interface__, 1)],
            out_shape=(H, W),
            transform=transform,
            fill=0,
            dtype=np.uint8,
        ).astype(bool)
    else:
        mask = np.zeros((H, W), dtype=bool)

    _LAND_MASK_CACHE["mask"] = mask
    return mask

# ── Scene reader ───────────────────────────────────────────────────────────────

def _read_scene(href):
    import rasterio
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.vrt import WarpedVRT

    transform = from_bounds(*BBOX, W, H)
    with rasterio.open(href) as src:
        with WarpedVRT(
            src,
            crs=CRS.from_epsg(4326),
            transform=transform,
            width=W,
            height=H,
            resampling=Resampling.average,
        ) as vrt:
            arr = vrt.read(1).astype(np.float32)
    valid_mask = arr > 0
    return np.where(arr <= 0, np.float32(1e-10), arr), valid_mask

# ── Detection ──────────────────────────────────────────────────────────────────

def _detect_ships(arr, valid_mask):
    land_mask = _get_land_mask()
    db = 10.0 * np.log10(arr)
    db[land_mask] = np.nan
    db[~valid_mask] = np.nan  # nodata pixels must not enter sea statistics

    sea_px = db[np.isfinite(db)]
    if sea_px.size < 100:
        return []

    threshold = float(np.median(sea_px)) + max(10.0, 5.0 * float(sea_px.std()))

    lon_range = BBOX[2] - BBOX[0]
    lat_range = BBOX[3] - BBOX[1]
    lat_mid   = (BBOX[1] + BBOX[3]) / 2
    m_per_lon = 111320 * cos(radians(lat_mid))
    m_per_lat = 111320

    bright = np.where(np.isfinite(db), db > threshold, False)
    labeled, n = ndlabel(bright)

    ships = []
    for i in range(1, n + 1):
        ys, xs = np.where(labeled == i)
        if len(ys) < 2:
            continue

        lon_px = BBOX[0] + (xs / W) * lon_range
        lat_px = BBOX[3] - (ys / H) * lat_range
        cx_lon = float(lon_px.mean())
        cy_lat = float(lat_px.mean())

        # PCA: measure along hull axis, not axis-aligned bbox
        q = np.column_stack([
            (lon_px - cx_lon) * m_per_lon,
            (lat_px - cy_lat) * m_per_lat,
        ])
        _, ev = np.linalg.eigh(np.cov(q.T))
        proj_long  = q @ ev[:, -1]
        proj_short = q @ ev[:, 0]
        length_m = float((proj_long.max() - proj_long.min())   + RESOLUTION)
        width_m  = max(float((proj_short.max() - proj_short.min()) + RESOLUTION), RESOLUTION)

        if length_m < HULL_MIN_M or length_m > HULL_MAX_M:
            continue
        if length_m / width_m < ASPECT_MIN:
            continue

        aspect = length_m / width_m
        cls = "vlcc" if length_m >= VLCC_MIN_M else ("mid" if length_m >= MID_MIN_M else "small")
        ships.append({
            "cx_lon": cx_lon, "cy_lat": cy_lat,
            "length_m": length_m, "aspect": aspect, "cls": cls,
        })
    return ships

def _mark_stationary(ships_now, ships_prev):
    for s in ships_now:
        s["is_stationary"] = False
        for p in ships_prev:
            if s["cls"] != p["cls"]:
                continue
            dlat = (s["cy_lat"] - p["cy_lat"]) * 111.32
            dlon = (s["cx_lon"] - p["cx_lon"]) * 111.32 * cos(radians(s["cy_lat"]))
            if (dlat**2 + dlon**2) ** 0.5 <= STATIONARY_KM:
                s["is_stationary"] = True
                break

# ── STAC discovery ─────────────────────────────────────────────────────────────

def _discover_dates(date_from, date_to):
    """Search month-by-month (max_items=100 per month) to avoid duplicate frames eating the global limit."""
    import pystac_client
    import planetary_computer
    from calendar import monthrange

    cat = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )

    seen = set()
    cur = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)

    while cur <= end:
        y, m = cur.year, cur.month
        last_day = monthrange(y, m)[1]
        mo_start = date(y, m, 1).isoformat()
        mo_end   = min(date(y, m, last_day), end).isoformat()

        search = cat.search(
            collections=["sentinel-1-rtc"],
            bbox=BBOX,
            datetime=f"{mo_start}/{mo_end}",
            query={"sat:relative_orbit": {"eq": ORBIT}},
            max_items=100,
        )
        for item in search.items():
            d = (item.datetime or datetime.utcnow()).strftime("%Y-%m-%d")
            seen.add(d)

        # advance to next month
        if m == 12:
            cur = date(y + 1, 1, 1)
        else:
            cur = date(y, m + 1, 1)

    return sorted(seen)

# ── Per-date worker ────────────────────────────────────────────────────────────

def _process_date(date_str):
    import pystac_client
    import planetary_computer
    try:
        cat = pystac_client.Client.open(
            "https://planetarycomputer.microsoft.com/api/stac/v1",
            modifier=planetary_computer.sign_inplace,
        )
        search = cat.search(
            collections=["sentinel-1-rtc"],
            bbox=BBOX,
            datetime=f"{date_str}/{date_str}",
            query={"sat:relative_orbit": {"eq": ORBIT}},
            max_items=10,
        )
        items = list(search.items())
        if not items:
            return date_str, [], ""

        land_mask = _get_land_mask()
        sea_total = int((~land_mask).sum())

        best_item  = None
        best_arr   = None
        best_valid = None
        best_cov   = -1.0

        for item in items:
            asset = item.assets.get("vv") or item.assets.get("VV")
            if asset is None:
                continue
            arr, valid_mask = _read_scene(asset.href)
            cov = int((valid_mask & ~land_mask).sum()) / sea_total if sea_total > 0 else 0.0
            if cov > best_cov:
                best_cov   = cov
                best_arr   = arr
                best_valid = valid_mask
                best_item  = item

        if best_cov < 0.9:
            return date_str, None, f"coverage={best_cov:.2f}<0.9"

        ships = _detect_ships(best_arr, best_valid)
        return date_str, ships, best_item.id
    except Exception as e:
        return date_str, None, str(e)

# ── Backfill ───────────────────────────────────────────────────────────────────

def run_backfill(dates):
    DIAG_DIR.mkdir(parents=True, exist_ok=True)

    done = set()
    if JSONL_PATH.exists():
        with open(JSONL_PATH) as f:
            for line in f:
                try:
                    done.add(json.loads(line)["date"])
                except Exception:
                    pass

    todo = [d for d in dates if d not in done]
    print(f"Corridor orbit-{ORBIT}: {len(dates)} dates total, {len(done)} cached, {len(todo)} to process")

    if not todo:
        _jsonl_to_csv()
        return

    results_by_date = {}

    with open(JSONL_PATH, "a") as jf:
        with Pool(6) as pool:
            futures = {d: pool.apply_async(_process_date, (d,)) for d in todo}
            for d in todo:
                try:
                    date_str, ships, scene_id = futures[d].get(timeout=SCENE_TIMEOUT)
                except MPTimeout:
                    print(f"  {d}: timeout, skip")
                    continue
                except Exception as e:
                    print(f"  {d}: pool error {e}, skip")
                    continue

                if ships is None:
                    print(f"  {d}: error — {scene_id}")
                    continue

                prev_key = None
                for k in sorted(results_by_date):
                    if k < d:
                        prev_key = k
                _mark_stationary(ships, results_by_date.get(prev_key, []))
                results_by_date[d] = ships

                vlcc  = sum(1 for s in ships if s["cls"] == "vlcc")
                mid   = sum(1 for s in ships if s["cls"] == "mid")
                small = sum(1 for s in ships if s["cls"] == "small")
                stat  = sum(1 for s in ships if s.get("is_stationary"))
                row = {
                    "date": d, "orbit": ORBIT,
                    "vlcc": vlcc, "mid": mid, "small": small,
                    "stationary": stat, "scene_id": scene_id,
                }
                jf.write(json.dumps(row) + "\n")
                jf.flush()
                print(f"  {d}: vlcc={vlcc} mid={mid} small={small}  scene={scene_id}")

    _jsonl_to_csv()
    print(f"\nCSV: {CSV_PATH}")

def _jsonl_to_csv():
    import csv
    rows = []
    if JSONL_PATH.exists():
        with open(JSONL_PATH) as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    if not rows:
        return
    rows.sort(key=lambda r: r["date"])
    with open(CSV_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date","orbit","vlcc","mid","small","stationary","scene_id"])
        w.writeheader()
        w.writerows(rows)

# ── Debug: single-date ship list ──────────────────────────────────────────────

def run_debug_date(date_str):
    """Print per-ship table (lat, lon, length_m, aspect, cls) + coverage stats."""
    import pystac_client
    import planetary_computer

    print(f"\n=== DEBUG {date_str} orbit-{ORBIT} ===")
    cat = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )
    search = cat.search(
        collections=["sentinel-1-rtc"],
        bbox=BBOX,
        datetime=f"{date_str}/{date_str}",
        query={"sat:relative_orbit": {"eq": ORBIT}},
        max_items=10,
    )
    items = list(search.items())
    if not items:
        print("  No items found")
        return

    land_mask = _get_land_mask()
    sea_total = int((~land_mask).sum())

    print(f"  Items found: {len(items)}")
    for item in items:
        asset = item.assets.get("vv") or item.assets.get("VV")
        if asset is None:
            continue
        arr, valid_mask = _read_scene(asset.href)
        cov = int((valid_mask & ~land_mask).sum()) / sea_total if sea_total > 0 else 0.0
        print(f"  scene={item.id}  overall_cov={cov:.3f}")

        if cov < 0.9:
            print(f"  SKIP: coverage {cov:.3f} < 0.9")
            continue

        ships = _detect_ships(arr, valid_mask)
        print(f"  Ships detected: {len(ships)}")
        if ships:
            print(f"  {'lat':>9} {'lon':>9} {'length_m':>9} {'aspect':>7} {'cls':>6}")
            for s in sorted(ships, key=lambda x: x["cy_lat"], reverse=True):
                print(f"  {s['cy_lat']:>9.5f} {s['cx_lon']:>9.5f} {s['length_m']:>9.1f}"
                      f" {s['aspect']:>7.2f} {s['cls']:>6}")


# ── Supabase ───────────────────────────────────────────────────────────────────

def _sb_headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

def _upsert_rows(rows):
    batch = 500
    for i in range(0, len(rows), batch):
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/{TABLE}",
            headers=_sb_headers(),
            json=rows[i:i+batch],
            timeout=30,
        )
        if r.status_code not in (200, 201):
            print(f"  DB error {r.status_code}: {r.text[:200]}")
        else:
            print(f"  upserted {len(rows[i:i+batch])} rows")

def _fetch_existing_dates():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{TABLE}?select=date&order=date.asc",
        headers={**_sb_headers(), "Prefer": ""},
        timeout=30,
    )
    if r.status_code == 200:
        return {row["date"] for row in r.json()}
    return set()

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--backfill",    metavar="DATE")
    mode.add_argument("--incremental", action="store_true")
    mode.add_argument("--write-db",    action="store_true")
    mode.add_argument("--debug-date",  metavar="DATE")
    args = ap.parse_args()

    today = date.today().isoformat()

    if args.debug_date:
        run_debug_date(args.debug_date)
        return

    if args.write_db:
        import csv as _csv
        if not CSV_PATH.exists():
            sys.exit(f"FATAL: {CSV_PATH} not found")
        rows = []
        with open(CSV_PATH) as f:
            for row in _csv.DictReader(f):
                row["orbit"] = int(row["orbit"])
                for k in ("vlcc", "mid", "small", "stationary"):
                    row[k] = int(row[k])
                rows.append(row)
        _upsert_rows(rows)
        return

    if args.backfill:
        date_from = args.backfill
        if DATES_JSON.exists():
            dates = json.loads(DATES_JSON.read_text())
            print(f"Loaded {len(dates)} dates from {DATES_JSON.name}")
        else:
            print(f"Discovering orbit-{ORBIT} dates {date_from} → {today}…")
            DIAG_DIR.mkdir(parents=True, exist_ok=True)
            dates = _discover_dates(date_from, today)
            DATES_JSON.write_text(json.dumps(dates, indent=2))
            print(f"Found {len(dates)} dates, cached to {DATES_JSON.name}")
        run_backfill(dates)
        os._exit(0)

    elif args.incremental:
        date_from = (date.today() - timedelta(days=14)).isoformat()
        dates = _discover_dates(date_from, today)
        existing = _fetch_existing_dates()
        new_dates = [d for d in dates if d not in existing]
        print(f"Incremental: {len(new_dates)} new dates")
        if new_dates:
            run_backfill(new_dates)
            import csv as _csv
            rows = []
            with open(CSV_PATH) as f:
                for row in _csv.DictReader(f):
                    row["orbit"] = int(row["orbit"])
                    for k in ("vlcc", "mid", "small", "stationary"):
                        row[k] = int(row[k])
                    rows.append(row)
            _upsert_rows(rows)
    else:
        date_from = (date.today() - timedelta(days=14)).isoformat()
        dates = _discover_dates(date_from, today)
        run_backfill(dates)


if __name__ == "__main__":
    main()
