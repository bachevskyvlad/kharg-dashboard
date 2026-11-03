"""
quick_snap.py — download one SAR scene per year (2019,2020,2021,2024),
crop to the 3 berth corridors from ТЗ-7.3, save PNG.
No background, no detection — raw scene_dB only.

Usage: python quick_snap.py
"""
import os, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder,
)

config = SHConfig()
config.sh_client_id     = os.environ["CLIENT_ID"]
config.sh_client_secret = os.environ["CLIENT_SECRET"]
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

S1 = DataCollection.SENTINEL1_IW.define_from(
    "s1_snap", service_url="https://sh.dataspace.copernicus.eu"
)

AOI  = BBox([50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)
RES  = 10
SZ   = bbox_to_dimensions(AOI, resolution=RES)

EVAL = """
//VERSION=3
function setup(){return{input:["VV"],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[Math.max(s.VV,1e-10)];}
"""

# 3 corridor crops: (lon_min, lat_min, lon_max, lat_max) + name
CROPS = [
    ("sea_island",     50.274, 29.212, 50.300, 29.246),
    ("east_t_head",    50.325, 29.213, 50.350, 29.248),
    ("southeast_pier", 50.327, 29.205, 50.348, 29.228),
]

# One scene per year — take the first available scene in Jan-Jun
YEARS = [2019, 2020, 2021, 2024]

OUT = Path(__file__).parent.parent / "diagnostics" / "berth_v3" / "snaps"
OUT.mkdir(parents=True, exist_ok=True)

catalog = SentinelHubCatalog(config=config)

for year in YEARS:
    print(f"\n--- {year} ---")
    results = list(catalog.search(
        S1, bbox=AOI,
        time=(f"{year}-01-01T00:00:00Z", f"{year}-06-30T23:59:59Z"),
        fields={"include": ["id", "properties.datetime",
                            "properties.sat:relative_orbit",
                            "properties.sat:orbit_state"]},
    ))
    if not results:
        print(f"  no scene found, skip")
        continue
    sc = results[0]
    dt_str = sc["properties"]["datetime"]
    orbit  = sc["properties"].get("sat:relative_orbit", "?")

    sc = results[0]
    dt_str = sc["properties"]["datetime"]
    orbit  = sc["properties"].get("sat:relative_orbit", "?")
    print(f"  scene={sc['id']}  orbit={orbit}  dt={dt_str}")

    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    t0 = (dt - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t1 = (dt + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    req = SentinelHubRequest(
        evalscript=EVAL,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1,
            time_interval=(t0, t1),
            mosaicking_order=MosaickingOrder.MOST_RECENT,
            other_args={"dataFilter": {"polarization": "DV"}},
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=AOI, size=SZ, config=config,
    )
    data = req.get_data()
    if not data or data[0] is None:
        print("  fetch failed")
        continue

    raw = np.array(data[0], dtype=np.float32)
    lin = raw[:,:,0] if raw.ndim == 3 else raw
    eps = 1e-10
    db  = 10 * np.log10(np.where(lin < eps, eps, lin))

    # one figure per date: 3 columns (one per corridor)
    fig, axes = plt.subplots(1, 3, figsize=(14, 6))
    for ax, (name, lon0, lat0, lon1, lat1) in zip(axes, CROPS):
        # pixel bounds
        W, H = SZ
        c0 = int((lon0 - AOI.min_x) / (AOI.max_x - AOI.min_x) * W)
        c1 = int((lon1 - AOI.min_x) / (AOI.max_x - AOI.min_x) * W)
        r0 = int((AOI.max_y - lat1) / (AOI.max_y - AOI.min_y) * H)
        r1 = int((AOI.max_y - lat0) / (AOI.max_y - AOI.min_y) * H)
        c0 = max(0, c0); c1 = min(W, c1)
        r0 = max(0, r0); r1 = min(H, r1)
        crop = db[r0:r1, c0:c1]
        im = ax.imshow(crop, cmap="gray", vmin=-25, vmax=5,
                       origin="upper", aspect="equal")
        plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
        ax.set_title(name, fontsize=9)
        ax.axis("off")

    scene_date = dt_str[:10]
    fig.suptitle(f"{scene_date}  orbit={orbit}  ({sc['properties'].get('sat:orbit_state','')})",
                 fontsize=12)
    plt.tight_layout()
    fname = OUT / f"{year}_{scene_date}_o{orbit}_snaps.png"
    plt.savefig(fname, dpi=140, bbox_inches="tight")
    plt.close()
    print(f"  saved: {fname.name}")

print("\nDone.")
