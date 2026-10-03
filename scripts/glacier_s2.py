"""
glacier_s2.py — clearest summer Sentinel-2 true-colour snapshot of a glacier.

Usage: python scripts/glacier_s2.py [YEAR ...]   (default: 2025)
For each year: searches Jul 1 – Sep 30 L2A scenes over the AOI, scores every
date by local cloud/shadow fraction from SCL (not tile-level cloud cover),
renders the cleanest one to diagnostics/glacier/glacier_<YEAR>.png.
"""
import os, sys, json, requests
from io import BytesIO
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"
SH_CATALOG_URL   = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"

LAT, LON = 42.52350680025239, 74.53423795606166
DLAT, DLON = 0.030, 0.040                     # ≈ 6.7 × 6.6 km
BBOX = [LON - DLON, LAT - DLAT, LON + DLON, LAT + DLAT]
WIDTH, HEIGHT = 660, 667                      # ≈ 10 m/px
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "diagnostics", "glacier")
YEARS = [int(y) for y in sys.argv[1:]] or [2025]

CRS = {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}

EVAL_RGB = """
//VERSION=3
function setup(){return{input:["B04","B03","B02","dataMask"],output:{bands:3}};}
function evaluatePixel(s){
  // gentle gamma so bright ice doesn't clip and shadowed rock stays readable
  function t(v){return Math.pow(Math.min(1,Math.max(0,v*1.6)),0.75);}
  return [t(s.B04),t(s.B03),t(s.B02)];
}
"""
# SCL classes: 3 shadow, 8/9 cloud, 10 cirrus → 1 = bad
EVAL_SCL = """
//VERSION=3
function setup(){return{input:["SCL","dataMask"],output:{bands:2,sampleType:"UINT8"}};}
function evaluatePixel(s){
  var bad=(s.SCL==3||s.SCL==8||s.SCL==9||s.SCL==10)?1:0;
  return [bad,s.dataMask];
}
"""

def token():
    r = requests.post(SH_TOKEN_URL, data={"grant_type": "client_credentials",
        "client_id": SH_CLIENT_ID, "client_secret": SH_CLIENT_SECRET}, timeout=60)
    r.raise_for_status()
    return r.json()["access_token"]

def catalog(tok, year):
    body = {"collections": ["sentinel-2-l2a"], "bbox": BBOX,
            "datetime": f"{year}-07-01T00:00:00Z/{year}-09-30T23:59:59Z",
            "limit": 100, "filter": "eo:cloud_cover < 40", "filter-lang": "cql2-text"}
    dates, nxt = {}, None
    while True:
        if nxt is not None: body["next"] = nxt
        r = requests.post(SH_CATALOG_URL, json=body,
                          headers={"Authorization": f"Bearer {tok}"}, timeout=60)
        r.raise_for_status()
        j = r.json()
        for f in j["features"]:
            d = f["properties"]["datetime"][:10]
            cc = f["properties"].get("eo:cloud_cover", 100)
            dates[d] = min(cc, dates.get(d, 100))
        nxt = j.get("context", {}).get("next")
        if not nxt: break
    return sorted(dates.items(), key=lambda x: x[1])

def process(tok, date, evalscript, w, h, fmt="image/png"):
    payload = {
        "input": {"bounds": {"bbox": BBOX, "properties": CRS},
                  "data": [{"type": "sentinel-2-l2a", "dataFilter": {
                      "timeRange": {"from": f"{date}T00:00:00Z", "to": f"{date}T23:59:59Z"},
                      "mosaickingOrder": "leastCC"}}]},
        "output": {"width": w, "height": h,
                   "responses": [{"identifier": "default", "format": {"type": fmt}}]},
        "evalscript": evalscript,
    }
    r = requests.post(SH_PROCESS_URL, json=payload,
                      headers={"Authorization": f"Bearer {tok}"}, timeout=120)
    if r.status_code != 200:
        print(f"  {date}: HTTP {r.status_code} {r.text[:200]}")
        return None
    return Image.open(BytesIO(r.content))

def local_cloud(tok, date):
    im = process(tok, date, EVAL_SCL, 132, 133)
    if im is None: return None
    a = np.array(im)
    bad, valid = a[..., 0], a[..., 1] > 0
    if valid.sum() < 0.95 * valid.size: return None   # partial swath
    return float(bad[valid].mean())

def label(img, text):
    d = ImageDraw.Draw(img)
    W, H = img.size
    d.rectangle([(0, H - 36), (W, H)], fill=(0, 0, 0))
    try:
        f  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        fs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except Exception:
        f = fs = ImageFont.load_default()
    d.text((12, H - 31), text, fill=(255, 255, 255), font=f)
    d.text((W - 290, H - 26), f"Sentinel-2 L2A · {LAT:.4f}N {LON:.4f}E", fill=(170, 170, 170), font=fs)
    # target marker
    cx, cy = W // 2, H // 2
    d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], outline=(255, 60, 60), width=2)
    return img

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    tok = token()
    summary = {}
    for year in YEARS:
        cands = catalog(tok, year)
        print(f"{year}: {len(cands)} candidate dates (tile CC<40%)")
        scored = []
        for date, cc in cands[:15]:
            lc = local_cloud(tok, date)
            print(f"  {date}  tileCC={cc:5.1f}%  localBad={'n/a' if lc is None else f'{lc*100:.2f}%'}")
            if lc is not None: scored.append((lc, cc, date))
        if not scored:
            print(f"  {year}: nothing usable"); continue
        # late-season preferred on ties: least seasonal snow over the ice
        scored.sort(key=lambda x: (round(x[0], 3), x[1], -int(x[2].replace("-", ""))))
        lc, cc, date = scored[0]
        img = process(tok, date, EVAL_RGB, WIDTH, HEIGHT).convert("RGB")
        img = label(img, f"{date}   cloud/shadow in AOI: {lc*100:.1f}%")
        path = os.path.join(OUT_DIR, f"glacier_{year}.png")
        img.save(path, optimize=True)
        summary[year] = {"date": date, "local_cloud_shadow": lc, "tile_cc": cc,
                         "ranked": [{"date": d, "local": l, "tile_cc": c} for l, c, d in scored]}
        print(f"  → {path}  ({date}, {lc*100:.2f}%)")
    with open(os.path.join(OUT_DIR, "glacier_scenes.json"), "w") as fh:
        json.dump({"bbox": BBOX, "years": summary}, fh, indent=2)

if __name__ == "__main__":
    main()
