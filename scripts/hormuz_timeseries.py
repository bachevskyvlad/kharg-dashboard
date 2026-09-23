"""
hormuz_timeseries.py - weekly ship counts in the Strait of Hormuz.
"""

import os, sys, csv
import numpy as np
from datetime import datetime, timedelta
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.ndimage import label, uniform_filter

SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
START_DATE       = os.environ.get("START_DATE", "2025-12-01")

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

BBOX       = BBox([56.0, 26.15, 57.1, 26.75], crs=CRS.WGS84)
RESOLUTION = 40

lat_mid  = (BBOX.min_y + BBOX.max_y) / 2
width_m  = (BBOX.max_x - BBOX.min_x) * 111320 * np.cos(np.deg2rad(lat_mid))
height_m = (BBOX.max_y - BBOX.min_y) * 111320
IMG_W = min(int(width_m  / RESOLUTION), 2400)
IMG_H = min(int(height_m / RESOLUTION), 2400)

WINDOW_SIZE = 15
SIGMA       = 4
MIN_SHIP_PX = 3
MAX_SHIP_PX = 150

lon_range_deg = BBOX.max_x - BBOX.min_x
lat_range_deg = BBOX.max_y - BBOX.min_y
excl_col = int((56.35 - BBOX.min_x) / lon_range_deg * IMG_W)
excl_row = int((BBOX.max_y - 26.45) / lat_range_deg * IMG_H)

EVALSCRIPT = """
//VERSION=3
function setup() {
  return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) { return [s.VV]; }
"""

end_dt   = datetime.utcnow()
start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")

print(f"Searching scenes {START_DATE} -> {end_dt.strftime('%Y-%m-%d')} ...")
catalog = SentinelHubCatalog(config=cfg)
scenes = list(catalog.search(
    S1_CDSE, bbox=BBOX,
    time=(START_DATE, end_dt.strftime("%Y-%m-%d")),
    fields={"include": ["properties.datetime"]},
))
scenes.sort(key=lambda s: s["properties"]["datetime"])

seen_weeks = {}
for s in scenes:
    dt = datetime.strptime(s["properties"]["datetime"][:10], "%Y-%m-%d")
    wk = dt.isocalendar()[:2]
    if wk not in seen_weeks:
        seen_weeks[wk] = s["properties"]["datetime"][:10]

weekly_dates = sorted(seen_weeks.values())
print(f"Weekly scenes: {len(weekly_dates)}")
print(weekly_dates)

def detect_ships(scene_date):
    next_date = (datetime.strptime(scene_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(scene_date, next_date),
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
    data = req.get_data()
    if not data or data[0] is None:
        return None

    img = data[0].squeeze().astype(np.float32)
    img_db = 10 * np.log10(np.clip(img, 1e-10, None))
    valid_mask = np.isfinite(img_db) & (img > 0)

    if valid_mask.sum() < 1000:
        return None

    local_mean  = uniform_filter(img_db, size=WINDOW_SIZE, mode='reflect')
    local_mean2 = uniform_filter(img_db**2, size=WINDOW_SIZE, mode='reflect')
    local_std   = np.sqrt(np.maximum(local_mean2 - local_mean**2, 0))

    water_mask = local_mean < -8.0

    anchorage_mask = np.ones(img_db.shape, dtype=bool)
    anchorage_mask[excl_row:, :excl_col] = False

    threshold  = local_mean + SIGMA * local_std
    detections = (img_db > threshold) & valid_mask & water_mask & anchorage_mask

    struct = np.ones((3, 3), dtype=int)
    labeled, n_clusters = label(detections, structure=struct)

    return sum(
        1 for i in range(1, n_clusters + 1)
        if MIN_SHIP_PX <= (labeled == i).sum() <= MAX_SHIP_PX
    )

results = []
for i, date in enumerate(weekly_dates):
    print(f"[{i+1}/{len(weekly_dates)}] {date} ...", end=" ", flush=True)
    try:
        n = detect_ships(date)
        print(str(n) if n is not None else "partial")
        results.append({"date": date, "ships": n, "status": "ok" if n is not None else "partial"})
    except Exception as e:
        print(f"ERROR: {e}")
        results.append({"date": date, "ships": None, "status": "error"})

CSV_OUT = "/tmp/hormuz_timeseries.csv"
with open(CSV_OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date", "ships", "status"])
    w.writeheader()
    w.writerows(results)
print(f"\nCSV saved: {CSV_OUT}")

print("\n" + "="*45)
print(f"{'Date':<12}  {'Ships':>6}  {'Note'}")
print("-"*45)
for r in results:
    note = "" if r["status"] == "ok" else f"({r['status']})"
    ships_str = str(r["ships"]) if r["ships"] is not None else "-"
    print(f"{r['date']:<12}  {ships_str:>6}  {note}")
print("="*45)

valid = [r["ships"] for r in results if r["ships"] is not None]
if valid:
    print(f"Mean: {np.mean(valid):.1f}  Min: {min(valid)}  Max: {max(valid)}  N={len(valid)}")

dates_ok = [r["date"] for r in results if r["ships"] is not None]
ships_ok = [r["ships"] for r in results if r["ships"] is not None]

if dates_ok:
    fig, ax = plt.subplots(figsize=(14, 4), facecolor="#0d1117")
    ax.set_facecolor("#0d1117")
    ax.plot(range(len(dates_ok)), ships_ok, color="#00e5ff", linewidth=1.5, marker="o", markersize=4)
    ax.fill_between(range(len(dates_ok)), ships_ok, alpha=0.15, color="#00e5ff")
    step = max(1, len(dates_ok) // 10)
    ax.set_xticks(range(0, len(dates_ok), step))
    ax.set_xticklabels([dates_ok[i] for i in range(0, len(dates_ok), step)],
                       rotation=30, ha="right", color="#aaa", fontsize=8)
    ax.set_ylabel("Ships detected", color="#aaa", fontsize=9)
    ax.tick_params(colors="#555")
    ax.spines[:].set_color("#333")
    ax.set_title(
        f"Strait of Hormuz - Weekly Ship Count  ({START_DATE} -> {dates_ok[-1]})\n"
        f"sigma={SIGMA}, {RESOLUTION}m/px, Khasab bay excluded",
        color="white", fontsize=11,
    )
    plt.tight_layout()
    CHART_OUT = "/tmp/hormuz_timeseries.jpg"
    plt.savefig(CHART_OUT, dpi=100, bbox_inches="tight", facecolor="#0d1117",
                pil_kwargs={"quality": 85})
    print(f"Chart saved: {CHART_OUT}")

    import base64
    with open(CHART_OUT, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    print("CHART_B64_START")
    for i in range(0, len(b64), 76):
        print(b64[i:i+76])
    print("CHART_B64_END")

    with open(CSV_OUT, "rb") as f:
        b64csv = base64.b64encode(f.read()).decode()
    print("CSV_B64_START")
    for i in range(0, len(b64csv), 76):
        print(b64csv[i:i+76])
    print("CSV_B64_END")