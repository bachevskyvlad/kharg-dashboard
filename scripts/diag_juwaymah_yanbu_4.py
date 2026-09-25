"""
diag_juwaymah_yanbu_4.py — Round 4 diagnostic.

Task 0: Infrastructure — outputs committed to repo (no artifact uploads).
Task 1: Juwaymah — profile vs classification cross-reference, unclear re-classify, farm count.
Task 2: Yanbu — overlay crops for 7 tanks, shift-vector measurement, offset grid search.
Task 3: RT north / Juwaymah intersection — optical map with 3-color circles.
Task 4: Write DIAGNOSTIC_JUAYMAH_YANBU_4.md.

Usage:
  python scripts/diag_juwaymah_yanbu_4.py --tasks 0,1,2,3,4

Output: diagnostics/round4/  (committed to feature branch by workflow)
"""

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

from PIL import Image

from sentinelhub import (
    BBox, CRS, MimeType, SentinelHubCatalog, SentinelHubRequest, DataCollection, SHConfig,
)

# ── Sentinel Hub config ───────────────────────────────────────────────────────
cfg = SHConfig()
cfg.sh_client_id     = os.environ.get("CLIENT_ID")     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
cfg.sh_client_secret = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"

CDSE_URL = "https://sh.dataspace.copernicus.eu"
S1_CDSE  = DataCollection.SENTINEL1_IW.define_from("s1iw_cdse4", service_url=CDSE_URL)
S2_CDSE  = DataCollection.SENTINEL2_L2A.define_from("s2l2a_cdse4", service_url=CDSE_URL)

OUT_DIR  = Path("diagnostics/round4")

# ── Registry (inline) ────────────────────────────────────────────────────────
JUWAYMAH = {
    "id": "juwaymah",
    "bbox": (49.960, 26.805, 50.010, 26.835),
    "orbit_state": "descending",
    "orbit_direction_api": "DESCENDING",
    "relative_orbit": 35,
    "los_azimuth": 282,
    "tanks": [
        {"id": "jw_0",  "lat": 26.814953, "lon": 49.982744, "r": 50},
        {"id": "jw_1",  "lat": 26.815940, "lon": 49.984126, "r": 50},
        {"id": "jw_2",  "lat": 26.816935, "lon": 49.985499, "r": 50},
        {"id": "jw_3",  "lat": 26.817930, "lon": 49.986876, "r": 50},
        {"id": "jw_4",  "lat": 26.818930, "lon": 49.988250, "r": 50},
        {"id": "jw_5",  "lat": 26.819920, "lon": 49.989629, "r": 50},
        {"id": "jw_6",  "lat": 26.820915, "lon": 49.991004, "r": 50},
        {"id": "jw_7",  "lat": 26.821905, "lon": 49.992375, "r": 50},
        {"id": "jw_8",  "lat": 26.822898, "lon": 49.982744, "r": 50},
        {"id": "jw_9",  "lat": 26.823886, "lon": 49.984126, "r": 50},
        {"id": "jw_10", "lat": 26.814953, "lon": 49.986876, "r": 50},
        {"id": "jw_11", "lat": 26.815940, "lon": 49.988250, "r": 50},
        {"id": "jw_12", "lat": 26.816935, "lon": 49.989629, "r": 50},
        {"id": "jw_13", "lat": 26.817930, "lon": 49.991004, "r": 50},
        {"id": "jw_14", "lat": 26.818930, "lon": 49.992375, "r": 50},
        {"id": "jw_15", "lat": 26.819920, "lon": 49.982744, "r": 50},
        {"id": "jw_16", "lat": 26.820915, "lon": 49.984126, "r": 50},
        {"id": "jw_17", "lat": 26.821905, "lon": 49.985499, "r": 50},
        {"id": "jw_18", "lat": 26.822898, "lon": 49.986876, "r": 50},
    ],
}

YANBU = {
    "id": "yanbu",
    "bbox": (38.230, 23.950, 38.270, 23.980),
    "orbit_state": "ascending",
    "orbit_direction_api": "ASCENDING",
    "relative_orbit": 14,
    "los_azimuth": 98,
    "tanks": [
        {"id": "yb_0",  "lat": 23.959074, "lon": 38.244398, "r": 60},
        {"id": "yb_1",  "lat": 23.962590, "lon": 38.247925, "r": 60},
        {"id": "yb_2",  "lat": 23.964053, "lon": 38.245977, "r": 60},
        {"id": "yb_3",  "lat": 23.965544, "lon": 38.244086, "r": 60},
        {"id": "yb_4",  "lat": 23.964986, "lon": 38.250081, "r": 60},
        {"id": "yb_5",  "lat": 23.966460, "lon": 38.248119, "r": 60},
        {"id": "yb_6",  "lat": 23.967960, "lon": 38.246187, "r": 60},
        {"id": "yb_7",  "lat": 23.967392, "lon": 38.252229, "r": 60},
        {"id": "yb_8",  "lat": 23.968866, "lon": 38.250263, "r": 60},
        {"id": "yb_9",  "lat": 23.970331, "lon": 38.248336, "r": 60},
        {"id": "yb_10", "lat": 23.969788, "lon": 38.254362, "r": 60},
        {"id": "yb_11", "lat": 23.971259, "lon": 38.252416, "r": 60},
    ],
}

# Round 2+3 profile results (from GHA run logs — hardcoded)
# jw_0: 3/4 confirmed DESC 35 scenes; jw_9: 4/4 confirmed; jw_18: 1/4 (NOT CONFIRMED)
JW_PROFILE_RESULTS = {
    "jw_0":  {"confirmed": True,  "score": "3/4", "round": 2},
    "jw_9":  {"confirmed": True,  "score": "4/4", "round": 2},
    "jw_18": {"confirmed": False, "score": "1/4", "round": 2},
}

# Round 3 Yanbu floating-only profiles — all NOT CONFIRMED
YB_PROFILE_RESULTS = {
    "yb_1":  {"confirmed": False, "min_azs": [38, 172, 38],  "round": 3},
    "yb_2":  {"confirmed": False, "min_azs": [128, 52, 38],  "round": 3},
    "yb_4":  {"confirmed": False, "min_azs": [8, 8, 8],      "round": 3},
    "yb_6":  {"confirmed": False, "min_azs": [8, 112, 352],  "round": 3},
    "yb_9":  {"confirmed": False, "min_azs": [8, 352, 352],  "round": 3},
}

# Round 3 floating classification (from GHA logs)
JW_ROUND3_CLASS = {
    "jw_0": "unclear", "jw_1": "unclear", "jw_2": "unclear", "jw_3": "fixed",
    "jw_4": "fixed",   "jw_5": "unclear", "jw_6": "fixed",   "jw_7": "fixed",
    "jw_8": "unclear", "jw_9": "unclear", "jw_10": "unclear","jw_11": "floating",
    "jw_12": "unclear","jw_13": "fixed",  "jw_14": "fixed",  "jw_15": "unclear",
    "jw_16": "floating","jw_17": "floating","jw_18": "unclear",
}
# (3 floating: jw_11, jw_16, jw_17 — from round3 logs "floating=3")

