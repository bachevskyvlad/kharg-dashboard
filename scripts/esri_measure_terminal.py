"""
esri_measure_terminal.py — ESRI z18 16-ray measurement for any terminal.
Usage: python esri_measure_terminal.py --terminal <id>

Outputs:
  diagnostics/<terminal>/esri_clean/<prefix>_*.png
  diagnostics/<terminal>/ESRI_RADIUS_TABLE.md
  diagnostics/<terminal>/esri_raw.json
  diagnostics/<terminal>/esri_atlas.png   (4-diameter window, r_fit ring, Web-Mercator annotation)
"""
import argparse, math, io, time, json
from pathlib import Path
import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont

parser = argparse.ArgumentParser()
parser.add_argument("--terminal", required=True)
args = parser.parse_args()
TERMINAL = args.terminal

import sys
sys.path.insert(0, str(Path(__file__).parent))
from tank_registry import LOCATIONS

loc = next((l for l in LOCATIONS if l["id"] == TERMINAL), None)
if loc is None:
    print(f"Terminal '{TERMINAL}' not found in registry")
    sys.exit(1)

TANKS = loc["tanks"]
print(f"Terminal: {TERMINAL}  tanks: {len(TANKS)}")

ZOOM       = 18
CROP_DIAMS = 4       # 4-diameter crop window (ТЗ: окно 4 диаметра)
TILE_SIZE  = 256
N_RAYS     = 16      # 16-ray measurement (ТЗ)
ESRI_URL   = "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

OUT_DIR   = Path(__file__).parent.parent / "diagnostics" / TERMINAL
TABLE_DIR = OUT_DIR
CROP_DIR  = OUT_DIR / "esri_clean"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CROP_DIR.mkdir(parents=True, exist_ok=True)


def deg2tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lr = math.radians(lat)
    y = int((1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * n)
    return x, y

def tile2deg_nw(x, y, z):
    n = 2 ** z
    lon = x / n * 360 - 180
    lr = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lr), lon

def meters_per_pixel(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)

def fetch_tile(z, x, y, session):
    url = ESRI_URL.format(z=z, x=x, y=y)
    for _ in range(3):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            time.sleep(1.5)
    return Image.new("RGB", (TILE_SIZE, TILE_SIZE), (30, 30, 30))

def fetch_clean_crop(tank, session):
    lat, lon = tank["lat"], tank["lon"]
    r_reg    = tank["radius_m"]
    mpp      = meters_per_pixel(lat, ZOOM)
    half_px  = int(CROP_DIAMS * r_reg / mpp)
    window   = 2 * half_px

    tx_c, ty_c = deg2tile(lat, lon, ZOOM)
    lat_nw, lon_nw = tile2deg_nw(tx_c, ty_c, ZOOM)

    cy_in = int((lat_nw - lat) * 111320.0 / mpp)
    cx_in = int((lon - lon_nw) * 111320.0 * math.cos(math.radians(lat)) / mpp)

    tiles_n = math.ceil((half_px + TILE_SIZE) / TILE_SIZE)
    cs = (2 * tiles_n + 1) * TILE_SIZE
    canvas = Image.new("RGB", (cs, cs))
    for dy in range(-tiles_n, tiles_n + 1):
        for dx in range(-tiles_n, tiles_n + 1):
            t = fetch_tile(ZOOM, tx_c + dx, ty_c + dy, session)
            canvas.paste(t, ((dx + tiles_n) * TILE_SIZE, (dy + tiles_n) * TILE_SIZE))

    cx_abs = tiles_n * TILE_SIZE + cx_in
    cy_abs = tiles_n * TILE_SIZE + cy_in
    crop   = canvas.crop((cx_abs - half_px, cy_abs - half_px,
                          cx_abs + half_px, cy_abs + half_px))
    if crop.size != (window, window):
        crop = crop.resize((window, window))
    return crop, mpp

def gaussian_blur(arr, sigma=1.0):
    r = int(math.ceil(3 * sigma))
    x = np.arange(-r, r + 1, dtype=np.float32)
    k = np.exp(-x**2 / (2 * sigma**2)); k /= k.sum()
    tmp = np.apply_along_axis(lambda row: np.convolve(row, k, 'same'), 1, arr)
    return np.apply_along_axis(lambda col: np.convolve(col, k, 'same'), 0, tmp)

