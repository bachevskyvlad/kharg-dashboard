"""
Sentinel-1 SAR vessel detection at the Bosphorus Black Sea anchorage.
Ships queue here waiting for passage into the strait.

Center: ~41.35N, 29.10E (Black Sea entrance anchorage)
Area covers: main anchorage + approach lanes north of strait entrance

Usage:
  python bosphorus_snapshot.py                    # latest available pass
  python bosphorus_snapshot.py --date 2026-08-01  # specific date
  python bosphorus_snapshot.py --backfill 10      # last N passes
  python bosphorus_snapshot.py --backfill-all     # all available (10yr)
"""

import sys, os, argparse, json
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

REGIONS = {
    'black_sea': {
        'bbox':  [28.70, 41.10, 29.40, 41.70],
        'label': 'Bosphorus Black Sea anchorage',
        'desc':  'Vessels queuing northbound into the Black Sea',
    },
    'marmara': {
        'bbox':  [28.40, 40.78, 29.40, 41.08],
        'label': 'Bosphorus Marmara anchorage',
        'desc':  'Vessels queuing southbound into the Marmara Sea',
    },
}

# Region is set in main() via --region arg; defaults to black_sea
REGION_ID   = 'black_sea'
REGION      = REGIONS[REGION_ID]
BBOX_COORDS = REGION['bbox']

bbox = BBox(bbox=BBOX_COORDS, crs=CRS.WGS84)
RESOLUTION = 35
size = bbox_to_dimensions(bbox, resolution=RESOLUTION)
print(f"Region: {REGION['label']}", flush=True)
print(f"Image size: {size[0]}×{size[1]}px at {RESOLUTION}m/px", flush=True)

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

# Vessel size classes — general maritime terminology
CLASS_COLORS = {
    'capesize':    (239, 68,  68),   # ≥290m
    'postpanamax': (249, 115, 22),   # ≥260m
    'panamax':     (234, 179, 8),    # ≥220m
    'handymax':    (34,  197, 94),   # ≥100m
}
CLASS_LABELS = {
    'capesize':    'Capesize  ≥290m',
    'postpanamax': 'Post-Panamax ≥260m',
    'panamax':     'Panamax  ≥220m',
    'handymax':    'Handymax ≥100m',
}

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
HISTORY_PATH = os.path.join(OUT_DIR, 'bosphorus_history.json')  # updated in main() for marmara


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
    import cartopy.io.shapereader as shpreader
    from shapely.geometry import box as shapely_box
    from shapely.ops import unary_union

    bbox_geom = shapely_box(BBOX_COORDS[0], BBOX_COORDS[1], BBOX_COORDS[2], BBOX_COORDS[3])
    land_shp = shpreader.natural_earth(resolution='10m', category='physical', name='land')
    reader = shpreader.Reader(land_shp)
    polys = [g.intersection(bbox_geom) for g in reader.geometries() if g.intersects(bbox_geom)]
    if not polys:
        return np.zeros((h, w), dtype=bool)
    land = unary_union(polys)

    mask_img = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(mask_img)

    def _draw(geom):
        if geom.geom_type == 'Polygon':
            pts = [lon_lat_to_px(x, y) for x, y in geom.exterior.coords]
            draw.polygon(pts, fill=255)
        elif geom.geom_type == 'MultiPolygon':
            for part in geom.geoms:
                _draw(part)

    _draw(land)
    mask = np.array(mask_img) > 0
    # ~875m coastal buffer to absorb clutter
    mask = ndimage.binary_dilation(mask, iterations=25)

    # Mask Bosphorus strait channel — ships there are in transit, not anchorage
    x0, y1 = lon_lat_to_px(28.94, 41.10)
    x1, y0 = lon_lat_to_px(29.12, 41.25)
    mask[max(0, y0):min(h, y1+1), max(0, x0):min(w, x1+1)] = True

    return mask


def classify_ship(length_m):
    if length_m >= 290:  return 'capesize'
    if length_m >= 260:  return 'postpanamax'
    if length_m >= 220:  return 'panamax'
    if length_m >= 100:  return 'handymax'
    return None  # <100m coastal/small vessels excluded


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
        draw.text((legend_x+16, legend_y), f"{CLASS_LABELS[cls]}: {count}", fill=(220, 220, 220), font=font_sm)
        legend_y += 18

    counts = {cls: sum(1 for s in ships if s['class'] == cls) for cls in CLASS_COLORS}
    total = len(ships)
    info = (f"{date_str}  |  {REGION['label']}  |  "
            f"{total} vessels  |  thr={threshold:.3f}  |  Sentinel-1 IW 35m")
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


def load_history():
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            return json.load(f)
    return {"updated": "", "passes": []}