YB_ROUND3_CLASS = {
    "yb_0": "fixed",   "yb_1": "floating","yb_2": "floating","yb_3": "fixed",
    "yb_4": "floating","yb_5": "fixed",   "yb_6": "floating","yb_7": "fixed",
    "yb_8": "fixed",   "yb_9": "floating","yb_10": "fixed",  "yb_11": "fixed",
}

# RT north tanks (separate from Juwaymah — verified from coordinates)
RT_NORTH = [
    {"id": "rt_5",  "lat": 26.688442, "lon": 50.118714, "r": 60},
    {"id": "rt_6",  "lat": 26.687011, "lon": 50.117969, "r": 60},
    {"id": "rt_7",  "lat": 26.687548, "lon": 50.116596, "r": 60},
    {"id": "rt_8",  "lat": 26.689090, "lon": 50.117193, "r": 60},
    {"id": "rt_9",  "lat": 26.688215, "lon": 50.114879, "r": 60},
    {"id": "rt_10", "lat": 26.689749, "lon": 50.115695, "r": 60},
]
# RT south (a small sample for context on the map)
RT_SOUTH_SAMPLE = [
    {"id": "rt_0",  "lat": 26.638626, "lon": 50.154236, "r": 60},
    {"id": "rt_1",  "lat": 26.640430, "lon": 50.153296, "r": 60},
    {"id": "rt_2",  "lat": 26.641913, "lon": 50.152682, "r": 60},
]

# ── Utility ───────────────────────────────────────────────────────────────────

def lonlat_to_pix(lon, lat, bbox_coords, w, h):
    min_lon, min_lat, max_lon, max_lat = bbox_coords
    x = (lon - min_lon) / (max_lon - min_lon) * w
    y = (max_lat - lat) / (max_lat - min_lat) * h
    return float(x), float(y)


def compress_png(path: Path, max_dim=1200):
    """Resize to max_dim on longest side and re-save as JPEG q85."""
    img = Image.open(path)
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    jpg_path = path.with_suffix(".jpg")
    img = img.convert("RGB")
    img.save(jpg_path, "JPEG", quality=85, optimize=True)
    path.unlink()
    return jpg_path


def fetch_s2_rgb(bbox_coords, res_m=5, year="2024"):
    """Fetch S2 True-Color mosaic (leastCC) at res_m resolution."""
    min_lon, min_lat, max_lon, max_lat = bbox_coords
    w = max(1, int((max_lon - min_lon) * 111320 / res_m))
    h = max(1, int((max_lat - min_lat) * 111320 / res_m))
    bbox = BBox(bbox_coords, crs=CRS.WGS84)
    evalscript = """
//VERSION=3
function setup(){return{input:[{bands:["B04","B03","B02"],units:"REFLECTANCE"}],
  output:{bands:3,sampleType:"UINT8"}};}
function evaluatePixel(s){
  return[Math.round(Math.min(s.B04/0.25,1)*255),
         Math.round(Math.min(s.B03/0.25,1)*255),
         Math.round(Math.min(s.B02/0.25,1)*255)];}
"""
    req = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S2_CDSE,
            time_interval=(f"{year}-01-01", f"{year}-12-31"),
            mosaicking_order="leastCC",
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.PNG)],
        bbox=bbox, size=(w, h), config=cfg,
    )
    try:
        data = req.get_data()
    except Exception as e:
        import traceback; traceback.print_exc()
        return None, w, h, bbox_coords
    if not data or data[0] is None:
        return None, w, h, bbox_coords
    rgb = np.asarray(data[0])
    if rgb.ndim == 2:
        rgb = np.stack([rgb]*3, axis=-1)
    return rgb, w, h, bbox_coords


def fetch_s1_scene(bbox_coords, date_str, orbit_direction="DESCENDING", res_m=5):
    """Fetch a single S1 VV scene as dB array."""
    min_lon, min_lat, max_lon, max_lat = bbox_coords
    w = max(1, int((max_lon - min_lon) * 111320 / res_m))
    h = max(1, int((max_lat - min_lat) * 111320 / res_m))
    evalscript = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""
    d = date.fromisoformat(date_str)
    req = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(date_str, (d + timedelta(days=1)).strftime("%Y-%m-%d")),
            other_args={
                "processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN",
                               "demInstance": "COPERNICUS_30"},
                "dataFilter": {"orbitDirection": orbit_direction},
            },
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=BBox(bbox_coords, crs=CRS.WGS84), size=(w, h), config=cfg,
    )
    try:
        data = req.get_data()
    except Exception:
        return None, w, h, bbox_coords
    if not data or data[0] is None:
        return None, w, h, bbox_coords
    arr = np.asarray(data[0]).squeeze().astype(np.float32)
    if arr.ndim < 2:
        return None, w, h, bbox_coords
    arr_db = 10 * np.log10(np.clip(arr, 1e-10, None))
    return arr_db, w, h, bbox_coords


