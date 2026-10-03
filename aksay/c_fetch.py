"""
aksay/c_fetch.py — rev4 Part C (network, GitHub Actions): every S2 L2A date in September 2017 over the fetch bbox.
Per date: SCL cloud/snow fraction in bbox (no tile-CC filter) + 10 m B02 B03 B04 B08 B11 SCL mask on the
same fetch grid, cropped to the frozen display crop → aksay/data/c2017/<date>.npz. Output aksay/data/c2017/index.json
"""
import os, json
from io import BytesIO
import numpy as np, tifffile
import fetch as F                      # reuse auth / catalog / process (same bbox and grid)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "c2017"); os.makedirs(OUT, exist_ok=True)
g = json.load(open(os.path.join(HERE, "aksay_geometry.json")))
LON0, LAT0, LON1, LAT1 = F.FETCH_BBOX
x0 = int(round((g["crop"][0] - LON0) / (LON1 - LON0) * F.W10)); x1 = int(round((g["crop"][2] - LON0) / (LON1 - LON0) * F.W10))
y0 = int(round((LAT1 - g["crop"][3]) / (LAT1 - LAT0) * F.H10)); y1 = int(round((LAT1 - g["crop"][1]) / (LAT1 - LAT0) * F.H10))

F.auth()
cat = F.catalog("sentinel-2-l2a", "2017-09-01", "2017-09-30", 101)
idx = {"crop_px_fetchgrid": [x0, y0, x1, y1], "dates": []}
for d, cc in sorted(cat.items()):
    s = F.score("sentinel-2-l2a", d)
    row = {"date": d, "tile_cc": cc, **(s or {})}
    raw = F.process("sentinel-2-l2a", d, F.EVAL_RAW["sentinel-2-l2a"], F.W10, F.H10)
    if raw is not None:
        a = tifffile.imread(BytesIO(raw))[y0:y1, x0:x1]
        np.savez_compressed(os.path.join(OUT, f"{d}.npz"), b02=a[..., 0], b03=a[..., 1], b04=a[..., 2],
                            b08=a[..., 3], b11=a[..., 4], scl=a[..., 5].astype(np.uint8), mask=a[..., 6].astype(np.uint8))
        row["saved"] = True
    idx["dates"].append(row); print(row)
json.dump(idx, open(os.path.join(OUT, "index.json"), "w"), indent=1)
