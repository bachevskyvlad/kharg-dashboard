"""
hormuz_ships.py — SAR ship detection in the Strait of Hormuz.

Focused on the navigable channel between Qeshm Island and Musandam (Oman).
Water mask excludes land and coastal clutter.
"""

import os, sys
import numpy as np
from datetime import datetime, timedelta
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from scipy.ndimage import label, uniform_filter

SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
SCENE_INDEX      = int(os.environ.get("SCENE_INDEX", "0"))

from sentinelhub import (
    SentinelHubRequest, SentinelHubCatalog, MimeType,
    BBox, CRS, SHConfig, DataCollection,
)

cfg = SHConfig()
cfg.sh_client_id     = SH_CLIENT_ID
cfg.sh_client_secret = SH_CLIENT_SECRET
cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"
cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

S1_CDSE = DataCollection.SENTINEL1.define_from(
    "S1_CDSE",
    service_url="https://sh.dataspace.copernicus.eu",
)

# ── Strait of Hormuz — channel between Qeshm south coast and Musandam (Oman)
BBOX = BBox([56.0, 26.15, 57.1, 26.75], crs=CRS.WGS84)
RESOLUTION = 40  # m/px

lat_mid = (BBOX.min_y + BBOX.max_y) / 2
width_m  = (BBOX.max_x - BBOX.min_x) * 111320 * np.cos(np.deg2rad(lat_mid))
height_m = (BBOX.max_y - BBOX.min_y) * 111320
IMG_W = min(int(width_m  / RESOLUTION), 2400)
IMG_H = min(int(height_m / RESOLUTION), 2400)
print(f"Image size: {IMG_W} x {IMG_H} px  ({RESOLUTION}m/px)")

# ── Find S1 scenes ──────────────────────────────────────────────
end_dt   = datetime.utcnow()
start_dt = end_dt - timedelta(days=60)

