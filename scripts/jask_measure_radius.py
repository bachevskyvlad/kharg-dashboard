"""
jask_measure_radius.py — gradient-profile radius + center fit on saved ESRI crops.

Reads diagnostics/jask/esri/jk_*.png (individual crops, NOT atlases).
Each crop is 650×650px, CROP_DIAMS=5, so center = (325,325).
Uses 32-ray gradient peak detection, then least-squares circle fit.

Outputs:
  diagnostics/jask/JASK_RADIUS_TABLE.md
  diagnostics/jask/jask_radius_raw.json
"""

import math, json
from pathlib import Path
import numpy as np
from PIL import Image

CROP_DIAMS = 5
TILE_SIZE  = 256
ZOOM       = 18
N_RAYS     = 32

# All 20 tanks: registry coords + r_reg
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

IN_DIR  = Path(__file__).parent.parent / "diagnostics" / "jask" / "esri"
OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "jask"


def meters_per_pixel(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)


def gaussian_blur(arr, sigma=1.5):
    """Simple separable Gaussian blur using numpy."""
    from math import ceil, exp
    r = int(ceil(3 * sigma))
    x = np.arange(-r, r + 1, dtype=np.float32)
    kernel = np.exp(-x**2 / (2 * sigma**2))
    kernel /= kernel.sum()
    tmp = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode='same'), 1, arr)
    return np.apply_along_axis(lambda col: np.convolve(col, kernel, mode='same'), 0, tmp)


def gradient_magnitude(gray):
    """Sobel-like gradient magnitude."""
    gy = np.gradient(gray, axis=0)
    gx = np.gradient(gray, axis=1)
    return np.sqrt(gx**2 + gy**2)


def circle_fit_lstsq(xs, ys):
    """Algebraic least-squares circle fit. Returns cx, cy, r."""
    A = np.column_stack([2 * xs, 2 * ys, np.ones(len(xs))])
    b = xs**2 + ys**2
    res, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = res[0], res[1]
    r = math.sqrt(res[2] + cx**2 + cy**2)
    return cx, cy, r


def measure_crop(img_path, tank):
    lat, r_reg = tank["lat"], tank["r_reg"]
    mpp = meters_per_pixel(lat, ZOOM)

    img = Image.open(img_path).convert("L")  # grayscale
    arr = np.array(img, dtype=np.float32)
    H, W = arr.shape

    cx_nom = W / 2.0
    cy_nom = H / 2.0
    r_nom_px = r_reg / mpp

    blurred = gaussian_blur(arr, sigma=1.5)
    gmag = gradient_magnitude(blurred)

    # Suppress the overlay: cyan beams (high R, high G, low B) and
    # yellow circle (high R, high G, low B) drawn on top — work in grayscale
    # but exclude the drawn annotation band near r_nom_px ±20% from beam search
    ann_inner = r_nom_px * 0.55
    ann_outer = r_nom_px * 1.45

    edge_xs, edge_ys = [], []
    r_readings = []

    angles = np.linspace(0, 2 * math.pi, N_RAYS, endpoint=False)
    for ang in angles:
        r_min = int(r_nom_px * 0.45)
        r_max = int(r_nom_px * 1.55)
        rs = np.arange(r_min, r_max)

        xs = cx_nom + rs * math.cos(ang)
        ys = cy_nom - rs * math.sin(ang)  # image y flipped

        # Clip to image bounds
        valid = (xs >= 0) & (xs < W - 1) & (ys >= 0) & (ys < H - 1)
        if valid.sum() < 4:
            continue
        rs, xs, ys = rs[valid], xs[valid], ys[valid]

        # Bilinear sample of gradient magnitude
        xi = xs.astype(int)
        yi = ys.astype(int)
        dx = xs - xi
        dy = ys - yi
        vals = (gmag[yi,   xi]   * (1 - dx) * (1 - dy) +
                gmag[yi,   xi+1] *      dx  * (1 - dy) +
                gmag[yi+1, xi]   * (1 - dx) *      dy  +
                gmag[yi+1, xi+1] *      dx  *      dy)

        if vals.max() < 1.0:
            continue

        peak_idx = int(np.argmax(vals))
        r_px = rs[peak_idx]
        r_readings.append(r_px * mpp)
        edge_xs.append(float(xs[peak_idx]))
        edge_ys.append(float(ys[peak_idx]))

    if len(r_readings) < 6:
        return None

    r_median_m = float(np.median(r_readings))

    # Circle fit on edge points
    ex = np.array(edge_xs)
    ey = np.array(edge_ys)
    try:
        fit_cx, fit_cy, fit_r_px = circle_fit_lstsq(ex, ey)
        r_fit_m = fit_r_px * mpp
    except Exception:
        fit_cx, fit_cy = cx_nom, cy_nom
        r_fit_m = r_median_m

    # RMS residual of edge points from fitted circle
    dists = np.sqrt((ex - fit_cx)**2 + (ey - fit_cy)**2)
    rms_px = float(np.sqrt(np.mean((dists - (r_fit_m / mpp))**2)))
    rms_m  = rms_px * mpp

    # Center shift in metres
    dx_px = fit_cx - cx_nom
    dy_px = fit_cy - cy_nom
    # image: x→east, y→south (y increases downward)
    shift_m  = math.sqrt(dx_px**2 + dy_px**2) * mpp
    shift_e_m = dx_px * mpp   # positive = east
    shift_s_m = dy_px * mpp   # positive = south (image y down)

    return {
        "r_fit_m":   round(r_fit_m,  1),
        "r_median_m": round(r_median_m, 1),
        "shift_m":   round(shift_m,  1),
        "shift_e_m": round(shift_e_m, 1),
        "shift_s_m": round(shift_s_m, 1),
        "rms_m":     round(rms_m,    1),
        "n_rays":    len(r_readings),
        "mpp":       round(mpp, 4),
    }


