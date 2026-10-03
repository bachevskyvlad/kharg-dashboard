"""
diag_equiv_7_6.py — Round 7.6: prove sweep ≡ prod crescent mask.

Gate chain (strict):
  Task 1: pixel-level mask equivalence (prod mask == diag mask for same az, same tank)
  Task 2: sweep using prod mask passes 7.5 synthetic unit test (4 azimuths)
  Task 3: Kharg gate #1 — t60_1 and t40_33, 4 DESC-35 scenes, PASS = min_az ∈ 282±30
           on ≥3/4 AND Δ ≥ 3 dB. Crops + sector tables committed.
  Task 4: RT gate (only if Task 3 PASS) — rt_0, 4 scenes, same criteria.
           FAIL documents per-tank RT signal as weak; does NOT cancel the round.
  Task 5: Stop-line (verbatim per ТЗ). No JW/YB tables, no round-8 recommendations.

Security constraints (unchanged from round 7.5):
  - No Supabase writes. Kharg tables not touched.
  - No paid API calls.
  - No new catalog queries (Kharg catalog metadata-only is allowed).
  - Honesty rule.
  - Prod not modified.

Output: diagnostics/round7_6/EQUIVALENCE_7_6.md + PNGs
"""

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

try:
    from sentinelhub import (
        BBox, CRS, MimeType, SentinelHubCatalog, SentinelHubRequest,
        DataCollection, SHConfig,
    )
    _SH_OK = True
except ImportError as e:
    print(f"[WARN] sentinelhub import: {e}")
    _SH_OK = False

# ── Import prod mask function directly from sar_fetch ──────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
try:
    from sar_fetch import build_crescent_masks as _prod_build_crescent_masks
    from sar_fetch import lonlat_to_pix as _prod_lonlat_to_pix
    _PROD_IMPORT_OK = True
except ImportError as e:
    print(f"[FATAL] cannot import build_crescent_masks from sar_fetch: {e}")
    _PROD_IMPORT_OK = False

# ── Constants (must match prod values in config.py) ────────────────────────────
RESOLUTION  = 5          # m/px
LOS_AZ_DESC = 282        # degrees
LOS_AZ_ASC  = 98
WIDTH_WIDE  = 15         # m (annulus width)
SWEEP_STEP  = 15         # degrees
PASS_TOL    = 30         # degrees
DELTA_DB_THR = 3.0       # minimum |Δ dB| for PASS (not PASS_WEAK)

CLIENT_ID     = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")

if _SH_OK:
    # Use prod config and S1_CDSE directly from config.py (already registered by
    # sar_fetch import above; re-defining raises ValueError).
    try:
        from config import config as cfg, S1_CDSE
        # Override credentials from env if provided (for GHA secrets injection)
        if CLIENT_ID:
            cfg.sh_client_id = CLIENT_ID
        if CLIENT_SECRET:
            cfg.sh_client_secret = CLIENT_SECRET
    except ImportError:
        cfg = SHConfig()
        cfg.sh_client_id     = CLIENT_ID     or ""
        cfg.sh_client_secret = CLIENT_SECRET or ""
        cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"
        cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
        S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
            "S1_CDSE_76", service_url=cfg.sh_base_url
        )

OUT_DIR = Path("diagnostics/round7_6")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Hardcoded scene dates (from rounds 6/7 logs, no new catalog queries) ───────
RT_SCENES    = ["2026-07-26", "2026-08-13", "2026-08-01", "2026-07-14"]

# Kharg: catalog query IS allowed per ТЗ (metadata-only, DESC-35)
KHARG_BBOX_TUPLE = (50.295, 29.205, 50.340, 29.240)
RT_BBOX_TUPLE    = (50.135, 26.625, 50.175, 26.655)

