"""
Sentinel-1 SAR ship detection at the Bosphorus Black Sea anchorage.
Ships queue here waiting for passage into the strait.

Center: ~41.35N, 29.10E (Black Sea entrance anchorage)
Area covers: main anchorage + approach lanes north of strait entrance

Usage:
  python bosphorus_snapshot.py                    # latest available pass
  python bosphorus_snapshot.py --date 2026-08-01  # specific date
"""

import sys, os, argparse
from datetime import datetime, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    MimeType, bbox_to_dimensions, MosaickingOrder, SentinelHubCatalog,
)
from scipy import ndimage
from PIL import Image, ImageDraw, ImageFont

config = SHConfig()
config.sh_client_id     = "sh-4a504e67-3704-4c78-bfbc-e5b92cb1d5b4"
config.sh_client_secret = "U1W6BcqS4bFEIJy2u9xXdlBMZPxtQMBU"
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

# Black Sea anchorage — wide zone north of Bosphorus entrance
# Covers: full Black Sea waiting area + lateral anchorage zones
BBOX_COORDS = [28.70, 41.10, 29.40, 41.70]

bbox = BBox(bbox=BBOX_COORDS, crs=CRS.WGS84)
RESOLUTION = 35  # 35m — wide zone fits within 2500px limit
size = bbox_to_dimensions(bbox, resolution=RESOLUTION)
print(f"Image size: {size[0]}×{size[1]}px at {RESOLUTION}m/px", flush=True)

# Land mask boxes — only the narrow strait body and immediate shores
# Keep minimal: false land masking kills valid ship detections
# Coastline polygon — traces the Black Sea shore of Istanbul + Bosphorus + Asian coast.
# Points go clockwise: bottom edge → Asian coast → strait → European coast → bottom edge.
# Ships anchor north of this line; everything inside the polygon is masked as land.
LAND_POLYGON = [
    # Bottom-left corner
    (28.70, 41.10),
    # European (Istanbul) Black Sea coast, west → east
    (28.70, 41.185),
    (28.75, 41.182),
    (28.80, 41.183),
    (28.85, 41.187),
    (28.90, 41.188),
    (28.93, 41.183),
    (28.96, 41.175),  # Rumeli Kavagi
    # Bosphorus strait — European side drops south, then Asian side rises north
    (28.97, 41.165),
    (28.98, 41.135),  # strait narrows
    (29.02, 41.125),
    (29.06, 41.130),
    (29.07, 41.155),
    (29.08, 41.175),  # Anadolu Kavagi
    # Asian (Anatolia) Black Sea coast, west → east
    (29.10, 41.195),
    (29.15, 41.215),
    (29.20, 41.215),
    (29.25, 41.200),
    (29.30, 41.190),
    (29.35, 41.185),
    (29.40, 41.180),
    # Bottom-right corner and back
    (29.40, 41.10),
    (28.70, 41.10),
]

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_bosphorus", service_url="https://sh.dataspace.copernicus.eu"
)

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

CLASS_COLORS = {
    'vlcc':    (239, 68,  68),
    'suezmax': (249, 115, 22),
    'aframax': (234, 179, 8),
    'mr':      (34,  197, 94),
    'small':   (148, 163, 184),
}

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')


