"""
bosphorus_ships.py — SAR ship detection in the Bosphorus strait.

Steps:
  1. Download most recent Sentinel-1 VV image (last 30 days, descending)
  2. Mask land with NDWI proxy: low VV on water vs high on land,
     or use a simple fixed water polygon of the strait channel
  3. CFAR-lite detector: for each pixel, compare to local neighbourhood mean.
     Ship candidates = pixels > local_mean + threshold_sigma * local_std
  4. Cluster adjacent pixels into objects, filter by size
  5. Render annotated PNG + summary stats
"""

import os, sys, json
import numpy as np
from datetime import datetime, timedelta
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from scipy.ndimage import label, uniform_filter, generic_filter

# ── Credentials (same pattern as verify scripts) ──────────────────────────────
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"

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

# ── Ormuz strait bbox ─────────────────────────────────────────────────────────
# Main shipping lane of the Strait of Hormuz — narrowest navigable channel
BBOX = BBox([56.15, 26.35, 56.75, 26.75], crs=CRS.WGS84)
RESOLUTION = 25  # metres/pixel  (SH max 2500px per side)

width_m  = (BBOX.max_x - BBOX.min_x) * 111320 * np.cos(np.deg2rad(26.55))
height_m = (BBOX.max_y - BBOX.min_y) * 111320
IMG_W = int(width_m  / RESOLUTION)
IMG_H = int(height_m / RESOLUTION)
print(f"Image size: {IMG_W} x {IMG_H} px  ({RESOLUTION}m/px)  [Strait of Hormuz]")

# ── Find most recent S1 scene ──────────────────────────────────────────────────
end_dt   = datetime.utcnow()
start_dt = end_dt - timedelta(days=30)

