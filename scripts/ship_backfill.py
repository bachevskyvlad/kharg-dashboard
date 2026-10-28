import os
"""
Historical Sentinel-1 ship detection backfill for Kharg anchorage.
Iterates weekly from START_DATE to today, detects ships (no Small),
appends results to data/anchorage.json.

Usage:
  python ship_backfill.py               # full backfill from 2017
  python ship_backfill.py --year 2021   # single year only
  python ship_backfill.py --resume      # skip already-processed dates
"""

import sys, os, json, argparse
from datetime import datetime, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest, SentinelHubCatalog,
    MimeType, bbox_to_dimensions, MosaickingOrder
)
from scipy import ndimage

# ── Config ────────────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID", "")
config.sh_client_secret = os.environ.get("CLIENT_SECRET", "")
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

KHARG_CENTER = (50.32, 29.24)
bbox = BBox(
    bbox=[
        KHARG_CENTER[0] - 0.27,
        KHARG_CENTER[1] - 0.15,
        KHARG_CENTER[0] + 0.27,
        KHARG_CENTER[1] + 0.27,
    ],
    crs=CRS.WGS84
)
RESOLUTION = 25
size = bbox_to_dimensions(bbox, resolution=RESOLUTION)

PLATFORMS = [
    (50.14, 29.38), (50.10, 29.35), (50.08, 29.42), (50.05, 29.30),
    (50.18, 29.48), (50.22, 29.50), (50.28, 29.52), (50.35, 29.55),
    (50.40, 29.50), (50.45, 29.45), (50.15, 29.20), (50.12, 29.15),
]
PLATFORM_BUFFER_PX = 4
KHARG_ISLAND_BBOX = (50.285, 29.185, 50.375, 29.300)

START_DATE = datetime(2017, 1, 1)
OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'data', 'anchorage.json')

