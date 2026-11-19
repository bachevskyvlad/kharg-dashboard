"""
irlane_detect.py — SAR ship detection in the Iranian lane, orbit 93.

Zone: 56.55–57.10 E, 26.50–27.00 N

Land mask: built from radar backscatter (12 calibration scenes),
NOT from Natural Earth vectors.

Usage:
  python irlane_detect.py --build-mask          # build/rebuild land mask
  python irlane_detect.py --backfill 2025-01-01
  python irlane_detect.py --dates D1,D2,...
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

ORBIT      = 93
BBOX       = [56.55, 26.50, 57.10, 27.00]
RESOLUTION = 25  # metres

HULL_MIN_M    = 150   # minimum detectable hull
HULL_MAX_M    = 450
SMALL_MAX_M   = 220   # small: 150–220 m
LARGE_MIN_M   = 220   # large: ≥220 m (also used for moving/stay check)
VLCC_MIN_M    = 300   # vlcc: ≥300 m
ASPECT_MIN    = 2.0
PREV_PASS_KM  = 1.5   # radius for moving/stay comparison
SCENE_TIMEOUT = 120

REPO_ROOT  = Path(__file__).parent.parent
DIAG_DIR   = REPO_ROOT / "diagnostics"
DATES_JSON = DIAG_DIR / "dates_irlane.json"
JSONL_PATH = DIAG_DIR / "irlane_ships.jsonl"
CSV_PATH   = DIAG_DIR / "irlane_ships.csv"

# Radar mask paths
MASK_NPY  = DIAG_DIR / "irlane_land_mask.npy"
MASK_META = DIAG_DIR / "irlane_land_mask_meta.json"

# 12 calibration scenes for radar land mask (spread across seasons)
MASK_DATES = [
    "2025-02-16", "2025-05-11", "2025-08-03", "2025-11-01",
    "2026-01-24", "2026-03-13", "2026-05-18", "2026-07-06",
    "2026-08-23", "2026-10-04", "2026-11-03", "2026-11-15",
]

# Radar mask parameters
LAND_P20_DB   = -16.0   # 20th-pctile > -16 dB → persistent land
STATIC_DB     = -8.0    # scene > -8 dB in ≥40% scenes → static bright (buildings, rocks)
STATIC_FRAC   = 0.40
MIN_COMP_PX   = 50      # discard components smaller than this
DILATION_M    = 1500    # dilate combined mask by this many metres (covers mangrove channels near Khuhaistaq)

# ── Grid dimensions ────────────────────────────────────────────────────────────

def _grid_dims(bbox, res_m):
    lat_mid  = (bbox[1] + bbox[3]) / 2
    width_m  = (bbox[2] - bbox[0]) * 111320 * cos(radians(lat_mid))
    height_m = (bbox[3] - bbox[1]) * 111320
    return max(1, round(width_m / res_m)), max(1, round(height_m / res_m))

W, H = _grid_dims(BBOX, RESOLUTION)

# ── Scene reader ───────────────────────────────────────────────────────────────

def _read_scene(href, bbox=None, w=None, h=None):
    import rasterio
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.vrt import WarpedVRT

    if bbox is None:
        bbox, w, h = BBOX, W, H
    transform = from_bounds(*bbox, w, h)
    with rasterio.open(href) as src:
        with WarpedVRT(
            src,
            crs=CRS.from_epsg(4326),
            transform=transform,
            width=w,
            height=h,
            resampling=Resampling.average,
        ) as vrt:
            arr = vrt.read(1).astype(np.float32)
    valid_mask = arr > 0
    return np.where(arr <= 0, np.float32(1e-10), arr), valid_mask

# ── Radar land mask ────────────────────────────────────────────────────────────

_MASK_CACHE = {}

def _disk_struct(r):
    y, x = np.ogrid[-r:r+1, -r:r+1]
    return (x * x + y * y) <= r * r

def build_radar_mask(force=False):
    """
    Build the radar land mask for irlane from MASK_DATES calibration scenes.

    Algorithm:
      1. Download 12 VV scenes, compute dB arrays.
      2. p20 = 20th percentile per pixel across scenes; land_raw = p20 > LAND_P20_DB.
      3. static = fraction of scenes where dB > STATIC_DB >= STATIC_FRAC.
      4. combined = land_raw (after fill_holes + drop <50px) | static.
      5. mask = binary_dilation(combined, 400 m disk).
      6. Save to MASK_NPY + MASK_META.
    """
    from scipy.ndimage import binary_dilation, binary_fill_holes, label as ndlb
    import pystac_client
    import planetary_computer

    if not force and MASK_NPY.exists():
        print(f"Mask exists: {MASK_NPY} ({MASK_NPY.stat().st_size} bytes)")
        return np.load(MASK_NPY)

    print(f"Building radar land mask from {len(MASK_DATES)} calibration scenes…")
    DIAG_DIR.mkdir(parents=True, exist_ok=True)

    cat = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )

    scenes_db = []
    for d in MASK_DATES:
        search = cat.search(
            collections=["sentinel-1-rtc"],
            bbox=BBOX,
            datetime=f"{d}/{d}",
            query={"sat:relative_orbit": {"eq": ORBIT}},
            max_items=5,
        )
        items = list(search.items())
        loaded = False
        for item in items:
            asset = item.assets.get("vv") or item.assets.get("VV")
            if asset is None:
                continue
            try:
                arr, valid = _read_scene(asset.href)
                db = 10.0 * np.log10(arr)
                db[~valid] = np.nan
                scenes_db.append(db)
                print(f"  {d}: loaded scene {item.id}")
                loaded = True
                break
            except Exception as e:
                print(f"  {d}: read error {e}")
        if not loaded:
            print(f"  {d}: no scene found")

    n_loaded = len(scenes_db)
    print(f"Loaded {n_loaded}/{len(MASK_DATES)} calibration scenes")
    if n_loaded < 4:
        print("WARN: too few scenes for radar mask, returning empty mask")
        empty = np.zeros((H, W), dtype=bool)
        np.save(MASK_NPY, empty)
        MASK_META.write_text(json.dumps({"n_scenes": n_loaded, "note": "insufficient scenes"}))
        return empty

    stack = np.stack(scenes_db, axis=0)  # (N, H, W)

    # Step 2: 20th-percentile land
    p20 = np.nanpercentile(stack, 20, axis=0)
    land_raw = (p20 > LAND_P20_DB)

    # Fill holes and remove small components
    land_filled = binary_fill_holes(land_raw)
    labeled, n_comp = ndlb(land_filled)
    for i in range(1, n_comp + 1):
        if (labeled == i).sum() < MIN_COMP_PX:
            land_filled[labeled == i] = False

    # Step 3: static bright features
    valid_count = (~np.isnan(stack)).sum(axis=0)
    bright = stack > STATIC_DB
    bright[np.isnan(stack)] = False
    static = np.where(valid_count > 0,
                      bright.sum(axis=0) / valid_count.clip(1) >= STATIC_FRAC,
                      False).astype(bool)

    # Step 4: combined
    combined = land_filled | static

    # Step 5: dilate
    dilation_px = max(1, round(DILATION_M / RESOLUTION))
    struct = _disk_struct(dilation_px)
    mask = binary_dilation(combined, structure=struct).astype(bool)

    # Step 6: save
    np.save(MASK_NPY, mask)
    meta = {
        "n_scenes": n_loaded,
        "grid": {"W": W, "H": H, "res_m": RESOLUTION},
        "land_px": int(mask.sum()),
        "params": {
            "LAND_P20_DB": LAND_P20_DB,
            "STATIC_DB": STATIC_DB,
            "STATIC_FRAC": STATIC_FRAC,
            "MIN_COMP_PX": MIN_COMP_PX,
            "DILATION_M": DILATION_M,
        },
        "built_at": datetime.utcnow().isoformat(),
    }
    MASK_META.write_text(json.dumps(meta, indent=2))
    print(f"Mask saved: {MASK_NPY}  ({mask.sum()} land px out of {W*H})")
    return mask

def _get_land_mask():
    if "mask" in _MASK_CACHE:
        return _MASK_CACHE["mask"]
    if MASK_NPY.exists():
        mask = np.load(MASK_NPY)
    else:
        mask = build_radar_mask()
    _MASK_CACHE["mask"] = mask
    return mask

# ── Detection ──────────────────────────────────────────────────────────────────

def _detect_ships(arr, valid_mask):
    land_mask = _get_land_mask()
    db = 10.0 * np.log10(arr)
    db[land_mask] = np.nan
    db[~valid_mask] = np.nan

    sea_db = db[np.isfinite(db)]
    if sea_db.size < 100:
        return []

    median_db = float(np.median(sea_db))
    # robust background sigma: pixels below median+6dB only (excludes ships)
    bg_px = sea_db[sea_db < median_db + 6.0]
    sigma_db = float(bg_px.std()) if bg_px.size > 10 else 1.0
    threshold = median_db + max(10.0, 5.0 * sigma_db)

    lon_range = BBOX[2] - BBOX[0]
    lat_range = BBOX[3] - BBOX[1]
    lat_mid   = (BBOX[1] + BBOX[3]) / 2
    m_per_lon = 111320 * cos(radians(lat_mid))
    m_per_lat = 111320

    bright  = np.where(np.isfinite(db), db > threshold, False)
    # Note: land_mask already zeroed in db above, so bright never fires on masked pixels
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

        q = np.column_stack([
            (lon_px - cx_lon) * m_per_lon,
            (lat_px - cy_lat) * m_per_lat,
        ])
        _, ev = np.linalg.eigh(np.cov(q.T))
        proj_long  = q @ ev[:, -1]
        proj_short = q @ ev[:, 0]
        length_m = float((proj_long.max()  - proj_long.min())  + RESOLUTION)
        width_m  = max(float((proj_short.max() - proj_short.min()) + RESOLUTION), RESOLUTION)

        if length_m < HULL_MIN_M or length_m > HULL_MAX_M:
            continue
        if length_m / width_m < ASPECT_MIN:
            continue

        ships.append({
            "cx_lon":   cx_lon,
            "cy_lat":   cy_lat,
            "length_m": length_m,
        })
    return ships

def _count_moving(ships_now, prev_positions):
    """
    moving: ship in ships_now where no prev ship with length >= LARGE_MIN_M
    is within PREV_PASS_KM.
    Returns (moving_count, stay_count).
    """
    lat_mid = (BBOX[1] + BBOX[3]) / 2
    m_per_lon = 111320 * cos(radians(lat_mid))
    m_per_lat = 111320

    moving = 0
    stay   = 0
    for s in ships_now:
        found_large_nearby = False
        for p in prev_positions:
            if p["length_m"] < LARGE_MIN_M:
                continue
            dlat_m = (s["cy_lat"] - p["cy_lat"]) * m_per_lat
            dlon_m = (s["cx_lon"] - p["cx_lon"]) * m_per_lon
            dist_km = ((dlat_m**2 + dlon_m**2) ** 0.5) / 1000.0
            if dist_km <= PREV_PASS_KM:
                found_large_nearby = True
                break
        if found_large_nearby:
            stay += 1
        else:
            moving += 1
    return moving, stay

# ── STAC discovery ─────────────────────────────────────────────────────────────

def _discover_dates(date_from, date_to):
    import pystac_client
    import planetary_computer
    from calendar import monthrange

    cat = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )

    seen = set()
    cur  = date.fromisoformat(date_from)
    end  = date.fromisoformat(date_to)

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

        cur = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)

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
    # Ensure mask exists before forking (workers inherit the cache)
    _get_land_mask()

    DIAG_DIR.mkdir(parents=True, exist_ok=True)

    done = {}
    if JSONL_PATH.exists():
        with open(JSONL_PATH) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done[r["date"]] = r.get("positions", [])
                except Exception:
                    pass

    todo = [d for d in dates if d not in done]
    print(f"irlane orbit-{ORBIT}: {len(dates)} dates total, {len(done)} cached, {len(todo)} to process")

    if not todo:
        _jsonl_to_csv()
        return

    # Build prev-pass lookup from cached rows + new results
    # Key: date_str → list of {cx_lon, cy_lat, length_m}
    results_by_date = dict(done)
    n_ok = n_skip = n_err = 0

    with open(JSONL_PATH, "a") as jf:
        with Pool(6) as pool:
            futures = {d: pool.apply_async(_process_date, (d,)) for d in todo}
            for d in todo:
                try:
                    date_str, ships, scene_id = futures[d].get(timeout=SCENE_TIMEOUT)
                except MPTimeout:
                    print(f"  {d}: err timeout")
                    n_err += 1
                    continue
                except Exception as e:
                    print(f"  {d}: err {e}")
                    n_err += 1
                    continue

                if ships is None:
                    if scene_id.startswith("coverage="):
                        cov_val = scene_id.split("=")[1].split("<")[0]
                        print(f"  {d}: skip cov={cov_val}")
                        n_skip += 1
                    else:
                        print(f"  {d}: err {scene_id}")
                        n_err += 1
                    continue

                # Find previous pass positions
                prev_key = None
                for k in sorted(results_by_date.keys()):
                    if k < d:
                        prev_key = k
                prev_pos = results_by_date.get(prev_key, [])

                moving, stay = _count_moving(ships, prev_pos)
                small  = sum(1 for s in ships if s["length_m"] < LARGE_MIN_M)
                large  = sum(1 for s in ships if s["length_m"] >= LARGE_MIN_M)
                vlcc   = sum(1 for s in ships if s["length_m"] >= VLCC_MIN_M)
                n_ok += 1

                positions = [{"cx_lon": s["cx_lon"], "cy_lat": s["cy_lat"],
                              "length_m": s["length_m"]} for s in ships]
                results_by_date[d] = positions

                row = {
                    "date": d, "orbit": ORBIT,
                    "small": small, "large": large, "vlcc": vlcc,
                    "moving": moving, "stay": stay,
                    "scene_id": scene_id,
                    "positions": positions,
                }
                jf.write(json.dumps(row) + "\n")
                jf.flush()
                print(f"  {d}: ok  small={small} large={large} vlcc={vlcc} moving={moving} stay={stay}")

    print(f"\ntotal={len(todo)} ok={n_ok} skip={n_skip} err={n_err}")
    _jsonl_to_csv()
    print(f"CSV: {CSV_PATH}")

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
        w = csv.DictWriter(f, fieldnames=["date", "large", "vlcc", "small", "moving", "stay", "scene_id"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--build-mask",  action="store_true")
    mode.add_argument("--backfill",    metavar="DATE")
    mode.add_argument("--dates",       metavar="D1,D2,...")
    args = ap.parse_args()

    today = date.today().isoformat()

    if args.build_mask:
        build_radar_mask(force=True)
        return

    if args.dates:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
        print(f"Explicit date list: {len(dates)} dates")
        run_backfill(dates)
        os._exit(0)

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


if __name__ == "__main__":
    main()
