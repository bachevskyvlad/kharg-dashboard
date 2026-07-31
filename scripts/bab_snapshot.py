"""
Fetch a single Sentinel-1 SAR snapshot of Bab-el-Mandeb and save as PNG.
Annotates detected ships with colored bounding boxes by class.

Usage:
  python bab_snapshot.py                    # latest available pass
  python bab_snapshot.py --date 2026-07-20  # specific week start
"""

import sys, os, argparse
from datetime import datetime, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    MimeType, bbox_to_dimensions, MosaickingOrder
)
from scipy import ndimage
from PIL import Image, ImageDraw, ImageFont

config = SHConfig()
config.sh_client_id     = "sh-4a504e67-3704-4c78-bfbc-e5b92cb1d5b4"
config.sh_client_secret = "U1W6BcqS4bFEIJy2u9xXdlBMZPxtQMBU"
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

BAB_CENTER = (44.0, 12.4)
OFFSET_LON, OFFSET_LAT = 0.48, 0.38

bbox = BBox(
    bbox=[
        BAB_CENTER[0] - OFFSET_LON,
        BAB_CENTER[1] - OFFSET_LAT,
        BAB_CENTER[0] + OFFSET_LON,
        BAB_CENTER[1] + OFFSET_LAT,
    ],
    crs=CRS.WGS84
)

RESOLUTION = 45
size = bbox_to_dimensions(bbox, resolution=RESOLUTION)

LAND_BOXES = [
    (43.38, 12.62, 43.48, 12.70),
    (43.05, 12.70, 44.55, 13.00),
    (43.05, 11.80, 43.55, 12.15),
]

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

# Colors per class (RGB)
CLASS_COLORS = {
    'vlcc':    (239, 68,  68),   # red
    'suezmax': (249, 115, 22),   # orange
    'aframax': (234, 179, 8),    # yellow
    'mr':      (34,  197, 94),   # green
}

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')


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


def classify_ship(length_m):
    if length_m >= 290:   return 'vlcc'
    if length_m >= 260:   return 'suezmax'
    if length_m >= 220:   return 'aframax'
    if length_m >= 100:   return 'mr'
    return None


def detect_ships(arr):
    h, w = arr.shape
    land_mask = np.zeros((h, w), dtype=bool)
    for (lon0, lat0, lon1, lat1) in LAND_BOXES:
        ix0, iy1 = lon_lat_to_px(lon0, lat0)
        ix1, iy0 = lon_lat_to_px(lon1, lat1)
        land_mask[max(0, iy0):min(h, iy1+1), max(0, ix0):min(w, ix1+1)] = True

    arr_m = arr.copy()
    arr_m[~np.isfinite(arr_m)] = 0
    arr_m[land_mask] = 0

    threshold = adaptive_threshold(arr_m)
    labeled, num = ndimage.label(arr_m > threshold)

    ships = []
    for i in range(1, num + 1):
        comp = (labeled == i)
        area = comp.sum()
        if area < 3 or area > 400:
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
    """Convert float32 SAR array to grayscale RGB PIL image."""
    a = np.clip(arr, 0, 1)
    # Gamma stretch to bring out detail
    a = np.power(a, 0.6)
    gray = (a * 255).astype(np.uint8)
    rgb = np.stack([gray, gray, gray], axis=2)
    # Tint land slightly blue-grey
    rgb[land_mask, 0] = (rgb[land_mask, 0] * 0.4).astype(np.uint8)
    rgb[land_mask, 1] = (rgb[land_mask, 1] * 0.4).astype(np.uint8)
    rgb[land_mask, 2] = np.clip(rgb[land_mask, 2] * 0.4 + 40, 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, 'RGB')


def annotate_image(img, ships, date_str, threshold):
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 11)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 9)
    except Exception:
        font = font_sm = ImageFont.load_default()

    pad = 3
    for s in ships:
        color = CLASS_COLORS[s['class']]
        x0, y0, x1, y1 = s['x0']-pad, s['y0']-pad, s['x1']+pad, s['y1']+pad
        draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
        label = f"{s['class'].upper()} {s['length_m']}m"
        draw.text((x0, y0-13), label, fill=color, font=font_sm)

    # Legend
    legend_x, legend_y = 10, 10
    for cls, color in CLASS_COLORS.items():
        draw.rectangle([legend_x, legend_y, legend_x+12, legend_y+12], fill=color)
        counts = sum(1 for s in ships if s['class'] == cls)
        draw.text((legend_x+16, legend_y), f"{cls.upper()}: {counts}", fill=(220, 220, 220), font=font_sm)
        legend_y += 18

    # Date + total
    total = len(ships)
    info = f"{date_str}  |  {total} tankers detected  |  thr={threshold:.3f}  |  45m/px  |  Sentinel-1 IW"
    h = img.height
    draw.rectangle([0, h-20, img.width, h], fill=(0, 0, 0, 180))
    draw.text((8, h-16), info, fill=(150, 150, 150), font=font_sm)

    return img


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, help='Week start YYYY-MM-DD (default: latest)')
    args = parser.parse_args()

    if args.date:
        date_from = datetime.strptime(args.date, '%Y-%m-%d')
    else:
        date_from = datetime.utcnow() - timedelta(days=14)
        date_from = date_from - timedelta(days=date_from.weekday())

    date_to = date_from + timedelta(days=6)
    date_str = date_from.strftime('%Y-%m-%d')
    print(f"Fetching {date_str} – {date_to.strftime('%Y-%m-%d')} …")

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
    data = request.get_data()
    raw = np.array(data[0])
    if raw.ndim == 3 and raw.shape[2] >= 1:
        arr = raw[:, :, 0]
    elif raw.ndim == 2:
        arr = raw
    else:
        arr = raw.reshape(raw.shape[-2], raw.shape[-1])

    print(f"Image: {arr.shape}  max={arr.max():.3f}  mean={arr.mean():.3f}")

    ships, threshold, land_mask = detect_ships(arr)
    print(f"Detected {len(ships)} ships: " +
          ", ".join(f"{s['class']}({s['length_m']}m)" for s in ships))

    img = arr_to_image(arr, land_mask)
    img = annotate_image(img, ships, date_str, threshold)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"bab_snapshot_{date_str}.png")
    img.save(out_path)
    print(f"Saved: {out_path}")


if __name__ == '__main__':
    main()