evalscript = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  var db = 10 * Math.log10(s.VV + 1e-10);
  var norm = Math.max(0, Math.min(1, (db + 25) / 40));
  return [norm];
}
"""

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse", service_url="https://sh.dataspace.copernicus.eu"
)

def lon_lat_to_px(lon, lat):
    px = int((lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * size[0])
    py = int((bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * size[1])
    return px, py

def adaptive_threshold(arr):
    valid = arr[np.isfinite(arr) & (arr > 0.01)]
    if len(valid) == 0:
        return 0.80
    sea_level = np.percentile(valid, 70)
    sea_pixels = valid[valid <= sea_level]
    if len(sea_pixels) < 100:
        return sea_level + 0.20
    return float(np.clip(sea_level + 3.5 * sea_pixels.std(), 0.55, 0.92))

def detect_ships(arr):
    h, w = arr.shape
    mask = np.zeros((h, w), dtype=bool)

    ilon0, ilat0, ilon1, ilat1 = KHARG_ISLAND_BBOX
    ix0, iy1 = lon_lat_to_px(ilon0, ilat0)
    ix1, iy0 = lon_lat_to_px(ilon1, ilat1)
    mask[max(0,iy0):min(h,iy1), max(0,ix0):min(w,ix1)] = True

    for lon, lat in PLATFORMS:
        px, py = lon_lat_to_px(lon, lat)
        if 0 <= px < w and 0 <= py < h:
            mask[max(0,py-PLATFORM_BUFFER_PX):min(h,py+PLATFORM_BUFFER_PX),
                 max(0,px-PLATFORM_BUFFER_PX):min(w,px+PLATFORM_BUFFER_PX)] = True

    arr_m = arr.copy()
    arr_m[~np.isfinite(arr_m)] = 0
    arr_m[mask] = 0

    threshold = adaptive_threshold(arr_m)
    labeled, num = ndimage.label(arr_m > threshold)

    counts = {'vlcc': 0, 'suezmax': 0, 'aframax': 0, 'mr': 0, 'small': 0}
    for i in range(1, num + 1):
        comp = (labeled == i)
        area = comp.sum()
        if area < 3 or area > 400:
            continue
        ys, xs = np.where(comp)
        length_px = max(xs.max() - xs.min(), ys.max() - ys.min()) + 1
        length_m = length_px * RESOLUTION
        if length_m >= 290:
            counts['vlcc'] += 1
        elif length_m >= 260:
            counts['suezmax'] += 1
        elif length_m >= 220:
            counts['aframax'] += 1
        elif length_m >= 100:
            counts['mr'] += 1
        else:
            counts['small'] += 1

    return counts, threshold

def get_sar_pass_dates(start, end):
    """Get SAR pass dates from data_daily.json (same dates as oil storage pipeline)."""
    data_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'data_daily.json')
    try:
        with open(data_file) as f:
            data = json.load(f)
        from datetime import date as date_type
        dates = []
        for d in data.get('dates', []):
            try:
                dt = datetime.strptime(d, '%Y-%m-%d').date()
                if start.date() <= dt <= end.date():
                    dates.append(dt)
            except Exception:
                continue
        return sorted(dates)
    except Exception as e:
        print(f"Could not read data_daily.json: {e}")
        return []

def fetch_pass(pass_date):
    """Fetch SAR image for a single pass date (±0 day window)."""
    d = pass_date.strftime('%Y-%m-%d') if hasattr(pass_date, 'strftime') else str(pass_date)
    request = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(d, d),
            mosaicking_order=MosaickingOrder.MOST_RECENT,
            other_args={"dataFilter": {"polarization": "VV"}},
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox, size=size, config=config,
    )
    try:
        data = request.get_data()
    except Exception as e:
        print(f"  API error: {e}")
        return None
    if not data or data[0] is None:
        return None
    raw = np.array(data[0])
    if raw.ndim == 3 and raw.shape[2] >= 1:
        arr = raw[:, :, 0]
    elif raw.ndim == 3 and raw.shape[0] == 1:
        arr = raw[0]
    elif raw.ndim == 2:
        arr = raw
    else:
        arr = raw.reshape(raw.shape[-2], raw.shape[-1])
    if arr.max() < 0.01:
        return None
    return arr

def load_existing():
    if not os.path.exists(OUT_FILE):
        return {}
    with open(OUT_FILE) as f:
        data = json.load(f)
    # index by date string
    return {r['date']: r for r in data.get('records', [])}

def save_records(records):
    sorted_recs = sorted(records.values(), key=lambda r: r['date'])
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, 'w') as f:
        json.dump({'updated': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                   'records': sorted_recs}, f)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--year', type=int, default=None)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()

    records = load_existing() if args.resume else {}
    print(f"Loaded {len(records)} existing records")

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    if args.year:
        start = datetime(args.year, 1, 1)
        end   = datetime(args.year, 12, 31)
    else:
        start = START_DATE
        end   = today

    print(f"Querying Sentinel Hub catalog for SAR pass dates {start.date()} → {end.date()}...")
    pass_dates = get_sar_pass_dates(start, end)
    print(f"Found {len(pass_dates)} SAR passes")

    processed = 0
    for pass_date in pass_dates:
        date_key = str(pass_date)
        processed += 1

        # With --resume, skip already-processed dates UNLESS within last 5 days
        if args.resume and date_key in records:
            days_ago = (today.date() - pass_date).days if hasattr(pass_date, 'year') else (today - datetime.strptime(date_key, '%Y-%m-%d')).days
            if days_ago > 5:
                continue

        print(f"[{processed}/{len(pass_dates)}] {date_key}", end=' ', flush=True)

        arr = fetch_pass(pass_date)
        if arr is None:
            print("no data")
            continue

        counts, thr = detect_ships(arr)
        total_export = counts['vlcc'] + counts['suezmax'] + counts['aframax'] + counts['mr']
        print(f"thr={thr:.2f} total={total_export} "
              f"(V:{counts['vlcc']} S:{counts['suezmax']} A:{counts['aframax']} MR:{counts['mr']})")

        # Skip zero detections for recent passes — data may not be fully available
        days_ago = (today.date() - pass_date).days if hasattr(pass_date, 'year') else (today - datetime.strptime(date_key, '%Y-%m-%d')).days
        if total_export == 0 and days_ago < 2:
            print(f"  → skipping {date_key}: zero detections, SAR data may not be available yet")
            continue

        records[date_key] = {
            'date': date_key,
            'vlcc':    counts['vlcc'],
            'suezmax': counts['suezmax'],
            'aframax': counts['aframax'],
            'mr':      counts['mr'],
            'total':   total_export,
        }

        if processed % 10 == 0:
            save_records(records)

    save_records(records)
    print(f"\nDone. {len(records)} records saved to {OUT_FILE}")

if __name__ == '__main__':
    main()
