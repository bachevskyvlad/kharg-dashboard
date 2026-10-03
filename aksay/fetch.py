"""
aksay/fetch.py — Stage 0 (network part) of the Ak-Sai glacier timelapse.
Runs in GitHub Actions (CDSE creds from Secrets). R&D, independent of OilRadar.

For each year 2015..2026:
  1. Catalog S2 L2A over FETCH_BBOX, Aug 1 – Sep 30, tile cloud_cover < 15 %.
     No usable scene → widen to Jul 15 – Oct 10 (flagged). No L2A at all → L1C (flagged).
  2. Score every date inside the bbox (not the tile): cloud/shadow fraction from SCL
     (L1C: CLM), snow/ice fraction (SCL 11 / NDSI>0.4) as a fresh-snow proxy.
  3. Pick: valid ≥ 98 %, cloud ≤ min+0.5 pp (floor 1 %), then least snow, then latest.
  4. Download the pick at 10 m: B02 B03 B04 B08 B11 (×1e4, UINT16) + SCL + dataMask
     → aksay/data/raw/<year>.npz ; 20 m quicklooks of top-4 candidates for eyeballing.
Outputs: aksay/data/candidates.json
"""
import os, sys, json, requests
from io import BytesIO
import numpy as np
import tifffile
from PIL import Image

CID  = os.environ.get("CLIENT_ID") or ""
CSEC = os.environ.get("CLIENT_SECRET") or ""
if not (CID and CSEC):
    sys.exit("FATAL: CLIENT_ID / CLIENT_SECRET missing")
TOKEN_URL   = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
CATALOG_URL = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"

# Generous fetch window around Ak-Sai (tongue + accumulation); final crop is done locally.
FETCH_BBOX = [74.490, 42.480, 74.580, 42.560]          # lon_min, lat_min, lon_max, lat_max
W10, H10 = 740, 888                                     # ≈10 m/px at 42.52°N
YEARS = list(range(2015, 2027))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(HERE, "data")
CRS  = {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}

S = requests.Session()

def auth():
    r = S.post(TOKEN_URL, data={"grant_type": "client_credentials",
               "client_id": CID, "client_secret": CSEC}, timeout=60)
    r.raise_for_status()
    S.headers["Authorization"] = "Bearer " + r.json()["access_token"]

def catalog(coll, t0, t1, maxcc):
    body = {"collections": [coll], "bbox": FETCH_BBOX, "datetime": f"{t0}T00:00:00Z/{t1}T23:59:59Z",
            "limit": 100, "filter": f"eo:cloud_cover < {maxcc}", "filter-lang": "cql2-text"}
    out = {}
    while True:
        r = S.post(CATALOG_URL, json=body, timeout=60); r.raise_for_status(); j = r.json()
        for f in j["features"]:
            d = f["properties"]["datetime"][:10]
            out[d] = min(out.get(d, 100.0), f["properties"].get("eo:cloud_cover", 100.0))
        nxt = j.get("context", {}).get("next")
        if not nxt: return out
        body["next"] = nxt

def process(coll, date, evalscript, w, h, fmt="image/tiff"):
    p = {"input": {"bounds": {"bbox": FETCH_BBOX, "properties": CRS},
                   "data": [{"type": coll, "dataFilter": {
                       "timeRange": {"from": f"{date}T00:00:00Z", "to": f"{date}T23:59:59Z"},
                       "mosaickingOrder": "leastCC"}}]},
         "output": {"width": w, "height": h,
                    "responses": [{"identifier": "default", "format": {"type": fmt}}]},
         "evalscript": evalscript}
    for attempt in range(3):
        r = S.post(PROCESS_URL, json=p, timeout=180)
        if r.status_code == 401: auth(); continue
        if r.status_code == 200: return r.content
        print(f"    {coll} {date}: HTTP {r.status_code} {r.text[:200]}")
        if r.status_code < 500 and r.status_code != 429: return None
    return None