def circle_fit(xs, ys):
    A = np.column_stack([2*xs, 2*ys, np.ones(len(xs))])
    b = xs**2 + ys**2
    res, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = res[0], res[1]
    r = math.sqrt(max(0, res[2] + cx**2 + cy**2))
    return cx, cy, r

def measure(crop_arr, r_reg, mpp):
    gray    = crop_arr.astype(np.float32).mean(axis=2)
    blurred = gaussian_blur(gray, sigma=1.0)
    gy, gx  = np.gradient(blurred)
    gmag    = np.sqrt(gx**2 + gy**2)

    H, W = gray.shape
    cx_nom, cy_nom = W / 2.0, H / 2.0
    r_nom_px = r_reg / mpp

    edge_xs, edge_ys, r_readings = [], [], []
    for i in range(N_RAYS):
        ang   = 2 * math.pi * i / N_RAYS
        r_min = int(r_nom_px * 0.55)
        r_max = int(r_nom_px * 1.45)
        rs    = np.arange(r_min, r_max)
        xs    = cx_nom + rs * math.cos(ang)
        ys    = cy_nom - rs * math.sin(ang)
        valid = (xs >= 0) & (xs < W-1) & (ys >= 0) & (ys < H-1)
        if valid.sum() < 3:
            continue
        rs, xs, ys = rs[valid], xs[valid], ys[valid]
        xi, yi = xs.astype(int), ys.astype(int)
        dx_f, dy_f = xs - xi, ys - yi
        vals = (gmag[yi, xi]     * (1-dx_f)*(1-dy_f) +
                gmag[yi, xi+1]   *    dx_f *(1-dy_f) +
                gmag[yi+1, xi]   * (1-dx_f)*   dy_f  +
                gmag[yi+1, xi+1] *    dx_f *   dy_f)
        if vals.max() < 0.5:
            continue
        peak = int(np.argmax(vals))
        r_readings.append(rs[peak] * mpp)
        edge_xs.append(float(xs[peak]))
        edge_ys.append(float(ys[peak]))

    if len(r_readings) < 5:
        return None

    r_median_m = float(np.median(r_readings))
    ex, ey = np.array(edge_xs), np.array(edge_ys)
    try:
        fit_cx, fit_cy, fit_r_px = circle_fit(ex, ey)
        r_fit_m = fit_r_px * mpp
    except Exception:
        fit_cx, fit_cy = cx_nom, cy_nom
        r_fit_m = r_median_m

    dists  = np.sqrt((ex - fit_cx)**2 + (ey - fit_cy)**2)
    rms_m  = float(np.sqrt(np.mean((dists - fit_r_px)**2))) * mpp

    dx_px  = fit_cx - cx_nom
    dy_px  = fit_cy - cy_nom
    shift_m   = math.sqrt(dx_px**2 + dy_px**2) * mpp
    shift_e_m = dx_px * mpp
    shift_s_m = dy_px * mpp

    return {
        "r_fit_m":    round(r_fit_m, 1),
        "r_median_m": round(r_median_m, 1),
        "shift_m":    round(shift_m, 1),
        "shift_e_m":  round(shift_e_m, 1),
        "shift_s_m":  round(shift_s_m, 1),
        "rms_m":      round(rms_m, 1),
        "n_rays":     len(r_readings),
    }

def draw_annotated(crop_img, res, tid, r_reg):
    """Draw r_fit ring + label onto crop."""
    img = crop_img.copy()
    draw = ImageDraw.Draw(img)
    W, H = img.size
    if res is None:
        draw.text((4, 4), tid, fill=(255, 80, 80))
        return img

    mpp_approx = (r_reg * CROP_DIAMS * 2) / W   # rough mpp
    cx = W / 2 + res["shift_e_m"] / mpp_approx
    cy = H / 2 + res["shift_s_m"] / mpp_approx
    r_px = res["r_fit_m"] / mpp_approx

    # r_fit ring (cyan, 1px)
    draw.ellipse([(cx-r_px, cy-r_px), (cx+r_px, cy+r_px)], outline=(0, 220, 220), width=1)
    # label
    label = f"{tid}  r={res['r_fit_m']}m  rms={res['rms_m']}m"
    draw.text((4, 4), label, fill=(255, 255, 80))
    return img