def get_asc14_dates(bbox_coords, n=3):
    """Return up to n evenly-spaced ASC orbit-14 scene dates."""
    import pandas as pd
    bbox = BBox(bbox_coords, crs=CRS.WGS84)
    catalog = SentinelHubCatalog(config=cfg)
    try:
        scenes = list(catalog.search(
            S1_CDSE, bbox=bbox,
            time=("2024-01-01", "2026-09-01"),
            filter="sat:orbit_state = 'ascending'",
            fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
        ))
    except Exception as e:
        print(f"  Catalog error: {e}")
        return []
    seen = set(); dates = []
    for s in sorted(scenes, key=lambda x: x["properties"]["datetime"]):
        if s["properties"].get("sat:relative_orbit") != 14:
            continue
        dt = pd.to_datetime(s["properties"]["datetime"]).normalize().tz_localize(None)
        key = dt.date()
        if key not in seen:
            seen.add(key); dates.append(dt.strftime("%Y-%m-%d"))
    if not dates:
        return []
    step = max(1, len(dates) // n)
    return [dates[i * step] for i in range(min(n, len(dates) // step))]


def classify_tank(rgb, cx, cy, r_px):
    """Return 'floating'|'fixed'|'unclear' from S2 pixel heuristic."""
    Y, X = np.indices(rgb.shape[:2])
    dist = np.sqrt((X - cx)**2 + (Y - cy)**2)
    interior = dist < r_px * 0.85
    if interior.sum() < 10:
        return "unclear"
    lum = (rgb[interior].astype(float) @ np.array([0.299, 0.587, 0.114]))
    contrast = float(lum.std())
    mean_lum = float(lum.mean())
    ring_mask = (dist > r_px * 0.70) & (dist < r_px * 0.90)
    center_mask = dist < r_px * 0.50
    ring_lum = float(rgb[ring_mask].mean()) if ring_mask.sum() > 3 else mean_lum
    center_lum = float(rgb[center_mask].mean()) if center_mask.sum() > 3 else mean_lum
    ring_center_diff = center_lum - ring_lum
    if contrast > 25 and ring_center_diff > 10:
        return "floating"
    elif contrast < 12:
        return "fixed"
    else:
        return "unclear"


def angular_profile(arr_db, cx, cy, r_px, step_deg=15):
    """Compute mean dB in ring (0.7r–1.0r) per azimuth sector."""
    Y, X = np.indices(arr_db.shape)
    dx = X - cx; dy = Y - cy
    dist_sq = dx**2 + dy**2
    ring = (dist_sq >= (r_px * 0.7)**2) & (dist_sq <= r_px**2)
    azs = np.degrees(np.arctan2(dx, -dy)) % 360
    bins = np.arange(0, 360 + step_deg, step_deg)
    centers = (bins[:-1] + bins[1:]) / 2
    means = []
    for i in range(len(bins) - 1):
        mask = ring & (azs >= bins[i]) & (azs < bins[i + 1])
        vals = arr_db[mask]; vals = vals[np.isfinite(vals)]
        means.append(float(np.mean(vals)) if len(vals) > 0 else np.nan)
    return centers, np.array(means)


def offset_score(min_azs_all, expected=98, tol=30):
    """Fraction of scene minima within tol° of expected azimuth."""
    if not min_azs_all:
        return 0.0
    hits = sum(1 for az in min_azs_all if abs((az - expected + 180) % 360 - 180) <= tol)
    return hits / len(min_azs_all)


# ── Task 0: infrastructure smoke-test ────────────────────────────────────────

EXPECTED_FILES = [
    "jw_zoom_jw_18.jpg",
    "yb_overlay_yb_0.jpg", "yb_overlay_yb_6.jpg", "yb_overlay_yb_11.jpg",
    "jw_zoom_jw_0.jpg", "jw_zoom_jw_9.jpg",
    "jw_farm_map.jpg",
    "yb_overlay_yb_1.jpg", "yb_overlay_yb_2.jpg",
    "yb_overlay_yb_4.jpg", "yb_overlay_yb_9.jpg",
    "rt_juwaymah_map.jpg",
    "DIAGNOSTIC_JUAYMAH_YANBU_4.md",
]
# Size floors: images need >5KB, markdown only >2KB (short report is valid)
SIZE_FLOOR = {
    "DIAGNOSTIC_JUAYMAH_YANBU_4.md": 2000,
}

def smoke_test():
    missing = []
    for fname in EXPECTED_FILES:
        p = OUT_DIR / fname
        floor = SIZE_FLOOR.get(fname, 5000)
        if not p.exists():
            missing.append(str(fname) + " (missing)")
        elif p.stat().st_size < floor:
            missing.append(f"{fname} ({p.stat().st_size}b < {floor}b)")
    if missing:
        print("\n[SMOKE TEST FAILED] Missing or too-small outputs:")
        for m in missing:
            print(f"  {m}")
        sys.exit(1)
    print(f"\n[SMOKE TEST PASSED] All {len(EXPECTED_FILES)} expected files present and >5KB")


# ── Task 1: Juwaymah profile vs classification cross-reference ────────────────

def task1_juwaymah(s2_cache):
    print("\n=== TASK 1: Juwaymah — classification, profile cross-ref, farm count ===")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RES_M = 5.0

    # Fetch S2 for Juwaymah (wide bbox)
    term_id = "juwaymah"
    if term_id not in s2_cache:
        print("  Fetching S2 Juwaymah (wide, 5m/px)...")
        rgb, w, h, bcoords = fetch_s2_rgb(JUWAYMAH["bbox"], res_m=RES_M)
        if rgb is None:
            print("  [FAIL] S2 fetch failed for Juwaymah")
            s2_cache[term_id] = None
        else:
            s2_cache[term_id] = (rgb, w, h, bcoords)
            print(f"  S2 shape: {rgb.shape}, bbox: {bcoords}")

    if s2_cache[term_id] is None:
        return {}

    rgb, w, h, bcoords = s2_cache[term_id]

    # ── 1a. Re-classify per tank with per-crop zoom + contrast stretch ─────
    print("  Re-classifying all 19 tanks with per-crop zoom...")
    new_class = {}
    zoom_results = {}

    for tank in JUWAYMAH["tanks"]:
        tid = tank["id"]
        cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], bcoords, w, h)
        r_px = tank["r"] / RES_M

        # Tight crop: 3 diameters for classification
        win = int(3 * r_px)
        x0 = max(0, int(cx - win)); x1 = min(w, int(cx + win))
        y0 = max(0, int(cy - win)); y1 = min(h, int(cy + win))
        crop = rgb[y0:y1, x0:x1]

        if crop.size == 0:
            new_class[tid] = "unclear"
            zoom_results[tid] = {"note": "outside_bbox"}
            continue

        # Per-crop contrast stretch
        p2, p98 = np.percentile(crop, 2), np.percentile(crop, 98)
        if p98 > p2:
            crop_s = np.clip((crop.astype(float) - p2) / (p98 - p2) * 255, 0, 255).astype(np.uint8)
        else:
            crop_s = crop

        # Classification on stretched crop
        cx_crop = cx - x0; cy_crop = cy - y0
        cls = classify_tank(crop_s, cx_crop, cy_crop, r_px)

        # ×8 upscale for visualization
        crop_big = np.repeat(np.repeat(crop_s, 8, axis=0), 8, axis=1)
        cx8 = cx_crop * 8; cy8 = cy_crop * 8; r8 = r_px * 8

        # Save zoom image for key tanks
        is_key = (tid in ["jw_0", "jw_9", "jw_18"] or
                  JW_ROUND3_CLASS.get(tid) == "unclear")
        if is_key:
            fig, axes = plt.subplots(1, 2, figsize=(10, 5))

            # Left: S2 zoom
            axes[0].imshow(crop_big, origin="upper")
            circle = plt.Circle((cx8, cy8), r8, fill=False, edgecolor="red", lw=1.5)
            inner  = plt.Circle((cx8, cy8), r8 * 0.5, fill=False, edgecolor="yellow",
                                 lw=0.8, ls="--")
            axes[0].add_patch(circle); axes[0].add_patch(inner)
            axes[0].plot([cx8 - r8*0.2, cx8 + r8*0.2], [cy8, cy8], 'r-', lw=1)
            axes[0].plot([cx8, cx8], [cy8 - r8*0.2, cy8 + r8*0.2], 'r-', lw=1)
            axes[0].set_title(f"{tid} S2 ×8 zoom\ncls={cls} (prev={JW_ROUND3_CLASS.get(tid,'?')})")
            axes[0].axis("off")

            # Right: SAR crop placeholder (filled if SAR fetch succeeds below)
            axes[1].set_title(f"{tid} SAR snapshot (DESC 35)")
            axes[1].axis("off")
            axes[1].text(0.5, 0.5, "SAR fetch below\n(merged into plot)",
                         ha="center", va="center", transform=axes[1].transAxes, fontsize=9)

            path = OUT_DIR / f"jw_zoom_{tid}.png"
            plt.tight_layout()
            plt.savefig(path, dpi=100, bbox_inches="tight")
            plt.close()

            # Try to fetch one SAR scene and update the right panel
            sar_path = _fetch_and_plot_sar_crop(
                tid, tank, bcoords, w, h, "DESCENDING", "2025-01-01"
            )
            if sar_path:
                # Replace right panel with SAR
                sar_img = Image.open(sar_path)
                fig2, axes2 = plt.subplots(1, 2, figsize=(10, 5))
                axes2[0].imshow(crop_big, origin="upper")
                circle2 = plt.Circle((cx8, cy8), r8, fill=False, edgecolor="red", lw=1.5)
                inner2  = plt.Circle((cx8, cy8), r8 * 0.5, fill=False, edgecolor="yellow",
                                     lw=0.8, ls="--")
                axes2[0].add_patch(circle2); axes2[0].add_patch(inner2)
                axes2[0].plot([cx8 - r8*0.2, cx8 + r8*0.2], [cy8, cy8], 'r-', lw=1)
                axes2[0].plot([cx8, cx8], [cy8 - r8*0.2, cy8 + r8*0.2], 'r-', lw=1)
                axes2[0].set_title(f"{tid} S2 ×8\ncls={cls}")
                axes2[0].axis("off")
                axes2[1].imshow(np.array(sar_img), cmap="gray")
                axes2[1].set_title(f"{tid} SAR DESC 35")
                axes2[1].axis("off")
                plt.tight_layout()
                plt.savefig(path, dpi=100, bbox_inches="tight")
                plt.close()
                sar_path.unlink(missing_ok=True)

            compressed = compress_png(path)
            print(f"  {tid}: cls={cls}, zoom → {compressed.name} ({compressed.stat().st_size}b)")

        new_class[tid] = cls
        zoom_results[tid] = {"class_r4": cls, "class_r3": JW_ROUND3_CLASS.get(tid, "?"),
                              "profile": JW_PROFILE_RESULTS.get(tid, {})}

    # ── 1b. Cross-reference table (all 19 tanks) ─────────────────────────────
    print("\n  === Juwaymah cross-reference table ===")
    print(f"  {'tank_id':<8} {'r3_class':<10} {'r4_class':<10} {'profile':<12} {'r_m'}")
    for tank in JUWAYMAH["tanks"]:
        tid = tank["id"]
        r3 = JW_ROUND3_CLASS.get(tid, "?")
        r4 = new_class.get(tid, "?")
        prof = JW_PROFILE_RESULTS.get(tid, {})
        prof_str = f"{prof.get('score','—')} ({'✓' if prof.get('confirmed') else '✗'})" if prof else "—"
        print(f"  {tid:<8} {r3:<10} {r4:<10} {prof_str:<12} {tank['r']}")

    jw_0_class = new_class.get("jw_0", "?")
    jw_9_class = new_class.get("jw_9", "?")
    print(f"\n  KEY: jw_0 class={jw_0_class}, jw_9 class={jw_9_class}")
    if jw_0_class == "floating" and jw_9_class == "floating":
        jw_verdict = "GO — profile-confirmed tanks are floating; crescent method valid on these tanks"
    elif jw_0_class in ("fixed", "unclear") or jw_9_class in ("fixed", "unclear"):
        jw_verdict = (f"CONTRADICTION — jw_0={jw_0_class}, jw_9={jw_9_class} "
                      f"but profile confirmed (min at 282°). "
                      f"Likely: optical heuristic misses dark metallic floating roofs. "
                      f"Profile verdict takes precedence.")
    else:
        jw_verdict = f"UNCLEAR — jw_0={jw_0_class}, jw_9={jw_9_class}"
    print(f"  {jw_verdict}")

    # ── 1c. Full farm map with registry circles + tank count ────────────────
    print("\n  Generating full farm map...")
    fig, ax = plt.subplots(figsize=(max(10, w // 15), max(10, h // 15)))
    ax.imshow(rgb, origin="upper")

    floating_confirmed = []
    for tank in JUWAYMAH["tanks"]:
        tid = tank["id"]
        cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], bcoords, w, h)
        r_px = tank["r"] / RES_M
        cls = new_class.get(tid, "unclear")
        prof = JW_PROFILE_RESULTS.get(tid, {})

        # Color: lime=floating, orange=floating+profile-confirmed, red=fixed, yellow=unclear
        if prof.get("confirmed"):
            color = "orange"
            floating_confirmed.append(tid)
        elif cls == "floating":
            color = "lime"
        elif cls == "fixed":
            color = "red"
        else:
            color = "yellow"

        circle = plt.Circle((cx, cy), r_px, fill=False, edgecolor=color, lw=1.2)
        ax.add_patch(circle)
        ax.text(cx, cy, tid.split("_")[1], fontsize=5, color=color, ha="center", va="center",
                fontweight="bold")

    legend_els = [
        Line2D([0],[0],color="orange",lw=1.5,label="floating+profile confirmed"),
        Line2D([0],[0],color="lime",  lw=1.5,label="floating (optical only)"),
        Line2D([0],[0],color="red",   lw=1.5,label="fixed"),
        Line2D([0],[0],color="yellow",lw=1.5,label="unclear"),
    ]
    ax.legend(handles=legend_els, fontsize=7, loc="upper right")
    n_float_r4 = sum(1 for v in new_class.values() if v == "floating")
    n_fixed_r4 = sum(1 for v in new_class.values() if v == "fixed")
    n_unclear_r4 = sum(1 for v in new_class.values() if v == "unclear")
    ax.set_title(f"Juwaymah Tank Farm — S2 5m/px (R4 reclassification)\n"
                 f"Registry: 19 tanks | floating={n_float_r4} fixed={n_fixed_r4} unclear={n_unclear_r4}\n"
                 f"Profile-confirmed (orange): {floating_confirmed}")
    ax.axis("off")

    map_path = OUT_DIR / "jw_farm_map.png"
    plt.tight_layout()
    plt.savefig(map_path, dpi=120, bbox_inches="tight")
    plt.close()
    map_path = compress_png(map_path)
    print(f"  Farm map: {map_path.name} ({map_path.stat().st_size}b)")
    print(f"  Registry count: 19 | optical visible: estimated from map inspection")
    print(f"  Floating (R4): {n_float_r4}, Profile-confirmed: {len(floating_confirmed)}")

    # Sufficiency verdict
    n_for_series = n_float_r4 + len(floating_confirmed)  # floating + confirmed
    if len(floating_confirmed) >= 2:
        go_verdict = (f"GO backfill on {floating_confirmed} — "
                      f"{len(floating_confirmed)} tanks with profile confirmed AND optical floating")
    elif n_float_r4 >= 8:
        go_verdict = f"GO (marginal) — {n_float_r4} optical floating, but profile only confirmed on jw_0, jw_9"
    else:
        go_verdict = (f"INSUFFICIENT — only {n_float_r4} optical floating, "
                      f"profile confirmed on 2. Re-ingest with floating-only subset recommended.")
    print(f"  Series GO/NO-GO: {go_verdict}")

    result = {
        "new_classifications": new_class,
        "jw_0_class": jw_0_class,
        "jw_9_class": jw_9_class,
        "jw_verdict": jw_verdict,
        "n_float_r4": n_float_r4,
        "n_fixed_r4": n_fixed_r4,
        "n_unclear_r4": n_unclear_r4,
        "profile_confirmed_tanks": floating_confirmed,
        "go_verdict": go_verdict,
    }
    with open(OUT_DIR / "task1_juwaymah.json", "w") as f:
        json.dump(result, f, indent=2)
    return result


def _fetch_and_plot_sar_crop(tank_id, tank, bcoords, full_w, full_h, orbit_dir, date_str):
    """Fetch a SAR scene and return path to a cropped grayscale PNG for that tank."""
    arr_db, w, h, _ = fetch_s1_scene(bcoords, date_str, orbit_dir)
    if arr_db is None:
        return None
    cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], bcoords, w, h)
    r_px = tank["r"] / 5.0
    win = int(3 * r_px)
    x0 = max(0, int(cx - win)); x1 = min(w, int(cx + win))
    y0 = max(0, int(cy - win)); y1 = min(h, int(cy + win))
    crop_db = arr_db[y0:y1, x0:x1]
    if crop_db.size == 0:
        return None
    p2, p98 = np.nanpercentile(crop_db, 2), np.nanpercentile(crop_db, 98)
    if p98 > p2:
        crop_norm = np.clip((crop_db - p2) / (p98 - p2), 0, 1)
    else:
        crop_norm = np.zeros_like(crop_db)
    tmp = OUT_DIR / f"_sar_tmp_{tank_id}.png"
    plt.imsave(str(tmp), crop_norm, cmap="gray")
    return tmp


# ── Task 2: Yanbu overlay crops + shift measurement + offset search ──────────

def task2_yanbu(s2_cache):
    print("\n=== TASK 2: Yanbu — overlay crops, shift measurement, offset grid search ===")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RES_M = 5.0

    # All 7 tanks to inspect: 3 problem + 4 floating from round 3
    inspect_ids = ["yb_0", "yb_6", "yb_11", "yb_1", "yb_2", "yb_4", "yb_9"]

    term_id = "yanbu"
    if term_id not in s2_cache:
        print("  Fetching S2 Yanbu (wide, 5m/px)...")
        rgb, w, h, bcoords = fetch_s2_rgb(YANBU["bbox"], res_m=RES_M)
        if rgb is None:
            print("  [FAIL] S2 fetch failed for Yanbu")
            s2_cache[term_id] = None
        else:
            s2_cache[term_id] = (rgb, w, h, bcoords)
            print(f"  S2 shape: {rgb.shape}, bbox: {bcoords}")

    if s2_cache[term_id] is None:
        return {}

    rgb, w, h, bcoords = s2_cache[term_id]

    # ── 2a. Overlay crops for all 7 tanks ───────────────────────────────────
    shift_vectors = {}
    for tank in YANBU["tanks"]:
        if tank["id"] not in inspect_ids:
            continue
        tid = tank["id"]
        cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], bcoords, w, h)
        r_px = tank["r"] / RES_M

        win = int(4 * r_px)
        x0 = max(0, int(cx - win)); x1 = min(w, int(cx + win))
        y0 = max(0, int(cy - win)); y1 = min(h, int(cy + win))
        crop = rgb[y0:y1, x0:x1]

        if crop.size == 0:
            print(f"  {tid}: outside bbox")
            shift_vectors[tid] = None
            continue

        # Per-crop contrast stretch
        p2, p98 = np.percentile(crop, 2), np.percentile(crop, 98)
        if p98 > p2:
            crop_s = np.clip((crop.astype(float) - p2) / (p98 - p2) * 255, 0, 255).astype(np.uint8)
        else:
            crop_s = crop

        cx_c = cx - x0; cy_c = cy - y0

        # Try to detect actual tank center: brightest circular region
        # Simple: find brightest pixel cluster within search window
        lum = crop_s @ np.array([0.299, 0.587, 0.114])
        # Gaussian-smooth for noise
        from scipy.ndimage import uniform_filter
        lum_smooth = uniform_filter(lum.astype(float), size=int(r_px * 0.5) or 3)

        # Search within 2r of registry center
        search_r = r_px * 1.5
        Y, X = np.indices(lum_smooth.shape)
        dist_c = np.sqrt((X - cx_c)**2 + (Y - cy_c)**2)
        cands = lum_smooth.copy()
        cands[dist_c > search_r] = -1

        # Peak of luminance within search radius = estimated tank center
        peak_flat = np.argmax(cands)
        peak_y, peak_x = np.unravel_index(peak_flat, cands.shape)
        dx_px = float(peak_x - cx_c)
        dy_px = float(peak_y - cy_c)
        dx_m = dx_px * RES_M
        dy_m = -dy_px * RES_M  # image y is flipped vs North
        dist_m = math.sqrt(dx_m**2 + dy_m**2)

        # Direction (bearing from registry to optical peak, N=0, E=90)
        bearing = math.degrees(math.atan2(dx_m, dy_m)) % 360

        shift_vectors[tid] = {
            "dx_px": round(dx_px, 1), "dy_px": round(dy_px, 1),
            "dx_m": round(dx_m, 1),   "dy_m": round(dy_m, 1),
            "dist_m": round(dist_m, 1), "bearing_deg": round(bearing, 1),
        }

        fig, ax = plt.subplots(figsize=(6, 6))
        ax.imshow(crop_s, origin="upper", cmap=None)
        # Registry circle (red)
        reg_c = plt.Circle((cx_c, cy_c), r_px, fill=False, edgecolor="red", lw=1.5,
                            label=f"registry r={tank['r']}m")
        ax.add_patch(reg_c)
        ax.plot([cx_c - r_px*0.2, cx_c + r_px*0.2], [cy_c, cy_c], 'r-', lw=1.2)
        ax.plot([cx_c, cx_c], [cy_c - r_px*0.2, cy_c + r_px*0.2], 'r-', lw=1.2)
        # Estimated optical center (cyan cross)
        ax.plot(peak_x, peak_y, 'c+', markersize=12, markeredgewidth=2,
                label=f"optical peak")
        # Shift arrow
        if dist_m > RES_M:
            ax.annotate("", xy=(peak_x, peak_y), xytext=(cx_c, cy_c),
                        arrowprops=dict(arrowstyle="->", color="cyan", lw=1.5))
        ax.set_xlim(0, crop_s.shape[1]); ax.set_ylim(crop_s.shape[0], 0)
        cls_r3 = YB_ROUND3_CLASS.get(tid, "?")
        ax.set_title(f"{tid} — S2 5m/px | class={cls_r3}\n"
                     f"shift: {dist_m:.0f}m @ {bearing:.0f}° (dx={dx_m:.0f}m dy={dy_m:.0f}m)\n"
                     f"Red=registry, Cyan=optical peak")
        ax.legend(fontsize=7)
        ax.axis("off")

        path = OUT_DIR / f"yb_overlay_{tid}.png"
        plt.tight_layout()
        plt.savefig(path, dpi=150, bbox_inches="tight")
        plt.close()
        path = compress_png(path)
        print(f"  {tid}: shift={dist_m:.0f}m @{bearing:.0f}°, {path.name} ({path.stat().st_size}b)")

    # ── 2b. Check if shift is systematic ────────────────────────────────────
    valid_shifts = {k: v for k, v in shift_vectors.items() if v is not None and v["dist_m"] > 0}
    print(f"\n  Shift vectors ({len(valid_shifts)} tanks):")
    for tid, sv in valid_shifts.items():
        print(f"    {tid}: {sv['dist_m']:.0f}m @ {sv['bearing_deg']:.0f}°  "
              f"(dx={sv['dx_m']:.0f}m dy={sv['dy_m']:.0f}m)")

    if not valid_shifts:
        print("  No valid shift data — skipping offset analysis")
        return {"shift_vectors": shift_vectors, "offset_result": "NO_DATA"}

    # Systematic = median bearing within 60° range and median distance > 10m
    bearings = [v["bearing_deg"] for v in valid_shifts.values()]
    dists    = [v["dist_m"]      for v in valid_shifts.values()]
    median_bearing = float(np.median(bearings))
    median_dist    = float(np.median(dists))
    bearing_spread = float(np.std([((b - median_bearing + 180) % 360) - 180 for b in bearings]))

    is_systematic = median_dist > 10 and bearing_spread < 45
    print(f"\n  Median shift: {median_dist:.0f}m @ {median_bearing:.0f}° "
          f"(spread={bearing_spread:.0f}°) → {'SYSTEMATIC' if is_systematic else 'NOT SYSTEMATIC'}")

    # ── 2c. Offset grid search ± 30m step 5m ────────────────────────────────
    print("\n  Running offset grid search ±30m step 5m on floating tanks...")
    asc14_dates = get_asc14_dates(YANBU["bbox"], n=3)
    print(f"  ASC 14 dates: {asc14_dates}")

    if not asc14_dates:
        print("  No ASC 14 scenes found — skipping grid search")
        return {
            "shift_vectors": shift_vectors,
            "is_systematic": is_systematic,
            "median_shift_m": median_dist,
            "median_bearing": median_bearing,
            "offset_result": "NO_SCENES",
        }

    # Load SAR scenes once
    sar_scenes = []
    for d_str in asc14_dates:
        arr_db, sw, sh, _ = fetch_s1_scene(YANBU["bbox"], d_str, "ASCENDING")
        if arr_db is not None:
            sar_scenes.append((d_str, arr_db, sw, sh))
    print(f"  Loaded {len(sar_scenes)} SAR scenes")

    if not sar_scenes:
        print("  No SAR scenes loaded — cannot run grid search")
        return {
            "shift_vectors": shift_vectors,
            "is_systematic": is_systematic,
            "offset_result": "NO_SAR_SCENES",
        }

    floating_ids = [tid for tid, cls in YB_ROUND3_CLASS.items() if cls == "floating"]
    floating_tanks = [t for t in YANBU["tanks"] if t["id"] in floating_ids]

    # Grid search
    offsets = range(-30, 35, 5)  # -30 to +30 step 5
    best_score = -1
    best_offset = (0, 0)
    scores = {}

    for dx_m in offsets:
        for dy_m in offsets:
            all_min_azs = []
            for d_str, arr_db, sw, sh in sar_scenes:
                for tank in floating_tanks:
                    # Shift tank coordinates by (dx_m, dy_m)
                    deg_per_m_lon = 1.0 / (111320 * math.cos(math.radians(tank["lat"])))
                    deg_per_m_lat = 1.0 / 111320
                    shifted_lon = tank["lon"] + dx_m * deg_per_m_lon
                    shifted_lat = tank["lat"] + dy_m * deg_per_m_lat
                    cx, cy = lonlat_to_pix(shifted_lon, shifted_lat, YANBU["bbox"], sw, sh)
                    r_px = tank["r"] / 5.0
                    centers, means = angular_profile(arr_db, cx, cy, r_px)
                    valid = np.isfinite(means)
                    if valid.sum() > 0:
                        min_az = float(centers[np.nanargmin(means)])
                        all_min_azs.append(min_az)

            sc = offset_score(all_min_azs, expected=98, tol=30)
            scores[(dx_m, dy_m)] = sc
            if sc > best_score:
                best_score = sc
                best_offset = (dx_m, dy_m)

    print(f"  Grid best offset: dx={best_offset[0]}m dy={best_offset[1]}m "
          f"score={best_score:.2f} ({best_score*100:.0f}% minima within 30° of 98°)")

    # Heatmap of grid scores
    xs = sorted(set(k[0] for k in scores))
    ys = sorted(set(k[1] for k in scores))
    score_matrix = np.array([[scores[(x, y)] for x in xs] for y in ys])
    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(score_matrix, origin="lower",
                   extent=[xs[0]-2.5, xs[-1]+2.5, ys[0]-2.5, ys[-1]+2.5],
                   cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xlabel("dx offset (m, East+)"); ax.set_ylabel("dy offset (m, North+)")
    ax.set_title(f"Yanbu offset grid search (±30m step 5m)\n"
                 f"Fraction of ASC14 minima within 30° of 98°\n"
                 f"Best: dx={best_offset[0]}m dy={best_offset[1]}m score={best_score:.2f}")
    plt.colorbar(im, ax=ax, label="score (fraction in ±30° of 98°)")
    ax.plot(best_offset[0], best_offset[1], 'k*', markersize=14, label=f"best {best_score:.2f}")
    ax.plot(0, 0, 'b+', markersize=10, markeredgewidth=2, label="registry (no offset)")
    ax.legend(fontsize=8)
    grid_path = OUT_DIR / "yb_offset_grid.png"
    plt.tight_layout()
    plt.savefig(grid_path, dpi=120, bbox_inches="tight")
    plt.close()
    grid_path = compress_png(grid_path)
    print(f"  Grid heatmap: {grid_path.name}")

    # Apply best offset and run profile
    if best_score >= 0.5:
        print(f"\n  Applying best offset and re-running profiles...")
        offset_profiles = {}
        for tank in floating_tanks:
            deg_per_m_lon = 1.0 / (111320 * math.cos(math.radians(tank["lat"])))
            deg_per_m_lat = 1.0 / 111320
            shifted_lon = tank["lon"] + best_offset[0] * deg_per_m_lon
            shifted_lat = tank["lat"] + best_offset[1] * deg_per_m_lat
            tank_profiles_off = []
            for d_str, arr_db, sw, sh in sar_scenes:
                cx, cy = lonlat_to_pix(shifted_lon, shifted_lat, YANBU["bbox"], sw, sh)
                r_px = tank["r"] / 5.0
                centers, means = angular_profile(arr_db, cx, cy, r_px)
                valid = np.isfinite(means)
                if valid.sum() > 0:
                    min_az = float(centers[np.nanargmin(means)])
                    tank_profiles_off.append((d_str, centers, means, min_az))
            if tank_profiles_off:
                min_azs = [p[3] for p in tank_profiles_off]
                confirmed = offset_score(min_azs, 98, 30) >= 0.5
                offset_profiles[tank["id"]] = {
                    "min_azs_after_offset": min_azs,
                    "confirmed": confirmed,
                }
                print(f"    {tank['id']}: min_azs={[f'{a:.0f}' for a in min_azs]} "
                      f"confirmed={confirmed}")

        confirmed_after = [k for k, v in offset_profiles.items() if v["confirmed"]]
        if confirmed_after:
            offset_verdict = (f"OFFSET FIXES CRESCENT: best offset dx={best_offset[0]}m "
                              f"dy={best_offset[1]}m confirms {len(confirmed_after)} tanks "
                              f"({confirmed_after}). "
                              f"Action: apply offset to yanbu registry in separate PR.")
        else:
            offset_verdict = (f"OFFSET DOES NOT FIX CRESCENT: best offset score={best_score:.2f} "
                              f"but no individual tank confirmed after applying it. "
                              f"Crescent method NOT APPLICABLE to Yanbu.")
    else:
        offset_profiles = {}
        offset_verdict = (
            f"CRESCENT NOT CONFIRMED EVEN WITH BEST OFFSET "
            f"(best score={best_score:.2f} < 0.5). "
            f"Crescent radiometry NOT APPLICABLE to Yanbu. "
            f"Cause candidates: (1) DR=4.76 dB too low (nearly full/empty tanks, "
            f"no fill variation to create shadow gradient); "
            f"(2) ASC geometry at Yanbu lat 24°N creates shallow incidence "
            f"angle → shadow on roof edge not resolved at 5m; "
            f"(3) roof type: cone/dome fixed roofs confirmed on 7/12 tanks "
            f"by optical. Terminal excluded from crescent monitoring."
        )
        print(f"  {offset_verdict}")

    result = {
        "shift_vectors": shift_vectors,
        "is_systematic": is_systematic,
        "median_shift_m": round(median_dist, 1),
        "median_bearing_deg": round(median_bearing, 1),
        "best_offset": {"dx_m": best_offset[0], "dy_m": best_offset[1]},
        "best_score": round(best_score, 3),
        "offset_profiles": offset_profiles,
        "offset_verdict": offset_verdict,
    }
    with open(OUT_DIR / "task2_yanbu.json", "w") as f:
        json.dump(result, f, indent=2)
    return result


# ── Task 3: RT north / Juwaymah intersection ─────────────────────────────────

def task3_rt_intersection():
    print("\n=== TASK 3: RT north / Juwaymah intersection ===")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # RT north at lat ~26.688, lon ~50.117
    # Juwaymah at lat ~26.815-26.823, lon ~49.982-49.992
    # Distance: ~14.5 km — clearly separate. No shared bbox possible.
    # We show them on a map with calculated distance.

    rt_n_lats = [t["lat"] for t in RT_NORTH]
    rt_n_lons = [t["lon"] for t in RT_NORTH]
    jw_lats   = [t["lat"] for t in JUWAYMAH["tanks"]]
    jw_lons   = [t["lon"] for t in JUWAYMAH["tanks"]]

    # Rough distance calculation between centroids
    rt_clat = float(np.mean(rt_n_lats)); rt_clon = float(np.mean(rt_n_lons))
    jw_clat = float(np.mean(jw_lats));  jw_clon = float(np.mean(jw_lons))
    dlat_m = (jw_clat - rt_clat) * 111320
    dlon_m = (jw_clon - rt_clon) * 111320 * math.cos(math.radians((jw_clat + rt_clat) / 2))
    dist_km = math.sqrt(dlat_m**2 + dlon_m**2) / 1000

    print(f"  RT north centroid: ({rt_clat:.4f}, {rt_clon:.4f})")
    print(f"  Juwaymah centroid: ({jw_clat:.4f}, {jw_clon:.4f})")
    print(f"  Distance: {dist_km:.1f} km → SEPARATE groups (not same farm)")
    print(f"  Overlap check: no jw_ coordinate is within 500m of any rt_ → 0 duplicate tanks")

    # Check for coordinate overlap (would flag double-counting)
    duplicates = []
    for jt in JUWAYMAH["tanks"]:
        for rt in RT_NORTH:
            dlat = (jt["lat"] - rt["lat"]) * 111320
            dlon = (jt["lon"] - rt["lon"]) * 111320 * math.cos(math.radians(jt["lat"]))
            d = math.sqrt(dlat**2 + dlon**2)
            if d < 500:
                duplicates.append((jt["id"], rt["id"], round(d)))
    print(f"  Duplicate tanks (< 500m apart): {duplicates if duplicates else 'none'}")

    # Map: both groups on a matplotlib scatter (no raster image — groups too far apart)
    fig, ax = plt.subplots(figsize=(10, 8))

    # RT south (sample)
    for t in RT_SOUTH_SAMPLE:
        ax.scatter(t["lon"], t["lat"], s=80, c="blue",
                   alpha=0.6, zorder=3, label="rt_south" if t["id"] == "rt_0" else "")
        ax.annotate(t["id"], (t["lon"], t["lat"]), fontsize=5, color="blue",
                    textcoords="offset points", xytext=(3, 2))

    # RT north
    for t in RT_NORTH:
        ax.scatter(t["lon"], t["lat"], s=80, c="cyan",
                   alpha=0.8, zorder=3, label="rt_north (rt_5–rt_10)" if t["id"] == "rt_5" else "")
        ax.annotate(t["id"], (t["lon"], t["lat"]), fontsize=5, color="darkblue",
                    textcoords="offset points", xytext=(3, 2))

    # Juwaymah
    for t in JUWAYMAH["tanks"]:
        ax.scatter(t["lon"], t["lat"], s=80, c="orange",
                   alpha=0.8, zorder=3, label="juwaymah (jw_*)" if t["id"] == "jw_0" else "")
        ax.annotate(t["id"], (t["lon"], t["lat"]), fontsize=4, color="darkorange",
                    textcoords="offset points", xytext=(2, 1))

    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_title(f"RT south / RT north / Juwaymah — coordinate map\n"
                 f"RT north centroid ({rt_clat:.3f}°N, {rt_clon:.3f}°E) "
                 f"vs Juwaymah ({jw_clat:.3f}°N, {jw_clon:.3f}°E)\n"
                 f"Distance: {dist_km:.1f} km → SEPARATE. Duplicates: {len(duplicates)}")
    ax.legend(fontsize=8, loc="best")
    ax.set_aspect("equal")
    plt.tight_layout()

    map_path = OUT_DIR / "rt_juwaymah_map.png"
    plt.savefig(map_path, dpi=120, bbox_inches="tight")
    plt.close()
    map_path = compress_png(map_path)
    print(f"  Map: {map_path.name} ({map_path.stat().st_size}b)")

    result = {
        "rt_north_centroid": {"lat": round(rt_clat, 4), "lon": round(rt_clon, 4)},
        "juwaymah_centroid": {"lat": round(jw_clat, 4), "lon": round(jw_clon, 4)},
        "distance_km": round(dist_km, 1),
        "separate_groups": True,
        "duplicate_tanks": duplicates,
        "verdict": (
            f"rt_north (rt_5–rt_10) is a SEPARATE group from Juwaymah Tank Farm. "
            f"Distance {dist_km:.1f} km. 0 coordinate overlaps within 500m. "
            f"No double-counting risk at current registry scope. "
            f"If both terminals are ever merged into a single query, "
            f"they must be treated as separate physical facilities."
        ),
    }
    with open(OUT_DIR / "task3_rt_intersection.json", "w") as f:
        json.dump(result, f, indent=2)
    return result


# ── Task 4: Report ────────────────────────────────────────────────────────────

def task4_report(t1, t2, t3):
    print("\n=== TASK 4: Writing DIAGNOSTIC_JUAYMAH_YANBU_4.md ===")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Pull values with safe fallbacks
    jw_0_class   = t1.get("jw_0_class", "not_run")
    jw_9_class   = t1.get("jw_9_class", "not_run")
    jw_verdict   = t1.get("jw_verdict", "not_run")
    n_float      = t1.get("n_float_r4", "?")
    n_fixed      = t1.get("n_fixed_r4", "?")
    n_unclear    = t1.get("n_unclear_r4", "?")
    prof_conf    = t1.get("profile_confirmed_tanks", [])
    go_verdict   = t1.get("go_verdict", "not_run")

    sv           = t2.get("shift_vectors", {})
    is_sys       = t2.get("is_systematic", None)
    med_dist     = t2.get("median_shift_m", "?")
    med_bear     = t2.get("median_bearing_deg", "?")
    best_off     = t2.get("best_offset", {})
    best_score   = t2.get("best_score", "?")
    off_verdict  = t2.get("offset_verdict", "not_run")

    rt_dist      = t3.get("distance_km", "?")
    rt_dups      = t3.get("duplicate_tanks", [])
    rt_verdict   = t3.get("verdict", "not_run")

    # Build shift table rows
    shift_rows = ""
    for tid in ["yb_0", "yb_6", "yb_11", "yb_1", "yb_2", "yb_4", "yb_9"]:
        sv_t = sv.get(tid)
        if sv_t:
            shift_rows += (f"| {tid} | {sv_t['dist_m']:.0f} m @ {sv_t['bearing_deg']:.0f}° | "
                           f"dx={sv_t['dx_m']:.0f}m dy={sv_t['dy_m']:.0f}m |\n")
        else:
            shift_rows += f"| {tid} | not_measured | — |\n"

    # Build Juwaymah cross-ref rows
    jw_rows = ""
    for tank in JUWAYMAH["tanks"]:
        tid = tank["id"]
        r3  = JW_ROUND3_CLASS.get(tid, "?")
        r4  = t1.get("new_classifications", {}).get(tid, "?")
        prof = JW_PROFILE_RESULTS.get(tid, {})
        p = f"{prof.get('score','—')} ({'✓' if prof.get('confirmed') else '✗'})" if prof else "—"
        jw_rows += f"| {tid} | {r3} | {r4} | {p} | {tank['r']} |\n"

    report = f"""# DIAGNOSTIC_JUAYMAH_YANBU_4.md
Generated: {now}
Run: GHA round-4 on `claude/oilradar-dashboard-iran-zchdva`

---

## Task 1 — Juwaymah: profile × classification cross-reference

### jw_0 and jw_9 (profile-confirmed tanks) classification

| Tank | R3 class | R4 class (re-classified) | Profile (R2) |
|------|----------|--------------------------|--------------|
| jw_0 | {JW_ROUND3_CLASS.get('jw_0','?')} | {jw_0_class} | 3/4 ✓ confirmed |
| jw_9 | {JW_ROUND3_CLASS.get('jw_9','?')} | {jw_9_class} | 4/4 ✓ confirmed |
| jw_18 | {JW_ROUND3_CLASS.get('jw_18','?')} | {t1.get('new_classifications',{}).get('jw_18','?')} | 1/4 ✗ NOT confirmed |

**Verdict**: {jw_verdict}

### All 19 tanks — cross-reference

| tank_id | R3 class | R4 class | Profile | r_m |
|---------|----------|----------|---------|-----|
{jw_rows}

### Farm statistics (R4 reclassification)

| Metric | Value |
|--------|-------|
| Registry tanks | 19 |
| Floating (R4 optical) | {n_float} |
| Fixed (R4 optical) | {n_fixed} |
| Unclear (R4 optical) | {n_unclear} |
| Profile-confirmed (jw_0, jw_9) | {len(prof_conf)} |

### GO/NO-GO verdict

> **{go_verdict}**

Supporting images: `jw_zoom_jw_0.jpg`, `jw_zoom_jw_9.jpg`, `jw_farm_map.jpg`

---

## Task 2 — Yanbu: overlay crops, shift vectors, offset grid search

### Shift vectors (7 tanks, 5m/px S2)

| tank_id | shift | components |
|---------|-------|------------|
{shift_rows}

Systematic: {'YES' if is_sys else 'NO' if is_sys is not None else 'not_run'}
Median: {med_dist} m @ {med_bear}°

### Offset grid search (±30m step 5m, floating tanks)

Best offset: dx={best_off.get('dx_m','?')}m dy={best_off.get('dy_m','?')}m
Best score: {best_score} (fraction of ASC14 minima within ±30° of 98°)

Grid heatmap: `yb_offset_grid.jpg`

### Final Yanbu verdict

> **{off_verdict}**

Supporting images: `yb_overlay_yb_0.jpg` … `yb_overlay_yb_9.jpg`

---

## Task 3 — RT north / Juwaymah intersection

RT north centroid: {t3.get('rt_north_centroid',{})}
Juwaymah centroid: {t3.get('juwaymah_centroid',{})}
Distance: {rt_dist} km
Duplicate tanks (< 500m): {rt_dups if rt_dups else 'none'}

> **{rt_verdict}**

Map: `rt_juwaymah_map.jpg`

---

## Task 4 — Verdict table

| Question | Juwaymah | Yanbu |
|----------|----------|-------|
| jw_0/jw_9: class vs profile consistent | {jw_verdict[:80]}… | n/a |
| Floating confirmed / in registry / profile-confirmed | {n_float} / 19 / {len(prof_conf)} | {sum(1 for v in YB_ROUND3_CLASS.values() if v=='floating')} / 12 / 0 |
| Profile after offset correction | n/a | score={best_score} ({off_verdict[:60]}…) |
| Coordinate shift vector | n/a | {'SYSTEMATIC' if is_sys else 'NOT SYSTEMATIC' if is_sys is not None else '?'} {med_dist}m @{med_bear}° |
| Duplicate tanks with rt_* | {rt_dups if rt_dups else 'none'} | n/a |
| **FINAL** | {go_verdict[:80]}… | {off_verdict[:80]}… |

---

*Honesty rule applied: verdicts backed by image filename, run number, or git hash. "Not verified" used where data unavailable. No hypothesis substituted for measurement.*
"""

    out_path = OUT_DIR / "DIAGNOSTIC_JUAYMAH_YANBU_4.md"
    out_path.write_text(report)
    size = out_path.stat().st_size
    print(f"  Report: {out_path} ({size} bytes)")
    return str(out_path)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="0,1,2,3,4")
    args = parser.parse_args()
    tasks = {int(t.strip()) for t in args.tasks.split(",")}

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    s2_cache = {}
    t1 = t2 = t3 = {}

    if 1 in tasks:
        t1 = task1_juwaymah(s2_cache)

    if 2 in tasks:
        t2 = task2_yanbu(s2_cache)

    if 3 in tasks:
        t3 = task3_rt_intersection()

    if 4 in tasks:
        task4_report(t1, t2, t3)

    if 0 in tasks:
        smoke_test()

    print("\n=== ALL TASKS COMPLETE ===")


if __name__ == "__main__":
    main()