# ── Tank geometry ───────────────────────────────────────────────────────────────
KHARG_ETALON = [
    {"id": "t60_1",  "lat": 29.221638820210273, "lon": 50.315043243946754,
     "radius": 60, "h_max": 18},
    {"id": "t40_33", "lat": 29.230947410364493, "lon": 50.31324186520479,
     "radius": 40, "h_max": 14},
]
RT_ETALON = [
    {"id": "rt_0", "lat": 26.638626, "lon": 50.154236, "radius": 60, "h_max": 18},
]


# ── Sentinel Hub helpers ────────────────────────────────────────────────────────

_EVALSCRIPT_VV = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""


def _bbox_to_size(bbox_tuple):
    min_lon, min_lat, max_lon, max_lat = bbox_tuple
    w = int((max_lon - min_lon) * 111320 / RESOLUTION)
    h = int((max_lat - min_lat) * 111320 / RESOLUTION)
    return w, h


def fetch_s1(bbox_tuple, date_str, orbit="DESCENDING"):
    if not _SH_OK:
        return None, 0, 0
    bbox = BBox(bbox_tuple, crs=CRS.WGS84)
    size = _bbox_to_size(bbox_tuple)
    d = date.fromisoformat(date_str)
    try:
        req = SentinelHubRequest(
            evalscript=_EVALSCRIPT_VV,
            input_data=[SentinelHubRequest.input_data(
                data_collection=S1_CDSE,
                time_interval=(date_str, (d + timedelta(days=1)).strftime("%Y-%m-%d")),
                other_args={
                    "processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN",
                                   "demInstance": "COPERNICUS_30"},
                    "dataFilter": {"orbitDirection": orbit},
                    "mosaicking": "ORBIT",
                },
            )],
            responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
            bbox=bbox, size=size, config=cfg,
        )
        data = req.get_data()
        if not data:
            return None, size[0], size[1]
        arr = np.array(data[0], dtype=np.float32)
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        if arr.std() == 0:
            return None, size[0], size[1]
        return arr, arr.shape[1], arr.shape[0]
    except Exception as e:
        print(f"    [WARN] fetch {date_str}: {e}")
        return None, size[0], size[1]


def catalog_kharg_desc35(n=4):
    """Return n most-recent DESC-35 scene dates over Kharg (catalog metadata only)."""
    if not _SH_OK:
        return []
    bbox = BBox(KHARG_BBOX_TUPLE, crs=CRS.WGS84)
    catalog = SentinelHubCatalog(config=cfg)
    try:
        scenes = list(catalog.search(
            S1_CDSE, bbox=bbox,
            time=("2024-01-01", "2026-09-27"),
            filter="sat:orbit_state = 'descending'",
            fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
        ))
    except Exception as e:
        print(f"  [WARN] catalog: {e}")
        return []
    desc35 = [s for s in scenes
              if s.get("properties", {}).get("sat:relative_orbit") == 35]
    desc35.sort(key=lambda s: s["properties"]["datetime"], reverse=True)
    dates = []
    seen = set()
    for s in desc35:
        d = s["properties"]["datetime"][:10]
        if d not in seen:
            seen.add(d)
            dates.append(d)
        if len(dates) >= n:
            break
    return dates


# ── Diag crescent mask (must be proved equivalent to prod) ─────────────────────

def _diag_crescent_mask(cx, cy, r_outer_px, r_inner_px, img_w, img_h, az_deg):
    """Diagnostic single-tank crescent mask (same formula as prod, different API)."""
    Y, X = np.mgrid[0:img_h, 0:img_w]
    dx = np.sin(np.deg2rad(az_deg))
    dy = -np.cos(np.deg2rad(az_deg))
    dist_sq = (X - cx)**2 + (Y - cy)**2
    dot = (X - cx) * dx + (Y - cy) * dy
    return (dist_sq <= r_outer_px**2) & (dist_sq >= r_inner_px**2) & (dot > 0)


# ── Sweep using prod mask function ─────────────────────────────────────────────

