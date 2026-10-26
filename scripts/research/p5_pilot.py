"""
p5_pilot.py — SAR image variants pilot: 20 scenes, 2 terminals.

Outputs (all in diagnostics/mega/p5_pilot/):
  crops_pilot.npz          — raw crops, float32 linear gamma0, 10m + 5m,
                             geotransform per scene+res, plus scene metadata
  variants/<scene_id>/<variant>.npz  — processed crops, same shape as 10m crop
  image_variants_pilot.csv — (terminal, tank_id, date, variant,
                               ring15_far, ring15_near, R_pos_m, R_max_dB,
                               peak_width_m, pixel_valid_frac)
  qc_<date>_<terminal>.png — visual QC grids for 3 Kharg scenes

What NOT done here: no correlation, no best-variant selection, no "improved".
"""

import csv, gzip, io, os, sys, math, datetime, warnings
import numpy as np
from pathlib import Path
from collections import defaultdict

# ---- optional imports (graceful degradation) --------------------------------
try:
    from sentinelhub import (
        SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
        MimeType, bbox_to_dimensions,
    )
    HAS_SH = True
except ImportError:
    HAS_SH = False

try:
    import skimage.restoration as skr
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

try:
    import bm3d as _bm3d
    HAS_BM3D = True
except ImportError:
    HAS_BM3D = False

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ---- config -----------------------------------------------------------------

OUT_DIR = Path("diagnostics/mega/p5_pilot")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CA = os.environ.get("REQUESTS_CA_BUNDLE") or True

# Pilot scene lists
KHARG_DATES = [
    "2019-04-21","2019-05-03","2019-06-08",
    "2024-09-21","2024-10-03","2024-10-15",
    "2026-04-14","2026-05-02","2026-05-14",
    "2026-06-19","2026-06-26","2026-07-08",
]
RT_DATES = [
    "2019-08-31","2019-09-24","2019-10-06",
    "2018-06-12","2018-07-06",
    "2022-11-14","2023-03-19","2023-04-12",
]

QC_SCENES = [  # (terminal, date)
    ("kharg",     "2024-10-03"),
    ("kharg",     "2026-05-14"),
    ("kharg",     "2019-06-08"),
]

# Farm centres and radius (m) for crop bbox
TERMINAL_META = {
    "kharg": {
        "lat": 29.247, "lon": 49.997,
        "crop_km": 4.0,   # +300m around ~3.5 km farm radius
        "rel_orbit": 35, "direction": "DESCENDING",
    },
    "ras_tanura": {
        "lat": 26.693, "lon": 50.093,
        "crop_km": 5.5,
        "rel_orbit": 35, "direction": "DESCENDING",
    },
}

VARIANTS_ORDER = [
    "baseline",
    "lee_5","lee_7",
    "refined_lee_5",
    "frost_5",
    "gamma_map_5",
    "quegan_mt",
    "nlm",
    "bm3d",
    "mt_mean_2","mt_mean_3",
    "coregister",
    "resample_5m_bilinear","resample_5m_bicubic",
    "coreg_mean_2",
]

# ---- Sentinel Hub fetch -----------------------------------------------------

def sh_config():
    cfg = SHConfig()
    cfg.sh_client_id     = os.environ["SH_CLIENT_ID"]
    cfg.sh_client_secret = os.environ["SH_CLIENT_SECRET"]
    return cfg

EVALSCRIPT_VV = """
//VERSION=3
function setup() {
  return {input:[{bands:["VV"],units:"LINEAR_16BIT"}],output:{bands:1,sampleType:"FLOAT32"}};
}
function evaluatePixel(s) { return [s.VV / 65535.0]; }
"""