catalog = SentinelHubCatalog(config=cfg)
scenes = list(catalog.search(
    S1_CDSE, bbox=BBOX,
    time=(start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")),
    filter="sat:orbit_state = 'descending'",
    fields={"include": ["properties.datetime"]},
))
scenes.sort(key=lambda s: s["properties"]["datetime"], reverse=True)

if not scenes:
    print("No scenes found — try ascending orbit or wider date range")
    sys.exit(1)

latest_dt = scenes[0]["properties"]["datetime"]
scene_date = latest_dt[:10]
print(f"Latest scene: {latest_dt}")

# ── Download VV backscatter ────────────────────────────────────────────────────
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

# ── Convert to dB ──────────────────────────────────────────────────────────────
img_db = 10 * np.log10(np.clip(img, 1e-10, None))

# ── Water mask ─────────────────────────────────────────────────────────────────
# Land has persistently high backscatter. Use local 200px median as land proxy:
# water pixels = those near the strait centre with low overall dB.
# Simple approach: pixels below the 65th percentile of the image are "water-likely"
# (land / buildings are brighter). The strait is dark, ships are bright peaks.
pct65 = np.percentile(img_db[np.isfinite(img_db)], 65)
water_mask = (img_db < pct65) | False  # start broad — refine below

# Better: use a neighbourhood std to find flat (water) vs textured (land) areas
local_std = generic_filter(img_db, np.std, size=15, mode='reflect')
flat_mask  = local_std < 3.5   # water is flat in dB; land is textured

# Combine: valid pixels are finite, not NaN/0, and look like water
valid_mask = np.isfinite(img_db) & (img > 0)
water_mask = flat_mask & valid_mask

print(f"Water mask coverage: {water_mask.mean()*100:.1f}% of image")

# ── CFAR-lite ship detector ────────────────────────────────────────────────────
# For each pixel compute local mean & std in a guard-cell window.
# Ship = pixel > local_mean + SIGMA * local_std, within water mask.
GUARD_SIZE  = 5   # pixels ignored around candidate (guard cell)
WINDOW_SIZE = 41  # neighbourhood window
SIGMA       = 10  # detection threshold (high to avoid false alarms on coast edges)
MIN_SHIP_PX = 3   # minimum cluster size to count as ship (at 10m: 3px = 30m object)
MAX_SHIP_PX = 150 # maximum (larger = likely noise / coast fragment)

# Compute local mean and std in the window (fast uniform filter proxy)
local_mean = uniform_filter(img_db, size=WINDOW_SIZE, mode='reflect')
local_mean2= uniform_filter(img_db**2, size=WINDOW_SIZE, mode='reflect')
local_std2 = np.sqrt(np.maximum(local_mean2 - local_mean**2, 0))

threshold = local_mean + SIGMA * local_std2
detections = (img_db > threshold) & water_mask & valid_mask

# ── Cluster detections into ship objects ──────────────────────────────────────
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
        ship_pixels.append(sz)

n_ships = len(ship_boxes)
print(f"\nDetections: {n_clusters} raw clusters → {n_ships} ships after size filter")
print(f"Ship sizes: {sorted(ship_pixels, reverse=True)[:20]} px")

# ── Render PNG ────────────────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(18, 10), facecolor="#0d1117")
for ax in axes:
    ax.set_facecolor("#0d1117")

# --- Left: raw VV dB image ---
ax1 = axes[0]
vmin, vmax = np.percentile(img_db[valid_mask], [2, 98])
im1 = ax1.imshow(img_db, cmap="gray", vmin=vmin, vmax=vmax, origin="upper",
                 aspect="auto")
ax1.set_title(f"Strait of Hormuz — Sentinel-1 VV  ({scene_date})",
              color="white", fontsize=13, pad=8)
ax1.axis("off")
plt.colorbar(im1, ax=ax1, fraction=0.025, pad=0.02).set_label("dB", color="white")
ax1.get_figure().axes[-1].yaxis.set_tick_params(color="white")
ax1.get_figure().axes[-1].tick_params(colors="white")

# --- Right: detections overlay ---
ax2 = axes[1]
ax2.imshow(img_db, cmap="gray", vmin=vmin, vmax=vmax, origin="upper", aspect="auto")

for (x0, y0, x1, y1) in ship_boxes:
    w = max(x1 - x0 + 1, 4)
    h = max(y1 - y0 + 1, 4)
    # expand box slightly for visibility
    pad = 3
    rect = mpatches.Rectangle(
        (x0 - pad, y0 - pad), w + 2*pad, h + 2*pad,
        linewidth=1.2, edgecolor="#00e5ff", facecolor="none", alpha=0.85,
    )
    ax2.add_patch(rect)

ax2.set_title(
    f"Ship detections: {n_ships}  (σ={SIGMA}, {MIN_SHIP_PX}–{MAX_SHIP_PX} px)",
    color="white", fontsize=13, pad=8,
)
ax2.axis("off")

# Lat/lon ticks approximation
lat_range = BBOX.max_y - BBOX.min_y
lon_range = BBOX.max_x - BBOX.min_x
for ax in axes:
    xt = np.linspace(0, IMG_W, 5)
    yt = np.linspace(0, IMG_H, 5)
    xl = [f"{BBOX.min_x + x/IMG_W*lon_range:.3f}°E" for x in xt]
    yl = [f"{BBOX.max_y - y/IMG_H*lat_range:.3f}°N" for y in yt]
    ax.set_xticks(xt); ax.set_xticklabels(xl, color="#aaa", fontsize=7, rotation=20)
    ax.set_yticks(yt); ax.set_yticklabels(yl, color="#aaa", fontsize=7)
    ax.tick_params(length=2, color="#555")

fig.suptitle(
    "Strait of Hormuz — SAR Ship Detection",
    color="white", fontsize=15, fontweight="bold", y=0.98,
)
plt.tight_layout(rect=[0, 0, 1, 0.97])

OUT = "/tmp/hormuz_ships.png"
plt.savefig(OUT, dpi=150, bbox_inches="tight", facecolor="#0d1117")
print(f"\nSaved: {OUT}")

# Print base64 for extraction from GHA logs
import base64
with open(OUT, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
chunk = 76
print("PNG_B64_START")
for i in range(0, len(b64), chunk):
    print(b64[i:i+chunk])
print("PNG_B64_END")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "="*50)
print(f"STRAIT OF HORMUZ SHIP COUNT SUMMARY")
print(f"  Scene date   : {scene_date}")
print(f"  Orbit        : descending")
print(f"  Resolution   : {RESOLUTION} m/px")
print(f"  Image size   : {IMG_W} × {IMG_H} px")
print(f"  Detector     : CFAR-lite σ={SIGMA}, window={WINDOW_SIZE}px")
print(f"  Ships found  : {n_ships}")
if ship_pixels:
    sizes_m = [round(np.sqrt(s) * RESOLUTION) for s in ship_pixels]
    print(f"  Largest ship : ~{max(sizes_m)} m equivalent diameter")
    print(f"  Median size  : ~{int(np.median(sizes_m))} m")
print("="*50)
