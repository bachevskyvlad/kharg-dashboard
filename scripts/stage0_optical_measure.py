"""
Stage 0 — Optical radius/center verification for jw_9, yb_1, yb_9.

Fetches Sentinel-2 L2A true-color at ~2.5m/px via SentinelHub CDSE.
Applies 8-ray gradient profile to measure actual rim radius and center.
Outputs:
  diagnostics/stage0/crops/<tank_id>.png   — optical + red (registry) + green (measured)
  diagnostics/stage0/stage0_report.txt     — table + verdict
"""

import os, sys, json, math
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

sys.path.insert(0, str(Path(__file__).parent))
from config import config

from sentinelhub import (
    SentinelHubRequest, DataCollection, MimeType,
    CRS, BBox, bbox_to_dimensions, SHConfig
)

# ── Tanks to measure ─────────────────────────────────────────────────────────
TANKS = [
    dict(id="jw_9",  lat=26.819531458247540, lon=49.992284316605510, r_reg=50),
    dict(id="yb_1",  lat=23.962590,          lon=38.247925,          r_reg=60),
    dict(id="yb_9",  lat=23.970331,          lon=38.248336,          r_reg=60),
]

RESOLUTION  = 2.5   # m/px for S2 (native 10m resampled to 2.5 via SH super-resolve)
BUFFER_M    = 200   # metres around center
N_RAYS      = 16    # radial rays for gradient profile
SMOOTH_PX   = 1     # Gaussian sigma for gradient smoothing (px)
MIN_GRAD    = 0.01  # minimum gradient to count as edge candidate
OUT_DIR     = Path("diagnostics/stage0/crops")

# ─────────────────────────────────────────────────────────────────────────────

def latlon_to_meter_bbox(lat, lon, buf_m):
    """Return (lon_min, lat_min, lon_max, lat_max) with buf_m padding."""
    deg_per_m_lat = 1.0 / 111320.0
    deg_per_m_lon = 1.0 / (111320.0 * math.cos(math.radians(lat)))
    lat_d = buf_m * deg_per_m_lat
    lon_d = buf_m * deg_per_m_lon
    return (lon - lon_d, lat - lat_d, lon + lon_d, lat + lat_d)


def fetch_s2_optical(lat, lon, buf_m=BUFFER_M, res=RESOLUTION):
    """Fetch S2 L2A TCI (true-color) patch. Returns (rgb_uint8, bbox_deg)."""
    bbox_deg = latlon_to_meter_bbox(lat, lon, buf_m)
    bb = BBox(bbox=bbox_deg, crs=CRS.WGS84)
    size = bbox_to_dimensions(bb, resolution=res)

    evalscript = """
//VERSION=3
function setup() {
  return { input: ["B04","B03","B02","dataMask"], output: { bands: 4 } };
}
function evaluatePixel(s) {
  return [3.5*s.B04, 3.5*s.B03, 3.5*s.B02, s.dataMask];
}
"""
    # Try recent dates going back up to 12 months, pick first cloud-free
    end_date   = datetime(2026, 9, 1)
    start_date = end_date - timedelta(days=365)

    req = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[
            SentinelHubRequest.input_data(
                data_collection=DataCollection.SENTINEL2_L2A.define_from(
                    "s2l2a_cdse",
                    service_url="https://sh.dataspace.copernicus.eu",
                ),
                time_interval=(start_date.strftime("%Y-%m-%d"),
                               end_date.strftime("%Y-%m-%d")),
                other_args={
                    "dataFilter": {"maxCloudCoverage": 10},
                    "processing": {"upsampling": "BICUBIC"},
                },
            )
        ],
        responses=[SentinelHubRequest.output_response("default", MimeType.PNG)],
        bbox=bb,
        size=size,
        config=config,
    )
    imgs = req.get_data()
    if not imgs or imgs[0] is None:
        raise RuntimeError("No S2 data returned")
    rgb = imgs[0][:, :, :3]  # drop alpha
    return rgb, bbox_deg


