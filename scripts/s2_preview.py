"""
Fetch Sentinel-2 true-color image over Kharg anchorage zone.
Same AOI and credentials as ship_detect.py.
"""
import numpy as np
from datetime import datetime, timedelta
from PIL import Image
import os

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    MimeType, bbox_to_dimensions, MosaickingOrder
)

config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID")     or "sh-4a504e67-3704-4c78-bfbc-e5b92cb1d5b4"
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or "U1W6BcqS4bFEIJy2u9xXdlBMZPxtQMBU"
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

KHARG_CENTER = (50.32, 29.24)
bbox = BBox(
    bbox=[
        KHARG_CENTER[0] - 0.27,
        KHARG_CENTER[1] - 0.15,
        KHARG_CENTER[0] + 0.27,
        KHARG_CENTER[1] + 0.27,
    ],
    crs=CRS.WGS84
)
RESOLUTION = 25
size = bbox_to_dimensions(bbox, resolution=RESOLUTION)
print(f"Image size: {size[0]}x{size[1]} px @ {RESOLUTION}m/px")

evalscript = """
//VERSION=3
function setup() {
  return { input: ["B04","B03","B02"], output: { bands: 3, sampleType: "UINT8" } };
}
function evaluatePixel(s) {
  // True color, enhanced contrast
  return [
    Math.min(255, s.B04 * 3.5 * 255),
    Math.min(255, s.B03 * 3.5 * 255),
    Math.min(255, s.B02 * 3.5 * 255)
  ];
}
"""

S2_CDSE = DataCollection.SENTINEL2_L2A.define_from(
    "s2_cdse",
    service_url="https://sh.dataspace.copernicus.eu"
)

today = datetime.utcnow()
date_from = (today - timedelta(days=30)).strftime('%Y-%m-%d')
date_to   = today.strftime('%Y-%m-%d')
print(f"Fetching S2 true-color: {date_from} → {date_to}")

request = SentinelHubRequest(
    evalscript=evalscript,
    input_data=[
        SentinelHubRequest.input_data(
            data_collection=S2_CDSE,
            time_interval=(date_from, date_to),
            mosaicking_order=MosaickingOrder.LEAST_CC,
            other_args={"dataFilter": {"maxCloudCoverage": 20}},
        )
    ],
    responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
    bbox=bbox,
    size=size,
    config=config,
)

data = request.get_data()
if not data or data[0] is None:
    print("No data returned")
    exit(1)

arr = np.array(data[0])
print(f"Array shape: {arr.shape}, dtype: {arr.dtype}")

if arr.ndim == 3 and arr.shape[2] == 3:
    img = Image.fromarray(arr.astype(np.uint8), mode='RGB')
elif arr.ndim == 3 and arr.shape[0] == 3:
    img = Image.fromarray(arr.transpose(1,2,0).astype(np.uint8), mode='RGB')
else:
    print(f"Unexpected shape: {arr.shape}")
    exit(1)

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'kharg_s2_preview.png')
img.save(out)
print(f"Saved: {out}")
