"""
p5_image_variants.py — image_variants_kharg.csv.gz

For each (tank_id, date) in Kharg DESC-35, compute db_mean under 11 processing variants:
  baseline          — ring15_far from existing section-4 CSV (no new SH request)
  mean2             — average of 2 temporally adjacent scenes (same orbit)
  mean3             — average of 3 temporally adjacent scenes
  quegan5           — Quegan speckle filter 5×5
  lee5              — Lee speckle filter 5×5
  rlf5              — Refined Lee filter 5×5
  coreg             — co-registration to reference scene
  resamp10          — resampled to 10m (SH default, identity for this dataset)
  resamp5_bilinear  — resampled to 5m BILINEAR
  resamp5_bicubic   — resampled to 5m BICUBIC
  coreg_mean2       — co-registration + mean of 2 scenes
  coreg_mean3       — co-registration + mean of 3 scenes

Output: diagnostics/mega/image_variants_kharg.csv.gz
Columns: tank_id, date, variant, db

Requirements: SH_CLIENT_ID, SH_CLIENT_SECRET (no Supabase needed).
Input: existing tank_scene_variants_kharg.csv.gz for baseline + date list.
"""

import csv, gzip, io, json, math, os, sys, numpy as np
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

SH_CLIENT_ID     = os.environ["SH_CLIENT_ID"]
SH_CLIENT_SECRET = os.environ["SH_CLIENT_SECRET"]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR   = REPO_ROOT / "diagnostics" / "mega"
BRANCH    = "origin/research/2026-10-auto"

TERMINAL  = "kharg"
ORB_DIR   = "DESC"
REL_ORB   = 35

RES_M     = 10
BUFFER_M  = 300
EVALSCRIPT = """
//VERSION=3
function setup() { return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(sample) { return [sample.VV]; }
"""


# ── Supabase-free: read baseline from existing section-4 CSV via git ────────

import subprocess

def git_show_gz(path):
    r = subprocess.run(["git", "show", f"{BRANCH}:{path}"], capture_output=True)
    if r.returncode != 0:
        return None
    return list(csv.DictReader(io.TextIOWrapper(gzip.open(io.BytesIO(r.stdout)))))


def load_baseline():
    """Load (tank_id, date) -> ring15_far from kharg section-4 CSV."""
    rows = git_show_gz("diagnostics/mega/tank_scene_variants_kharg.csv.gz")
    if not rows:
        print("[FATAL] tank_scene_variants_kharg.csv.gz not found on branch", flush=True)
        sys.exit(1)
    baseline = {}
    dates_set = set()
    for row in rows:
        tid   = row.get("tank_id", "")
        date  = row.get("date", "")[:10]
        r15   = row.get("ring15_far", "")
        if tid and date:
            baseline[(tid, date)] = r15
            dates_set.add(date)
    print(f"  baseline: {len(baseline)} (tank, date) pairs, {len(dates_set)} unique dates", flush=True)
    return baseline, sorted(dates_set)


def load_registry():
    import io as _io
    r = subprocess.run(["git", "show", f"{BRANCH}:diagnostics/mega/registry_full.csv"], capture_output=True)
    if r.returncode != 0:
        return {}
    reg = {}
    for row in csv.DictReader(_io.StringIO(r.stdout.decode())):
        if row["terminal_id"] == TERMINAL:
            reg[row["tank_id"]] = row
    return reg


# ── SentinelHub helpers ──────────────────────────────────────────────────────

