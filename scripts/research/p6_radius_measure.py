"""
p6_radius_measure.py — Measure actual tank radius via ESRI z=18 optical imagery.

Method: fetch ESRI World Imagery tiles (z=18, ~0.6 m/px at 26°N), crop 4-diameter
window centred on registered coordinates, apply 16-ray gradient-peak measurement,
least-squares circle fit to edge points.

Output: diagnostics/mega/tank_radius_measured.csv
  terminal_id, tank_id, radius_registry_m, radius_measured_m,
  diff_m, diff_pct, n_rays_hit, rms_m, method, confidence

Confidence:
  HIGH   n_rays_hit >= 12 and rms_m <= 3
  MEDIUM n_rays_hit >= 8  and rms_m <= 6
  LOW    otherwise (include with flag)
"""

import csv, io, math, os, sys, time
from pathlib import Path
import numpy as np
import requests

ESRI_URL  = ("https://services.arcgisonline.com/arcgis/rest/services/"
             "World_Imagery/MapServer/tile/{z}/{y}/{x}")
ZOOM      = 18
TILE_SIZE = 256
N_RAYS    = 16
CROP_DIAMS = 4   # crop window = 4× registry diameter
OUT_DIR   = Path("diagnostics/mega")

YANBU_TANKS = [
    {"terminal": "yanbu", "id": "yb_0",  "lat": 23.959074, "lon": 38.244398, "radius_m": 60},
    {"terminal": "yanbu", "id": "yb_1",  "lat": 23.962590, "lon": 38.247925, "radius_m": 60},
    {"terminal": "yanbu", "id": "yb_2",  "lat": 23.964053, "lon": 38.245977, "radius_m": 60},
    {"terminal": "yanbu", "id": "yb_3",  "lat": 23.965544, "lon": 38.244086, "radius_m": 60},
    {"terminal": "yanbu", "id": "yb_4",  "lat": 23.964986, "lon": 38.250081, "radius_m": 60},
    {"terminal": "yanbu", "id": "yb_5",  "lat": 23.966460, "lon": 38.248119, "radius_m": 60},
    {"terminal": "yanbu", "id": "yb_6",  "lat": 23.967960, "lon": 38.246187, "radius_m": 60},
    {"terminal": "yanbu", "id": "yb_7",  "lat": 23.967392, "lon": 38.252229, "radius_m": 60},
    {"terminal": "yanbu", "id": "yb_8",  "lat": 23.968866, "lon": 38.250263, "radius_m": 60},
    {"terminal": "yanbu", "id": "yb_9",  "lat": 23.970331, "lon": 38.248336, "radius_m": 60},
    {"terminal": "yanbu", "id": "yb_10", "lat": 23.969788, "lon": 38.254362, "radius_m": 60},
    {"terminal": "yanbu", "id": "yb_11", "lat": 23.971259, "lon": 38.252416, "radius_m": 60},
]

