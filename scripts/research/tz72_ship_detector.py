"""
ТЗ-7.2 — Kharg pier ship detector: water whitelist + background subtraction.
Research script. Outputs raw CSV + PNG overlays. No conclusions.

Usage (via GHA workflow_dispatch only):
  python scripts/research/tz72_ship_detector.py --start 2024-01-01 --end 2024-12-31
  python scripts/research/tz72_ship_detector.py --dates 2024-03-15,2024-04-01

Writes to: data/research/tz72/
  detections.csv       — one row per pass
  images/YYYY-MM-DD_*.png — raw SAR + overlay per pass
"""

import os, sys, json, csv, argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    MimeType, bbox_to_dimensions, MosaickingOrder
)

# ── SentinelHub config ────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or sys.exit("FATAL: CLIENT_ID not set")
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or sys.exit("FATAL: CLIENT_SECRET not set")
config.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url      = "https://sh.dataspace.copernicus.eu"

# ── AOI: pier / south anchorage zone only (tighter than prod bbox) ─────────────
# Prod bbox: center (50.32, 29.24) ± 0.27 lon, -0.15/+0.27 lat
# Pier/south anchorage: focused on export pier and staging area south of island
PIER_BBOX = BBox(
    bbox=[50.14, 29.10, 50.52, 29.38],  # lon_min, lat_min, lon_max, lat_max
    crs=CRS.WGS84
)
RESOLUTION = 10  # finer than prod 25m for pier detail
SIZE = bbox_to_dimensions(PIER_BBOX, resolution=RESOLUTION)

# ── Kharg Island polygon (pixels to mask — same logic as prod) ─────────────────
ISLAND_BBOX_GEO = (50.285, 29.185, 50.375, 29.300)  # lon0, lat0, lon1, lat1

# ── Known fixed structures: platforms + SPM buoy coords ───────────────────────
# Copied from prod ship_backfill.py + pier-specific structures
FIXED_STRUCTURES = [
    (50.14, 29.38), (50.10, 29.35), (50.08, 29.42), (50.05, 29.30),
    (50.18, 29.48), (50.22, 29.50), (50.28, 29.52), (50.35, 29.55),
    (50.40, 29.50), (50.45, 29.45), (50.15, 29.20), (50.12, 29.15),
    # Pier structures (approximate): T-jetty head, SPM-1, SPM-2
    (50.33, 29.17), (50.34, 29.16), (50.30, 29.13),
]
STRUCT_BUFFER_PX = 6  # slightly larger buffer than prod (10m/px)

# ── Output ─────────────────────────────────────────────────────────────────────
OUT_DIR = Path(__file__).parents[2] / "data" / "research" / "tz72"
IMG_DIR = OUT_DIR / "images"
CSV_PATH = OUT_DIR / "detections.csv"
BG_PATH  = OUT_DIR / "background.npy"

evalscript = """
//VERSION=3
function setup() {
  return { input: ["VV"], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  var db = 10 * Math.log10(s.VV + 1e-10);
  var norm = Math.max(0, Math.min(1, (db + 25) / 40));
  return [norm];
}
"""

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse", service_url="https://sh.dataspace.copernicus.eu"
)


def geo_to_px(lon, lat):
    px = int((lon - PIER_BBOX.min_x) / (PIER_BBOX.max_x - PIER_BBOX.min_x) * SIZE[0])
    py = int((PIER_BBOX.max_y - lat) / (PIER_BBOX.max_y - PIER_BBOX.min_y) * SIZE[1])
    return px, py


def build_static_mask():
    h, w = SIZE[1], SIZE[0]
    mask = np.zeros((h, w), dtype=bool)
    # Island
    ix0, iy1 = geo_to_px(ISLAND_BBOX_GEO[0], ISLAND_BBOX_GEO[1])
    ix1, iy0 = geo_to_px(ISLAND_BBOX_GEO[2], ISLAND_BBOX_GEO[3])
    mask[max(0,iy0):min(h,iy1), max(0,ix0):min(w,ix1)] = True
    # Fixed structures
    for lon, lat in FIXED_STRUCTURES:
        px, py = geo_to_px(lon, lat)
        b = STRUCT_BUFFER_PX
        if 0 <= px < w and 0 <= py < h:
            mask[max(0,py-b):min(h,py+b), max(0,px-b):min(w,px+b)] = True
    return mask


STATIC_MASK = build_static_mask()