catalog = SentinelHubCatalog(config=cfg)
scenes = list(catalog.search(
    S1_CDSE, bbox=BBOX,
    time=(start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")),
    fields={"include": ["properties.datetime"]},
))
scenes.sort(key=lambda s: s["properties"]["datetime"], reverse=True)
# deduplicate by date
seen = set()
uniq = []
for s in scenes:
    d = s["properties"]["datetime"][:10]
    if d not in seen:
        seen.add(d)
        uniq.append(s)
scenes = uniq
print(f"Available scenes ({len(scenes)}): {[s['properties']['datetime'][:10] for s in scenes[:8]]}")

if not scenes:
    print("No scenes found"); sys.exit(1)
if SCENE_INDEX >= len(scenes):
    print(f"SCENE_INDEX={SCENE_INDEX} out of range ({len(scenes)} scenes)"); sys.exit(1)

latest_dt  = scenes[SCENE_INDEX]["properties"]["datetime"]
scene_date = latest_dt[:10]
print(f"Scene [{SCENE_INDEX}]: {latest_dt}")

# ── Download VV backscatter ────────────────────────────────────────────────────────────────────────────────────
EVALSCRIPT = """
//VERSION=3
function setup() {
  return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) { return [s.VV]; }
"""

request = SentinelHubRequest(
    evalscript=EVALSCRIPT,
    input_data=[SentinelHubRequest.input_data(
        data_collection=S1_CDSE,
        time_interval=(scene_date, (
            datetime.strptime(scene_date, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")),
        other_args={"processing": {
            "orthorectify": True,
            "backCoeff": "GAMMA0_TERRAIN",
            "demInstance": "COPERNICUS_30",
        }},
    )],
    responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
    bbox=BBOX,
    size=(IMG_W, IMG_H),
    config=cfg,
)

data = request.get_data()
if not data or data[0] is None:
    print("No data returned"); sys.exit(1)

img = data[0].squeeze().astype(np.float32)
print(f"Image shape: {img.shape}, min={img.min():.4f}, max={img.max():.4f}")

# ── Convert to dB ───────────────────────────────────────────────────────────────────────────────────────
img_db = 10 * np.log10(np.clip(img, 1e-10, None))

# ── Valid mask + CFAR ───────────────────────────────────────────────────────────────────────────────────────────────
valid_mask = np.isfinite(img_db) & (img > 0)

WINDOW_SIZE = 15
SIGMA       = 4
MIN_SHIP_PX = 3
MAX_SHIP_PX = 150

local_mean  = uniform_filter(img_db, size=WINDOW_SIZE, mode='reflect')
local_mean2 = uniform_filter(img_db**2, size=WINDOW_SIZE, mode='reflect')
local_std   = np.sqrt(np.maximum(local_mean2 - local_mean**2, 0))

# Water mask: local background < -8 dB = open water, excludes land/coast
water_mask = local_mean < -8.0

# Anchorage exclusion: Khasab bay (SW corner) — lon<56.35°E && lat<26.45°N
lon_range_deg = BBOX.max_x - BBOX.min_x
lat_range_deg = BBOX.max_y - BBOX.min_y
excl_col = int((56.35 - BBOX.min_x) / lon_range_deg * IMG_W)
excl_row = int((BBOX.max_y - 26.45) / lat_range_deg * IMG_H)
anchorage_mask = np.ones(img_db.shape, dtype=bool)
anchorage_mask[excl_row:, :excl_col] = False  # exclude bottom-left bay

threshold  = local_mean + SIGMA * local_std
detections = (img_db > threshold) & valid_mask & water_mask & anchorage_mask
print(f"Raw detections: {detections.sum()} px")

# ── Cluster ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
struct = np.ones((3, 3), dtype=int)
labeled, n_clusters = label(detections, structure=struct)

ship_boxes  = []
ship_pixels = []
for i in range(1, n_clusters + 1):
    mask_i = labeled == i
    sz = mask_i.sum()
    if MIN_SHIP_PX <= sz <= MAX_SHIP_PX:
        rows, cols = np.where(mask_i)
        ship_boxes.append((cols.min(), rows.min(), cols.max(), rows.max()))
        ship_pixels.append(int(sz))

n_ships = len(ship_boxes)
print(f"Detections: {n_clusters} clusters → {n_ships} ships")
print(f"Ship sizes (px): {sorted(ship_pixels, reverse=True)}")

# ── Render ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(1, 1, figsize=(12, 6), facecolor="#0d1117")
ax.set_facecolor("#0d1117")

vmin, vmax = np.percentile(img_db[valid_mask], [2, 98])
ax.imshow(img_db, cmap="gray", vmin=vmin, vmax=vmax, origin="upper", aspect="auto")

for (x0, y0, x1, y1) in ship_boxes:
    w = max(x1 - x0 + 1, 3)
    h = max(y1 - y0 + 1, 3)
    pad = 5
    rect = mpatches.Rectangle(
        (x0 - pad, y0 - pad), w + 2*pad, h + 2*pad,
        linewidth=1.5, edgecolor="#00e5ff", facecolor="none", alpha=0.9,
    )
    ax.add_patch(rect)

lon_range = BBOX.max_x - BBOX.min_x
lat_range = BBOX.max_y - BBOX.min_y
xt = np.linspace(0, IMG_W, 7)
yt = np.linspace(0, IMG_H, 5)
xl = [f"{BBOX.min_x + x/IMG_W*lon_range:.2f}°E" for x in xt]
yl = [f"{BBOX.max_y - y/IMG_H*lat_range:.2f}°N" for y in yt]
ax.set_xticks(xt); ax.set_xticklabels(xl, color="#aaa", fontsize=8)
ax.set_yticks(yt); ax.set_yticklabels(yl, color="#aaa", fontsize=8)
ax.tick_params(length=3, color="#555")

ax.text(0.01, 0.04, "OMAN / Musandam", transform=ax.transAxes,
        color="#ffcc00", fontsize=9, fontweight="bold", alpha=0.9)
ax.text(0.01, 0.96, "IRAN / Qeshm", transform=ax.transAxes,
        color="#ffcc00", fontsize=9, fontweight="bold", alpha=0.9, va="top")

ax.set_title(
    f"Strait of Hormuz — Sentinel-1 VV  {scene_date}\n"
    f"Ships in channel: {n_ships}  (σ={SIGMA}, {RESOLUTION}m/px, water-masked CFAR)",
    color="white", fontsize=12, pad=10,
)

plt.tight_layout()

OUT = "/tmp/hormuz_ships.jpg"
plt.savefig(OUT, dpi=100, bbox_inches="tight", facecolor="#0d1117",
            pil_kwargs={"quality": 85})
print(f"\nSaved: {OUT}")

import base64
with open(OUT, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
print("IMG_B64_START")
for i in range(0, len(b64), 76):
    print(b64[i:i+76])
print("IMG_B64_END")

print("\n" + "="*50)
print(f"STRAIT OF HORMUZ SHIP COUNT SUMMARY")
print(f"  Scene date   : {scene_date}")
print(f"  Scene index  : {SCENE_INDEX}")
print(f"  Resolution   : {RESOLUTION} m/px")
print(f"  CFAR         : σ={SIGMA}, window={WINDOW_SIZE}px, water<-8dB")
print(f"  Size filter  : {MIN_SHIP_PX}–{MAX_SHIP_PX} px")
print(f"  Ships found  : {n_ships}")
if ship_pixels:
    sizes_m = [round(np.sqrt(s) * RESOLUTION) for s in ship_pixels]
    print(f"  Largest      : ~{max(sizes_m)} m")
    print(f"  Median       : ~{int(np.median(sizes_m))} m")
print("="*50)