def measure_tank(rgb, lat, lon, r_reg_m, bbox_deg, res=RESOLUTION):
    """
    Gradient-profile radius & center measurement.
    Returns dict with r_measured_m, center_shift_m, cy_px, cx_px, r_px,
    and per-ray arrays for debug.
    """
    from scipy.ndimage import gaussian_filter

    h, w = rgb.shape[:2]
    lon_min, lat_min, lon_max, lat_max = bbox_deg

    # Registry center in pixel coords
    cx = (lon - lon_min) / (lon_max - lon_min) * w
    cy = (lat_max - lat) / (lat_max - lat_min) * h  # y increases downward

    # Grayscale luminance
    gray = rgb.astype(np.float32).mean(axis=2)
    gray_s = gaussian_filter(gray, sigma=SMOOTH_PX)

    # Gradient magnitude
    gy, gx = np.gradient(gray_s)
    gmag = np.sqrt(gx**2 + gy**2)

    r_max_px = int((r_reg_m * 1.5) / res)  # search up to 1.5× registry radius

    edge_xs, edge_ys = [], []
    r_readings = []

    angles = np.linspace(0, 2 * math.pi, N_RAYS, endpoint=False)
    for ang in angles:
        # Sample gradient along ray from center
        rs = np.arange(max(5, int(r_reg_m * 0.4) // int(res)),
                       r_max_px)
        xs = cx + rs * math.cos(ang)
        ys = cy + rs * math.sin(ang)

        # Bilinear sample of gmag along ray
        from scipy.ndimage import map_coordinates
        vals = map_coordinates(gmag, [ys, xs], order=1, mode='nearest')

        if vals.max() < MIN_GRAD:
            continue
        peak_idx = int(np.argmax(vals))
        r_px = rs[peak_idx]
        r_readings.append(r_px * res)
        edge_xs.append(float(xs[peak_idx]))
        edge_ys.append(float(ys[peak_idx]))

    if len(r_readings) < 4:
        return None

    r_measured_m = float(np.median(r_readings))

    # Refined center = centroid of edge pixels
    if edge_xs:
        new_cx = float(np.mean(edge_xs))
        new_cy = float(np.mean(edge_ys))
        shift_px = math.sqrt((new_cx - cx)**2 + (new_cy - cy)**2)
        shift_m  = shift_px * res
    else:
        new_cx, new_cy = cx, cy
        shift_m = 0.0

    return dict(
        r_measured_m=r_measured_m,
        center_shift_m=shift_m,
        cx_reg=cx, cy_reg=cy,
        cx_new=new_cx, cy_new=new_cy,
        r_reg_px=r_reg_m / res,
        r_new_px=r_measured_m / res,
        n_rays=len(r_readings),
    )


def save_crop(rgb, result, tank_id, r_reg_m, out_path):
    fig, ax = plt.subplots(1, 1, figsize=(8, 8))
    ax.imshow(np.clip(rgb, 0, 255).astype(np.uint8))

    if result:
        # Registry circle — red
        c_reg = plt.Circle((result["cx_reg"], result["cy_reg"]),
                             result["r_reg_px"], color="red",
                             fill=False, lw=1.5, label=f"Registry r={r_reg_m}m")
        # Measured circle — green
        c_new = plt.Circle((result["cx_new"], result["cy_new"]),
                             result["r_new_px"], color="lime",
                             fill=False, lw=1.5,
                             label=f"Measured r={result['r_measured_m']:.1f}m  Δc={result['center_shift_m']:.1f}m")
        ax.add_patch(c_reg)
        ax.add_patch(c_new)
        ax.legend(loc="upper right", fontsize=8, framealpha=0.7)

    ax.set_title(tank_id, fontsize=10)
    ax.axis("off")
    fig.tight_layout(pad=0.2)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_lines = [
        "Stage 0 — Optical radius/center check",
        f"Run: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"Resolution: {RESOLUTION}m/px  Rays: {N_RAYS}",
        "",
        f"{'tank':<8} {'r_registry':>12} {'r_measured':>12} {'Δr%':>8} {'shift_m':>10} {'n_rays':>8}",
        "-" * 62,
    ]

    rows = []
    for tk in TANKS:
        tid = tk["id"]
        lat, lon, r_reg = tk["lat"], tk["lon"], tk["r_reg"]
        print(f"\n── {tid}  lat={lat:.5f}  lon={lon:.5f}  r_reg={r_reg}m")

        try:
            rgb, bbox_deg = fetch_s2_optical(lat, lon)
            print(f"   fetched {rgb.shape[1]}×{rgb.shape[0]}px")
        except Exception as e:
            print(f"   FETCH FAILED: {e}")
            row = dict(id=tid, r_reg=r_reg, r_meas=None, dr_pct=None,
                       shift_m=None, n_rays=0, status="FETCH_FAIL")
            rows.append(row)
            report_lines.append(f"{tid:<8} {r_reg:>12.1f} {'N/A':>12} {'N/A':>8} {'N/A':>10} {'N/A':>8}  FETCH_FAIL")
            continue

        result = measure_tank(rgb, lat, lon, r_reg, bbox_deg)
        crop_path = OUT_DIR / f"{tid}.png"

        if result is None:
            print(f"   MEASURE FAILED (too few ray hits)")
            row = dict(id=tid, r_reg=r_reg, r_meas=None, dr_pct=None,
                       shift_m=None, n_rays=0, status="MEASURE_FAIL")
            rows.append(row)
            save_crop(rgb, None, tid, r_reg, crop_path)
            report_lines.append(f"{tid:<8} {r_reg:>12.1f} {'N/A':>12} {'N/A':>8} {'N/A':>10}   0        MEASURE_FAIL")
            continue

        r_meas = result["r_measured_m"]
        dr_pct = (r_meas - r_reg) / r_reg * 100
        shift  = result["center_shift_m"]
        n      = result["n_rays"]
        print(f"   r_measured={r_meas:.1f}m  Δr={dr_pct:+.1f}%  shift={shift:.1f}m  rays={n}")

        save_crop(rgb, result, tid, r_reg, crop_path)
        row = dict(id=tid, r_reg=r_reg, r_meas=r_meas, dr_pct=dr_pct,
                   shift_m=shift, n_rays=n, status="OK")
        rows.append(row)
        report_lines.append(
            f"{tid:<8} {r_reg:>12.1f} {r_meas:>12.1f} {dr_pct:>+8.1f}% {shift:>9.1f}m {n:>8}"
        )

    # ── Verdict ──────────────────────────────────────────────────────────────
    report_lines.append("")
    ok_rows = [r for r in rows if r["status"] == "OK"]
    if not ok_rows:
        verdict = "INCONCLUSIVE — all fetches failed; manual measurement required"
    else:
        max_dr = max(abs(r["dr_pct"]) for r in ok_rows)
        if max_dr > 10:
            verdict = f"STAGE 1 FULL — max |Δr|={max_dr:.1f}% > 10%; fix both centers AND radii"
        else:
            verdict = f"STAGE 1 CENTERS ONLY — max |Δr|={max_dr:.1f}% ≤ 10%; radii OK, fix centers only"
    report_lines.append(f"VERDICT: {verdict}")

    report_path = Path("diagnostics/stage0/stage0_report.txt")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report_lines) + "\n")

    print("\n" + "=" * 62)
    for line in report_lines:
        print(line)
    print(f"\nCrops → {OUT_DIR}/")
    print(f"Report → {report_path}")


if __name__ == "__main__":
    main()