def fetch_scene(cfg, terminal, date_str, res_m):
    meta = TERMINAL_META[terminal]
    half = meta["crop_km"] * 1000 / 2
    cx, cy = meta["lon"], meta["lat"]
    # rough degree conversion
    dlat = half / 111320
    dlon = half / (111320 * math.cos(math.radians(cy)))
    bbox = BBox((cx - dlon, cy - dlat, cx + dlon, cy + dlat), crs=CRS.WGS84)
    size = bbox_to_dimensions(bbox, resolution=res_m)

    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_VV,
        input_data=[SentinelHubRequest.input_data(
            data_collection=DataCollection.SENTINEL1_IW,
            time_interval=(date_str, date_str),
            other_args={
                "dataFilter": {
                    "mosaickingOrder": "mostRecent",
                    "acquisitionMode": "IW",
                    "polarization": "DV",
                    "orbitDirection": meta["direction"],
                    "orbitId": meta["rel_orbit"],
                },
                "processing": {
                    "backCoeff": "GAMMA0_TERRAIN",
                    "orthorectify": True,
                    "demInstance": "COPERNICUS_30",
                },
            },
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox,
        size=size,
        config=cfg,
    )
    data = req.get_data()[0]              # H x W x 1
    arr = data[:, :, 0].astype(np.float32)
    # build simple geotransform [x_min, x_res, 0, y_max, 0, -y_res]
    gt = [cx - dlon, (2*dlon) / size[0], 0,
          cy + dlat, 0, -(2*dlat) / size[1]]
    return arr, gt


# ---- ring15 radiometry  -----------------------------------------------------

def ring_annulus(arr, cx, cy, r_near_px, r_far_px):
    """Return mean dB of far-side crescent (azimuth 270°±45°, i.e. west shadow)."""
    H, W = arr.shape
    xs = np.arange(W) - cx
    ys = np.arange(H).reshape(-1, 1) - cy
    dist = np.sqrt(xs**2 + ys**2)
    mask_far  = (dist >= r_far_px  * 0.85) & (dist <= r_far_px  * 1.15)
    mask_near = (dist >= r_near_px * 0.85) & (dist <= r_near_px * 1.15)
    # far-side azimuth: west quadrant (135°..225° from north = cos<0)
    az_mask = xs < 0
    pix_far  = arr[mask_far  & az_mask]
    pix_near = arr[mask_near & ~az_mask]
    def safe_db(px):
        px = px[px > 0]
        return float(10 * np.log10(np.mean(px))) if len(px) > 5 else np.nan
    return safe_db(pix_far), safe_db(pix_near), len(pix_far)


KHARG_TANKS_APPROX = [
    # (lat, lon, radius_m) — approximate, good enough for pilot ring15
    (29.235, 49.992, 60),(29.237, 49.994, 60),(29.238, 49.990, 60),
    (29.240, 49.996, 60),(29.242, 49.993, 60),
    (29.245, 49.985, 55),(29.247, 49.987, 55),(29.249, 49.989, 55),
    (29.250, 49.991, 55),(29.252, 49.993, 55),(29.244, 49.999, 55),
    (29.246, 50.001, 55),(29.248, 50.003, 55),(29.250, 50.005, 55),
    (29.252, 50.007, 55),(29.241, 49.981, 55),(29.243, 49.983, 55),
    (29.255, 49.995, 55),(29.257, 49.997, 55),(29.257, 50.003, 55),
    (29.259, 50.001, 55),(29.259, 49.999, 55),(29.255, 50.009, 55),
    (29.255, 49.991, 40),(29.253, 49.989, 40),(29.255, 50.001, 40),
    (29.250, 49.981, 40),(29.253, 49.983, 40),(29.247, 49.981, 40),
    (29.245, 49.979, 40),(29.243, 49.977, 40),(29.241, 49.975, 40),
    (29.239, 49.977, 40),(29.241, 50.003, 40),(29.255, 50.007, 40),
    (29.240, 49.988, 25),(29.242, 49.986, 25),(29.238, 49.984, 25),
    (29.244, 49.975, 25),(29.246, 49.977, 25),(29.248, 49.979, 25),
    (29.250, 49.975, 25),(29.252, 49.977, 25),(29.237, 49.986, 25),
    (29.239, 50.001, 25),(29.241, 50.005, 25),(29.257, 50.009, 25),
]

RT_TANKS_APPROX = [
    (26.688, 50.081, 55),(26.690, 50.083, 55),(26.692, 50.085, 55),
    (26.694, 50.087, 55),(26.688, 50.087, 55),(26.690, 50.089, 55),
    (26.692, 50.091, 55),(26.686, 50.085, 40),(26.694, 50.083, 40),
    (26.696, 50.085, 40),(26.696, 50.081, 40),(26.698, 50.083, 40),
]

TERMINAL_TANKS = {"kharg": KHARG_TANKS_APPROX, "ras_tanura": RT_TANKS_APPROX}


def measure_tank(arr, gt, lat, lon, radius_m, res_m):
    """Return ring15 metrics for one tank in arr."""
    x_min, x_res = gt[0], gt[1]
    y_max, y_res = gt[3], gt[5]  # y_res is negative
    cx_px = (lon - x_min) / x_res
    cy_px = (lat - y_max) / y_res
    r_px   = radius_m / res_m
    r_far  = r_px
    r_near = r_px * 0.7
    far_db, near_db, n_pix = ring_annulus(arr, cx_px, cy_px, r_near, r_far)
    total_px = math.pi * r_px**2
    pvf = n_pix / total_px if total_px > 0 else 0.0
    # R_max_dB: peak dB in far annulus; peak_width_m: FWHM approx
    H, W = arr.shape
    xs = np.arange(W) - cx_px
    ys = np.arange(H).reshape(-1, 1) - cy_px
    dist = np.sqrt(xs**2 + ys**2)
    mask = (dist >= r_far*0.7) & (dist <= r_far*1.3) & (xs < 0) & (arr > 0)
    if mask.sum() > 5:
        vals_db = 10 * np.log10(arr[mask].clip(1e-10))
        r_max_db = float(np.max(vals_db))
        # R_pos_m: radial distance of peak
        dists_masked = dist[mask]
        r_pos_m = float(dists_masked[np.argmax(vals_db)] * res_m)
        # FWHM: width where vals > peak - 3dB
        above = vals_db > (r_max_db - 3)
        peak_width_m = float(np.sum(above) * res_m**2 / (2 * r_px * res_m))
    else:
        r_max_db = r_pos_m = peak_width_m = float("nan")
    tank_id = f"t{int(radius_m*2)}_{int(lat*1000)%100:02d}"
    return {
        "ring15_far":       round(far_db, 4) if not math.isnan(far_db) else "",
        "ring15_near":      round(near_db, 4) if not math.isnan(near_db) else "",
        "R_pos_m":          round(r_pos_m, 2) if not math.isnan(r_pos_m) else "",
        "R_max_dB":         round(r_max_db, 4) if not math.isnan(r_max_db) else "",
        "peak_width_m":     round(peak_width_m, 2) if not math.isnan(peak_width_m) else "",
        "pixel_valid_frac": round(pvf, 4),
        "_tank_id":         tank_id,
    }


# ---- image processing variants ----------------------------------------------

def lee_filter(arr, ksize):
    from scipy.ndimage import uniform_filter
    mean  = uniform_filter(arr.astype(float), ksize)
    mean2 = uniform_filter(arr.astype(float)**2, ksize)
    var   = mean2 - mean**2
    # noise variance estimate (ENL=4 for GRDH)
    enl   = 4.0
    noise_var = mean**2 / enl
    w     = var / (var + noise_var + 1e-30)
    return (mean + w * (arr - mean)).astype(np.float32)


def refined_lee(arr, ksize):
    """Simplified Refined Lee: edge-directed weighted average."""
    from scipy.ndimage import uniform_filter, generic_filter
    base = lee_filter(arr, ksize)
    # gradient magnitude to weight toward edge-aligned
    gx = np.gradient(arr.astype(float), axis=1)
    gy = np.gradient(arr.astype(float), axis=0)
    mag = np.sqrt(gx**2 + gy**2)
    mag_s = uniform_filter(mag, ksize)
    alpha = np.clip(mag_s / (mag_s + 0.01), 0, 1).astype(np.float32)
    return (alpha * arr + (1 - alpha) * base).astype(np.float32)


def frost_filter(arr, ksize):
    from scipy.ndimage import uniform_filter
    d = ksize // 2
    mean  = uniform_filter(arr.astype(float), ksize)
    mean2 = uniform_filter(arr.astype(float)**2, ksize)
    var   = np.maximum(mean2 - mean**2, 0)
    cv2   = var / (mean**2 + 1e-30)
    # damping factor k=1
    H, W = arr.shape
    yi, xi = np.mgrid[-d:d+1, -d:d+1]
    dist   = np.sqrt(xi**2 + yi**2)
    result = np.zeros_like(arr, dtype=float)
    for r in range(H):
        for c in range(W):
            r0, r1 = max(0, r-d), min(H, r+d+1)
            c0, c1 = max(0, c-d), min(W, c+d+1)
            patch = arr[r0:r1, c0:c1].astype(float)
            dr0, dr1 = r0-r+d, r1-r+d
            dc0, dc1 = c0-c+d, c1-c+d
            w = np.exp(-cv2[r, c] * dist[dr0:dr1, dc0:dc1])
            result[r, c] = np.sum(w * patch) / (np.sum(w) + 1e-30)
    return result.astype(np.float32)


def gamma_map_filter(arr, ksize):
    from scipy.ndimage import uniform_filter
    enl   = 4.0
    mean  = uniform_filter(arr.astype(float), ksize)
    mean2 = uniform_filter(arr.astype(float)**2, ksize)
    var   = np.maximum(mean2 - mean**2, 0)
    cv2_i = var / (mean**2 + 1e-30)
    cu2   = 1.0 / enl
    # Gamma-MAP weights
    w = np.where(cv2_i > cu2,
                 (cv2_i - cu2) / (cv2_i + cu2 * enl**2 / (enl + 1) + 1e-30),
                 0.0)
    return (mean + w * (arr - mean)).astype(np.float32)


def quegan_mt(stack):
    """Quegan multi-temporal: local ratio weighting across N scenes."""
    # stack: list of 2D float32 arrays, same shape
    N = len(stack)
    if N < 2:
        return stack[0]
    s = np.stack(stack, axis=0).astype(float)   # N x H x W
    from scipy.ndimage import uniform_filter
    means = np.array([uniform_filter(s[i], 7) for i in range(N)])
    grand = means.mean(axis=0)
    ratios = means / (grand + 1e-30)
    normalized = s / (ratios + 1e-30)
    filtered = normalized.mean(axis=0) * ratios[N//2]
    return filtered.astype(np.float32)


def nlm_filter(arr):
    if not HAS_SKIMAGE:
        return None
    sigma_est = np.std(arr) * 0.1
    return skr.denoise_nl_means(arr, h=sigma_est * 1.15,
                                fast_mode=True, patch_size=5,
                                patch_distance=6).astype(np.float32)


def bm3d_filter(arr):
    if not HAS_BM3D:
        return None
    sigma = np.std(arr) * 0.1
    # bm3d expects [0,1]
    vmax = arr.max()
    if vmax == 0:
        return arr
    norm = arr / vmax
    out = _bm3d.bm3d(norm, sigma_psd=sigma/vmax)
    return (out * vmax).astype(np.float32)


def mt_mean(stack, n):
    """Multi-temporal mean over n closest scenes."""
    sub = stack[:n]
    return np.stack(sub, axis=0).mean(axis=0).astype(np.float32)


def coregister(arr, ref):
    """Register arr to ref via phase cross correlation, upsample=20."""
    if not HAS_SKIMAGE:
        return arr
    from skimage.registration import phase_cross_correlation
    from scipy.ndimage import shift
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sh, _ = phase_cross_correlation(ref, arr, upsample_factor=20)
    return shift(arr, sh).astype(np.float32)


def resample_5m(arr10, mode="bilinear"):
    from scipy.ndimage import zoom
    order = 1 if mode == "bilinear" else 3
    return zoom(arr10, 2.0, order=order).astype(np.float32)


# ---- main -------------------------------------------------------------------

def main():
    if not HAS_SH:
        print("[FATAL] sentinelhub not installed", flush=True)
        sys.exit(1)

    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M UTC")
    print(f"=== P5 Pilot — {ts} ===\n", flush=True)

    cfg = sh_config()

    print(f"BM3D available: {HAS_BM3D}", flush=True)
    print(f"skimage available: {HAS_SKIMAGE}", flush=True)
    print(f"matplotlib available: {HAS_MPL}", flush=True)

    scenes = (
        [("kharg",      d) for d in KHARG_DATES] +
        [("ras_tanura", d) for d in RT_DATES]
    )
    print(f"\nTotal pilot scenes: {len(scenes)}", flush=True)

    # 1. Fetch all raw crops (10m + 5m)
    print("\n=== 1. Fetch raw crops ===", flush=True)
    raw10 = {}   # (terminal, date) -> arr
    raw5  = {}
    gt10  = {}
    gt5   = {}
    meta_records = []

    for terminal, date_str in scenes:
        print(f"  {terminal} {date_str} …", flush=True)
        try:
            a10, g10 = fetch_scene(cfg, terminal, date_str, res_m=10)
            a5,  g5  = fetch_scene(cfg, terminal, date_str, res_m=5)
            raw10[(terminal, date_str)] = a10
            raw5 [(terminal, date_str)] = a5
            gt10 [(terminal, date_str)] = g10
            gt5  [(terminal, date_str)] = g5
            print(f"    10m: {a10.shape}  5m: {a5.shape}  "
                  f"min/max: {a10.min():.4f}/{a10.max():.4f}", flush=True)
            meta_records.append({
                "terminal": terminal, "date": date_str,
                "shape_10m": str(a10.shape), "shape_5m": str(a5.shape),
                "min_10m": float(a10.min()), "max_10m": float(a10.max()),
            })
        except Exception as e:
            print(f"    [WARN] fetch failed: {e}", flush=True)

    # Save crops_pilot.npz
    npz_data = {}
    for (terminal, date_str), arr in raw10.items():
        key = f"{terminal}__{date_str}__10m"
        npz_data[key] = arr
        npz_data[key + "__gt"] = np.array(gt10[(terminal, date_str)])
    for (terminal, date_str), arr in raw5.items():
        key = f"{terminal}__{date_str}__5m"
        npz_data[key] = arr
        npz_data[key + "__gt"] = np.array(gt5[(terminal, date_str)])
    crops_path = OUT_DIR / "crops_pilot.npz"
    np.savez_compressed(str(crops_path), **npz_data)
    print(f"\nSaved: {crops_path} ({crops_path.stat().st_size//1024} KB)", flush=True)

    # 2. Build per-orbit stacks for multi-temporal variants
    print("\n=== 2. Build orbit stacks ===", flush=True)
    orbit_stacks = defaultdict(list)  # terminal -> ordered list of (date, arr)
    for terminal, date_str in sorted(scenes, key=lambda x: x[1]):
        if (terminal, date_str) in raw10:
            orbit_stacks[terminal].append((date_str, raw10[(terminal, date_str)]))

    # 3. Process variants per scene
    print("\n=== 3. Process variants ===", flush=True)
    variants_dir = OUT_DIR / "variants"
    variants_dir.mkdir(exist_ok=True)

    csv_rows = []
    pu_count = 0

    for terminal, date_str in scenes:
        if (terminal, date_str) not in raw10:
            continue
        arr = raw10[(terminal, date_str)]
        gt  = gt10[(terminal, date_str)]
        res_m = 10

        scene_dir = variants_dir / f"{terminal}__{date_str}"
        scene_dir.mkdir(exist_ok=True)

        # Build local stack (this scene's position in orbit stack)
        stack_all = [a for d, a in orbit_stacks[terminal]]
        idx = [d for d, _ in orbit_stacks[terminal]].index(date_str)

        # Get 2-3 neighbours (preceding scenes for multi-temporal)
        stack_2 = [stack_all[max(0, idx-1)], arr]
        stack_3 = [stack_all[max(0, idx-2)], stack_all[max(0, idx-1)], arr]

        # Coregistration reference: mean of stack
        ref_mean = np.stack(stack_all, 0).mean(0).astype(np.float32) if len(stack_all) > 1 else arr

        variant_arrays = {}

        # baseline
        variant_arrays["baseline"] = arr

        # Lee
        variant_arrays["lee_5"] = lee_filter(arr, 5)
        variant_arrays["lee_7"] = lee_filter(arr, 7)

        # Refined Lee
        variant_arrays["refined_lee_5"] = refined_lee(arr, 5)

        # Frost (slow — smaller kernel OK for pilot)
        try:
            variant_arrays["frost_5"] = frost_filter(arr, 5)
        except Exception as e:
            print(f"    [WARN] frost_5 {terminal} {date_str}: {e}", flush=True)
            variant_arrays["frost_5"] = arr

        # Gamma-MAP
        variant_arrays["gamma_map_5"] = gamma_map_filter(arr, 5)

        # Quegan multi-temporal
        variant_arrays["quegan_mt"] = quegan_mt(stack_all)

        # NLM
        nlm = nlm_filter(arr)
        variant_arrays["nlm"] = nlm if nlm is not None else arr

        # BM3D
        bm3d_out = bm3d_filter(arr)
        variant_arrays["bm3d"] = bm3d_out if bm3d_out is not None else arr

        # Multi-temporal means
        variant_arrays["mt_mean_2"] = mt_mean(stack_2, 2)
        variant_arrays["mt_mean_3"] = mt_mean(stack_3, 3)

        # Coregistration
        coreg = coregister(arr, ref_mean)
        variant_arrays["coregister"] = coreg

        # Resample to 5m
        variant_arrays["resample_5m_bilinear"] = resample_5m(arr, "bilinear")
        variant_arrays["resample_5m_bicubic"]  = resample_5m(arr, "bicubic")

        # Coreg + mean_2
        coreg2 = coregister(stack_2[0], ref_mean)
        variant_arrays["coreg_mean_2"] = np.stack([coreg, coreg2], 0).mean(0).astype(np.float32)

        # Save variant npz files
        for vname, varr in variant_arrays.items():
            vpath = scene_dir / f"{vname}.npz"
            np.savez_compressed(str(vpath), arr=varr)

        # Count PU (each 10m crop fetch = ~1 PU, 5m also ~1 PU)
        pu_count += 2

        # Measure ring15 for each tank for each variant
        tanks = TERMINAL_TANKS[terminal]
        for vname, varr in variant_arrays.items():
            # For 5m variants, use 5m array and halved res
            if vname in ("resample_5m_bilinear", "resample_5m_bicubic"):
                use_arr = varr
                use_gt  = gt5.get((terminal, date_str), gt)
                use_res = 5
            else:
                use_arr = varr
                use_gt  = gt
                use_res = res_m

            for lat, lon, radius_m in tanks:
                try:
                    m = measure_tank(use_arr, use_gt, lat, lon, radius_m, use_res)
                except Exception:
                    m = {"ring15_far":"","ring15_near":"","R_pos_m":"","R_max_dB":"",
                         "peak_width_m":"","pixel_valid_frac":"","_tank_id":"err"}
                csv_rows.append({
                    "terminal":         terminal,
                    "tank_id":          m["_tank_id"],
                    "date":             date_str,
                    "variant":          vname,
                    "ring15_far":       m["ring15_far"],
                    "ring15_near":      m["ring15_near"],
                    "R_pos_m":          m["R_pos_m"],
                    "R_max_dB":         m["R_max_dB"],
                    "peak_width_m":     m["peak_width_m"],
                    "pixel_valid_frac": m["pixel_valid_frac"],
                })

        print(f"  {terminal} {date_str}: {len(variant_arrays)} variants done", flush=True)

    # 4. Write CSV
    print("\n=== 4. Write image_variants_pilot.csv ===", flush=True)
    csv_path = OUT_DIR / "image_variants_pilot.csv"
    fields = ["terminal","tank_id","date","variant",
              "ring15_far","ring15_near","R_pos_m","R_max_dB",
              "peak_width_m","pixel_valid_frac"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(csv_rows)
    print(f"  {csv_path}: {len(csv_rows)} rows", flush=True)

    # 5. QC PNGs
    print("\n=== 5. QC PNG grids ===", flush=True)
    if not HAS_MPL:
        print("  [SKIP] matplotlib not available", flush=True)
    else:
        for terminal, date_str in QC_SCENES:
            if (terminal, date_str) not in raw10:
                print(f"  [SKIP] {terminal} {date_str} not fetched", flush=True)
                continue
            scene_dir = variants_dir / f"{terminal}__{date_str}"
            arr_base  = raw10[(terminal, date_str)]

            # Crop a sub-region with ~6 tanks (centre of farm)
            H, W = arr_base.shape
            cx, cy = W//2, H//2
            pad = min(H, W, 200) // 2
            sl = np.s_[cy-pad:cy+pad, cx-pad:cx+pad]

            # Consistent dB scale
            db_base = 10 * np.log10(arr_base[sl].clip(1e-10))
            vmin, vmax_v = float(np.percentile(db_base, 2)), float(np.percentile(db_base, 98))

            ordered_variants = [v for v in VARIANTS_ORDER if v in variant_arrays]
            ncols = 4
            nrows = math.ceil((1 + len(ordered_variants)) / ncols)
            fig, axes = plt.subplots(nrows, ncols,
                                     figsize=(ncols*3, nrows*3), dpi=100)
            axes = axes.flatten()

            all_panels = ["baseline"] + [v for v in ordered_variants if v != "baseline"]
            for i, vname in enumerate(all_panels):
                ax = axes[i]
                vpath = scene_dir / f"{vname}.npz"
                if vpath.exists():
                    varr = np.load(str(vpath))["arr"]
                    if varr.shape[0] > H or varr.shape[1] > W:
                        # 5m resample — take equivalent region
                        sl5 = np.s_[cy*2-pad*2:cy*2+pad*2, cx*2-pad*2:cx*2+pad*2]
                        patch = 10 * np.log10(varr[sl5].clip(1e-10))
                    else:
                        patch = 10 * np.log10(varr[sl].clip(1e-10))
                    ax.imshow(patch, cmap="gray", vmin=vmin, vmax=vmax_v,
                              interpolation="nearest", aspect="equal")
                ax.set_title(vname, fontsize=6, pad=2)
                ax.axis("off")

            for j in range(len(all_panels), len(axes)):
                axes[j].axis("off")

            fig.suptitle(f"{terminal}  {date_str}  (dB {vmin:.1f}–{vmax_v:.1f})",
                         fontsize=9)
            fig.tight_layout()
            png_path = OUT_DIR / f"qc_{terminal}_{date_str}.png"
            fig.savefig(str(png_path), dpi=100, bbox_inches="tight")
            plt.close(fig)
            print(f"  Saved: {png_path}", flush=True)

    # 6. Summary
    print("\n=== Summary ===", flush=True)
    print(f"  Scenes fetched: {len(raw10)}/{len(scenes)}", flush=True)
    print(f"  Variants per scene: {len(VARIANTS_ORDER)}", flush=True)
    print(f"  CSV rows: {len(csv_rows)}", flush=True)
    print(f"  Estimated SH PU used: ~{pu_count} "
          f"(2 per scene × {len(raw10)} scenes fetched)", flush=True)
    print(f"  BM3D: {'available' if HAS_BM3D else 'NOT available — variant is copy of baseline'}", flush=True)
    print(f"\nOutputs:", flush=True)
    for p in sorted(OUT_DIR.rglob("*")):
        if p.is_file():
            print(f"  {p}  ({p.stat().st_size//1024} KB)", flush=True)


if __name__ == "__main__":
    main()
