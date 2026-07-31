"""
Historical Sentinel-1 ship detection backfill for Bab-el-Mandeb anchorage zone.
Covers the Gulf of Aden approaches east of the strait (~80km x 80km box).
Ships queue here waiting to transit into the Red Sea.

Usage:
  python ship_backfill_bab.py               # full backfill from 2017
  python ship_backfill_bab.py --year 2024   # single year only
  python ship_backfill_bab.py --resume      # skip already-processed dates
"""

import sys, os, json, argparse
from datetime import datetime, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    MimeType, bbox_to_dimensions, MosaickingOrder
)
from scipy import ndimage

config = SHConfig()
config.sh_client_id     = "sh-4a504e67-3704-4c78-bfbc-e5b92cb1d5b4"
config.sh_client_secret = "U1W6BcqS4bFEIJy2u9xXdlBMZPxtQMBU"
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

# Bab-el-Mandeb anchorage: Gulf of Aden approaches + strait entrance
# Gulf of Aden anchorage — ships queue here before transiting north into Red Sea
# IRTC Point A is ~45°E; main holding area: 44.5–46.5°E, 11.3–12.3°N
BAB_CENTER = (45.5, 11.8)
OFFSET_LON = 1.0   # ~111km east–west
OFFSET_LAT = 0.50  # ~55km north–south

bbox = BBox(
    bbox=[
        BAB_CENTER[0] - OFFSET_LON,
        BAB_CENTER[1] - OFFSET_LAT,
        BAB_CENTER[0] + OFFSET_LON,
        BAB_CENTER[1] + OFFSET_LAT,
    ],
    crs=CRS.WGS84
)

RESOLUTION = 90  # 90m → image ~2420×1100px, within API 2500px limit
size = bbox_to_dimensions(bbox, resolution=RESOLUTION)

# Land exclusions — Perim Island and coastal margins
# Perim Island: 12.655°N, 43.424°E (~4km × 2km)
LAND_BOXES = [
    # Somalia/Gulf of Aden north coast (Yemen east)
    (44.0, 12.15, 46.5, 12.50),
    # Somalia/Berbera coast (south shore of Gulf of Aden)
    (44.0, 11.10, 46.5, 11.35),
]

START_DATE = datetime(2017, 1, 1)
OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'data', 'bab_anchorage.json')

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
    return float(np.clip(sea_level + 2.5 * sea_pixels.std(), 0.50, 0.88))

def detect_ships(arr):
    h, w = arr.shape
    mask = np.zeros((h, w), dtype=bool)

    for (lon0, lat0, lon1, lat1) in LAND_BOXES:
        ix0, iy1 = lon_lat_to_px(lon0, lat0)
        ix1, iy0 = lon_lat_to_px(lon1, lat1)
        mask[max(0,iy0):min(h,iy1+1), max(0,ix0):min(w,ix1+1)] = True

    arr_m = arr.copy()
    arr_m[~np.isfinite(arr_m)] = 0
    arr_m[mask] = 0

    threshold = adaptive_threshold(arr_m)
    labeled, num = ndimage.label(arr_m > threshold)

    counts = {'vlcc': 0, 'suezmax': 0, 'aframax': 0, 'mr': 0}
    for i in range(1, num + 1):
        comp = (labeled == i)
        area = comp.sum()
        if area < 2 or area > 500:
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

    return counts, threshold

def fetch_week(date_from, date_to):
    request = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(date_from.strftime('%Y-%m-%d'), date_to.strftime('%Y-%m-%d')),
            mosaicking_order=MosaickingOrder.MOST_RECENT,
            other_args={"dataFilter": {"polarization": "DV"}},
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
    return {r['date']: r for r in data.get('records', [])}

def save_records(records):
    sorted_recs = sorted(records.values(), key=lambda r: r['date'])
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, 'w') as f:
        json.dump({'updated': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                   'records': sorted_recs}, f)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--year', type=int)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--from-date', type=str, help='YYYY-MM-DD')
    args = parser.parse_args()

    records = load_existing() if args.resume or args.year else {}

    start = START_DATE
    if args.year:
        start = datetime(args.year, 1, 1)
        end = datetime(args.year, 12, 31)
    elif args.from_date:
        start = datetime.strptime(args.from_date, '%Y-%m-%d')
        end = datetime.utcnow()
    else:
        end = datetime.utcnow()

    cur = start
    while cur <= end:
        date_str = cur.strftime('%Y-%m-%d')
        if args.resume and date_str in records:
            cur += timedelta(weeks=1)
            continue

        date_to = cur + timedelta(days=6)
        print(f"Processing {date_str}…", end=' ', flush=True)

        arr = fetch_week(cur, date_to)
        if arr is None:
            print("no data")
            records[date_str] = {'date': date_str, 'vlcc': 0, 'suezmax': 0, 'aframax': 0, 'mr': 0, 'total': 0}
            cur += timedelta(weeks=1)
            continue

        counts, threshold = detect_ships(arr)
        total = counts['vlcc'] + counts['suezmax'] + counts['aframax'] + counts['mr']
        records[date_str] = {
            'date': date_str,
            'vlcc': counts['vlcc'],
            'suezmax': counts['suezmax'],
            'aframax': counts['aframax'],
            'mr': counts['mr'],
            'total': total,
        }
        print(f"VLCC={counts['vlcc']} SX={counts['suezmax']} AF={counts['aframax']} MR={counts['mr']} total={total} thr={threshold:.3f}")

        save_records(records)
        cur += timedelta(weeks=1)

    print(f"\nDone. {len(records)} records saved to {OUT_FILE}")

if __name__ == '__main__':
    main()
