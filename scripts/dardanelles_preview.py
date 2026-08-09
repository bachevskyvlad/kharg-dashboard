"""
Sentinel-1 SAR vessel detection — Dardanelles anchorage zones.
Two zones:
  aegean_entrance — Aegean side (Bozcaada / Çanakkale)
  marmara_south   — Marmara south coast (Şarköy)
"""
import os, sys, json
from datetime import datetime, timedelta
import numpy as np
from scipy import ndimage
from PIL import Image, ImageDraw

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    MimeType, bbox_to_dimensions, SentinelHubCatalog,
)

config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID")     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

ZONES = {
    "aegean_entrance": {
        "bbox":     [26.00, 40.00, 26.55, 40.22],  # tighter — strait entrance, Gallipoli coast
        "label":    "Dardanelles — Aegean entrance",
        "dilation": 30,
    },
    "marmara_south": {
        "bbox":     [26.80, 40.52, 27.40, 40.68],  # trim north (другое море), stay offshore
        "label":    "Dardanelles — Marmara Sea",
        "dilation": 40,
    },
}

RESOLUTION = 35

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


def classify_ship(length_m):
    if length_m >= 290: return 'capesize'
    if length_m >= 260: return 'postpanamax'
    if length_m >= 220: return 'panamax'
    if length_m >= 100: return 'handymax'
    return None


def adaptive_threshold(arr):
    valid = arr[np.isfinite(arr) & (arr > 0.01)]
    if len(valid) == 0:
        return 0.75
    sea_level = np.percentile(valid, 70)
    sea_pixels = valid[valid <= sea_level]
    if len(sea_pixels) < 100:
        return sea_level + 0.20
    thr = sea_level + 2.5 * sea_pixels.std()
    return float(np.clip(thr, 0.42, 0.90))


def build_land_mask(h, w, bbox_coords, dilation):
    import cartopy.io.shapereader as shpreader
    from shapely.geometry import box as shapely_box
    from shapely.ops import unary_union

    bbox_geom = shapely_box(*bbox_coords)
    land_shp = shpreader.natural_earth(resolution='10m', category='physical', name='land')
    reader = shpreader.Reader(land_shp)
    polys = [g.intersection(bbox_geom) for g in reader.geometries() if g.intersects(bbox_geom)]
    if not polys:
        return np.zeros((h, w), dtype=bool)
    land = unary_union(polys)

    def lon_lat_to_px(lon, lat):
        px = int((lon - bbox_coords[0]) / (bbox_coords[2] - bbox_coords[0]) * w)
        py = int((bbox_coords[3] - lat) / (bbox_coords[3] - bbox_coords[1]) * h)
        return px, py

    mask_img = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(mask_img)

    def _draw(geom):
        if geom.geom_type == 'Polygon':
            pts = [lon_lat_to_px(x, y) for x, y in geom.exterior.coords]
            if len(pts) >= 3:
                draw.polygon(pts, fill=255)
        elif geom.geom_type == 'MultiPolygon':
            for part in geom.geoms:
                _draw(part)

    _draw(land)
    mask = np.array(mask_img) > 0
    mask = ndimage.binary_dilation(mask, iterations=dilation)
    return mask


def detect_ships(arr, land_mask):
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
    return ships, threshold


def arr_to_image(arr, land_mask):
    a = np.clip(arr, 0, 1)
    a = np.power(a, 0.55)
    gray = (a * 255).astype(np.uint8)
    rgb = np.stack([gray, gray, gray], axis=2)
    rgb[land_mask, 0] = (rgb[land_mask, 0] * 0.3).astype(np.uint8)
    rgb[land_mask, 1] = (rgb[land_mask, 1] * 0.3).astype(np.uint8)
    rgb[land_mask, 2] = np.clip(rgb[land_mask, 2] * 0.3 + 50, 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, 'RGB')


def draw_ships(img, ships):
    draw = ImageDraw.Draw(img)
    for s in ships:
        color = CLASS_COLORS.get(s['class'], (255, 255, 255))
        pad = 3
        draw.rectangle(
            [s['x0'] - pad, s['y0'] - pad, s['x1'] + pad, s['y1'] + pad],
            outline=color, width=2
        )
    return img


import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--date', type=str, default=None, help='Specific date YYYY-MM-DD')
args, _ = parser.parse_known_args()

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
today = datetime.utcnow()
if args.date:
    date_from = args.date
    date_to   = args.date
else:
    date_from = (today - timedelta(days=14)).strftime('%Y-%m-%d')
    date_to   = today.strftime('%Y-%m-%d')
print(f"SAR date range: {date_from} → {date_to}")

for zone_id, zone in ZONES.items():
    print(f"\n--- {zone['label']} ---")
    bbox = BBox(bbox=zone['bbox'], crs=CRS.WGS84)
    size = bbox_to_dimensions(bbox, resolution=RESOLUTION)
    size = (min(size[0], 2000), min(size[1], 2000))
    print(f"  Size: {size[0]}×{size[1]}px")

    # Find latest SAR pass via catalog
    catalog = SentinelHubCatalog(config=config)
    results = list(catalog.search(
        collection=S1_CDSE,
        bbox=bbox,
        time=(date_from, date_to),
    ))
    if not results:
        print("  No SAR pass found in date range")
        continue
    # Use most recent
    results.sort(key=lambda r: r['properties']['datetime'], reverse=True)
    best = results[0]
    acq_date = best['properties']['datetime'][:10]
    print(f"  Latest SAR pass: {acq_date}")

    req = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(acq_date, acq_date),
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox, size=size, config=config,
    )

    data = req.get_data()
    if not data or data[0] is None:
        print("  No data returned")
        continue

    arr = np.array(data[0])
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    print(f"  Array shape: {arr.shape}, range: [{arr.min():.3f}, {arr.max():.3f}]")

    h, w = arr.shape
    land_mask = build_land_mask(h, w, zone['bbox'], zone['dilation'])
    ships, thr = detect_ships(arr, land_mask)
    print(f"  Threshold: {thr:.3f} | Ships detected: {len(ships)}")
    for s in ships:
        print(f"    {s['class']:12s}  {s['length_m']}m")

    img = arr_to_image(arr, land_mask)
    img = draw_ships(img, ships)

    out = os.path.join(OUT_DIR, f"dardanelles_sar_{zone_id}.png")
    img.save(out)
    print(f"  Saved: {out}")

print("\nDone.")