def lon_lat_to_px(lon, lat):
    px = int((lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * size[0])
    py = int((bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * size[1])
    return px, py


def adaptive_threshold(arr):
    valid = arr[np.isfinite(arr) & (arr > 0.01)]
    if len(valid) == 0:
        return 0.75
    nan_frac = (~np.isfinite(arr)).sum() / arr.size
    sea_level = np.percentile(valid, 70)
    sea_pixels = valid[valid <= sea_level]
    if len(sea_pixels) < 100:
        return sea_level + 0.20
    thr = sea_level + 2.5 * sea_pixels.std()
    min_thr = 0.55 if nan_frac > 0.15 else 0.42
    return float(np.clip(thr, min_thr, 0.90))


def build_land_mask(h, w):
    # Convert coastline polygon to pixel coords and fill with PIL
    px_coords = [lon_lat_to_px(lon, lat) for lon, lat in LAND_POLYGON]
    mask_img = Image.new('L', (w, h), 0)
    ImageDraw.Draw(mask_img).polygon(px_coords, fill=255)
    return np.array(mask_img) > 0


def classify_ship(length_m):
    if length_m >= 290:  return 'vlcc'
    if length_m >= 260:  return 'suezmax'
    if length_m >= 220:  return 'aframax'
    if length_m >= 100:  return 'mr'
    if length_m >= 40:   return 'small'
    return None


def detect_ships(arr):
    h, w = arr.shape
    land_mask = build_land_mask(h, w)

    arr_m = arr.copy()
    arr_m[~np.isfinite(arr_m)] = 0
    arr_m[land_mask] = 0

    threshold = adaptive_threshold(arr_m)
    labeled, num = ndimage.label(arr_m > threshold)

    ships = []
    for i in range(1, num + 1):
        comp = (labeled == i)
        area = comp.sum()
        if area < 5 or area > 600:
            continue
        ys, xs = np.where(comp)
        length_px = max(xs.max() - xs.min(), ys.max() - ys.min()) + 1
        length_m = length_px * RESOLUTION
        cls = classify_ship(length_m)
        if cls is None:
            continue
        ships.append({
            'class': cls,
            'x0': int(xs.min()), 'y0': int(ys.min()),
            'x1': int(xs.max()), 'y1': int(ys.max()),
            'length_m': int(length_m),
        })
    return ships, threshold, land_mask


def arr_to_image(arr, land_mask):
    a = np.clip(arr, 0, 1)
    a = np.power(a, 0.55)
    gray = (a * 255).astype(np.uint8)
    rgb = np.stack([gray, gray, gray], axis=2)
    rgb[land_mask, 0] = (rgb[land_mask, 0] * 0.3).astype(np.uint8)
    rgb[land_mask, 1] = (rgb[land_mask, 1] * 0.3).astype(np.uint8)
    rgb[land_mask, 2] = np.clip(rgb[land_mask, 2] * 0.3 + 50, 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, 'RGB')


def annotate_image(img, ships, date_str, threshold):
    draw = ImageDraw.Draw(img)
    try:
        font    = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 11)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 9)
    except Exception:
        font = font_sm = ImageFont.load_default()

    pad = 3
    for s in ships:
        color = CLASS_COLORS[s['class']]
        x0, y0, x1, y1 = s['x0']-pad, s['y0']-pad, s['x1']+pad, s['y1']+pad
        draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
        label = f"{s['class'].upper()} ~{s['length_m']}m"
        draw.text((x0, max(0, y0-13)), label, fill=color, font=font_sm)

    legend_x, legend_y = 10, 10
    for cls, color in CLASS_COLORS.items():
        count = sum(1 for s in ships if s['class'] == cls)
        draw.rectangle([legend_x, legend_y, legend_x+12, legend_y+12], fill=color)
        draw.text((legend_x+16, legend_y), f"{cls.upper()}: {count}", fill=(220, 220, 220), font=font_sm)
        legend_y += 18

    counts = {cls: sum(1 for s in ships if s['class'] == cls) for cls in CLASS_COLORS}
    total = len(ships)
    queue_index = counts['vlcc'] + counts['suezmax'] + counts['aframax'] + counts['mr']
    info = (f"{date_str}  |  Bosphorus Black Sea anchorage  |  "
            f"{total} vessels  |  Queue index: {queue_index}  |  thr={threshold:.3f}  |  Sentinel-1 IW 35m")
    h = img.height
    draw.rectangle([0, h-20, img.width, h], fill=(0, 0, 0))
    draw.text((8, h-16), info, fill=(150, 150, 150), font=font_sm)
    return img


def get_sar_dates(days_back=14, limit=None):
    """Return sorted list of unique SAR pass dates (YYYY-MM-DD strings)."""
    try:
        import pandas as pd
        catalog = SentinelHubCatalog(config=config)
        start = (datetime.utcnow() - timedelta(days=days_back)).strftime('%Y-%m-%d')
        end   = datetime.utcnow().strftime('%Y-%m-%d')
        scenes = list(catalog.search(
            S1_CDSE, bbox=bbox,
            time=(start, end),
            fields={"include": ["properties.datetime"]}
        ))
        if not scenes:
            return []
        raw_dates = sorted(pd.to_datetime(s["properties"]["datetime"]) for s in scenes)
        seen, unique = set(), []
        for d in reversed(raw_dates):
            ds = d.strftime('%Y-%m-%d')
            if ds not in seen:
                seen.add(ds)
                unique.append(ds)
        unique.reverse()
        return unique if limit is None else unique[-limit:]
    except Exception as e:
        print(f"Catalog error: {e}")
        return []


