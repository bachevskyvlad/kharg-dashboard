"""
p7_image_variants_pilot.py — Task 1.1 pilot filter comparison

Terminals / dates:
  Kharg DESC-35: 2019-04-21, 2019-05-03, 2019-06-08, 2024-09-21, 2024-10-03,
                 2024-10-15, 2026-04-14, 2026-05-02, 2026-05-14, 2026-06-19,
                 2026-06-26, 2026-07-08
  RT DESC-35:    2018-06-12, 2018-07-06, 2019-08-31, 2019-09-24, 2019-10-06,
                 2022-11-14, 2023-03-19, 2023-04-12

Variants: baseline, lee5, lee7, rlf5, frost5, gamma_map5, quegan5, nlm,
          bm3d, mean2, mean3, coreg, resamp5_bilinear, resamp5_bicubic,
          coreg_mean2, coreg_mean3

Outputs (all written to diagnostics/mega/):
  crops_pilot.npz              — raw +300m crops, linear float32, 10m + 5m grids
  variant_<name>.npz           — per-variant processed crop, float32
  image_variants_pilot.csv     — (terminal, tank_id, date, variant) x metrics
  png_check_<date>_<terminal>.png — grids of baseline+variants for 4 check dates

Secrets: CLIENT_ID, CLIENT_SECRET  (GHA secrets)
"""

import csv, gzip, io, json, math, os, sys, warnings
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

SH_CLIENT_ID     = os.environ["CLIENT_ID"]
SH_CLIENT_SECRET = os.environ["CLIENT_SECRET"]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR   = REPO_ROOT / "diagnostics" / "mega"

PILOT_SCENES = {
    "kharg": {
        "rel_orbit":   35,
        "direction":   "DESCENDING",
        "los_azimuth": 282,
        "dates": [
            "2019-04-21","2019-05-03","2019-06-08",
            "2024-09-21","2024-10-03","2024-10-15",
            "2026-04-14","2026-05-02","2026-05-14",
            "2026-06-19","2026-06-26","2026-07-08",
        ],
    },
    "ras_tanura": {
        "rel_orbit":   35,
        "direction":   "DESCENDING",
        "los_azimuth": 282,
        "dates": [
            "2018-06-12","2018-07-06","2019-08-31","2019-09-24",
            "2019-10-06","2022-11-14","2023-03-19","2023-04-12",
        ],
    },
}

PNG_CHECK = {
    "kharg":      ["2024-10-03","2026-05-14","2019-06-08"],
    "ras_tanura": ["2019-09-24"],
}

VARIANTS = [
    "baseline","lee5","lee7","rlf5","frost5","gamma_map5",
    "quegan5","nlm","bm3d","mean2","mean3",
    "coreg","resamp5_bilinear","resamp5_bicubic",
    "coreg_mean2","coreg_mean3",
]

BUFFER_M = 300
RING_IN_M  = 15
RING_OUT_M = 30
SECTOR_HALF = 45

EVALSCRIPT = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""


# ── SentinelHub helpers ──────────────────────────────────────────────────────

def sh_token():
    import requests
    r = requests.post(
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
        data={"grant_type":"client_credentials","client_id":SH_CLIENT_ID,"client_secret":SH_CLIENT_SECRET},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def sh_request(token, bbox, date_str, rel_orbit, direction, resolution=10, resampling="BILINEAR"):
    import requests, tifffile
    url = "https://sh.dataspace.copernicus.eu/api/v1/process"
    payload = {
        "input": {
            "bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{
                "type": "sentinel-1-grd",
                "dataFilter": {
                    "timeRange": {"from": f"{date_str}T00:00:00Z", "to": f"{date_str}T23:59:59Z"},
                    "acquisitionMode": "IW",
                    "polarization": "DV",
                    "orbitDirection": direction.upper(),
                    "relativeOrbit": rel_orbit,
                },
                "processing": {
                    "orthorectify": True,
                    "demInstance": "COPERNICUS",
                    "backCoeff": "GAMMA0_ELLIPSOID",
                    "speckleFilter": {"isActive": False},
                    "upsampling": resampling,
                    "downsampling": resampling,
                },
            }],
        },
        "evalscript": EVALSCRIPT,
        "output": {
            "resx": resolution / 111320,
            "resy": resolution / 111320,
            "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}],
        },
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    if resp.status_code != 200:
        print(f"    SH {resp.status_code}: {resp.text[:200]}", flush=True)
        return None
    arr = tifffile.imread(io.BytesIO(resp.content))
    return arr.astype(np.float32)