def sweep_prod_mask(img_lin, cx, cy, radius_m, img_w, img_h,
                    res_m=RESOLUTION, los_az=LOS_AZ_DESC, step_deg=SWEEP_STEP):
    """
    Sweep using prod crescent mask rotated over full 360°.
    Returns (eff_azimuths, db_means) — eff_az = (los_az + rot) % 360.
    Calls _prod_build_crescent_masks with a synthetic single-tank registry
    to guarantee pixel-level identity with the pipeline.
    """
    angles, means = [], []
    bbox_synthetic = BBox(
        (0.0, 0.0, img_w * res_m / 111320.0, img_h * res_m / 111320.0),
        crs=CRS.WGS84,
    ) if _SH_OK else None

    # Compute cx/cy in synthetic bbox coords:
    # In prod lonlat_to_pix: x = (lon - min_lon)/(max_lon-min_lon)*w
    # → lon = min_lon + cx/w*(max_lon-min_lon) = cx*res_m/111320
    # So synthetic lon/lat that round-trips to exactly (cx, cy):
    syn_lon = cx * res_m / 111320.0
    syn_lat = img_h * res_m / 111320.0 - cy * res_m / 111320.0  # y flipped

    for rot in range(0, 360, step_deg):
        eff_az = (los_az + rot) % 360
        # Build prod mask at this azimuth
        tank_spec = [{"id": "t", "lon": syn_lon, "lat": syn_lat,
                      "radius": radius_m, "h_max": 18}]
        if _SH_OK and bbox_synthetic is not None:
            tanks_data = _prod_build_crescent_masks(
                tank_spec, bbox_synthetic, img_w, img_h, los_azimuth=eff_az
            )
            mask = tanks_data[0]["crescent"]
        else:
            # fallback to diag mask (only used if sentinelhub not available)
            r_outer = radius_m / res_m
            r_inner = max(0.0, radius_m - WIDTH_WIDE) / res_m
            mask = _diag_crescent_mask(cx, cy, r_outer, r_inner, img_w, img_h, eff_az)

        px = img_lin[mask]
        px = px[np.isfinite(px)]
        db = 10 * np.log10(np.clip(np.mean(px), 1e-10, None)) if px.size else np.nan
        angles.append(eff_az)
        means.append(db)

    return np.array(angles), np.array(means)


def profile_pass(angles, db_means, expected_az, tol=PASS_TOL):
    """Returns (passed, min_az, min_db, delta_db). delta_db = min_sector - median."""
    valid = np.isfinite(db_means)
    if not valid.any():
        return False, np.nan, np.nan, np.nan
    idx = int(np.nanargmin(db_means))
    min_az  = float(angles[idx])
    min_db  = float(db_means[idx])
    median_db = float(np.nanmedian(db_means))
    delta_db  = min_db - median_db
    diff = abs((min_az - expected_az + 180) % 360 - 180)
    passed = (diff <= tol) and (abs(delta_db) >= DELTA_DB_THR)
    return passed, min_az, min_db, delta_db


# ── Visual helpers ─────────────────────────────────────────────────────────────

def save_contour_comparison(img_lin, prod_mask, diag_mask, cx, cy, tank_id, outpath):
    """Overlay prod (red) and diag (blue) crescent contours on SAR crop."""
    r = int(max(60, (prod_mask.shape[1] * 0.15)))
    x0 = max(0, int(cx) - r); x1 = min(img_lin.shape[1], int(cx) + r)
    y0 = max(0, int(cy) - r); y1 = min(img_lin.shape[0], int(cy) + r)
    crop = img_lin[y0:y1, x0:x1]
    crop_dB = 10 * np.log10(np.clip(crop, 1e-10, None))

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, data, title in zip(axes,
                                [crop_dB, prod_mask[y0:y1, x0:x1].astype(float),
                                 diag_mask[y0:y1, x0:x1].astype(float)],
                                ["SAR dB", "Prod mask", "Diag mask"]):
        ax.imshow(data, cmap="gray", origin="upper")
        ax.set_title(title)
        ax.axis("off")
    plt.suptitle(f"{tank_id} — mask comparison (red=prod, blue=diag)")
    # overlay contours on first panel
    axes[0].contour(prod_mask[y0:y1, x0:x1].astype(float), levels=[0.5],
                    colors=["red"], linewidths=1.5)
    axes[0].contour(diag_mask[y0:y1, x0:x1].astype(float), levels=[0.5],
                    colors=["blue"], linewidths=1, linestyles="dashed")
    diff_px = int(np.sum(prod_mask ^ diag_mask))
    axes[0].set_title(f"SAR dB | Δ pixels={diff_px}")
    plt.tight_layout()
    plt.savefig(outpath, dpi=100)
    plt.close()
    return diff_px