def run_date(date_str):
    """Fetch SAR, detect ships, save PNG. Returns result dict."""
    print(f"\nFetching SAR image for {date_str}…")
    request = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(date_str, date_str),
            mosaicking_order=MosaickingOrder.MOST_RECENT,
            other_args={"dataFilter": {"polarization": "DV"}},
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox, size=size, config=config,
    )
    data = request.get_data()
    raw = np.array(data[0])
    if raw.ndim == 3:
        arr = raw[:, :, 0]
    elif raw.ndim == 2:
        arr = raw
    else:
        arr = raw.reshape(raw.shape[-2], raw.shape[-1])

    nanmax = float(np.nanmax(arr)) if np.any(np.isfinite(arr)) else 0.0
    nanmean = float(np.nanmean(arr)) if np.any(np.isfinite(arr)) else 0.0
    nan_pct = (~np.isfinite(arr)).sum() / arr.size * 100
    print(f"  shape={arr.shape}  max={nanmax:.3f}  mean={nanmean:.4f}  nan={nan_pct:.0f}%")

    if nanmax < 0.01:
        print("  No data for this date.")
        return None

    ships, threshold, land_mask = detect_ships(arr)
    counts = {cls: sum(1 for s in ships if s['class'] == cls) for cls in CLASS_COLORS}
    queue_index = counts['vlcc'] + counts['suezmax'] + counts['aframax'] + counts['mr']

    img = arr_to_image(arr, land_mask)
    img = annotate_image(img, ships, date_str, threshold)
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"bosphorus_{date_str}.png")
    img.save(out_path)

    return {'date': date_str, 'total': len(ships), 'queue_index': queue_index,
            'counts': counts, 'threshold': threshold, 'path': out_path}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, default=None)
    parser.add_argument('--backfill', type=int, default=None,
                        help='Process last N SAR passes')
    args = parser.parse_args()

    if args.backfill:
        n = args.backfill
        print(f"Querying catalog for last {n} SAR passes (up to 90 days back)…")
        dates = get_sar_dates(days_back=90, limit=n)
        if not dates:
            print("No SAR passes found.")
            return
        print(f"Found {len(dates)} passes: {dates}")
        results = []
        for d in dates:
            r = run_date(d)
            if r:
                results.append(r)
        print(f"\n{'='*60}")
        print(f"{'Date':<12} {'Total':>6} {'QueueIdx':>9} {'VLCC':>5} {'SMAX':>5} {'AFRX':>5} {'MR':>5} {'SML':>5}  thr")
        print(f"{'-'*60}")
        for r in results:
            c = r['counts']
            print(f"{r['date']:<12} {r['total']:>6} {r['queue_index']:>9} "
                  f"{c['vlcc']:>5} {c['suezmax']:>5} {c['aframax']:>5} "
                  f"{c['mr']:>5} {c['small']:>5}  {r['threshold']:.3f}")
        return

    if args.date:
        date_str = args.date
    else:
        print("Querying catalog for latest SAR pass…")
        dates = get_sar_dates(days_back=14, limit=1)
        if dates:
            date_str = dates[-1]
        else:
            date_str = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d')
        print(f"Latest pass: {date_str}")

    r = run_date(date_str)
    if not r:
        return
    print(f"\n=== Bosphorus Queue — {r['date']} ===")
    print(f"Total vessels:  {r['total']}")
    print(f"Queue index:    {r['queue_index']}  (VLCC+Suezmax+Aframax+MR)")
    for cls in CLASS_COLORS:
        print(f"  {cls.upper():10s}: {r['counts'][cls]}")
    print(f"Threshold:      {r['threshold']:.3f}")
    print(f"\nSaved: {r['path']}")


if __name__ == '__main__':
    main()