RT_TANKS = [
    {"terminal": "ras_tanura", "id": "rt_0",  "lat": 26.638626,          "lon": 50.154236,          "radius_m": 60},
    {"terminal": "ras_tanura", "id": "rt_1",  "lat": 26.640430,          "lon": 50.153296,          "radius_m": 60},
    {"terminal": "ras_tanura", "id": "rt_2",  "lat": 26.641913,          "lon": 50.152682,          "radius_m": 60},
    {"terminal": "ras_tanura", "id": "rt_3",  "lat": 26.650024,          "lon": 50.152807,          "radius_m": 60},
    {"terminal": "ras_tanura", "id": "rt_4",  "lat": 26.652085,          "lon": 50.149920,          "radius_m": 60},
    {"terminal": "ras_tanura", "id": "rt_5",  "lat": 26.688442,          "lon": 50.118714,          "radius_m": 60},
    {"terminal": "ras_tanura", "id": "rt_6",  "lat": 26.687011,          "lon": 50.117969,          "radius_m": 60},
    {"terminal": "ras_tanura", "id": "rt_7",  "lat": 26.687548,          "lon": 50.116596,          "radius_m": 60},
    {"terminal": "ras_tanura", "id": "rt_8",  "lat": 26.689090,          "lon": 50.117193,          "radius_m": 60},
    {"terminal": "ras_tanura", "id": "rt_9",  "lat": 26.688215,          "lon": 50.114879,          "radius_m": 60},
    {"terminal": "ras_tanura", "id": "rt_10", "lat": 26.689749,          "lon": 50.115695,          "radius_m": 60},
    {"terminal": "ras_tanura", "id": "rt_11", "lat": 26.651657,          "lon": 50.148320,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_12", "lat": 26.649988,          "lon": 50.148177,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_13", "lat": 26.650865,          "lon": 50.148946,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_14", "lat": 26.650985,          "lon": 50.150605,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_15", "lat": 26.648982,          "lon": 50.148984,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_16", "lat": 26.649609,          "lon": 50.149935,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_17", "lat": 26.650144,          "lon": 50.151086,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_18", "lat": 26.648029,          "lon": 50.149726,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_19", "lat": 26.648818,          "lon": 50.150545,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_20", "lat": 26.649339,          "lon": 50.151734,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_21", "lat": 26.646997,          "lon": 50.150259,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_22", "lat": 26.647971,          "lon": 50.151020,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_23", "lat": 26.648509,          "lon": 50.152502,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_24", "lat": 26.648872,          "lon": 50.153435,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_25", "lat": 26.647181,          "lon": 50.151415,          "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_26", "lat": 26.647496613,       "lon": 50.152485930,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_27", "lat": 26.647870961,       "lon": 50.153402153,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_28", "lat": 26.648243784,       "lon": 50.154304015,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_29", "lat": 26.646272546,       "lon": 50.151974459,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_30", "lat": 26.646641703,       "lon": 50.152926255,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_31", "lat": 26.647029129,       "lon": 50.153832257,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_32", "lat": 26.647394612,       "lon": 50.154733110,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_33", "lat": 26.645433932,       "lon": 50.152392769,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_34", "lat": 26.645827818,       "lon": 50.153347593,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_35", "lat": 26.646200394,       "lon": 50.154249852,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_36", "lat": 26.646570259,       "lon": 50.155158460,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_37", "lat": 26.643842240,       "lon": 50.153203110,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_38", "lat": 26.644232379,       "lon": 50.154144912,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_39", "lat": 26.644607973,       "lon": 50.155046837,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_40", "lat": 26.644975530,       "lon": 50.155944016,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_41", "lat": 26.643016459,       "lon": 50.153611656,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_42", "lat": 26.643406095,       "lon": 50.154572485,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_43", "lat": 26.643782414,       "lon": 50.155472085,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_44", "lat": 26.644156293,       "lon": 50.156383567,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_45", "lat": 26.641814725,       "lon": 50.154215785,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_46", "lat": 26.642210411,       "lon": 50.155178684,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_47", "lat": 26.642577330,       "lon": 50.156072461,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_48", "lat": 26.642950972,       "lon": 50.156992054,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_49", "lat": 26.641339934,       "lon": 50.155599952,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_50", "lat": 26.641729102,       "lon": 50.156516229,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_51", "lat": 26.642073615,       "lon": 50.157423258,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_52", "lat": 26.640476856,       "lon": 50.156043180,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_53", "lat": 26.640847523,       "lon": 50.156946870,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_54", "lat": 26.641188536,       "lon": 50.157794617,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_55", "lat": 26.641760390,       "lon": 50.159183452,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_56", "lat": 26.640105425,       "lon": 50.157328512,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_57", "lat": 26.640432763,       "lon": 50.158165902,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_58", "lat": 26.641106377,       "lon": 50.158670772,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_59", "lat": 26.640346939,       "lon": 50.159052659,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_60", "lat": 26.639675405,       "lon": 50.158553012,       "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_61", "lat": 26.643554305621972, "lon": 50.152191100588105, "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_62", "lat": 26.645139789560954, "lon": 50.151448430921810, "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_63", "lat": 26.645949000703600, "lon": 50.151042337312750, "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_64", "lat": 26.639988864810530, "lon": 50.154953473357590, "radius_m": 35},
    {"terminal": "ras_tanura", "id": "rt_65", "lat": 26.637591408424406, "lon": 50.160445373274800, "radius_m": 20},
]

ALL_TANKS = YANBU_TANKS + RT_TANKS


# ── tile fetch ──────────────────────────────────────────────────────────────

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
                # Decode to numpy array without PIL dependency
                from PIL import Image
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                return np.array(img)
        except Exception:
            time.sleep(1.5)
    return np.zeros((TILE_SIZE, TILE_SIZE, 3), dtype=np.uint8)