def save_sweep_crop(img_lin, cx, cy, radius_m, angles, db_means,
                    tank_id, date_str, passed, min_az, delta_db, res_m, outpath):
    r_px = int(radius_m / res_m * 1.5)
    x0 = max(0, int(cx) - r_px); x1 = min(img_lin.shape[1], int(cx) + r_px)
    y0 = max(0, int(cy) - r_px); y1 = min(img_lin.shape[0], int(cy) + r_px)
    crop_dB = 10 * np.log10(np.clip(img_lin[y0:y1, x0:x1], 1e-10, None))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    ax1.imshow(crop_dB, cmap="gray", origin="upper")
    ax1.set_title(f"{tank_id} {date_str}\nmin_az={min_az:.0f}° Δ={delta_db:.2f}dB "
                  f"{'PASS' if passed else 'FAIL'}")
    ax1.axis("off")

    valid = np.isfinite(db_means)
    ax2.bar(angles[valid], db_means[valid], width=12, color="steelblue", alpha=0.7)
    ax2.axvline(min_az, color="red", lw=1.5, label=f"min {min_az:.0f}°")
    ax2.axvline(LOS_AZ_DESC, color="orange", lw=1, linestyle="--", label=f"LOS {LOS_AZ_DESC}°")
    ax2.set_xlabel("eff_az (°)"); ax2.set_ylabel("dB")
    ax2.set_title(f"Sweep profile {'PASS' if passed else 'FAIL'}")
    ax2.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(outpath, dpi=100)
    plt.close()


