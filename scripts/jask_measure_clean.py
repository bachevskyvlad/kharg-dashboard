"""
jask_measure_clean.py — fetch clean (no-overlay) ESRI z=18 crops for all 20 Jask tanks,
measure radius + center with 32-ray gradient peak + least-squares circle fit.

Outputs:
  diagnostics/jask/esri_clean/jk_*.png       raw tiles, no overlay
  diagnostics/jask/JASK_RADIUS_TABLE.md
  diagnostics/jask/jask_radius_raw.json
"""

import math, io, time, json
from pathlib import Path
import requests
import numpy as np
from PIL import Image

ZOOM       = 18
CROP_DIAMS = 5
TILE_SIZE  = 256
ESRI_URL   = "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
N_RAYS     = 32

TANKS = [
    {"id": "jk_0",  "lat": 25.854547959796683, "lon": 57.30425500256653, "r_reg": 35},
    {"id": "jk_1",  "lat": 25.854285897427793, "lon": 57.30285809909571, "r_reg": 35},
    {"id": "jk_2",  "lat": 25.853004240288325, "lon": 57.303153860416884,"r_reg": 35},
    {"id": "jk_3",  "lat": 25.853270400262147, "lon": 57.30455531406189, "r_reg": 35},
    {"id": "jk_4",  "lat": 25.861242540725943, "lon": 57.30140013397687, "r_reg": 35},
    {"id": "jk_5",  "lat": 25.859992238216282, "lon": 57.301668785557,   "r_reg": 35},
    {"id": "jk_6",  "lat": 25.860219021790332, "lon": 57.30306060943653, "r_reg": 35},
    {"id": "jk_7",  "lat": 25.861503877842335, "lon": 57.30277881775879, "r_reg": 35},
    {"id": "jk_8",  "lat": 25.853687716748603, "lon": 57.30698588912281, "r_reg": 35},
    {"id": "jk_9",  "lat": 25.853928243776394, "lon": 57.30841264827629, "r_reg": 35},
    {"id": "jk_10", "lat": 25.855236751552415, "lon": 57.30810298608718, "r_reg": 35},
    {"id": "jk_11", "lat": 25.854958644693784, "lon": 57.30673002892039, "r_reg": 35},
    {"id": "jk_12", "lat": 25.861916665340250, "lon": 57.305243449053705,"r_reg": 35},
    {"id": "jk_13", "lat": 25.862202369924490, "lon": 57.30664059485674, "r_reg": 35},
    {"id": "jk_14", "lat": 25.860673586711076, "lon": 57.305524427309486,"r_reg": 35},
    {"id": "jk_15", "lat": 25.860904481636084, "lon": 57.30691674803369, "r_reg": 35},
    {"id": "jk_16", "lat": 25.858441643613652, "lon": 57.30597802808436, "r_reg": 35},
    {"id": "jk_17", "lat": 25.858705470577710, "lon": 57.30737158935134, "r_reg": 35},
    {"id": "jk_18", "lat": 25.857166423062460, "lon": 57.306251980766476,"r_reg": 35},
    {"id": "jk_19", "lat": 25.857398072650852, "lon": 57.30766780088818, "r_reg": 35},
]

OUT_DIR   = Path(__file__).parent.parent / "diagnostics" / "jask" / "esri_clean"
TABLE_DIR = Path(__file__).parent.parent / "diagnostics" / "jask"
OUT_DIR.mkdir(parents=True, exist_ok=True)


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
    lat, lon, r_reg = tank["lat"], tank["lon"], tank["r_reg"]
    mpp = meters_per_pixel(lat, ZOOM)
    half_px = int(CROP_DIAMS * r_reg / mpp)
    window  = 2 * half_px

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
    crop = canvas.crop((cx_abs - half_px, cy_abs - half_px,
                        cx_abs + half_px, cy_abs + half_px))
    if crop.size != (window, window):
        crop = crop.resize((window, window))
    return crop, mpp