def fetch_crop(tank, session):
    lat, lon   = tank["lat"], tank["lon"]
    r_reg      = tank["radius_m"]
    mpp        = meters_per_pixel(lat, ZOOM)
    half_px    = int(CROP_DIAMS * r_reg / mpp)
    window     = 2 * half_px

    tx_c, ty_c = deg2tile(lat, lon, ZOOM)
    lat_nw, lon_nw = tile2deg_nw(tx_c, ty_c, ZOOM)

    cy_in = int((lat_nw - lat) * 111320.0 / mpp)
    cx_in = int((lon - lon_nw) * 111320.0 * math.cos(math.radians(lat)) / mpp)

    tiles_n   = math.ceil((half_px + TILE_SIZE) / TILE_SIZE)
    cs        = (2 * tiles_n + 1) * TILE_SIZE
    canvas    = np.zeros((cs, cs, 3), dtype=np.uint8)

    for dy in range(-tiles_n, tiles_n + 1):
        for dx in range(-tiles_n, tiles_n + 1):
            tile = fetch_tile(ZOOM, tx_c + dx, ty_c + dy, session)
            r0 = (dy + tiles_n) * TILE_SIZE
            c0 = (dx + tiles_n) * TILE_SIZE
            canvas[r0:r0+TILE_SIZE, c0:c0+TILE_SIZE] = tile

    cx_abs = tiles_n * TILE_SIZE + cx_in
    cy_abs = tiles_n * TILE_SIZE + cy_in
    y0 = max(0, cy_abs - half_px)
    y1 = min(cs, cy_abs + half_px)
    x0 = max(0, cx_abs - half_px)
    x1 = min(cs, cx_abs + half_px)
    crop = canvas[y0:y1, x0:x1]

    if crop.shape[0] < window // 2 or crop.shape[1] < window // 2:
        return None, mpp
    return crop, mpp


# ── measurement ─────────────────────────────────────────────────────────────

def gaussian_blur(arr, sigma=1.0):
    r = int(math.ceil(3 * sigma))
    x = np.arange(-r, r + 1, dtype=np.float32)
    k = np.exp(-x**2 / (2 * sigma**2))
    k /= k.sum()
    tmp = np.apply_along_axis(lambda row: np.convolve(row, k, "same"), 1, arr)
    return np.apply_along_axis(lambda col: np.convolve(col, k, "same"), 0, tmp)


def circle_fit(xs, ys):
    A = np.column_stack([2*xs, 2*ys, np.ones(len(xs))])
    b = xs**2 + ys**2
    res, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = res[0], res[1]
    r = math.sqrt(max(0.0, res[2] + cx**2 + cy**2))
    return cx, cy, r


