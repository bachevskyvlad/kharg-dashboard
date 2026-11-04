"""
esri_water_crops.py — ESRI crops + r_fit ring for suspect water tanks.
Also computes approximate water_fraction per tank from S2 NDWI via Sentinel Hub.

Mode A (--esri-only): fetch ESRI tiles, draw ring, save PNG.
Mode B (--ndwi):      fetch S2 NDWI per tank bbox, compute water fraction.
Mode C (--all):       both A and B.

Default: --esri-only

Usage:
  python esri_water_crops.py [--esri-only|--ndwi|--all]
"""
import math, io, os, sys, time, json
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

TANKS = {
    "af_9":  {"lat": 29.96964506640903,  "lon": 48.45042448972437, "r_fit": 35.1},
    "af_2":  {"lat": 29.973482237683964, "lon": 48.44170682926327, "r_fit": 37.0},
    "af_0":  {"lat": 29.976037712391882, "lon": 48.44195552300743, "r_fit": 37.3},
    "af_3":  {"lat": 29.97506237979022,  "lon": 48.443256604287775,"r_fit": 36.6},
    "af_6":  {"lat": 29.971043737749962, "lon": 48.44490239092673, "r_fit": 36.2},
}

ZOOM       = 19
CROP_DIAMS = 4
TILE_SIZE  = 256
ESRI_URL   = "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw" / "water_crops"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ── ESRI helpers ──────────────────────────────────────────────────────────────

def deg2tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return x, y


def tile2deg_nw(x, y, z):
    n = 2 ** z
    lon = x / n * 360 - 180
    lat_r = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lat_r), lon


def meters_per_pixel(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)


def fetch_esri_tile(z, x, y, session):
    url = ESRI_URL.format(z=z, x=x, y=y)
    for attempt in range(3):
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200:
                return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            time.sleep(1)
    return Image.new("RGB", (TILE_SIZE, TILE_SIZE), (40, 40, 40))


def make_esri_crop(tank_id, lat, lon, r_fit, session):
    mpp = meters_per_pixel(lat, ZOOM)
    r_px = r_fit / mpp
    window_px = int(CROP_DIAMS * 2 * r_fit / mpp)

    tx_c, ty_c = deg2tile(lat, lon, ZOOM)
    lat_nw, lon_nw = tile2deg_nw(tx_c, ty_c, ZOOM)

    mpp_lat = 111320.0
    mpp_lon = 111320.0 * math.cos(math.radians(lat))
    cy_in = int((lat_nw - lat) * mpp_lat / mpp)
    cx_in = int((lon - lon_nw) * mpp_lon / mpp)

    half = window_px // 2
    tiles_n = math.ceil((half + TILE_SIZE) / TILE_SIZE)
    canvas_size = (2 * tiles_n + 1) * TILE_SIZE
    canvas = Image.new("RGB", (canvas_size, canvas_size))
    for dy in range(-tiles_n, tiles_n + 1):
        for dx in range(-tiles_n, tiles_n + 1):
            t = fetch_esri_tile(ZOOM, tx_c + dx, ty_c + dy, session)
            canvas.paste(t, ((dx + tiles_n) * TILE_SIZE, (dy + tiles_n) * TILE_SIZE))

    cx = tiles_n * TILE_SIZE + cx_in
    cy = tiles_n * TILE_SIZE + cy_in
    crop = canvas.crop((cx - half, cy - half, cx + half, cy + half))

    draw = ImageDraw.Draw(crop)
    lw = max(2, int(r_px * 0.06))
    draw.ellipse([half - r_px, half - r_px, half + r_px, half + r_px],
                 outline=(255, 220, 0), width=lw)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    draw.text((10, 10), f"{tank_id}  r={r_fit}m", fill=(255, 220, 0), font=font)
    return crop


# ── SH NDWI helpers ──────────────────────────────────────────────────────────

_token_cache = {}