def compute_new_latlon(tank, res):
    """Shift registry lat/lon by fit center offset."""
    lat, lon = tank["lat"], tank["lon"]
    mpp = meters_per_pixel(lat, ZOOM)
    # shift_e_m: east (lon direction), shift_s_m: south (negative lat direction)
    shift_e_m = res["shift_e_m"]
    shift_s_m = res["shift_s_m"]
    dlat = -shift_s_m / 111320.0                              # south → decrease lat
    dlon = shift_e_m  / (111320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def main():
    rows = []
    for t in TANKS:
        tid = t["id"]
        p = IN_DIR / f"{tid}.png"
        if not p.exists():
            print(f"  {tid}: MISSING")
            continue
        print(f"  {tid} … ", end="", flush=True)
        res = measure_crop(p, t)
        if res is None:
            print("FAIL (< 6 ray hits)")
            rows.append({"id": tid, "status": "FAIL", **{k: None for k in
                ["r_fit_m","shift_m","shift_e_m","shift_s_m","rms_m","n_rays"]}})
            continue
        new_lat, new_lon = compute_new_latlon(t, res)
        res.update({"id": tid, "r_reg": t["r_reg"],
                    "lat_reg": t["lat"], "lon_reg": t["lon"],
                    "lat_fit": round(new_lat, 9), "lon_fit": round(new_lon, 9),
                    "status": "OK"})
        rows.append(res)
        print(f"r_fit={res['r_fit_m']}m  shift={res['shift_m']}m  rms={res['rms_m']}m  rays={res['n_rays']}")

    # JSON
    out_json = OUT_DIR / "jask_radius_raw.json"
    out_json.write_text(json.dumps(rows, indent=2))

    # Markdown table
    md = [
        "# Jask ESRI z=18 — Radius & Center Measurement",
        "",
        "Method: 32-ray gradient-peak + least-squares circle fit on ESRI z=18 crops.",
        f"mpp ≈ {meters_per_pixel(25.857, 18):.4f} m/px at lat 25.857°N",
        "",
        "| Tank | r_reg | r_fit | Δr | shift_m | shift_E | shift_S | rms | rays |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r["status"] != "OK":
            md.append(f"| {r['id']} | {r.get('r_reg',35)} | FAIL | — | — | — | — | — | — |")
            continue
        dr = round(r["r_fit_m"] - r["r_reg"], 1)
        md.append(
            f"| {r['id']} | {r['r_reg']} | **{r['r_fit_m']}** | {dr:+.1f} "
            f"| {r['shift_m']} | {r['shift_e_m']:+.1f} | {r['shift_s_m']:+.1f} "
            f"| {r['rms_m']} | {r['n_rays']} |"
        )

    md += [
        "",
        "All shifts in metres. shift_E: positive = east, shift_S: positive = south.",
        "rms = RMS residual of edge points from fitted circle (m).",
    ]

    out_md = OUT_DIR / "JASK_RADIUS_TABLE.md"
    out_md.write_text("\n".join(md))
    print(f"\nTable → {out_md}")
    print(f"JSON  → {out_json}")


if __name__ == "__main__":
    main()