def measure(crop_arr, r_reg, mpp):
    gray    = crop_arr.astype(np.float32).mean(axis=2)
    blurred = gaussian_blur(gray, sigma=1.0)
    gy, gx  = np.gradient(blurred)
    gmag    = np.sqrt(gx**2 + gy**2)

    H, W       = gray.shape
    cx_nom     = W / 2.0
    cy_nom     = H / 2.0
    r_nom_px   = r_reg / mpp

    edge_xs, edge_ys, r_readings = [], [], []
    for i in range(N_RAYS):
        ang   = 2 * math.pi * i / N_RAYS
        r_min = int(r_nom_px * 0.55)
        r_max = int(r_nom_px * 1.45)
        rs    = np.arange(r_min, r_max)
        xs    = cx_nom + rs * math.cos(ang)
        ys    = cy_nom - rs * math.sin(ang)
        valid = (xs >= 0) & (xs < W - 1) & (ys >= 0) & (ys < H - 1)
        if valid.sum() < 3:
            continue
        rs, xs, ys = rs[valid], xs[valid], ys[valid]
        xi, yi   = xs.astype(int), ys.astype(int)
        dx_f     = xs - xi
        dy_f     = ys - yi
        vals = (gmag[yi,   xi  ] * (1-dx_f)*(1-dy_f) +
                gmag[yi,   xi+1] *    dx_f *(1-dy_f) +
                gmag[yi+1, xi  ] * (1-dx_f)*   dy_f  +
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
    ex, ey     = np.array(edge_xs), np.array(edge_ys)
    try:
        fit_cx, fit_cy, fit_r_px = circle_fit(ex, ey)
        r_fit_m = fit_r_px * mpp
    except Exception:
        r_fit_m = r_median_m
        fit_cx, fit_cy = cx_nom, cy_nom
        fit_r_px = r_median_m / mpp

    dists = np.sqrt((ex - fit_cx)**2 + (ey - fit_cy)**2)
    rms_m = float(np.sqrt(np.mean((dists - fit_r_px)**2))) * mpp

    return {
        "r_fit_m":    round(r_fit_m, 1),
        "rms_m":      round(rms_m, 1),
        "n_rays_hit": len(r_readings),
    }


# ── main ────────────────────────────────────────────────────────────────────

def main():
    import datetime
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M UTC")
    print(f"=== P6 radius measure — {ts} ===", flush=True)
    print(f"Tanks: {len(ALL_TANKS)} ({len(YANBU_TANKS)} Yanbu + {len(RT_TANKS)} RT)", flush=True)

    try:
        from PIL import Image  # noqa — test import
    except ImportError:
        print("[FATAL] Pillow not installed. Run: pip install Pillow", flush=True)
        sys.exit(1)

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 OilRadar-research/p6"

    fields = ["terminal_id", "tank_id", "radius_registry_m", "radius_measured_m",
              "diff_m", "diff_pct", "n_rays_hit", "rms_m", "method", "confidence"]
    rows = []

    for tank in ALL_TANKS:
        tid      = tank["id"]
        terminal = tank["terminal"]
        r_reg    = tank["radius_m"]
        print(f"  {terminal}/{tid}  r_reg={r_reg}m … ", end="", flush=True)

        try:
            crop, mpp = fetch_crop(tank, session)
        except Exception as e:
            print(f"FETCH_FAIL: {e}", flush=True)
            rows.append({
                "terminal_id": terminal, "tank_id": tid,
                "radius_registry_m": r_reg, "radius_measured_m": "",
                "diff_m": "", "diff_pct": "", "n_rays_hit": 0,
                "rms_m": "", "method": "esri_z18_16ray", "confidence": "FAIL",
            })
            continue

        if crop is None:
            print("CROP_EMPTY", flush=True)
            rows.append({
                "terminal_id": terminal, "tank_id": tid,
                "radius_registry_m": r_reg, "radius_measured_m": "",
                "diff_m": "", "diff_pct": "", "n_rays_hit": 0,
                "rms_m": "", "method": "esri_z18_16ray", "confidence": "FAIL",
            })
            continue

        res = measure(crop, r_reg, mpp)
        if res is None:
            print(f"MEASURE_FAIL (rays<5)", flush=True)
            rows.append({
                "terminal_id": terminal, "tank_id": tid,
                "radius_registry_m": r_reg, "radius_measured_m": "",
                "diff_m": "", "diff_pct": "", "n_rays_hit": 0,
                "rms_m": "", "method": "esri_z18_16ray", "confidence": "FAIL",
            })
            continue

        r_meas   = res["r_fit_m"]
        diff_m   = round(r_meas - r_reg, 1)
        diff_pct = round(100 * diff_m / r_reg, 1) if r_reg else ""
        n        = res["n_rays_hit"]
        rms      = res["rms_m"]

        if n >= 12 and rms <= 3:
            conf = "HIGH"
        elif n >= 8 and rms <= 6:
            conf = "MEDIUM"
        else:
            conf = "LOW"

        print(f"r_fit={r_meas}m  diff={diff_m:+.1f}m ({diff_pct:+.1f}%)  "
              f"rays={n}  rms={rms}m  [{conf}]", flush=True)
        rows.append({
            "terminal_id": terminal, "tank_id": tid,
            "radius_registry_m": r_reg, "radius_measured_m": r_meas,
            "diff_m": diff_m, "diff_pct": diff_pct,
            "n_rays_hit": n, "rms_m": rms,
            "method": "esri_z18_16ray", "confidence": conf,
        })
        time.sleep(0.05)  # polite rate limit

    out_path = OUT_DIR / "tank_radius_measured.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\nSaved: {out_path}  ({len(rows)} rows)", flush=True)

    # Summary
    high   = [r for r in rows if r["confidence"] == "HIGH"]
    medium = [r for r in rows if r["confidence"] == "MEDIUM"]
    low    = [r for r in rows if r["confidence"] == "LOW"]
    fail   = [r for r in rows if r["confidence"] == "FAIL"]
    print(f"\nConfidence: HIGH={len(high)}  MEDIUM={len(medium)}  "
          f"LOW={len(low)}  FAIL={len(fail)}", flush=True)

    measured = [r for r in rows if r["radius_measured_m"] != ""]
    if measured:
        by_terminal = {}
        for r in measured:
            by_terminal.setdefault(r["terminal_id"], []).append(float(r["diff_m"]))
        for term, diffs in sorted(by_terminal.items()):
            arr = np.array(diffs)
            print(f"  {term}: mean_diff={arr.mean():+.1f}m  "
                  f"median_diff={float(np.median(arr)):+.1f}m  "
                  f"std={arr.std():.1f}m  n={len(arr)}", flush=True)


if __name__ == "__main__":
    main()