def gaussian_blur(arr, sigma=1.0):
    import math as _math
    r = int(_math.ceil(3 * sigma))
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
    gray = crop_arr.astype(np.float32).mean(axis=2)
    blurred = gaussian_blur(gray, sigma=1.0)
    gy, gx = np.gradient(blurred)
    gmag = np.sqrt(gx**2 + gy**2)

    H, W = gray.shape
    cx_nom, cy_nom = W / 2.0, H / 2.0
    r_nom_px = r_reg / mpp

    edge_xs, edge_ys, r_readings = [], [], []
    for i in range(N_RAYS):
        ang = 2 * math.pi * i / N_RAYS
        r_min = int(r_nom_px * 0.55)
        r_max = int(r_nom_px * 1.45)
        rs = np.arange(r_min, r_max)

        xs = cx_nom + rs * math.cos(ang)
        ys = cy_nom - rs * math.sin(ang)

        valid = (xs >= 0) & (xs < W-1) & (ys >= 0) & (ys < H-1)
        if valid.sum() < 3:
            continue
        rs, xs, ys = rs[valid], xs[valid], ys[valid]

        xi, yi = xs.astype(int), ys.astype(int)
        dx, dy = xs - xi, ys - yi
        vals = (gmag[yi, xi]     * (1-dx)*(1-dy) +
                gmag[yi, xi+1]   *    dx *(1-dy) +
                gmag[yi+1, xi]   * (1-dx)*   dy  +
                gmag[yi+1, xi+1] *    dx *   dy)

        if vals.max() < 0.5:
            continue
        peak = int(np.argmax(vals))
        r_readings.append(rs[peak] * mpp)
        edge_xs.append(float(xs[peak]))
        edge_ys.append(float(ys[peak]))

    if len(r_readings) < 6:
        return None

    r_median_m = float(np.median(r_readings))
    ex, ey = np.array(edge_xs), np.array(edge_ys)
    try:
        fit_cx, fit_cy, fit_r_px = circle_fit(ex, ey)
        r_fit_m = fit_r_px * mpp
    except Exception:
        fit_cx, fit_cy = cx_nom, cy_nom
        r_fit_m = r_median_m

    dists = np.sqrt((ex - fit_cx)**2 + (ey - fit_cy)**2)
    rms_m = float(np.sqrt(np.mean((dists - fit_r_px)**2))) * mpp

    dx_px = fit_cx - cx_nom
    dy_px = fit_cy - cy_nom
    shift_m   = math.sqrt(dx_px**2 + dy_px**2) * mpp
    shift_e_m = dx_px * mpp
    shift_s_m = dy_px * mpp  # image y down = south

    return {
        "r_fit_m":    round(r_fit_m, 1),
        "r_median_m": round(r_median_m, 1),
        "shift_m":    round(shift_m, 1),
        "shift_e_m":  round(shift_e_m, 1),
        "shift_s_m":  round(shift_s_m, 1),
        "rms_m":      round(rms_m, 1),
        "n_rays":     len(r_readings),
    }


def main():
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 OilRadar-diagnostic"

    rows = []
    for t in TANKS:
        tid = t["id"]
        r_reg = t["r_reg"]
        print(f"  {tid} … ", end="", flush=True)
        try:
            crop_img, mpp = fetch_clean_crop(t, session)
        except Exception as e:
            print(f"FETCH FAIL: {e}")
            rows.append({"id": tid, "status": "FETCH_FAIL", "r_reg": r_reg})
            continue

        crop_arr = np.array(crop_img)
        crop_img.save(str(OUT_DIR / f"{tid}.png"))

        res = measure(crop_arr, r_reg, mpp)
        if res is None:
            print("MEASURE FAIL")
            rows.append({"id": tid, "status": "MEASURE_FAIL", "r_reg": r_reg})
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
        print(f"r_fit={res['r_fit_m']}m  shift={res['shift_m']}m  rms={res['rms_m']}m  rays={res['n_rays']}")

    # JSON
    (TABLE_DIR / "jask_radius_raw.json").write_text(json.dumps(rows, indent=2))

    # Markdown
    md = [
        "# Jask ESRI z=18 — Radius & Center Measurement",
        "",
        "Method: 32-ray gradient peak + least-squares circle fit on clean (no-overlay) ESRI z=18 crops.",
        f"mpp ≈ {meters_per_pixel(25.857, 18):.4f} m/px at lat ~25.857°N",
        "",
        "| Tank | r_reg | r_fit | Δr | shift_m | shift_E | shift_S | rms | rays |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r["status"] != "OK":
            md.append(f"| {r['id']} | 35 | {r['status']} | — | — | — | — | — | — |")
            continue
        dr = round(r["r_fit_m"] - r["r_reg"], 1)
        md.append(
            f"| {r['id']} | {r['r_reg']} | **{r['r_fit_m']}** | {dr:+.1f} "
            f"| {r['shift_m']} | {r['shift_e_m']:+.1f} | {r['shift_s_m']:+.1f} "
            f"| {r['rms_m']} | {r['n_rays']} |"
        )
    md += [
        "",
        "shift_E: +east, shift_S: +south (metres).  rms = RMS residual of edge points from fitted circle.",
    ]
    (TABLE_DIR / "JASK_RADIUS_TABLE.md").write_text("\n".join(md))
    print(f"\nTable → {TABLE_DIR}/JASK_RADIUS_TABLE.md")
    print(f"JSON  → {TABLE_DIR}/jask_radius_raw.json")


if __name__ == "__main__":
    main()