def fetch_pass(date_str: str) -> np.ndarray | None:
    request = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(date_str, date_str),
            mosaicking_order=MosaickingOrder.MOST_RECENT,
            other_args={"dataFilter": {"polarization": "SV"}},
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=PIER_BBOX, size=SIZE, config=config,
    )
    try:
        data = request.get_data()
    except Exception as e:
        print(f"  API error: {e}")
        return None
    if not data or data[0] is None:
        return None
    raw = np.array(data[0])
    if raw.ndim == 3:
        arr = raw[:, :, 0]
    elif raw.ndim == 2:
        arr = raw
    else:
        arr = raw.reshape(SIZE[1], SIZE[0])
    return arr if arr.max() > 0.01 else None


def build_background(passes: list[np.ndarray]) -> np.ndarray:
    """Pixel-wise median across all reference passes (water background)."""
    stack = np.stack([p for p in passes if p is not None], axis=0)
    return np.median(stack, axis=0)


def build_water_whitelist(background: np.ndarray) -> np.ndarray:
    """
    Water whitelist: pixels where background value is below land-level threshold
    and not in the static mask.
    Returns boolean mask (True = water pixel, valid for detection).
    """
    water = (~STATIC_MASK) & (background < 0.60)
    return water


def detect_on_pass(arr: np.ndarray, background: np.ndarray, water_wl: np.ndarray):
    """
    Background subtraction detection.
    anomaly = (arr - background) / (background_std + 1e-6) where background_std
    is computed from the reference stack stored separately.
    Returns list of detections: [{cx_lon, cy_lat, length_m, area_px, anomaly_score}]
    """
    bg_std = np.load(str(BG_PATH).replace("background.npy", "background_std.npy"))
    anomaly = (arr - background) / (bg_std + 1e-6)
    # Apply water whitelist — only look for ships where water is expected
    anomaly_masked = np.where(water_wl, anomaly, 0.0)

    # Threshold on anomaly score (raw value, not tuned)
    ANOMALY_THRESH = 3.0  # sigma above water background
    binary = anomaly_masked > ANOMALY_THRESH
    labeled, n = ndimage.label(binary)

    detections = []
    h, w = arr.shape
    for i in range(1, n + 1):
        comp = labeled == i
        area = int(comp.sum())
        if area < 3 or area > 1000:
            continue
        ys, xs = np.where(comp)
        length_px = max(xs.max() - xs.min(), ys.max() - ys.min()) + 1
        length_m = length_px * RESOLUTION
        cx_px = int(xs.mean())
        cy_px = int(ys.mean())
        cx_lon = PIER_BBOX.min_x + cx_px / w * (PIER_BBOX.max_x - PIER_BBOX.min_x)
        cy_lat = PIER_BBOX.max_y - cy_px / h * (PIER_BBOX.max_y - PIER_BBOX.min_y)
        score  = float(anomaly_masked[ys, xs].mean())
        detections.append({
            "cx_lon": round(cx_lon, 5),
            "cy_lat": round(cy_lat, 5),
            "length_m": int(length_m),
            "area_px": area,
            "anomaly_score": round(score, 2),
        })
    return detections, anomaly_masked