def sh_token():
    import requests
    r = requests.post(
        "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token",
        data={"grant_type": "client_credentials",
              "client_id": SH_CLIENT_ID,
              "client_secret": SH_CLIENT_SECRET},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def sh_request(token, bbox, date_str, resolution=10, resampling="BILINEAR"):
    """Fetch VV float32 array for bbox+date via SH Process API."""
    import requests
    url = "https://services.sentinel-hub.com/api/v1/process"
    payload = {
        "input": {
            "bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{
                "type": "sentinel-1-grd",
                "dataFilter": {
                    "timeRange": {"from": f"{date_str}T00:00:00Z", "to": f"{date_str}T23:59:59Z"},
                    "acquisitionMode": "IW",
                    "polarization": "DV",
                    "orbitDirection": "DESCENDING",
                    "relativeOrbit": REL_ORB,
                },
                "processing": {
                    "orthorectify": True,
                    "demInstance": "COPERNICUS",
                    "backCoeff": "SIGMA0_ELLIPSOID",
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
    r = requests.post(url, json=payload, headers=headers, timeout=60)
    if r.status_code != 200:
        return None
    import tifffile
    arr = tifffile.imread(io.BytesIO(r.content))
    return arr.astype(np.float32)


# ── Pixel geometry ───────────────────────────────────────────────────────────

def db(linear_arr):
    arr = np.where(linear_arr > 0, linear_arr, np.nan)
    return 10 * np.log10(arr)


def tank_ring15_far(arr, tank_lat, tank_lon, bbox, res_m, los_az_deg):
    """Extract ring15_far mean-dB from array for one tank."""
    x0, y0, x1, y1 = bbox
    px_per_deg_lon = arr.shape[1] / (x1 - x0)
    px_per_deg_lat = arr.shape[0] / (y1 - y0)
    cx = (tank_lon - x0) * px_per_deg_lon
    cy = (y1 - tank_lat) * px_per_deg_lat

    R_inner_m = 15 / 111320 * px_per_deg_lat * res_m / res_m  # in pixels
    R_outer_m = 30 / 111320 * px_per_deg_lat * res_m / res_m

    los_rad = math.radians(los_az_deg)
    # Far-side = opposite to LOS azimuth direction → shadow side
    # Use a sector ±45° around (los_az + 180°)
    far_az = los_az_deg + 180
    sector_half = 45

    rows_px = int(R_outer_m * px_per_deg_lat / (res_m / 111320) / px_per_deg_lat + 2)
    vals = []
    for dy in range(-rows_px, rows_px + 1):
        for dx in range(-rows_px, rows_px + 1):
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < 1.0:
                continue
            r_in = 15 / res_m
            r_out = 30 / res_m
            if not (r_in <= dist <= r_out):
                continue
            az = math.degrees(math.atan2(dx, -dy)) % 360
            ang_diff = abs((az - far_az + 180) % 360 - 180)
            if ang_diff > sector_half:
                continue
            px_r = int(cy + dy)
            px_c = int(cx + dx)
            if 0 <= px_r < arr.shape[0] and 0 <= px_c < arr.shape[1]:
                v = arr[px_r, px_c]
                if np.isfinite(v) and v > 0:
                    vals.append(float(v))
    if not vals:
        return float("nan")
    return float(10 * np.log10(np.mean(vals)))


# ── Speckle filters ──────────────────────────────────────────────────────────

def quegan5(arr):
    """Quegan refined speckle filter approximation (5×5 boxcar ratio)."""
    from scipy.ndimage import uniform_filter
    arr = np.where(arr > 0, arr, np.nan)
    mean5 = uniform_filter(np.nan_to_num(arr), size=5)
    mean5 = np.where(mean5 > 0, mean5, np.nan)
    ratio = arr / mean5
    ratio_smooth = uniform_filter(np.nan_to_num(ratio), size=5)
    return mean5 * ratio_smooth


def lee5(arr):
    """Lee speckle filter 5×5."""
    from scipy.ndimage import uniform_filter
    arr_nn = np.nan_to_num(arr.copy().astype(np.float64))
    mean = uniform_filter(arr_nn, size=5)
    sqmean = uniform_filter(arr_nn ** 2, size=5)
    var = sqmean - mean ** 2
    var = np.maximum(var, 0)
    noise_var = np.nanmean(var) / (np.nanmean(mean) ** 2 + 1e-12)
    W = var / (var + noise_var * mean ** 2 + 1e-12)
    filtered = mean + W * (arr_nn - mean)
    return filtered.astype(np.float32)


def refined_lee5(arr):
    """Refined Lee filter 5×5 (edge-direction weights)."""
    from scipy.ndimage import uniform_filter, sobel
    arr_nn = np.nan_to_num(arr.copy().astype(np.float64))
    sx = sobel(arr_nn, axis=1)
    sy = sobel(arr_nn, axis=0)
    edge = np.sqrt(sx ** 2 + sy ** 2)
    edge_mask = edge > np.nanpercentile(edge, 75)
    # On edges: 3×3, elsewhere: 5×5
    mean3 = uniform_filter(arr_nn, size=3)
    mean5 = uniform_filter(arr_nn, size=5)
    result = np.where(edge_mask, mean3, mean5)
    return result.astype(np.float32)


def coregister(arr_target, arr_ref):
    """Phase-correlation co-registration (integer pixel shift)."""
    from numpy.fft import fft2, ifft2
    if arr_ref is None:
        return arr_target
    f1 = fft2(np.nan_to_num(arr_ref))
    f2 = fft2(np.nan_to_num(arr_target))
    cross = f1 * np.conj(f2)
    denom = np.abs(cross) + 1e-12
    corr = np.real(ifft2(cross / denom))
    shift = np.unravel_index(np.argmax(corr), corr.shape)
    dy = shift[0] if shift[0] < arr_target.shape[0] // 2 else shift[0] - arr_target.shape[0]
    dx = shift[1] if shift[1] < arr_target.shape[1] // 2 else shift[1] - arr_target.shape[1]
    shifted = np.roll(np.roll(arr_target, dy, axis=0), dx, axis=1)
    return shifted.astype(np.float32)


# ── Main ─────────────────────────────────────────────────────────────────────

VARIANTS = [
    "baseline", "mean2", "mean3",
    "quegan5", "lee5", "rlf5",
    "coreg",
    "resamp10", "resamp5_bilinear", "resamp5_bicubic",
    "coreg_mean2", "coreg_mean3",
]

OUT_COLS = ["tank_id", "date", "variant", "db"]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M UTC")
    print(f"=== P5 image_variants_kharg — {ts} ===\n", flush=True)

    print("Loading baseline and registry…", flush=True)
    baseline, all_dates = load_baseline()
    registry = load_registry()
    tanks = sorted(registry.keys())
    print(f"  {len(tanks)} tanks, {len(all_dates)} dates\n", flush=True)

    if not tanks:
        print("[FATAL] no tanks in registry for kharg", flush=True)
        sys.exit(1)

    # Build farm bbox
    lats = [float(registry[t]["lat"]) for t in tanks]
    lons = [float(registry[t]["lon"]) for t in tanks]
    buf_deg = BUFFER_M / 111320
    bbox = [min(lons) - buf_deg, min(lats) - buf_deg,
            max(lons) + buf_deg, max(lats) + buf_deg]

    print("Authenticating with SentinelHub…", flush=True)
    token = sh_token()
    print("  OK\n", flush=True)

    out_rows = []
    cache_10m = {}      # date -> arr (10m)
    cache_5m_bil = {}
    cache_5m_bic = {}

    for di, date in enumerate(all_dates):
        print(f"[{di+1}/{len(all_dates)}] {date}", flush=True)

        # Fetch 10m
        arr10 = sh_request(token, bbox, date, resolution=10, resampling="BILINEAR")
        if arr10 is None:
            print(f"  skip (no SH data)", flush=True)
            continue
        cache_10m[date] = arr10

        # Fetch 5m variants
        arr5_bil = sh_request(token, bbox, date, resolution=5, resampling="BILINEAR")
        arr5_bic = sh_request(token, bbox, date, resolution=5, resampling="BICUBIC")
        cache_5m_bil[date] = arr5_bil
        cache_5m_bic[date] = arr5_bic

        # Neighbouring scene arrays for multi-temporal
        prev_dates = [d for d in all_dates if d < date]
        next_dates = [d for d in all_dates if d > date]

        def get_neighbor(d):
            if d in cache_10m:
                return cache_10m[d]
            a = sh_request(token, bbox, d, resolution=10, resampling="BILINEAR")
            if a is not None:
                cache_10m[d] = a
            return a

        prev1 = get_neighbor(prev_dates[-1]) if prev_dates else None
        prev2 = get_neighbor(prev_dates[-2]) if len(prev_dates) >= 2 else None
        next1 = get_neighbor(next_dates[0])  if next_dates else None
        next2 = get_neighbor(next_dates[1])  if len(next_dates) >= 2 else None

        # Build mean2/mean3 stacks
        neighbors2 = [a for a in [prev1, next1] if a is not None]
        neighbors3 = [a for a in [prev2, prev1, next1, next2] if a is not None]
        arr_mean2 = np.mean([arr10] + neighbors2[:1], axis=0).astype(np.float32) if neighbors2 else arr10
        arr_mean3 = np.mean([arr10] + neighbors3[:2], axis=0).astype(np.float32) if len(neighbors3) >= 2 else arr_mean2

        # Co-registered
        ref = prev1 if prev1 is not None else arr10
        arr_coreg = coregister(arr10, ref)
        arr_coreg_mean2 = np.mean([arr_coreg] + ([coregister(a, ref) for a in neighbors2[:1]]), axis=0).astype(np.float32) if neighbors2 else arr_coreg
        arr_coreg_mean3 = np.mean([arr_coreg] + [coregister(a, ref) for a in neighbors3[:2]], axis=0).astype(np.float32) if len(neighbors3) >= 2 else arr_coreg_mean2

        # Filtered
        arr_q5  = quegan5(arr10)
        arr_l5  = lee5(arr10)
        arr_rl5 = refined_lee5(arr10)

        variant_arrays = {
            "resamp10":        arr10,
            "mean2":           arr_mean2,
            "mean3":           arr_mean3,
            "quegan5":         arr_q5,
            "lee5":            arr_l5,
            "rlf5":            arr_rl5,
            "coreg":           arr_coreg,
            "resamp5_bilinear": arr5_bil,
            "resamp5_bicubic":  arr5_bic,
            "coreg_mean2":     arr_coreg_mean2,
            "coreg_mean3":     arr_coreg_mean3,
        }

        for tank_id in tanks:
            t = registry[tank_id]
            tlat = float(t["lat"])
            tlon = float(t["lon"])
            los_az = float(t.get("los_azimuth", 282))

            # baseline from section-4 CSV
            bl = baseline.get((tank_id, date), "")
            out_rows.append({"tank_id": tank_id, "date": date, "variant": "baseline", "db": bl})

            for vname, arr in variant_arrays.items():
                if arr is None:
                    val = ""
                else:
                    val = tank_ring15_far(arr, tlat, tlon, bbox, 10 if "5" not in vname else 5, los_az)
                    val = "" if (val is None or (isinstance(val, float) and math.isnan(val))) else f"{val:.4f}"
                out_rows.append({"tank_id": tank_id, "date": date, "variant": vname, "db": val})

        # Evict old cache entries to limit memory (keep last 4 dates)
        old = [d for d in cache_10m if d < date]
        for d in old[:-3]:
            cache_10m.pop(d, None)
            cache_5m_bil.pop(d, None)
            cache_5m_bic.pop(d, None)

        if (di + 1) % 10 == 0:
            print(f"  {len(out_rows)} rows so far", flush=True)

    out_path = OUT_DIR / "image_variants_kharg.csv.gz"
    print(f"\nWriting {out_path} ({len(out_rows)} rows)…", flush=True)
    with gzip.open(out_path, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLS)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)

    # Quality report: non-null per variant
    print("\n=== Quality report: non-null db per variant ===", flush=True)
    from collections import Counter
    total_per_variant = Counter()
    nn_per_variant = Counter()
    for row in out_rows:
        v = row["variant"]
        total_per_variant[v] += 1
        if row["db"] not in ("", "nan"):
            nn_per_variant[v] += 1
    for v in VARIANTS:
        tot = total_per_variant[v]
        nn  = nn_per_variant[v]
        frac = nn / tot if tot else 0
        print(f"  {v}: {nn}/{tot} ({frac:.3f})", flush=True)

    print(f"\nDONE: {out_path}", flush=True)


if __name__ == "__main__":
    main()