def main():
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 OilRadar-diagnostic"

    rows = []
    annotated_crops = []

    for t in TANKS:
        tid   = t["id"]
        r_reg = t["radius_m"]
        print(f"  {tid} … ", end="", flush=True)
        try:
            crop_img, mpp = fetch_clean_crop(t, session)
        except Exception as e:
            print(f"FETCH FAIL: {e}")
            rows.append({"id": tid, "status": "FETCH_FAIL", "r_reg": r_reg})
            annotated_crops.append(Image.new("RGB", (128, 128), (50, 30, 30)))
            continue

        crop_arr = np.array(crop_img)
        crop_img.save(str(CROP_DIR / f"{tid}.png"))

        res = measure(crop_arr, r_reg, mpp)
        if res is None:
            print("MEASURE FAIL")
            rows.append({"id": tid, "status": "MEASURE_FAIL", "r_reg": r_reg})
            annotated_crops.append(draw_annotated(crop_img, None, tid, r_reg))
            continue

        new_lat = t["lat"] - res["shift_s_m"] / 111320.0
        new_lon = t["lon"] + res["shift_e_m"] / (111320.0 * math.cos(math.radians(t["lat"])))
        res.update({
            "id": tid, "r_reg": r_reg,
            "lat_reg": t["lat"], "lon_reg": t["lon"],
            "lat_fit": round(new_lat, 9),
            "lon_fit": round(new_lon, 9),
            "mpp": round(mpp, 4),
            "status": "OK"
        })
        rows.append(res)
        annotated_crops.append(draw_annotated(crop_img, res, tid, r_reg))
        print(f"r_fit={res['r_fit_m']}m  shift={res['shift_m']}m  rms={res['rms_m']}m  rays={res['n_rays']}")

    # JSON
    (TABLE_DIR / "esri_raw.json").write_text(json.dumps(rows, indent=2))

    # Atlas: grid of annotated crops
    if annotated_crops:
        cols = min(5, len(annotated_crops))
        rows_grid = math.ceil(len(annotated_crops) / cols)
        cell = annotated_crops[0].size[0]
        atlas = Image.new("RGB", (cols * cell, rows_grid * cell), (20, 20, 20))
        for idx, img in enumerate(annotated_crops):
            img_resized = img.resize((cell, cell))
            r_g, c_g = divmod(idx, cols)
            atlas.paste(img_resized, (c_g * cell, r_g * cell))
        atlas.save(str(TABLE_DIR / "esri_atlas.png"))
        print(f"Atlas → {TABLE_DIR}/esri_atlas.png")

    # Markdown
    mpp_ref = meters_per_pixel(rows[0]["lat_reg"] if rows and rows[0].get("lat_reg") else 25.0, ZOOM)
    md = [
        f"# {TERMINAL.upper()} ESRI z=18 — Radius & Center Measurement",
        "",
        f"Method: {N_RAYS}-ray gradient peak + least-squares circle fit on clean ESRI z=18 crops.",
        f"mpp ≈ {mpp_ref:.4f} m/px",
        "",
        "| Tank | r_reg | r_fit | Δr | shift_m | shift_E | shift_S | rms | rays |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r["status"] != "OK":
            md.append(f"| {r['id']} | {r['r_reg']} | {r['status']} | — | — | — | — | — | — |")
            continue
        dr = round(r["r_fit_m"] - r["r_reg"], 1)
        md.append(
            f"| {r['id']} | {r['r_reg']} | **{r['r_fit_m']}** | {dr:+.1f} "
            f"| {r['shift_m']} | {r['shift_e_m']:+.1f} | {r['shift_s_m']:+.1f} "
            f"| {r['rms_m']} | {r['n_rays']} |"
        )
    md += ["", "shift_E: +east, shift_S: +south (metres). rms = RMS residual."]
    (TABLE_DIR / "ESRI_RADIUS_TABLE.md").write_text("\n".join(md))
    print(f"Table → {TABLE_DIR}/ESRI_RADIUS_TABLE.md")
    print(f"JSON  → {TABLE_DIR}/esri_raw.json")

if __name__ == "__main__":
    main()
