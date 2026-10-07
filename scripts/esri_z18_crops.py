"""
esri_z18_crops.py — ESRI z=18 mosaic crops for al_faw suspect tanks.
Wider context window (4×diameter at z=18) to show site environment.
"""
import math, io, time
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont

TANKS = {
    "af_9":  {"lat": 29.96964506640903,  "lon": 48.45042448972437, "r_fit": 35.1},
    "af_2":  {"lat": 29.973482237683964, "lon": 48.44170682926327, "r_fit": 37.0},
    "af_0":  {"lat": 29.976037712391882, "lon": 48.44195552300743, "r_fit": 37.3},
    "af_3":  {"lat": 29.97506237979022,  "lon": 48.443256604287775,"r_fit": 36.6},
    "af_6":  {"lat": 29.971043737749962, "lon": 48.44490239092673, "r_fit": 36.2},
}

ZOOM       = 18
CROP_DIAMS = 6   # wider at z=18 to show site context
TILE_SIZE  = 256
ESRI_URL   = "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw" / "z18_crops"
OUT_DIR.mkdir(parents=True, exist_ok=True)


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


def make_crop(tank_id, lat, lon, r_fit, session):
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
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
    draw.text((10, 10), f"{tank_id}  r={r_fit}m  z=18", fill=(255, 220, 0), font=font)
    return crop


def main():
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 OilRadar-diagnostic"
    for tid, info in TANKS.items():
        print(f"  {tid} … ", end="", flush=True)
        try:
            img = make_crop(tid, info["lat"], info["lon"], info["r_fit"], session)
            out = OUT_DIR / f"{tid}_z18.png"
            img.save(out)
            print(f"saved ({img.size[0]}×{img.size[1]}px)")
        except Exception as e:
            print(f"ERROR: {e}")
    print("Done.")


if __name__ == "__main__":
    main()
