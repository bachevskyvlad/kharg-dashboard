"""
aksay/h_fetch.py — rev6 Part H (network, GitHub Actions): Landsat Collection-2 Level-2 frames over Ak-Sai.
Source: Microsoft Planetary Computer STAC (collection landsat-c2-l2 = USGS C2 L2 archive), anonymous SAS signing.
Per year 1990–2026: scenes Aug 1 – Sep 30 (widened to Jul 15 – Oct 10 if none usable), scene cloud < 40 %.
Each candidate scored inside the fetch bbox from QA_PIXEL (cloud = dilated cloud | cirrus | cloud | shadow; snow bit;
fill = no data, incl. L7 SLC-off gaps). Valid fraction is checked inside the frozen tongue polygon (≥ 0.90).
Pick: cloud ≤ min + 0.5 pp (floor 1 %), then least snow, then latest — same rule as S2.
Output on a 30 m grid = the S2 fetch bbox at 247 × 296 px: aksay/data/landsat/<year>.npz (+ candidates.json, quicklooks).
"""
import os, sys, json, math
import numpy as np, requests
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.transform import from_bounds
from rasterio.enums import Resampling
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "landsat"); os.makedirs(os.path.join(OUT, "quicklooks"), exist_ok=True)
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/landsat-c2-l2"
BBOX = [74.490, 42.480, 74.580, 42.560]; W30, H30 = 247, 296
YEARS = range(int(os.environ.get("Y0", 1990)), int(os.environ.get("Y1", 2026)) + 1)
BANDS = ["blue", "green", "red", "nir08", "swir16", "qa_pixel"]
os.environ.update({"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".TIF,.tif",
                   "GDAL_HTTP_MAX_RETRY": "4", "GDAL_HTTP_RETRY_DELAY": "2"})
TP = None

def poly30():
    tg = json.load(open(os.path.join(HERE, "aksay_tongue_polygon.json")))
    m = Image.new("L", (W30, H30), 0)
    ImageDraw.Draw(m).polygon([((lo - BBOX[0]) / (BBOX[2] - BBOX[0]) * W30, (BBOX[3] - la) / (BBOX[3] - BBOX[1]) * H30)
                               for lo, la in tg["polygon"]], fill=1)
    return np.array(m).astype(bool)

_tok = {"t": None}
def token():
    if _tok["t"] is None: _tok["t"] = requests.get(SAS, timeout=60).json()["token"]
    return _tok["t"]

def search(t0, t1):
    body = {"collections": ["landsat-c2-l2"], "bbox": BBOX, "datetime": f"{t0}T00:00:00Z/{t1}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": 40}}, "limit": 100}
    r = requests.post(STAC, json=body, timeout=120); r.raise_for_status()
    return r.json()["features"]

def read(item, key):
    href = item["assets"][key]["href"] + "?" + token()
    with rasterio.open(href) as src, WarpedVRT(src, crs="EPSG:4326", transform=from_bounds(*BBOX, W30, H30),
                                               width=W30, height=H30, resampling=Resampling.nearest) as v:
        return v.read(1)

def score(item):
    qa = read(item, "qa_pixel").astype(np.uint16)
    fill = (qa & 1) > 0
    cloud = ((qa >> 1) & 1 | (qa >> 2) & 1 | (qa >> 3) & 1 | (qa >> 4) & 1).astype(bool)
    snow = ((qa >> 5) & 1).astype(bool)
    valid = ~fill
    if valid.mean() < 0.5 or valid[TP].mean() < 0.90: return {"valid_bbox": float(valid.mean()), "valid_poly": float(valid[TP].mean())}
    return {"valid_bbox": round(float(valid.mean()), 3), "valid_poly": round(float(valid[TP].mean()), 3),
            "cloud": float(cloud[valid].mean()), "cloud_poly": float(cloud[TP & valid].mean()), "snow": float(snow[valid].mean())}

def main():
    global TP
    TP = poly30()
    summary = {"bbox": BBOX, "size": [W30, H30], "source": "Planetary Computer landsat-c2-l2", "years": {}}
    for y in YEARS:
        rows, window = [], None
        for wname, t0, t1 in (("aug-sep", f"{y}-08-01", f"{y}-09-30"), ("jul15-oct10", f"{y}-07-15", f"{y}-10-10")):
            try: feats = search(t0, t1)
            except Exception as e: print(y, "search error", e); feats = []
            seen = set(); rows = []
            for f in sorted(feats, key=lambda f: f["properties"].get("eo:cloud_cover", 100))[:25]:
                key = (f["properties"]["datetime"][:10], f["properties"]["platform"])
                if key in seen: continue
                seen.add(key)
                try: s = score(f)
                except Exception as e: print("  score error", f["id"], e); continue
                rows.append({"id": f["id"], "date": key[0], "platform": key[1], "scene_cc": f["properties"].get("eo:cloud_cover"), **s, "_item": f})
                print(f"  {y} {wname} {key[0]} {key[1]} " + (f"cloud={s['cloud']*100:.1f}% poly={s['cloud_poly']*100:.1f}% snow={s['snow']*100:.1f}% valid_poly={s['valid_poly']}" if "cloud" in s else f"invalid {s}"), flush=True)
            ok = [r for r in rows if "cloud" in r]
            if ok and min(r["cloud"] for r in ok) <= 0.05: window = wname; break
        ok = [r for r in rows if "cloud" in r]
        if not ok: summary["years"][str(y)] = None; print(y, "no usable scene"); continue
        cmin = min(r["cloud"] for r in ok)
        pool = sorted([r for r in ok if r["cloud"] <= max(0.01, cmin + 0.005)],
                      key=lambda r: (round(r["snow"], 3), -int(r["date"].replace("-", ""))))
        best = pool[0]; it = best.pop("_item")
        arr = {k: read(it, k) for k in BANDS}
        np.savez_compressed(os.path.join(OUT, f"{y}.npz"), **{k: v.astype(np.uint16) for k, v in arr.items()},
                            platform=best["platform"], date=best["date"])
        sr = lambda b: np.clip(arr[b] * 0.0000275 - 0.2, 0, 1)
        rgb = np.stack([sr("red"), sr("green"), sr("blue")], -1)
        Image.fromarray((np.clip(rgb * 2.2, 0, 1) ** 0.8 * 255).astype(np.uint8)).save(os.path.join(OUT, "quicklooks", f"{y}_{best['date']}_{best['platform']}.jpg"))
        for r in ok: r.pop("_item", None)
        summary["years"][str(y)] = {"window": window, "pick": best, "candidates": ok}
        print(f"==> {y}: {best['date']} {best['platform']} cloud {best['cloud']*100:.2f}% snow {best['snow']*100:.1f}%", flush=True)
        json.dump(summary, open(os.path.join(OUT, "candidates.json"), "w"), indent=1, default=str)
    json.dump(summary, open(os.path.join(OUT, "candidates.json"), "w"), indent=1, default=str)

if __name__ == "__main__":
    main()
