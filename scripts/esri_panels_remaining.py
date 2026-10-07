"""
esri_panels_remaining.py — ESRI z=19 crops for the 19 remaining al_faw tanks
(af_7, af_11 stuck + all 17 live tanks). The 5 originally cropped tanks
(af_0/2/3/6/9) are skipped.

Output: diagnostics/al_faw/panels/{tank_id}_esri_crop.png
"""
import math, io, os, time
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont

ALREADY_DONE = {"af_0","af_2","af_3","af_6","af_9"}  # already in water_crops/

TANKS = {
    "af_1":  {"lat": 29.9750, "lon": 48.4460, "r_fit": 36.8, "stuck": False},
    "af_4":  {"lat": 29.9730, "lon": 48.4490, "r_fit": 37.0, "stuck": False},
    "af_5":  {"lat": 29.9720, "lon": 48.4510, "r_fit": 35.9, "stuck": False},
    "af_7":  {"lat": 29.9710, "lon": 48.4480, "r_fit": 36.3, "stuck": True},
    "af_8":  {"lat": 29.9740, "lon": 48.4450, "r_fit": 36.4, "stuck": False},
    "af_10": {"lat": 29.9760, "lon": 48.4470, "r_fit": 36.6, "stuck": False},
    "af_11": {"lat": 29.9700, "lon": 48.4520, "r_fit": 34.4, "stuck": True},
    "af_12": {"lat": 29.9680, "lon": 48.4500, "r_fit": 34.2, "stuck": False},
    "af_13": {"lat": 29.9690, "lon": 48.4430, "r_fit": 35.2, "stuck": False},
    "af_14": {"lat": 29.9770, "lon": 48.4440, "r_fit": 33.8, "stuck": False},
    "af_15": {"lat": 29.9760, "lon": 48.4530, "r_fit": 37.2, "stuck": False},
    "af_16": {"lat": 29.9780, "lon": 48.4460, "r_fit": 36.8, "stuck": False},
    "af_17": {"lat": 29.9720, "lon": 48.4420, "r_fit": 34.9, "stuck": False},
    "af_18": {"lat": 29.9700, "lon": 48.4410, "r_fit": 34.7, "stuck": False},
    "af_19": {"lat": 29.9760, "lon": 48.4510, "r_fit": 36.9, "stuck": False},
    "af_20": {"lat": 29.9680, "lon": 48.4460, "r_fit": 35.9, "stuck": False},
    "af_21": {"lat": 29.9740, "lon": 48.4420, "r_fit": 36.7, "stuck": False},
    "af_22": {"lat": 29.9790, "lon": 48.4480, "r_fit": 35.5, "stuck": False},
    "af_23": {"lat": 29.9800, "lon": 48.4500, "r_fit": 35.7, "stuck": False},
}

ZOOM       = 19
CROP_DIAMS = 4
TILE_SIZE  = 256
ESRI_URL   = "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw" / "panels"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def deg2tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_r) + 1/math.cos(lat_r)) / math.pi) / 2 * n)
    return x, y

def tile2deg_nw(x, y, z):
    n = 2 ** z
    lon = x / n * 360 - 180
    lat_r = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lat_r), lon

def meters_per_pixel(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)

def fetch_tile(z, x, y, session):
    url = ESRI_URL.format(z=z, x=x, y=y)
    for attempt in range(3):
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200:
                return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            time.sleep(1)
    return Image.new("RGB", (TILE_SIZE, TILE_SIZE), (40,40,40))

def make_crop(tank_id, lat, lon, r_fit, stuck, session):
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
            t = fetch_tile(ZOOM, tx_c + dx, ty_c + dy, session)
            canvas.paste(t, ((dx + tiles_n) * TILE_SIZE, (dy + tiles_n) * TILE_SIZE))
    cx = tiles_n * TILE_SIZE + cx_in
    cy = tiles_n * TILE_SIZE + cy_in
    crop = canvas.crop((cx - half, cy - half, cx + half, cy + half))
    draw = ImageDraw.Draw(crop)
    lw = max(2, int(r_px * 0.06))
    color = (255, 80, 80) if stuck else (255, 220, 0)  # red ring for stuck tanks
    draw.ellipse([half-r_px, half-r_px, half+r_px, half+r_px], outline=color, width=lw)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    tag = " [STUCK]" if stuck else ""
    draw.text((10, 10), f"{tank_id}{tag}  r={r_fit}m", fill=color, font=font)
    return crop


def main():
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 OilRadar-diagnostic"
    ok, err = 0, 0
    for tid, info in sorted(TANKS.items()):
        print(f"  {tid} … ", end="", flush=True)
        out = OUT_DIR / f"{tid}_panel.png"
        if out.exists():
            print("skip (exists)")
            continue
        try:
            img = make_crop(tid, info["lat"], info["lon"], info["r_fit"], info["stuck"], session)
            img.save(out)
            print(f"saved ({img.size[0]}×{img.size[1]})")
            ok += 1
        except Exception as e:
            print(f"ERROR: {e}")
            err += 1
    print(f"\nDone: {ok} saved, {err} errors")


if __name__ == "__main__":
    main()
