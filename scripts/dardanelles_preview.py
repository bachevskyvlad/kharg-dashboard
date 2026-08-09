"""Fetch Sentinel-2 true-color snapshots for two Dardanelles anchorage zones."""
import numpy as np
from datetime import datetime, timedelta
from PIL import Image
import os

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    MimeType, bbox_to_dimensions, MosaickingOrder
)

config = SHConfig()
config.sh_client_id     = "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
config.sh_client_secret = "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

ZONES = {
    "aegean_entrance": [25.5, 39.75, 26.6, 40.35],   # Bozcaada / Çanakkale entrance
    "marmara_south":   [26.8, 40.45, 27.85, 40.85],  # Şarköy / Marmara anchorage
}

evalscript = """
//VERSION=3
function setup() {
  return { input: ["B04","B03","B02"], output: { bands: 3, sampleType: "UINT8" } };
}
function evaluatePixel(s) {
  return [
    Math.min(255, s.B04 * 4.0 * 255),
    Math.min(255, s.B03 * 4.0 * 255),
    Math.min(255, s.B02 * 4.0 * 255)
  ];
}
"""

S2_CDSE = DataCollection.SENTINEL2_L2A.define_from(
    "s2_cdse", service_url="https://sh.dataspace.copernicus.eu"
)

today = datetime.utcnow()
date_from = (today - timedelta(days=45)).strftime('%Y-%m-%d')
date_to   = today.strftime('%Y-%m-%d')
print(f"Date range: {date_from} → {date_to}")

out_dir = os.path.dirname(os.path.abspath(__file__))

for name, coords in ZONES.items():
    print(f"\nFetching {name} ...")
    bbox = BBox(bbox=coords, crs=CRS.WGS84)
    size = bbox_to_dimensions(bbox, resolution=50)
    size = (min(size[0], 2000), min(size[1], 2000))
    print(f"  Size: {size[0]}x{size[1]} px")

    req = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S2_CDSE,
            time_interval=(date_from, date_to),
            mosaicking_order=MosaickingOrder.LEAST_CC,
            other_args={"dataFilter": {"maxCloudCoverage": 30}},
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox, size=size, config=config,
    )

    data = req.get_data()
    if not data or data[0] is None:
        print(f"  No data for {name}")
        continue

    arr = np.array(data[0])
    print(f"  Array shape: {arr.shape}")

    if arr.ndim == 3 and arr.shape[2] == 3:
        img = Image.fromarray(arr.astype(np.uint8), mode='RGB')
    elif arr.ndim == 3 and arr.shape[0] == 3:
        img = Image.fromarray(arr.transpose(1,2,0).astype(np.uint8), mode='RGB')
    else:
        print(f"  Unexpected shape {arr.shape}")
        continue

    out = os.path.join(out_dir, f"dardanelles_{name}.png")
    img.save(out)
    print(f"  Saved: {out}")

print("\nDone.")
