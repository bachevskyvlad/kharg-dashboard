"""
Fetch last 2 Sentinel-1 passes over Kharg anchorage zone (~20km radius),
detect ships, exclude known offshore platforms, classify by size.
"""

import sys
sys.path.insert(0, '/home/user/kharg-dashboard/scripts')

import numpy as np
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import os

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    MimeType, bbox_to_dimensions, MosaickingOrder
)

# ── Config ────────────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID")     or "sh-4a504e67-3704-4c78-bfbc-e5b92cb1d5b4"
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or "U1W6BcqS4bFEIJy2u9xXdlBMZPxtQMBU"
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

# ── AOI: 20km box around Kharg (center 29.24N, 50.32E) ───────────────────────
# ~0.18 degrees lat/lon ≈ 20km
KHARG_CENTER = (50.32, 29.24)
OFFSET = 0.18
bbox = BBox(
    bbox=[
        KHARG_CENTER[0] - OFFSET,  # west
        KHARG_CENTER[1] - OFFSET,  # south
        KHARG_CENTER[0] + OFFSET,  # east
        KHARG_CENTER[1] + OFFSET,  # north
    ],
    crs=CRS.WGS84
)

RESOLUTION = 10  # metres
size = bbox_to_dimensions(bbox, resolution=RESOLUTION)
print(f"Image size: {size[0]}x{size[1]} px @ {RESOLUTION}m/px")

# ── Known offshore platform locations near Kharg (blacklist) ─────────────────
# These are fixed installations that show up as bright SAR targets
# Coordinates: (lon, lat) — approximate, from public data
PLATFORMS = [
    (50.14, 29.38),  # Foroozan / Fereidoon cluster NW
    (50.10, 29.35),
    (50.08, 29.42),
    (50.05, 29.30),
    (50.18, 29.48),
    (50.22, 29.50),
    (50.28, 29.52),
    (50.35, 29.55),
    (50.40, 29.50),
    (50.45, 29.45),
    (50.15, 29.20),
    (50.12, 29.15),
]
PLATFORM_BUFFER_PX = 4  # pixels to mask around each platform at 10m/px ≈ 40m radius