EVAL_STATS = {
 "sentinel-2-l2a": """//VERSION=3
function setup(){return{input:["SCL","dataMask"],output:{bands:3,sampleType:"UINT8"}};}
function evaluatePixel(s){var c=(s.SCL==3||s.SCL==8||s.SCL==9||s.SCL==10)?1:0;
 return [c,(s.SCL==11)?1:0,s.dataMask];}""",
 "sentinel-2-l1c": """//VERSION=3
function setup(){return{input:["CLM","B03","B11","dataMask"],output:{bands:3,sampleType:"UINT8"}};}
function evaluatePixel(s){var n=(s.B03-s.B11)/(s.B03+s.B11+1e-6);
 return [s.CLM==1?1:0,n>0.4?1:0,s.dataMask];}""",
}
EVAL_RAW = {
 "sentinel-2-l2a": """//VERSION=3
function setup(){return{input:["B02","B03","B04","B08","B11","SCL","dataMask"],output:{bands:7,sampleType:"UINT16"}};}
function evaluatePixel(s){return [s.B02*1e4,s.B03*1e4,s.B04*1e4,s.B08*1e4,s.B11*1e4,s.SCL,s.dataMask];}""",
 "sentinel-2-l1c": """//VERSION=3
function setup(){return{input:["B02","B03","B04","B08","B11","CLM","dataMask"],output:{bands:7,sampleType:"UINT16"}};}
function evaluatePixel(s){return [s.B02*1e4,s.B03*1e4,s.B04*1e4,s.B08*1e4,s.B11*1e4,s.CLM==1?255:0,s.dataMask];}""",
}
EVAL_QL = """//VERSION=3
function setup(){return{input:["B02","B03","B04"],output:{bands:3}};}
function evaluatePixel(s){return [2.2*s.B04,2.2*s.B03,2.2*s.B02];}"""

def score(coll, date):
    raw = process(coll, date, EVAL_STATS[coll], W10 // 4, H10 // 4)
    if raw is None: return None
    a = tifffile.imread(BytesIO(raw))
    valid = a[..., 2] > 0
    vf = float(valid.mean())
    if vf < 0.98: return {"valid": vf}
    return {"valid": vf, "cloud": float(a[..., 0][valid].mean()), "snow": float(a[..., 1][valid].mean())}

def year_candidates(year):
    windows = [("aug-sep", f"{year}-08-01", f"{year}-09-30"),
               ("jul15-oct10", f"{year}-07-15", f"{year}-10-10")]
    for coll in ("sentinel-2-l2a", "sentinel-2-l1c"):
        for wname, t0, t1 in windows:
            cat = catalog(coll, t0, t1, 15)
            if not cat: continue
            rows = []
            for d, cc in sorted(cat.items()):
                s = score(coll, d)
                if s is None: continue
                rows.append({"date": d, "tile_cc": cc, **s})
                print(f"  {coll[-3:]} {wname:11s} {d} tileCC={cc:5.1f} " +
                      (f"valid={s['valid']:.2f}" if "cloud" not in s else
                       f"cloud={s['cloud']*100:5.2f}% snow={s['snow']*100:5.1f}%"))
            ok = [r for r in rows if "cloud" in r]
            if ok and min(r["cloud"] for r in ok) <= 0.02:
                return coll, wname, ok
    return None, None, []

def pick(rows):
    cmin = min(r["cloud"] for r in rows)
    pool = [r for r in rows if r["cloud"] <= max(0.01, cmin + 0.005)]
    pool.sort(key=lambda r: (round(r["snow"], 3), -int(r["date"].replace("-", ""))))
    return pool[0], pool

def main():
    os.makedirs(os.path.join(OUT, "raw"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "quicklooks"), exist_ok=True)
    auth()
    summary = {"fetch_bbox": FETCH_BBOX, "size": [W10, H10], "years": {}}
    for y in YEARS:
        print(f"== {y}")
        coll, wname, rows = year_candidates(y)
        if not rows:
            print("  no usable scene"); summary["years"][str(y)] = None; continue
        best, pool = pick(rows)
        ranked = sorted(pool, key=lambda r: (round(r["snow"], 3), -int(r["date"].replace("-", ""))))
        for r in ranked[:4]:
            ql = process(coll, r["date"], EVAL_QL, W10 // 2, H10 // 2, "image/jpeg")
            if ql: open(os.path.join(OUT, "quicklooks", f"{y}_{r['date']}.jpg"), "wb").write(ql)
        raw = process(coll, best["date"], EVAL_RAW[coll], W10, H10)
        a = tifffile.imread(BytesIO(raw))
        np.savez_compressed(os.path.join(OUT, "raw", f"{y}.npz"),
                            b02=a[..., 0], b03=a[..., 1], b04=a[..., 2], b08=a[..., 3], b11=a[..., 4],
                            scl=a[..., 5].astype(np.uint8), mask=a[..., 6].astype(np.uint8))
        summary["years"][str(y)] = {"collection": coll, "window": wname, "pick": best, "candidates": rows}
        print(f"  -> {best['date']} cloud={best['cloud']*100:.2f}% snow={best['snow']*100:.1f}% ({coll}, {wname})")
    json.dump(summary, open(os.path.join(OUT, "candidates.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