def sh_token():
    if _token_cache.get("exp", 0) > time.time() + 60:
        return _token_cache["token"]
    r = requests.post(SH_TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": SH_CLIENT_ID, "client_secret": SH_CLIENT_SECRET})
    r.raise_for_status()
    d = r.json()
    _token_cache["token"] = d["access_token"]
    _token_cache["exp"]   = time.time() + d.get("expires_in", 3600)
    return d["access_token"]


def fetch_ndwi(lat, lon, r_m, token, date_str="2024-07-01"):
    """Fetch S2 NDWI over a tank bbox. Returns (water_fraction, ndwi_median)."""
    try:
        import tifffile
    except ImportError:
        raise ImportError("pip install tifffile")

    dr_lat = r_m / 111320.0
    dr_lon = r_m / (111320.0 * math.cos(math.radians(lat)))
    bbox   = [lon - dr_lon*2, lat - dr_lat*2, lon + dr_lon*2, lat + dr_lat*2]

    evalscript = """
//VERSION=3
function setup(){
  return{input:[{bands:["B03","B08"],units:"REFLECTANCE"}],
         output:{bands:1,sampleType:"FLOAT32"}};
}
function evaluatePixel(s){
  var ndwi = (s.B03 - s.B08) / (s.B03 + s.B08 + 1e-6);
  return [ndwi];
}"""

    body = {
        "input": {
            "bounds": {"bbox": bbox,
                       "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{"type": "sentinel-2-l2a", "dataFilter": {
                "timeRange": {"from": f"2024-06-01T00:00:00Z", "to": f"2024-09-01T23:59:59Z"},
                "maxCloudCoverage": 30,
            }}]
        },
        "output": {"width": 20, "height": 20,
                   "responses": [{"identifier": "default",
                                  "format": {"type": "image/tiff"}}]},
        "evalscript": evalscript
    }

    resp = requests.post(SH_PROCESS_URL, json=body,
                         headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if resp.status_code != 200 or len(resp.content) < 16:
        return None, None

    arr = tifffile.imread(io.BytesIO(resp.content)).flatten().astype(np.float32)
    valid = arr[np.isfinite(arr)]
    if len(valid) == 0:
        return None, None

    # NDWI > 0.3 = water
    water_frac = float((valid > 0.3).mean())
    ndwi_med   = float(np.median(valid))
    return water_frac, ndwi_med


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    do_esri = "--ndwi" not in sys.argv   # default on
    do_ndwi = "--ndwi" in sys.argv or "--all" in sys.argv
    if "--esri-only" in sys.argv:
        do_ndwi = False

    results = {}

    if do_esri:
        print("=== ESRI crops ===")
        session = requests.Session()
        session.headers["User-Agent"] = "Mozilla/5.0 OilRadar-diagnostic"
        for tid, info in TANKS.items():
            print(f"  {tid} … ", end="", flush=True)
            try:
                img = make_esri_crop(tid, info["lat"], info["lon"], info["r_fit"], session)
                out = OUT_DIR / f"{tid}_esri_crop.png"
                img.save(out)
                print(f"saved ({img.size[0]}×{img.size[1]}px)")
                results[tid] = {"esri_path": str(out)}
            except Exception as e:
                print(f"ERROR: {e}")

    if do_ndwi:
        print("\n=== S2 NDWI water fraction ===")
        token = sh_token()
        ndwi_results = {}
        for tid, info in TANKS.items():
            print(f"  {tid} … ", end="", flush=True)
            try:
                wf, nm = fetch_ndwi(info["lat"], info["lon"], info["r_fit"] * 1.5, token)
                if wf is not None:
                    print(f"water_frac={wf:.3f}  ndwi_median={nm:.3f}")
                    ndwi_results[tid] = {"water_frac": round(wf, 4), "ndwi_median": round(nm, 4)}
                else:
                    print("no valid S2 data")
            except Exception as e:
                print(f"ERROR: {e}")
        out_j = OUT_DIR / "water_fractions.json"
        with open(out_j, "w") as f:
            json.dump(ndwi_results, f, indent=2)
        print(f"\n  → {out_j}")

    print("\nDone.")


if __name__ == "__main__":
    main()