def save_png(date_str: str, arr: np.ndarray, anomaly: np.ndarray, detections: list):
    """Save raw SAR image + anomaly overlay + detection bounding boxes."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as patches
    except ImportError:
        print("  matplotlib not available — skipping PNG")
        return

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(f"ТЗ-7.2 Kharg Pier — {date_str}", fontsize=12)

    # Raw SAR
    axes[0].imshow(arr, cmap="gray", vmin=0, vmax=1, aspect="auto")
    axes[0].set_title(f"Raw VV (norm) · {SIZE[0]}×{SIZE[1]}px @ {RESOLUTION}m")
    axes[0].axis("off")

    # Anomaly map
    im = axes[1].imshow(anomaly, cmap="hot", vmin=0, vmax=10, aspect="auto")
    axes[1].set_title(f"Anomaly (σ above water bg) · {len(detections)} detections")
    fig.colorbar(im, ax=axes[1], fraction=0.046, pad=0.04)

    h, w = arr.shape
    for d in detections:
        cx_px = int((d["cx_lon"] - PIER_BBOX.min_x) / (PIER_BBOX.max_x - PIER_BBOX.min_x) * w)
        cy_px = int((PIER_BBOX.max_y - d["cy_lat"]) / (PIER_BBOX.max_y - PIER_BBOX.min_y) * h)
        half  = max(5, d["length_m"] // RESOLUTION // 2)
        for ax in axes:
            rect = patches.Rectangle((cx_px - half, cy_px - half), half*2, half*2,
                                       linewidth=1, edgecolor="lime", facecolor="none")
            ax.add_patch(rect)
        axes[1].text(cx_px, cy_px - half - 3,
                     f"{d['length_m']}m σ{d['anomaly_score']:.1f}",
                     color="lime", fontsize=6, ha="center")

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    path = IMG_DIR / f"{date_str}_pier.png"
    plt.tight_layout()
    plt.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {path}")


def load_pass_dates(start: datetime, end: datetime) -> list[str]:
    data_file = Path(__file__).parents[2] / "data" / "data_daily.json"
    try:
        with open(data_file) as f:
            data = json.load(f)
        dates = []
        for d in data.get("dates", []):
            try:
                dt = datetime.strptime(d, "%Y-%m-%d")
                if start <= dt <= end:
                    dates.append(d)
            except Exception:
                continue
        return sorted(dates)
    except Exception as e:
        print(f"Could not read data_daily.json: {e}")
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end",   default=datetime.utcnow().strftime("%Y-%m-%d"))
    parser.add_argument("--dates", default=None, help="comma-separated YYYY-MM-DD list")
    parser.add_argument("--bg-window", default=90, type=int,
                        help="days before --start to use for background reference")
    parser.add_argument("--rebuild-bg", action="store_true",
                        help="force rebuild background even if cached")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end   = datetime.strptime(args.end,   "%Y-%m-%d")

    if args.dates:
        pass_dates = sorted(args.dates.split(","))
    else:
        pass_dates = load_pass_dates(start, end)

    print(f"Pass dates to process: {len(pass_dates)}")
    if not pass_dates:
        sys.exit("No dates found.")

    # ── Background: fetch reference window before analysis start ─────────────
    bg_path   = OUT_DIR / "background.npy"
    bgstd_path = OUT_DIR / "background_std.npy"
    wl_path   = OUT_DIR / "water_whitelist.npy"

    if args.rebuild_bg or not bg_path.exists():
        bg_start = start - timedelta(days=args.bg_window)
        bg_dates = load_pass_dates(bg_start, start - timedelta(days=1))
        print(f"Building background from {len(bg_dates)} reference passes ({bg_start.date()} → {(start - timedelta(1)).date()})…")
        ref_arrays = []
        for d in bg_dates:
            print(f"  bg fetch {d}", end=" ", flush=True)
            arr = fetch_pass(d)
            if arr is not None:
                ref_arrays.append(arr)
                print("ok")
            else:
                print("no data")
        if len(ref_arrays) < 3:
            sys.exit(f"Not enough background passes ({len(ref_arrays)}); need ≥3.")
        stack = np.stack(ref_arrays, axis=0)
        bg    = np.median(stack, axis=0)
        bg_std = stack.std(axis=0)
        wl    = build_water_whitelist(bg)
        np.save(str(bg_path),   bg)
        np.save(str(bgstd_path), bg_std)
        np.save(str(wl_path),   wl)
        print(f"Background saved: {bg_path}")
        print(f"Water whitelist: {int(wl.sum())} px ({100*wl.mean():.1f}% of AOI)")
    else:
        bg    = np.load(str(bg_path))
        bg_std = np.load(str(bgstd_path))
        wl    = np.load(str(wl_path))
        print(f"Loaded cached background: {bg_path}")

    # ── Detect ────────────────────────────────────────────────────────────────
    rows = []
    for date_str in pass_dates:
        print(f"[detect] {date_str}", end=" ", flush=True)
        arr = fetch_pass(date_str)
        if arr is None:
            print("no data")
            rows.append({"date": date_str, "n_det": 0, "n_large": 0, "n_vlcc": 0, "detections_json": "[]", "note": "no_data"})
            continue

        dets, anomaly = detect_on_pass(arr, bg, wl)
        n_large = sum(1 for d in dets if d["length_m"] >= 100)
        n_vlcc  = sum(1 for d in dets if d["length_m"] >= 290)
        print(f"n={len(dets)} large={n_large} vlcc={n_vlcc}")

        save_png(date_str, arr, anomaly, dets)

        rows.append({
            "date": date_str,
            "n_det": len(dets),
            "n_large": n_large,
            "n_vlcc": n_vlcc,
            "detections_json": json.dumps(dets),
            "note": "",
        })

    # ── Write CSV ─────────────────────────────────────────────────────────────
    with open(CSV_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "n_det", "n_large", "n_vlcc", "detections_json", "note"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nCSV → {CSV_PATH}")
    print(f"Images → {IMG_DIR}")


if __name__ == "__main__":
    main()