def sector_table_str(angles, db_means):
    lines = ["| sector_az | dB |", "|-----------|-----|"]
    for az, db in zip(angles, db_means):
        lines.append(f"| {az:.0f} | {db:.2f} |")
    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="1,2,3,4,5")
    args = parser.parse_args()
    tasks = [int(t) for t in args.tasks.split(",")]

    task1_pass = True
    task2_pass = True
    gate3 = False
    kharg_dates = []

    report_lines = [
        "# EQUIVALENCE_7_6.md",
        "Generated: 2026-09-27 UTC",
        "Run: GHA round-7.6 on `claude/oilradar-dashboard-iran-zchdva`",
        "",
        "---",
        "",
    ]

    # ── Pre-check: prod import ────────────────────────────────────────────────
    if not _PROD_IMPORT_OK:
        report_lines += [
            "## FATAL: prod import failed",
            "",
            "Cannot import `build_crescent_masks` from `sar_fetch`.",
            "Equivalence not provable. Fix import path and re-run.",
            "",
        ]
        (OUT_DIR / "EQUIVALENCE_7_6.md").write_text("\n".join(report_lines))
        sys.exit(1)

    report_lines += [
        "## Prod import",
        "",
        "```",
        "from sar_fetch import build_crescent_masks  # OK",
        "from sar_fetch import lonlat_to_pix          # OK",
        "```",
        "",
        "---",
        "",
    ]

    # ────────────────────────────────────────────────────────────────────────────
    # Task 1 — Pixel-level mask equivalence
    # ────────────────────────────────────────────────────────────────────────────
    if 1 in tasks:
        print("\n=== Task 1: pixel-level mask equivalence ===")
        report_lines += ["## Task 1 — Pixel-level mask equivalence", ""]

        # Fetch one Kharg scene for t60_1 and t40_33
        kharg_dates = catalog_kharg_desc35(n=4)
        if not kharg_dates:
            # fallback to known good date from round 7.5
            kharg_dates = ["2026-03-21", "2025-09-28", "2026-01-15", "2025-07-25"]
        print(f"  Kharg DESC-35 dates: {kharg_dates}")

        test_date = kharg_dates[0]
        img, img_w, img_h = fetch_s1(KHARG_BBOX_TUPLE, test_date, orbit="DESCENDING")

        if img is None:
            report_lines += [
                f"Scene {test_date}: fetch returned None — cannot run equivalence test.",
                "",
                "> **Task 1: BLOCKED — no image data**",
                "",
                "---",
                "",
            ]
            print("  [BLOCKED] no image — skipping equivalence test")
        else:
            print(f"  Image {test_date}: {img_w}×{img_h} px")

            bbox_prod = BBox(KHARG_BBOX_TUPLE, crs=CRS.WGS84)
            equiv_rows = []
            task1_pass = True

            for tank in KHARG_ETALON:
                tid = tank["id"]
                # prod mask
                tanks_data = _prod_build_crescent_masks(
                    [tank], bbox_prod, img_w, img_h, los_azimuth=LOS_AZ_DESC
                )
                prod_mask = tanks_data[0]["crescent"]

                # diag mask
                cx, cy = _prod_lonlat_to_pix(
                    tank["lon"], tank["lat"], bbox_prod, img_w, img_h
                )
                r_outer = tank["radius"] / RESOLUTION
                r_inner = max(0.0, tank["radius"] - WIDTH_WIDE) / RESOLUTION
                diag_mask = _diag_crescent_mask(cx, cy, r_outer, r_inner,
                                                img_w, img_h, LOS_AZ_DESC)

                equal = np.array_equal(prod_mask, diag_mask)
                diff_px = int(np.sum(prod_mask ^ diag_mask))
                prod_px = int(prod_mask.sum())
                diag_px = int(diag_mask.sum())

                # Assert
                assert equal, (
                    f"[FAIL] {tid}: prod_mask ≠ diag_mask, diff={diff_px}px "
                    f"(prod={prod_px}, diag={diag_px})"
                )

                # Save contour comparison PNG (even if equal, for audit record)
                png_path = OUT_DIR / f"task1_{tid}_{test_date}_contour.jpg"
                save_contour_comparison(img, prod_mask, diag_mask, cx, cy,
                                        tid, png_path)
                assert np.std(10 * np.log10(np.clip(img[prod_mask], 1e-10, None))) > 0, \
                    f"[FAIL] {tid}: std==0 on prod crescent pixels (smoke test)"

                equiv_rows.append({
                    "tank": tid, "prod_px": prod_px, "diag_px": diag_px,
                    "diff_px": diff_px, "equal": equal,
                })
                print(f"  {tid}: prod={prod_px}px diag={diag_px}px diff={diff_px}px "
                      f"equal={equal}")

            report_lines += [
                f"Scene: {test_date} (Kharg DESC-35)",
                "",
                "| tank | prod_px | diag_px | diff_px | equal |",
                "|------|---------|---------|---------|-------|",
            ]
            for r in equiv_rows:
                report_lines.append(
                    f"| {r['tank']} | {r['prod_px']} | {r['diag_px']} "
                    f"| {r['diff_px']} | {'YES' if r['equal'] else 'NO'} |"
                )

            if task1_pass:
                report_lines += [
                    "",
                    "> **Task 1: PASS — prod mask ≡ diag mask (np.array_equal)**",
                    "",
                ]
            else:
                report_lines += [
                    "",
                    "> **Task 1: FAIL — masks differ (see contour PNGs)**",
                    "",
                ]

            report_lines += ["---", ""]

    # ────────────────────────────────────────────────────────────────────────────
    # Task 2 — Sweep prod mask: synthetic unit test
    # ────────────────────────────────────────────────────────────────────────────
    if 2 in tasks:
        print("\n=== Task 2: sweep synthetic unit test ===")
        report_lines += ["## Task 2 — Sweep prod mask: synthetic unit test", ""]

        SYNTHETIC_AZ = [30, 98, 282, 350]
        IMG_SZ = 200
        RADIUS_PX = 20
        RADIUS_M  = RADIUS_PX * RESOLUTION
        cx_s, cy_s = IMG_SZ / 2, IMG_SZ / 2

        unit_rows = []
        task2_pass = True

        for plant_az in SYNTHETIC_AZ:
            # Build a synthetic image with a dark crescent at plant_az
            img_s = np.full((IMG_SZ, IMG_SZ), 0.5, dtype=np.float32)
            shadow_mask = _diag_crescent_mask(
                cx_s, cy_s,
                RADIUS_PX + 2, max(0, RADIUS_PX - 3),
                IMG_SZ, IMG_SZ, plant_az
            )
            img_s[shadow_mask] = 0.005  # deep shadow

            # Sweep using prod mask (los_az=0 so eff_az = rot)
            angles, db_means = sweep_prod_mask(
                img_s, cx_s, cy_s, RADIUS_M, IMG_SZ, IMG_SZ,
                res_m=RESOLUTION, los_az=0, step_deg=SWEEP_STEP
            )

            # Smoke test: std > 0
            assert np.nanstd(db_means) > 0, \
                f"[FATAL] plant_az={plant_az}: sweep std==0 (all sectors identical)"

            found_idx = int(np.nanargmin(db_means))
            found_az = float(angles[found_idx])
            min_db   = float(db_means[found_idx])
            delta_db = float(db_means[found_idx] - np.nanmedian(db_means))
            diff = abs((found_az - plant_az + 180) % 360 - 180)
            passed = diff <= PASS_TOL

            # Phase test: at plant_az=LOS_AZ_DESC with los_az=LOS_AZ_DESC, rot=0
            # → eff_az = (282+0)%360 = 282; prod_mask at az=282 should be brightest
            if plant_az == LOS_AZ_DESC:
                # Verify that rot=0 → eff_az=282 gives the darkest sector
                assert passed, \
                    f"[FAIL] phase test: plant_az=282 not found (found {found_az}°)"

            unit_rows.append({
                "plant_az": plant_az, "found_az": found_az,
                "min_db": min_db, "delta_db": delta_db, "pass": passed,
            })
            status = "PASS" if passed else "FAIL"
            print(f"  plant_az={plant_az}° → found={found_az:.0f}° "
                  f"Δ={delta_db:.1f}dB {status}")
            if not passed:
                task2_pass = False

        report_lines += [
            "| plant_az | found_az | min_db | Δ dB | result |",
            "|----------|----------|--------|------|--------|",
        ]
        for r in unit_rows:
            report_lines.append(
                f"| {r['plant_az']} | {r['found_az']:.0f} | {r['min_db']:.1f} "
                f"| {r['delta_db']:.1f} | {'PASS' if r['pass'] else 'FAIL'} |"
            )
        report_lines += [
            "",
            f"> **Task 2: {'PASS' if task2_pass else 'FAIL'}** — "
            f"sweep_prod_mask finds planted shadow within ±{PASS_TOL}°",
            "",
            "---",
            "",
        ]

    # ────────────────────────────────────────────────────────────────────────────
    # Task 3 — Kharg Gate #1 (etalon: t60_1, t40_33)
    # ────────────────────────────────────────────────────────────────────────────
    if 3 in tasks:
        print("\n=== Task 3: Kharg gate #1 ===")
        report_lines += ["## Task 3 — Kharg Gate #1 (t60_1 + t40_33)", ""]

        if not ('kharg_dates' in dir() and kharg_dates):
            kharg_dates = catalog_kharg_desc35(n=4)
        if not kharg_dates:
            kharg_dates = ["2026-08-01", "2026-03-21", "2025-09-28", "2026-01-15"]
        print(f"  Kharg scenes: {kharg_dates[:4]}")

        bbox_prod = BBox(KHARG_BBOX_TUPLE, crs=CRS.WGS84)
        kharg_rows = []
        sector_tables = {}

        for d_str in kharg_dates[:4]:
            img, img_w, img_h = fetch_s1(KHARG_BBOX_TUPLE, d_str, orbit="DESCENDING")
            if img is None:
                print(f"    [SKIP] {d_str}: no data")
                continue

            for tank in KHARG_ETALON:
                tid = tank["id"]
                cx, cy = _prod_lonlat_to_pix(
                    tank["lon"], tank["lat"], bbox_prod, img_w, img_h
                )
                angles, db_means = sweep_prod_mask(
                    img, cx, cy, tank["radius"], img_w, img_h,
                    res_m=RESOLUTION, los_az=LOS_AZ_DESC, step_deg=SWEEP_STEP
                )

                passed, min_az, min_db, delta_db = profile_pass(
                    angles, db_means, expected_az=LOS_AZ_DESC
                )
                kharg_rows.append({
                    "tank": tid, "date": d_str, "min_az": min_az,
                    "min_db": min_db, "delta_db": delta_db, "pass": passed,
                })
                print(f"    {tid} {d_str}: min_az={min_az:.0f}° "
                      f"Δ={delta_db:.2f}dB {'PASS' if passed else 'FAIL'}")

                # Sector table for t60_1 first scene (for manual inspection if FAIL)
                if tid == "t60_1" and d_str == kharg_dates[0] and \
                        tid not in sector_tables:
                    sector_tables[tid] = sector_table_str(angles, db_means)

                # Save sweep crop
                png_path = OUT_DIR / f"task3_{tid}_{d_str}_sweep.jpg"
                save_sweep_crop(img, cx, cy, tank["radius"], angles, db_means,
                                tid, d_str, passed, min_az, delta_db,
                                RESOLUTION, png_path)

        # Gate: ≥3/4 scenes PASS for each etalon tank
        if kharg_rows:
            n_pass_t60 = sum(1 for r in kharg_rows
                             if r["tank"] == "t60_1" and r["pass"])
            n_pass_t40 = sum(1 for r in kharg_rows
                             if r["tank"] == "t40_33" and r["pass"])
            gate3 = (n_pass_t60 >= 3) and (n_pass_t40 >= 3)
        else:
            gate3 = False
            n_pass_t60 = n_pass_t40 = 0

        report_lines += [
            "| tank | date | min_az | Δ dB | pass |",
            "|------|------|--------|------|------|",
        ]
        for r in kharg_rows:
            report_lines.append(
                f"| {r['tank']} | {r['date']} | {r['min_az']:.0f} "
                f"| {r['delta_db']:.2f} | {'YES' if r['pass'] else 'NO'} |"
            )

        report_lines += [
            "",
            f"> t60_1: {n_pass_t60}/{len([r for r in kharg_rows if r['tank']=='t60_1'])}  "
            f"t40_33: {n_pass_t40}/{len([r for r in kharg_rows if r['tank']=='t40_33'])}",
            f"> **Gate #1: {'PASS' if gate3 else 'FAIL'}**",
            "",
        ]

        if not gate3:
            report_lines += [
                "**FINDING: prod crescent on Kharg etalon does not reproduce sweep at 282°.**",
                "Per-sector dB table for t60_1 (first scene) — inspect where shadow falls:",
                "",
            ]
            for tid, tbl in sector_tables.items():
                report_lines += [f"### {tid} sector table", "", tbl, ""]
            report_lines += [
                "> Round 7.6 stops here. Tasks 4 and 5 not executed (gate chain broken).",
                "",
            ]

        report_lines += ["---", ""]

    # ────────────────────────────────────────────────────────────────────────────
    # Task 4 — RT gate (only if Gate #1 PASS)
    # ────────────────────────────────────────────────────────────────────────────
    if 4 in tasks and gate3:
        print("\n=== Task 4: RT gate #2 ===")
        report_lines += ["## Task 4 — RT Gate (rt_0)", ""]

        bbox_rt_prod = BBox(RT_BBOX_TUPLE, crs=CRS.WGS84)
        rt_rows = []

        for d_str in RT_SCENES:
            img, img_w, img_h = fetch_s1(RT_BBOX_TUPLE, d_str, orbit="DESCENDING")
            if img is None:
                print(f"    [SKIP] {d_str}: no data")
                continue

            tank = RT_ETALON[0]
            cx, cy = _prod_lonlat_to_pix(
                tank["lon"], tank["lat"], bbox_rt_prod, img_w, img_h
            )
            angles, db_means = sweep_prod_mask(
                img, cx, cy, tank["radius"], img_w, img_h,
                res_m=RESOLUTION, los_az=LOS_AZ_DESC, step_deg=SWEEP_STEP
            )

            passed, min_az, min_db, delta_db = profile_pass(
                angles, db_means, expected_az=LOS_AZ_DESC
            )
            rt_rows.append({
                "date": d_str, "min_az": min_az, "min_db": min_db,
                "delta_db": delta_db, "pass": passed,
            })
            print(f"    rt_0 {d_str}: min_az={min_az:.0f}° Δ={delta_db:.2f}dB "
                  f"{'PASS' if passed else 'FAIL'}")

            png_path = OUT_DIR / f"task4_rt_0_{d_str}_sweep.jpg"
            save_sweep_crop(img, cx, cy, tank["radius"], angles, db_means,
                            "rt_0", d_str, passed, min_az, delta_db,
                            RESOLUTION, png_path)

        n_rt = sum(1 for r in rt_rows if r["pass"])
        gate4 = n_rt >= 3

        report_lines += [
            "| date | min_az | Δ dB | pass |",
            "|------|--------|------|------|",
        ]
        for r in rt_rows:
            report_lines.append(
                f"| {r['date']} | {r['min_az']:.0f} "
                f"| {r['delta_db']:.2f} | {'YES' if r['pass'] else 'NO'} |"
            )

        report_lines += [
            "",
            f"> RT gate: {n_rt}/{len(rt_rows)} → {'PASS' if gate4 else 'FAIL'}",
        ]
        if not gate4:
            report_lines += [
                "> NOTE: per-tank RT signal weak — FAIL here does not cancel round 7.6.",
                "> Terminal-level RT is validated externally (event-validation 2026).",
                "> Per-tank single-tank sweep may be below detection threshold at farm bbox resolution.",
            ]
        report_lines += ["", "---", ""]

    elif 4 in tasks and not gate3:
        report_lines += [
            "## Task 4 — RT Gate",
            "",
            "> Skipped: Gate #1 (Task 3) FAIL.",
            "",
            "---",
            "",
        ]

    # ────────────────────────────────────────────────────────────────────────────
    # Task 5 — Stop-line (verbatim per ТЗ)
    # ────────────────────────────────────────────────────────────────────────────
    if 5 in tasks:
        report_lines += [
            "## Task 5 — Verdicts",
            "",
            "Вердикты rounds 6–7.5 по Juaymah/Yanbu аннулированы. "
            "Действующие факты: Kharg r=0.7 (внешняя валидация), "
            "RT — event-валидация 2026. "
            "Juaymah/Yanbu — статус неизвестен до прогона эквивалентным инструментом.",
            "",
            "---",
            "",
        ]

    # ── Write report ──────────────────────────────────────────────────────────
    report_path = OUT_DIR / "EQUIVALENCE_7_6.md"
    report_path.write_text("\n".join(report_lines))
    print(f"\nReport: {report_path}")
    print("Done.")


if __name__ == "__main__":
    main()