def save_history(new_results):
    os.makedirs(OUT_DIR, exist_ok=True)
    hist = load_history()
    existing = {p['date']: p for p in hist['passes']}
    for r in new_results:
        existing[r['date']] = {
            'date': r['date'],
            'total': r['total'],
            'capesize':    r['counts']['capesize'],
            'postpanamax': r['counts']['postpanamax'],
            'panamax':     r['counts']['panamax'],
            'handymax':    r['counts']['handymax'],
            'threshold':   round(r['threshold'], 3),
        }
    passes = sorted(existing.values(), key=lambda x: x['date'])
    hist['updated'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    hist['passes'] = passes
    with open(HISTORY_PATH, 'w') as f:
        json.dump(hist, f, separators=(',', ':'))
    print(f"History saved: {len(passes)} passes → {HISTORY_PATH}")


def run_date(date_str):
    """Fetch SAR, detect vessels, save PNG. Returns result dict."""
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

    nanmax  = float(np.nanmax(arr)) if np.any(np.isfinite(arr)) else 0.0
    nanmean = float(np.nanmean(arr)) if np.any(np.isfinite(arr)) else 0.0
    nan_pct = (~np.isfinite(arr)).sum() / arr.size * 100
    print(f"  shape={arr.shape}  max={nanmax:.3f}  mean={nanmean:.4f}  nan={nan_pct:.0f}%")

    if nanmax < 0.01:
        print("  No data for this date.")
        return None

    ships, threshold, land_mask = detect_ships(arr)
    counts = {cls: sum(1 for s in ships if s['class'] == cls) for cls in CLASS_COLORS}

    img = arr_to_image(arr, land_mask)
    img = annotate_image(img, ships, date_str, threshold)
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"bosphorus_{REGION_ID}_{date_str}.png")
    img.save(out_path)

    return {'date': date_str, 'total': len(ships), 'counts': counts,
            'threshold': threshold, 'path': out_path}


def main():
    global REGION_ID, REGION, BBOX_COORDS, bbox, size, S1_CDSE, HISTORY_PATH

    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, default=None)
    parser.add_argument('--backfill', type=int, default=None,
                        help='Process last N SAR passes')
    parser.add_argument('--backfill-all', action='store_true',
                        help='Process all available SAR passes (~10 years)')
    parser.add_argument('--region', type=str, default='black_sea',
                        choices=list(REGIONS.keys()),
                        help='Anchorage region: black_sea (default) or marmara')
    args = parser.parse_args()

    if args.region != REGION_ID:
        REGION_ID   = args.region
        REGION      = REGIONS[REGION_ID]
        BBOX_COORDS = REGION['bbox']
        bbox        = BBox(bbox=BBOX_COORDS, crs=CRS.WGS84)
        size        = bbox_to_dimensions(bbox, resolution=RESOLUTION)
        print(f"Region: {REGION['label']}")
        print(f"Image size: {size[0]}×{size[1]}px at {RESOLUTION}m/px", flush=True)
    HISTORY_PATH = os.path.join(OUT_DIR, f'bosphorus_{REGION_ID}_history.json')

    if args.backfill_all or args.backfill:
        if args.backfill_all:
            days_back = 3660  # ~10 years
            limit = None
            print("Querying catalog for all SAR passes (~10 years)…")
        else:
            n = args.backfill
            days_back = 90
            limit = n
            print(f"Querying catalog for last {n} SAR passes (up to 90 days back)…")

        dates = get_sar_dates(days_back=days_back, limit=limit)
        if not dates:
            print("No SAR passes found.")
            return
        print(f"Found {len(dates)} passes: {dates[0]} … {dates[-1]}")

        # Skip dates already in history to resume interrupted runs
        hist = load_history()
        done = {p['date'] for p in hist['passes']}
        pending = [d for d in dates if d not in done]
        print(f"Already processed: {len(done)}  Pending: {len(pending)}")

        results = []
        for d in pending:
            r = run_date(d)
            if r:
                results.append(r)

        if results:
            save_history(results)

        print(f"\n{'='*65}")
        print(f"{'Date':<12} {'Total':>6} {'Cape':>5} {'PostP':>5} {'Pan':>5} {'Hmax':>5}  thr")
        print(f"{'-'*65}")
        all_results = sorted(
            [{'date': p['date'], 'total': p['total'],
              'counts': {k: p[k] for k in CLASS_COLORS},
              'threshold': p['threshold']}
             for p in load_history()['passes']],
            key=lambda x: x['date']
        )
        for r in all_results[-20:]:
            c = r['counts']
            print(f"{r['date']:<12} {r['total']:>6} "
                  f"{c['capesize']:>5} {c['postpanamax']:>5} "
                  f"{c['panamax']:>5} {c['handymax']:>5}  {r['threshold']:.3f}")
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
    save_history([r])
    print(f"\n=== Bosphorus Queue — {r['date']} ===")
    print(f"Total vessels:  {r['total']}")
    for cls in CLASS_COLORS:
        print(f"  {cls:15s}: {r['counts'][cls]}")
    print(f"Threshold:      {r['threshold']:.3f}")
    print(f"\nSaved: {r['path']}")


if __name__ == '__main__':
    main()
