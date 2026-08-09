"""
Sentinel-1 SAR vessel detection at the Dardanelles Strait anchorage zones.

Zones:
  aegean_entrance — Aegean side (Bozcaada / Çanakkale)
  marmara_south   — Marmara south coast (Şarköy / Tekirdağ)

Usage:
  python dardanelles_snapshot.py                       # latest available pass, both zones
  python dardanelles_snapshot.py --date 2026-08-01     # specific date
  python dardanelles_snapshot.py --backfill 10         # last N passes
  python dardanelles_snapshot.py --backfill-all        # all available (~10yr)
  python dardanelles_snapshot.py --region aegean_entrance
  python dardanelles_snapshot.py --region marmara_south
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
config.sh_client_id     = "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
config.sh_client_secret = "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

ZONES = {
    'aegean_entrance': {
        'bbox':     [25.62, 39.70, 26.22, 39.98],
        'label':    'Dardanelles — Aegean entrance',
        'desc':     'Vessels queuing eastbound at the Aegean side (open Aegean anchorage)',
        'dilation': 30,
    },
    'marmara_south': {
        'bbox':     [26.80, 40.52, 27.40, 40.68],
        'label':    'Dardanelles — Marmara Sea',
        'desc':     'Vessels queuing westbound at the Marmara side (Şarköy anchorage)',
        'dilation': 55,
    },
}

REGION_ID   = 'aegean_entrance'
REGION      = ZONES[REGION_ID]
BBOX_COORDS = REGION['bbox']

bbox = BBox(bbox=BBOX_COORDS, crs=CRS.WGS84)
RESOLUTION = 35
size = bbox_to_dimensions(bbox, resolution=RESOLUTION)

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_dardanelles", service_url="https://sh.dataspace.copernicus.eu"
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
    'capesize':    (239, 68,  68),
    'postpanamax': (249, 115, 22),
    'panamax':     (234, 179, 8),
    'handymax':    (34,  197, 94),
}
CLASS_LABELS = {
    'capesize':    'Capesize  ≥290m',
    'postpanamax': 'Post-Panamax ≥260m',
    'panamax':     'Panamax  ≥220m',
    'handymax':    'Handymax ≥100m',
}

OUT_DIR      = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
HISTORY_PATH = os.path.join(OUT_DIR, 'dardanelles_aegean_history.json')


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

    bbox_geom = shapely_box(*BBOX_COORDS)
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
    mask = ndimage.binary_dilation(mask, iterations=REGION['dilation'])
    return mask


def classify_ship(length_m):
    if length_m >= 290: return 'capesize'
    if length_m >= 260: return 'postpanamax'
    if length_m >= 220: return 'panamax'
    if length_m >= 100: return 'handymax'
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
        draw.text((legend_x+16, legend_y), f"{CLASS_LABELS[cls]}: {count}", fill=(220, 220, 220), font=font_sm)
        legend_y += 18

    total = len(ships)
    info = (f"{date_str}  |  {REGION['label']}  |  "
            f"{total} vessels  |  thr={threshold:.3f}  |  Sentinel-1 IW 35m")
    h = img.height
    draw.rectangle([0, h-20, img.width, h], fill=(0, 0, 0))
    draw.text((8, h-16), info, fill=(150, 150, 150), font=font_sm)
    return img


def get_sar_dates(days_back=14, limit=None):
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
    out_path = os.path.join(OUT_DIR, f"dardanelles_{REGION_ID}_{date_str}.png")
    img.save(out_path)

    return {'date': date_str, 'total': len(ships), 'counts': counts,
            'threshold': threshold, 'path': out_path}


def _git_commit_history():
    import subprocess
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rel_path  = os.path.relpath(HISTORY_PATH, repo_root)
    subprocess.run(['git', '-C', repo_root, 'config', 'user.name', 'github-actions[bot]'], check=False)
    subprocess.run(['git', '-C', repo_root, 'config', 'user.email', 'github-actions[bot]@users.noreply.github.com'], check=False)
    subprocess.run(['git', '-C', repo_root, 'add', rel_path], check=False)
    diff = subprocess.run(['git', '-C', repo_root, 'diff', '--cached', '--quiet'])
    if diff.returncode == 0:
        return
    subprocess.run([
        'git', '-C', repo_root, 'commit', '-m',
        f'chore: dardanelles {REGION_ID} progress snapshot',
    ], check=False)
    push = subprocess.run(['git', '-C', repo_root, 'push', 'origin', 'HEAD:main'])
    if push.returncode != 0:
        subprocess.run(['git', '-C', repo_root, 'pull', '--rebase', 'origin', 'main'], check=False)
        subprocess.run(['git', '-C', repo_root, 'push', 'origin', 'HEAD:main'], check=False)
    print(f"[git] Progress committed ({HISTORY_PATH})")


def main():
    global REGION_ID, REGION, BBOX_COORDS, bbox, size, S1_CDSE, HISTORY_PATH

    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, default=None)
    parser.add_argument('--backfill', type=int, default=None,
                        help='Process last N SAR passes')
    parser.add_argument('--backfill-all', action='store_true',
                        help='Process all available SAR passes (~10 years)')
    parser.add_argument('--region', type=str, default='aegean_entrance',
                        choices=list(ZONES.keys()),
                        help='Zone: aegean_entrance (default) or marmara_south')
    args = parser.parse_args()

    if args.region != REGION_ID:
        REGION_ID   = args.region
        REGION      = ZONES[REGION_ID]
        BBOX_COORDS = REGION['bbox']
        bbox        = BBox(bbox=BBOX_COORDS, crs=CRS.WGS84)
        size        = bbox_to_dimensions(bbox, resolution=RESOLUTION)
        print(f"Region: {REGION['label']}")
        print(f"Image size: {size[0]}×{size[1]}px at {RESOLUTION}m/px", flush=True)

    history_name = 'dardanelles_aegean_history.json' if REGION_ID == 'aegean_entrance' else 'dardanelles_marmara_history.json'
    HISTORY_PATH = os.path.join(OUT_DIR, history_name)

    print(f"Region: {REGION['label']}")
    print(f"Image size: {size[0]}×{size[1]}px at {RESOLUTION}m/px", flush=True)

    if args.backfill_all or args.backfill:
        if args.backfill_all:
            days_back = 3660
            limit = None
            print("Querying catalog for all SAR passes (~10 years)…")
        else:
            days_back = 90
            limit = args.backfill
            print(f"Querying catalog for last {args.backfill} SAR passes…")

        dates = get_sar_dates(days_back=days_back, limit=limit)
        if not dates:
            print("No SAR passes found.")
            return
        print(f"Found {len(dates)} passes: {dates[0]} … {dates[-1]}")

        hist = load_history()
        done = {p['date'] for p in hist['passes']}
        pending = [d for d in dates if d not in done]
        print(f"Already processed: {len(done)}  Pending: {len(pending)}")

        results = []
        for i, d in enumerate(pending):
            r = run_date(d)
            if r:
                results.append(r)
            if results and (i + 1) % 25 == 0:
                save_history(results)
                _git_commit_history()
                results = []

        if results:
            save_history(results)
            _git_commit_history()

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
    print(f"\n=== Dardanelles Queue — {r['date']} ===")
    print(f"Total vessels:  {r['total']}")
    for cls in CLASS_COLORS:
        print(f"  {cls:15s}: {r['counts'][cls]}")
    print(f"Threshold:      {r['threshold']:.3f}")
    print(f"\nSaved: {r['path']}")


if __name__ == '__main__':
    main()