# ── Tank registry (inline from tank_registry.py) ────────────────────────────

import subprocess

def load_tanks(terminal_id):
    """Return list of dicts with id, lat, lon, radius_m, h_max_m."""
    r = subprocess.run(
        ["python3", "-c",
         f"import sys; sys.path.insert(0,'scripts'); from tank_registry import TERMINALS; "
         f"t=[x for x in TERMINALS if x['id']=='{terminal_id}']; "
         f"[print(tk['id'],tk['lat'],tk['lon'],tk['radius_m'],tk['h_max_m']) for tk in t[0]['tanks']]"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    tanks = []
    for line in r.stdout.strip().splitlines():
        parts = line.split()
        tanks.append({
            "id": parts[0], "lat": float(parts[1]), "lon": float(parts[2]),
            "radius_m": float(parts[3]), "h_max_m": float(parts[4]),
        })
    return tanks


# ── Geometry helpers ─────────────────────────────────────────────────────────

def bbox_for_tanks(tanks, buffer_m=BUFFER_M):
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    buf  = buffer_m / 111320
    return [min(lons)-buf, min(lats)-buf, max(lons)+buf, max(lats)+buf]


def ring_pixels(arr, cx, cy, r_in_px, r_out_px, far_az, sector_half=SECTOR_HALF):
    """Return linear values in ring sector (far side)."""
    vals = []
    imax = int(r_out_px) + 2
    for dy in range(-imax, imax+1):
        for dx in range(-imax, imax+1):
            dist = math.sqrt(dx*dx + dy*dy)
            if not (r_in_px <= dist <= r_out_px):
                continue
            az = math.degrees(math.atan2(dx, -dy)) % 360
            if abs((az - far_az + 180) % 360 - 180) > sector_half:
                continue
            pr, pc = int(cy+dy), int(cx+dx)
            if 0 <= pr < arr.shape[0] and 0 <= pc < arr.shape[1]:
                v = arr[pr, pc]
                if np.isfinite(v) and v > 0:
                    vals.append(float(v))
    return vals


def ring_metrics(arr, tank, bbox, res_m, los_az):
    x0, y0, x1, y1 = bbox
    cx = (tank["lon"] - x0) / (x1 - x0) * arr.shape[1]
    cy = (y1 - tank["lat"]) / (y1 - y0) * arr.shape[0]
    r_in  = RING_IN_M  / res_m
    r_out = RING_OUT_M / res_m

    far_az  = (los_az + 180) % 360
    near_az = los_az % 360

    # sectors s6 / s7 (non-overlapping halves of far sector)
    far_vals  = ring_pixels(arr, cx, cy, r_in, r_out, far_az)
    near_vals = ring_pixels(arr, cx, cy, r_in, r_out, near_az)

    # far ring → ring15_far
    if not far_vals:
        ring15_far = float("nan")
    else:
        ring15_far = 10 * math.log10(float(np.mean(far_vals)))

    # near ring → ring15_near
    if not near_vals:
        ring15_near = float("nan")
    else:
        ring15_near = 10 * math.log10(float(np.mean(near_vals)))

    # radial profile through tank centre (for R_pos_m, R_max_dB, peak_width_m)
    r_probe = tank["radius_m"] * 1.5 / res_m
    profile = []
    for r_px in np.linspace(0, r_probe, 60):
        r = r_px * res_m
        ax = math.cos(math.radians(far_az))
        ay = -math.sin(math.radians(far_az))
        pr = int(cy + ay * r_px)
        pc = int(cx + ax * r_px)
        if 0 <= pr < arr.shape[0] and 0 <= pc < arr.shape[1]:
            v = arr[pr, pc]
            profile.append((r, float(v) if v > 0 else float("nan")))

    vals_np  = np.array([p[1] for p in profile])
    r_np     = np.array([p[0] for p in profile])
    valid    = np.isfinite(vals_np)
    pixel_valid_frac = float(valid.mean()) if len(valid) else float("nan")

    if valid.sum() < 3:
        R_pos_m = float("nan")
        R_max_dB = float("nan")
        peak_width_m = float("nan")
    else:
        db_profile = 10 * np.log10(np.where(valid, vals_np, np.nan))
        peak_idx = int(np.nanargmax(db_profile))
        R_pos_m  = float(r_np[peak_idx])
        R_max_dB = float(db_profile[peak_idx])
        # half-power width
        half = R_max_dB - 3
        above = np.where(np.isfinite(db_profile) & (db_profile >= half))[0]
        if len(above) >= 2:
            peak_width_m = float(r_np[above[-1]] - r_np[above[0]])
        else:
            peak_width_m = float("nan")

    return ring15_far, ring15_near, R_pos_m, R_max_dB, peak_width_m, pixel_valid_frac


# ── Speckle / filter variants ────────────────────────────────────────────────

def apply_lee(arr, size):
    from scipy.ndimage import uniform_filter
    a = np.nan_to_num(arr.astype(np.float64))
    mean = uniform_filter(a, size=size)
    sq   = uniform_filter(a**2, size=size)
    var  = np.maximum(sq - mean**2, 0)
    nv   = np.nanmean(var) / (np.nanmean(mean)**2 + 1e-12)
    W    = var / (var + nv * mean**2 + 1e-12)
    return (mean + W * (a - mean)).astype(np.float32)


def apply_rlf5(arr):
    from scipy.ndimage import uniform_filter, sobel
    a  = np.nan_to_num(arr.astype(np.float64))
    sx = sobel(a, axis=1); sy = sobel(a, axis=0)
    edge = np.sqrt(sx**2+sy**2) > np.nanpercentile(np.sqrt(sx**2+sy**2), 75)
    return np.where(edge, uniform_filter(a, size=3), uniform_filter(a, size=5)).astype(np.float32)


def apply_frost5(arr, damping=2.0):
    from scipy.ndimage import uniform_filter
    a   = np.nan_to_num(arr.astype(np.float64))
    m   = uniform_filter(a, size=5)
    s2  = uniform_filter(a**2, size=5) - m**2
    cv2 = np.where(m > 0, s2 / (m**2 + 1e-12), 0)
    out = np.zeros_like(a)
    size = 5; half = size // 2
    padded = np.pad(a, half, mode="edge")
    for r in range(arr.shape[0]):
        for c in range(arr.shape[1]):
            patch = padded[r:r+size, c:c+size]
            d2    = np.sum((np.indices((size,size)) - half)**2, axis=0)
            w     = np.exp(-damping * cv2[r, c] * d2)
            w    /= w.sum() + 1e-12
            out[r, c] = (patch * w).sum()
    return out.astype(np.float32)


def apply_gamma_map5(arr):
    from scipy.ndimage import uniform_filter
    a   = np.nan_to_num(arr.astype(np.float64))
    m   = uniform_filter(a, size=5)
    s2  = np.maximum(uniform_filter(a**2, size=5) - m**2, 0)
    L   = 1.0
    cv2_noise = 1.0 / L
    cv2_x     = np.where(m > 0, s2 / (m**2 + 1e-12) - cv2_noise, 0)
    alpha = (1 + cv2_noise) / (np.maximum(cv2_x, 0) / (cv2_noise + 1e-12) + 1e-12)
    alpha = np.clip(alpha, 1, 50)
    result = (alpha * m + a) / (alpha + 1)
    return result.astype(np.float32)


def apply_quegan5(arr):
    from scipy.ndimage import uniform_filter
    a    = np.where(arr > 0, arr, np.nan)
    m5   = uniform_filter(np.nan_to_num(a), size=5)
    m5   = np.where(m5 > 0, m5, np.nan)
    r    = a / m5
    rs   = uniform_filter(np.nan_to_num(r), size=5)
    return (m5 * rs).astype(np.float32)


def apply_nlm(arr):
    try:
        from skimage.restoration import denoise_nl_means, estimate_sigma
        a = np.nan_to_num(arr.astype(np.float32))
        sig = float(estimate_sigma(a))
        return denoise_nl_means(a, h=0.8*sig, fast_mode=True, patch_size=5, patch_distance=6).astype(np.float32)
    except ImportError:
        return None


def apply_bm3d(arr):
    try:
        import bm3d
        a   = np.nan_to_num(arr.astype(np.float32))
        sig = float(np.nanstd(a)) * 0.1
        return bm3d.bm3d(a, sigma_psd=sig).astype(np.float32)
    except ImportError:
        return None


def coregister(arr_target, arr_ref):
    if arr_ref is None:
        return arr_target
    try:
        from skimage.registration import phase_cross_correlation
        shift, _, _ = phase_cross_correlation(
            np.nan_to_num(arr_ref), np.nan_to_num(arr_target), upsample_factor=20
        )
        dy, dx = int(round(shift[0])), int(round(shift[1]))
        return np.roll(np.roll(arr_target, dy, axis=0), dx, axis=1).astype(np.float32)
    except Exception:
        from numpy.fft import fft2, ifft2
        f1 = fft2(np.nan_to_num(arr_ref))
        f2 = fft2(np.nan_to_num(arr_target))
        cross = f1 * np.conj(f2)
        corr  = np.real(ifft2(cross / (np.abs(cross) + 1e-12)))
        shift = np.unravel_index(np.argmax(corr), corr.shape)
        dy = shift[0] if shift[0] < arr_target.shape[0]//2 else shift[0] - arr_target.shape[0]
        dx = shift[1] if shift[1] < arr_target.shape[1]//2 else shift[1] - arr_target.shape[1]
        return np.roll(np.roll(arr_target, dy, axis=0), dx, axis=1).astype(np.float32)


# ── PNG check output ─────────────────────────────────────────────────────────

def save_png_check(arr_dict, date_str, terminal, tanks, bbox, los_az, out_dir):
    """Save grid PNG: rows=tanks, cols=variants. arr_dict: variant→arr (linear float32)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors
    except ImportError:
        print("  matplotlib not available, skipping PNG", flush=True)
        return

    SHOW_VARS = ["baseline"] + [v for v in VARIANTS if v != "baseline"]

    x0, y0, x1, y1 = bbox
    n_tanks = min(len(tanks), 8)
    n_vars  = len(SHOW_VARS)

    fig, axes = plt.subplots(n_tanks, n_vars, figsize=(2.5*n_vars, 2.5*n_tanks))
    if n_tanks == 1:
        axes = axes[np.newaxis, :]
    if n_vars == 1:
        axes = axes[:, np.newaxis]

    # crop per tank: ±2×radius
    for ti, tank in enumerate(tanks[:n_tanks]):
        cx_deg = (tank["lon"] - x0) / (x1 - x0)
        cy_deg = (y1 - tank["lat"]) / (y1 - y0)
        for vi, vname in enumerate(SHOW_VARS):
            arr = arr_dict.get(vname)
            ax  = axes[ti, vi]
            if arr is None:
                ax.axis("off")
                ax.set_title(vname, fontsize=5)
                continue
            cx_px = cx_deg * arr.shape[1]
            cy_px = cy_deg * arr.shape[0]
            half  = int(tank["radius_m"] * 2 / 10) + 5
            r1 = max(0, int(cy_px)-half); r2 = min(arr.shape[0], int(cy_px)+half)
            c1 = max(0, int(cx_px)-half); c2 = min(arr.shape[1], int(cx_px)+half)
            crop = arr[r1:r2, c1:c2]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                db_crop = 10 * np.log10(np.where(crop > 0, crop, np.nan))
            vmin, vmax = np.nanpercentile(db_crop, [2, 98]) if np.any(np.isfinite(db_crop)) else (-5, 15)
            ax.imshow(db_crop, cmap="gray", vmin=vmin, vmax=vmax, origin="upper", aspect="auto")
            if ti == 0:
                ax.set_title(vname, fontsize=5, rotation=45, ha="left")
            if vi == 0:
                ax.set_ylabel(tank["id"], fontsize=5)
            ax.set_xticks([]); ax.set_yticks([])

    plt.suptitle(f"{terminal} {date_str} — filter comparison", fontsize=8)
    plt.tight_layout()
    out_path = out_dir / f"png_check_{date_str}_{terminal}.png"
    plt.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"  PNG saved: {out_path.name}", flush=True)


# ── Main ─────────────────────────────────────────────────────────────────────

OUT_COLS = [
    "terminal","tank_id","date","variant",
    "ring15_far","ring15_near","R_pos_m","R_max_dB","peak_width_m","pixel_valid_frac",
]


def process_terminal(terminal_id, config, token, out_rows, crops_dict, var_npz_buffers):
    rel_orbit  = config["rel_orbit"]
    direction  = config["direction"]
    los_az     = config["los_azimuth"]
    dates      = config["dates"]
    check_dates = set(PNG_CHECK.get(terminal_id, []))

    tanks = load_tanks(terminal_id)
    if not tanks:
        print(f"  [WARN] no tanks loaded for {terminal_id}", flush=True)
        return

    bbox10 = bbox_for_tanks(tanks, BUFFER_M)
    bbox5  = bbox10  # same geographic bbox, different resolution

    print(f"\n  Terminal: {terminal_id}  tanks={len(tanks)}  dates={len(dates)}", flush=True)

    # per-date 10m arrays cache (for mean2/mean3/coreg)
    cache10  = {}
    cache5b  = {}
    cache5c  = {}

    for di, date in enumerate(dates):
        print(f"  [{di+1}/{len(dates)}] {date}", flush=True)

        arr10 = sh_request(token, bbox10, date, rel_orbit, direction, resolution=10, resampling="BILINEAR")
        if arr10 is None:
            print(f"    no data for {date}", flush=True)
            continue
        cache10[date] = arr10

        arr5b = sh_request(token, bbox5, date, rel_orbit, direction, resolution=5, resampling="BILINEAR")
        arr5c = sh_request(token, bbox5, date, rel_orbit, direction, resolution=5, resampling="BICUBIC")
        cache5b[date] = arr5b
        cache5c[date] = arr5c

        # store raw crops in crops_dict
        key = f"{terminal_id}__{date}"
        crops_dict[f"{key}__10m"] = arr10.copy()
        if arr5b is not None:
            crops_dict[f"{key}__5m_bilinear"] = arr5b.copy()

        # neighbour arrays for multi-temporal variants
        prev_d = [d for d in sorted(cache10) if d < date]
        next_d = [d for d in dates if d > date and d in cache10]

        def get10(d):
            if d not in cache10:
                a = sh_request(token, bbox10, d, rel_orbit, direction, resolution=10)
                if a is not None:
                    cache10[d] = a
            return cache10.get(d)

        prev1 = get10(prev_d[-1]) if prev_d else None
        prev2 = get10(prev_d[-2]) if len(prev_d) >= 2 else None
        next1 = get10(next_d[0])  if next_d else None

        nb2 = [a for a in [prev1, next1] if a is not None]
        nb3 = [a for a in [prev2, prev1, next1] if a is not None]

        arr_mean2 = np.mean([arr10] + nb2[:1], axis=0).astype(np.float32) if nb2 else arr10
        arr_mean3 = np.mean([arr10] + nb3[:2], axis=0).astype(np.float32) if len(nb3) >= 2 else arr_mean2

        ref = prev1 if prev1 is not None else arr10
        arr_coreg     = coregister(arr10, ref)
        arr_cm2       = np.mean([arr_coreg] + [coregister(a, ref) for a in nb2[:1]], axis=0).astype(np.float32) if nb2 else arr_coreg
        arr_cm3       = np.mean([arr_coreg] + [coregister(a, ref) for a in nb3[:2]], axis=0).astype(np.float32) if len(nb3) >= 2 else arr_cm2

        arr_lee5      = apply_lee(arr10, 5)
        arr_lee7      = apply_lee(arr10, 7)
        arr_rlf5      = apply_rlf5(arr10)
        arr_frost5    = apply_frost5(arr10)
        arr_gmap5     = apply_gamma_map5(arr10)
        arr_quegan5   = apply_quegan5(arr10)
        arr_nlm       = apply_nlm(arr10)
        arr_bm3d      = apply_bm3d(arr10)

        variant_arrays = {
            "lee5":            arr_lee5,
            "lee7":            arr_lee7,
            "rlf5":            arr_rlf5,
            "frost5":          arr_frost5,
            "gamma_map5":      arr_gmap5,
            "quegan5":         arr_quegan5,
            "nlm":             arr_nlm,
            "bm3d":            arr_bm3d,
            "mean2":           arr_mean2,
            "mean3":           arr_mean3,
            "coreg":           arr_coreg,
            "resamp5_bilinear": arr5b,
            "resamp5_bicubic":  arr5c,
            "coreg_mean2":     arr_cm2,
            "coreg_mean3":     arr_cm3,
        }

        # accumulate per-variant npz buffers
        for vname, varr in variant_arrays.items():
            if varr is not None:
                vkey = f"{terminal_id}__{vname}__{date}"
                var_npz_buffers[vkey] = varr.copy()

        # compute metrics per tank per variant
        for tank in tanks:
            # baseline = 10m no filter
            rf, rn, rp, rm, pw, pvf = ring_metrics(arr10, tank, bbox10, 10, los_az)
            out_rows.append(dict(
                terminal=terminal_id, tank_id=tank["id"], date=date, variant="baseline",
                ring15_far=_f(rf), ring15_near=_f(rn), R_pos_m=_f(rp),
                R_max_dB=_f(rm), peak_width_m=_f(pw), pixel_valid_frac=_f(pvf),
            ))

            for vname, varr in variant_arrays.items():
                if varr is None:
                    out_rows.append(dict(
                        terminal=terminal_id, tank_id=tank["id"], date=date, variant=vname,
                        ring15_far="", ring15_near="", R_pos_m="", R_max_dB="",
                        peak_width_m="", pixel_valid_frac="",
                    ))
                    continue
                res_m = 5 if "resamp5" in vname else 10
                bb    = bbox5 if res_m == 5 else bbox10
                rf, rn, rp, rm, pw, pvf = ring_metrics(varr, tank, bb, res_m, los_az)
                out_rows.append(dict(
                    terminal=terminal_id, tank_id=tank["id"], date=date, variant=vname,
                    ring15_far=_f(rf), ring15_near=_f(rn), R_pos_m=_f(rp),
                    R_max_dB=_f(rm), peak_width_m=_f(pw), pixel_valid_frac=_f(pvf),
                ))

        # PNG check
        if date in check_dates:
            arr_for_png = {"baseline": arr10}
            arr_for_png.update({k: v for k, v in variant_arrays.items() if v is not None})
            save_png_check(arr_for_png, date, terminal_id, tanks, bbox10, los_az, OUT_DIR)

        # evict old cache entries (keep last 3)
        old = [d for d in sorted(cache10) if d < date][:-2]
        for d in old:
            cache10.pop(d, None)
            cache5b.pop(d, None)
            cache5c.pop(d, None)


def _f(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return f"{v:.4f}"


def quality_report(out_rows):
    from collections import defaultdict, Counter
    nn   = defaultdict(int)
    tot  = defaultdict(int)
    for row in out_rows:
        key = (row["terminal"], row["variant"])
        tot[key] += 1
        if row["ring15_far"] not in ("", "nan"):
            nn[key] += 1
    print("\n=== Quality report: non-null ring15_far per (terminal, variant) ===", flush=True)
    print(f"{'terminal':<15}{'variant':<22}{'nn':>6}{'tot':>7}{'frac':>8}", flush=True)
    for (term, var) in sorted(tot.keys()):
        t = tot[(term, var)]; n = nn[(term, var)]
        frac = n/t if t else 0
        print(f"  {term:<13}{var:<22}{n:>6}{t:>7}{frac:>8.3f}", flush=True)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M UTC")
    print(f"=== P7 image_variants_pilot — {ts} ===\n", flush=True)

    print("Installing optional deps...", flush=True)
    os.system("pip install scikit-image tifffile scipy matplotlib -q")
    try:
        os.system("pip install bm3d -q")
    except Exception:
        pass

    import importlib
    import tifffile  # noqa ensure available after pip

    print("Auth SentinelHub...", flush=True)
    token = sh_token()
    print("  OK\n", flush=True)

    out_rows       = []
    crops_dict     = {}
    var_npz_buffers = {}

    for terminal_id, config in PILOT_SCENES.items():
        process_terminal(terminal_id, config, token, out_rows, crops_dict, var_npz_buffers)

    # ── Save crops_pilot.npz ──────────────────────────────────────────────────
    crops_path = OUT_DIR / "crops_pilot.npz"
    print(f"\nSaving {crops_path.name} ({len(crops_dict)} arrays)...", flush=True)
    np.savez_compressed(crops_path, **{k.replace("/","_"): v for k, v in crops_dict.items()})

    # ── Save per-variant .npz ─────────────────────────────────────────────────
    from collections import defaultdict
    by_variant = defaultdict(dict)
    for k, v in var_npz_buffers.items():
        parts = k.split("__")
        vname = parts[1]
        by_variant[vname][k] = v

    for vname, arrays in by_variant.items():
        vpath = OUT_DIR / f"variant_{vname}.npz"
        np.savez_compressed(vpath, **{k.replace("/","_"): v for k, v in arrays.items()})
        print(f"  saved variant_{vname}.npz ({len(arrays)} arrays)", flush=True)

    # ── Save CSV ──────────────────────────────────────────────────────────────
    csv_path = OUT_DIR / "image_variants_pilot.csv"
    print(f"\nSaving {csv_path.name} ({len(out_rows)} rows)...", flush=True)
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLS)
        w.writeheader()
        w.writerows(out_rows)

    quality_report(out_rows)
    print(f"\nDONE", flush=True)


if __name__ == "__main__":
    main()