# ── Evalscript: VV backscatter in dB ────────────────────────────────────────
evalscript = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  // Convert linear to dB
  var db = 10 * Math.log10(s.VV + 1e-10);
  // Normalise to 0-255: ships are very bright (>0dB), sea is dark (<-20dB)
  var norm = Math.max(0, Math.min(1, (db + 25) / 40));
  return [norm];
}
"""

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse",
    service_url="https://sh.dataspace.copernicus.eu"
)

def fetch_pass(date_from, date_to, label):
    print(f"\nFetching {label}: {date_from} → {date_to}")
    request = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[
            SentinelHubRequest.input_data(
                data_collection=S1_CDSE,
                time_interval=(date_from, date_to),
                mosaicking_order=MosaickingOrder.MOST_RECENT,
                other_args={"dataFilter": {"polarization": "DV"}},
            )
        ],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox,
        size=size,
        config=config,
    )
    data = request.get_data()
    if not data or data[0] is None:
        print(f"  No data for {label}")
        return None
    arr = data[0][:, :, 0]
    print(f"  Got array {arr.shape}, range {arr.min():.3f}–{arr.max():.3f}")
    return arr

def lon_lat_to_px(lon, lat, bbox, size):
    """Convert lon/lat to pixel coordinates in the image."""
    px = int((lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * size[0])
    py = int((bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * size[1])
    return px, py

def detect_ships(arr, bbox, size, threshold=0.80):
    """
    Detect bright spots = ships.
    threshold: normalised brightness (0-1). Ships typically >0.80.
    Returns list of (px, py, size_px) for each detection.
    """
    h, w = arr.shape
    # Build platform mask
    mask = np.zeros((h, w), dtype=bool)
    for lon, lat in PLATFORMS:
        px, py = lon_lat_to_px(lon, lat, bbox, size)
        if 0 <= px < w and 0 <= py < h:
            y0 = max(0, py - PLATFORM_BUFFER_PX)
            y1 = min(h, py + PLATFORM_BUFFER_PX)
            x0 = max(0, px - PLATFORM_BUFFER_PX)
            x1 = min(w, px + PLATFORM_BUFFER_PX)
            mask[y0:y1, x0:x1] = True

    # Suppress platforms
    arr_masked = arr.copy()
    arr_masked[mask] = 0

    # Binary threshold
    bright = arr_masked > threshold

    # Label connected components (simple flood fill via scipy)
    from scipy import ndimage
    labeled, num = ndimage.label(bright)
    ships = []
    for i in range(1, num + 1):
        component = (labeled == i)
        area = component.sum()
        if area < 3:  # noise
            continue
        ys, xs = np.where(component)
        cx, cy = int(xs.mean()), int(ys.mean())
        length_px = max(xs.max() - xs.min(), ys.max() - ys.min()) + 1
        length_m = length_px * RESOLUTION
        ships.append({'cx': cx, 'cy': cy, 'length_m': length_m, 'area': area})

    return ships

def classify_vessel(length_m):
    if length_m >= 290:   return 'VLCC', '#ef4444'
    elif length_m >= 260: return 'Suezmax', '#f97316'
    elif length_m >= 220: return 'Aframax', '#eab308'
    elif length_m >= 100: return 'MR/Panamax', '#22c55e'
    else:                  return 'Small', '#94a3b8'

def render_image(arr, ships, label, outpath):
    # Normalise to 0-255
    img_arr = (np.clip(arr, 0, 1) * 255).astype(np.uint8)
    img = Image.fromarray(img_arr, mode='L').convert('RGB')
    draw = ImageDraw.Draw(img)

    counts = {}
    for s in ships:
        cls, color = classify_vessel(s['length_m'])
        counts[cls] = counts.get(cls, 0) + 1
        r = max(5, s['area'] // 2)
        cx, cy = s['cx'], s['cy']
        # Draw box
        draw.rectangle([cx-r, cy-r, cx+r, cy+r], outline=color, width=2)
        draw.text((cx+r+2, cy-6), f"{cls}\n{s['length_m']:.0f}m", fill=color)

    # Mark Kharg island center
    ix, iy = lon_lat_to_px(KHARG_CENTER[0], KHARG_CENTER[1], bbox, size)
    draw.ellipse([ix-8, iy-8, ix+8, iy+8], outline='#3b82f6', width=3)
    draw.text((ix+10, iy-8), 'KHARG', fill='#3b82f6')

    # Legend
    draw.rectangle([5, 5, 220, 20 + len(counts)*18], fill=(0,0,0,180))
    draw.text((10, 8), label, fill='white')
    for i, (cls, n) in enumerate(counts.items()):
        _, color = classify_vessel({'VLCC':300,'Suezmax':270,'Aframax':230,'MR/Panamax':150,'Small':80}[cls])
        draw.text((10, 26 + i*18), f"  {cls}: {n}", fill=color)

    img.save(outpath)
    print(f"  Saved: {outpath}")
    print(f"  Ships detected: {len(ships)}")
    for cls, n in counts.items():
        print(f"    {cls}: {n}")
    return counts

# ── Main ─────────────────────────────────────────────────────────────────────
out_dir = os.path.dirname(os.path.abspath(__file__))

today = datetime.utcnow()

# Pass 1: last ~6 days
arr1 = fetch_pass(
    (today - timedelta(days=7)).strftime('%Y-%m-%d'),
    today.strftime('%Y-%m-%d'),
    'Pass 1 (last 7d)'
)

# Pass 2: 7-14 days ago
arr2 = fetch_pass(
    (today - timedelta(days=14)).strftime('%Y-%m-%d'),
    (today - timedelta(days=7)).strftime('%Y-%m-%d'),
    'Pass 2 (7-14d ago)'
)

for arr, label, fname in [
    (arr1, f"Pass 1 — {(today-timedelta(days=7)).strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')}", 'kharg_ships_pass1.png'),
    (arr2, f"Pass 2 — {(today-timedelta(days=14)).strftime('%Y-%m-%d')} to {(today-timedelta(days=7)).strftime('%Y-%m-%d')}", 'kharg_ships_pass2.png'),
]:
    if arr is not None:
        ships = detect_ships(arr, bbox, size)
        render_image(arr, ships, label, os.path.join(out_dir, fname))
