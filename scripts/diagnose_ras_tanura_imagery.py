"""
diagnose_ras_tanura_imagery.py — Standalone diagnostic for Ras Tanura SAR data quality.

Downloads Sentinel-1 scenes for Ras Tanura over a configurable period with a matrix
of parameter variants (orbit, resolution, polarisation, cluster bbox) and saves:
  - Raw GeoTIFF images
  - Percentile-stretched PNG visualisations
  - Overlay PNG (tank circles + crescent masks)
  - Side-by-side ASC vs DESC overlay comparison
  - Zoom crops of 3 reference tanks
  - Sentinel-2 optical image + tank overlay
  - Per-scene JSON metadata
  - diagnostics_summary.csv

Nothing is written to Supabase. No production code is modified.
Run: python scripts/diagnose_ras_tanura_imagery.py

Dependencies: sentinelhub, numpy, pandas, matplotlib, Pillow (PIL), scipy, tifffile
"""

import os
import sys
import json
import math
import time
import csv
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ── Constants (edit here) ─────────────────────────────────────────────────────

DIAG_START = "2026-07-16"
DIAG_END   = "2026-09-16"
MIN_SCENES_PER_CLUSTER = 8  # if fewer found, expand window to 3 months

# Reference tanks for per-scene statistics
REF_TANK_60M  = "rt_0"    # large tank (radius=60m), south cluster
REF_TANK_35M  = "rt_11"   # small tank (radius=35m), south cluster
REF_TANK_NORTH = "rt_5"   # north cluster (radius=60m)

# Production pipeline parameters (for comparison in metadata)
PROD_RESOLUTION     = 5         # m/px
PROD_LOS_AZIMUTH    = 282       # degrees
PROD_WIDTH_WIDE     = 15        # metres annulus
PROD_BACK_COEFF     = "GAMMA0_TERRAIN"
PROD_ORTHORECTIFY   = True
PROD_DEM            = "COPERNICUS_30"
PROD_ORBIT_STATE    = "descending"
PROD_POLARISATION   = "VV"
PROD_LINEAR_DOMAIN  = True      # averages linear, converts to dB after

# Variant resolutions to test
RESOLUTIONS = [5, 10]           # m/px; "max" for IW GRD is effectively 5m floor

# Output directory
OUT_DIR = Path("diagnostics/ras_tanura")

# Sentinel Hub credentials (same env vars as ingest.py)
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_BASE_URL      = "https://sh.dataspace.copernicus.eu"

# ── Sentinelhub setup ─────────────────────────────────────────────────────────

from sentinelhub import (
    SHConfig, DataCollection, SentinelHubCatalog,
    SentinelHubRequest, BBox, CRS, MimeType
)

sh_config = SHConfig()
sh_config.sh_client_id     = SH_CLIENT_ID
sh_config.sh_client_secret = SH_CLIENT_SECRET
sh_config.sh_token_url     = SH_TOKEN_URL
sh_config.sh_base_url      = SH_BASE_URL

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_diag",
    service_url="https://sh.dataspace.copernicus.eu"
)

S2_CDSE = DataCollection.SENTINEL2_L2A.define_from(
    "s2l2a_cdse_diag",
    service_url="https://sh.dataspace.copernicus.eu"
)

# ── Tank geometry from registry ───────────────────────────────────────────────

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tank_registry import LOCATIONS

_rt = next(loc for loc in LOCATIONS if loc["id"] == "ras_tanura")
TANKS = _rt["tanks"]
CLUSTERS = _rt["clusters"]
TANK_BY_ID = {t["id"]: t for t in TANKS}

# ── Geometry helpers ──────────────────────────────────────────────────────────

def _bbox_for_tanks(tank_list, buffer_m=300):
    lats = [t["lat"] for t in tank_list]
    lons = [t["lon"] for t in tank_list]
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * math.cos(math.radians(min(lats))))
    return BBox([min(lons)-dlon, min(lats)-dlat, max(lons)+dlon, max(lats)+dlat], crs=CRS.WGS84)

def _img_size(bbox, res_m):
    lat0 = (bbox.min_y + bbox.max_y) / 2
    w = int((bbox.max_x - bbox.min_x) * 111320 * math.cos(math.radians(lat0)) / res_m)
    h = int((bbox.max_y - bbox.min_y) * 111320 / res_m)
    return max(w, 1), max(h, 1)

def _lonlat_to_pix(lon, lat, bbox, w, h):
    x = (lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * w
    y = (bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * h
    return x, y

def _crescent_mask(tank, bbox, w, h, los_az, width_m, res_m):
    cx, cy = _lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)
    az = math.radians(los_az)
    dx, dy = math.sin(az), -math.cos(az)
    Y, X = np.indices((h, w))
    dist_sq    = (X - cx)**2 + (Y - cy)**2
    dot        = (X - cx) * dx + (Y - cy) * dy
    r_outer    = tank["radius_m"] / res_m
    r_inner    = max(0, tank["radius_m"] - width_m) / res_m
    return (dist_sq <= r_outer**2) & (dist_sq >= r_inner**2) & (dot > 0)

# ── Output directories ────────────────────────────────────────────────────────

for sub in ["raw", "png", "overlay", "crops", "optical", "meta"]:
    (OUT_DIR / sub).mkdir(parents=True, exist_ok=True)

# ── Evalscripts ───────────────────────────────────────────────────────────────

def _evalscript_s1(pol="VV"):
    return f"""
//VERSION=3
function setup() {{ return {{ input: [{{ bands: ["{pol}"] }}], output: {{ bands: 1, sampleType: "FLOAT32" }} }}; }}
function evaluatePixel(s) {{ return [s.{pol}]; }}
"""

_EVALSCRIPT_S2_TRUE_COLOR = """
//VERSION=3
function setup() { return { input: [{ bands: ["B04","B03","B02"] }], output: { bands: 3, sampleType: "UINT8" } }; }
function evaluatePixel(s) { return [s.B04*255*3, s.B03*255*3, s.B02*255*3].map(v => Math.min(255,v)); }
"""

# ── Catalog query ─────────────────────────────────────────────────────────────

def query_scenes(bbox, start, end, orbit_state):
    """Returns list of {datetime, orbit_direction, relative_orbit, platform} dicts."""
    catalog = SentinelHubCatalog(config=sh_config)
    orbit_filter = f"sat:orbit_state = '{orbit_state}'" if orbit_state != "both" else None
    fields = {"include": [
        "properties.datetime",
        "properties.sat:orbit_state",
        "properties.sat:relative_orbit",
        "properties.sat:absolute_orbit",
        "properties.platform",
    ]}
    try:
        kwargs = dict(
            collection=S1_CDSE,
            bbox=bbox,
            time=(start, end),
            fields=fields,
        )
        if orbit_filter:
            kwargs["filter"] = orbit_filter
        scenes = list(catalog.search(**kwargs))
    except Exception as e:
        print(f"  [catalog] error: {e}")
        return []

    results = []
    seen = set()
    for s in sorted(scenes, key=lambda x: x["properties"]["datetime"]):
        p = s["properties"]
        dt = pd.to_datetime(p["datetime"])
        orbit = p.get("sat:relative_orbit")
        orbit_dir = p.get("sat:orbit_state", orbit_state)
        key = (orbit, dt.normalize().date(), orbit_dir)
        if key not in seen:
            seen.add(key)
            results.append({
                "datetime": dt,
                "orbit_direction": orbit_dir.upper(),
                "relative_orbit": orbit,
                "platform": p.get("platform", ""),
            })
    return results

# ── SAR image download ────────────────────────────────────────────────────────

def fetch_sar(bbox, date_dt, w, h, pol, orbit_state, retry=3):
    d_str  = date_dt.strftime("%Y-%m-%d")
    d_next = (date_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    orbit_filter = None if orbit_state == "both" else orbit_state
    kwargs = dict(
        evalscript=_evalscript_s1(pol),
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(d_str, d_next),
            other_args={"processing": {
                "orthorectify":  PROD_ORTHORECTIFY,
                "backCoeff":     PROD_BACK_COEFF,
                "demInstance":   PROD_DEM,
            }, **({"upsampling": "NEAREST"} if orbit_filter else {})},
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox,
        size=(w, h),
        config=sh_config,
    )
    for attempt in range(retry):
        try:
            data = SentinelHubRequest(**kwargs).get_data()
            if data and np.any(np.isfinite(data[0])):
                return data[0].squeeze().astype(np.float32)
            return None
        except Exception as e:
            if attempt < retry - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"    [!] fetch failed after {retry} attempts: {e}")
    return None

# ── PNG helpers ───────────────────────────────────────────────────────────────

def _to_png(arr_linear, path):
    """Save percentile-stretched grayscale PNG from linear SAR image."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    valid = arr_linear[np.isfinite(arr_linear) & (arr_linear > 0)]
    if valid.size == 0:
        return
    db = 10 * np.log10(np.clip(arr_linear, 1e-10, None))
    lo, hi = np.nanpercentile(db[np.isfinite(db)], [2, 98])
    stretched = np.clip((db - lo) / (hi - lo + 1e-9), 0, 1)
    plt.imsave(str(path), stretched, cmap="gray")
    plt.close("all")
    return db  # return dB array for stats

def _draw_overlay(arr_linear, bbox, w, h, tanks_subset, los_az, width_m, res_m, path):
    """PNG with tank circles (white) + crescent masks (red) drawn on the SAR image."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    db = 10 * np.log10(np.clip(arr_linear, 1e-10, None))
    lo, hi = np.nanpercentile(db[np.isfinite(db)], [2, 98])
    stretched = np.clip((db - lo) / (hi - lo + 1e-9), 0, 1)

    fig, ax = plt.subplots(figsize=(12, 12))
    ax.imshow(stretched, cmap="gray", origin="upper", interpolation="nearest")

    for tank in tanks_subset:
        cx, cy = _lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)
        r_px = tank["radius_m"] / res_m
        circle = plt.Circle((cx, cy), r_px, color="cyan", fill=False, lw=0.8, alpha=0.7)
        ax.add_patch(circle)
        # crescent overlay
        mask = _crescent_mask(tank, bbox, w, h, los_az, width_m, res_m)
        crescent_rgba = np.zeros((h, w, 4), dtype=np.float32)
        crescent_rgba[mask] = [1, 0, 0, 0.5]
        ax.imshow(crescent_rgba, origin="upper", interpolation="nearest")

    ax.set_title(f"LOS={los_az}°  res={res_m}m/px  WIDTH={width_m}m", fontsize=9)
    ax.axis("off")
    plt.tight_layout(pad=0)
    fig.savefig(str(path), dpi=150, bbox_inches="tight")
    plt.close("all")

def _side_by_side(img_a, label_a, img_b, label_b, bbox, w, h, tanks_subset,
                  los_az, width_m, res_m, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def _prep(arr):
        db = 10 * np.log10(np.clip(arr, 1e-10, None))
        lo, hi = np.nanpercentile(db[np.isfinite(db)], [2, 98])
        return np.clip((db - lo) / (hi - lo + 1e-9), 0, 1)

    fig, axes = plt.subplots(1, 2, figsize=(18, 9))
    for ax, arr, label in [(axes[0], img_a, label_a), (axes[1], img_b, label_b)]:
        ax.imshow(_prep(arr), cmap="gray", origin="upper", interpolation="nearest")
        for tank in tanks_subset:
            cx, cy = _lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)
            r_px = tank["radius_m"] / res_m
            ax.add_patch(plt.Circle((cx, cy), r_px, color="cyan", fill=False, lw=0.8, alpha=0.7))
            mask = _crescent_mask(tank, bbox, w, h, los_az, width_m, res_m)
            crescent_rgba = np.zeros((h, w, 4), dtype=np.float32)
            crescent_rgba[mask] = [1, 0, 0, 0.5]
            ax.imshow(crescent_rgba, origin="upper", interpolation="nearest")
        ax.set_title(label, fontsize=10)
        ax.axis("off")
    plt.suptitle(f"ASC vs DESC  |  LOS={los_az}°  res={res_m}m/px", fontsize=10)
    plt.tight_layout()
    fig.savefig(str(path), dpi=150, bbox_inches="tight")
    plt.close("all")

def _zoom_crops(arr_linear, bbox, w, h, ref_tank_ids, res_m, los_az, width_m, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ref_tanks = [TANK_BY_ID[tid] for tid in ref_tank_ids if tid in TANK_BY_ID]
    n = len(ref_tanks)
    if n == 0:
        return
    db = 10 * np.log10(np.clip(arr_linear, 1e-10, None))
    fig, axes = plt.subplots(1, n, figsize=(5 * n, 5))
    if n == 1:
        axes = [axes]
    SCALE = 8
    for ax, tank in zip(axes, ref_tanks):
        cx, cy = _lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)
        r_px = tank["radius_m"] / res_m
        pad = int(r_px * 5)
        x0, x1 = max(0, int(cx)-pad), min(w, int(cx)+pad)
        y0, y1 = max(0, int(cy)-pad), min(h, int(cy)+pad)
        crop = db[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        lo, hi = np.nanpercentile(crop[np.isfinite(crop)], [2, 98]) if np.any(np.isfinite(crop)) else (0, 1)
        stretched = np.clip((crop - lo) / (hi - lo + 1e-9), 0, 1)
        # nearest-neighbor upscale
        zoomed = np.repeat(np.repeat(stretched, SCALE, axis=0), SCALE, axis=1)
        ax.imshow(zoomed, cmap="gray", origin="upper", interpolation="nearest")
        # circle and crescent on zoomed crop
        cxz = (cx - x0) * SCALE
        cyz = (cy - y0) * SCALE
        r_z = r_px * SCALE
        ax.add_patch(plt.Circle((cxz, cyz), r_z, color="cyan", fill=False, lw=1.5))
        r_inner_z = max(0, r_px - width_m / res_m) * SCALE
        ax.add_patch(plt.Circle((cxz, cyz), r_inner_z, color="yellow", fill=False, lw=1.0, ls="--"))
        crescent_px = int(math.pi * ((r_px)**2 - (max(0, r_px - width_m/res_m))**2) / 2)
        ax.set_title(f"{tank['id']}  r={tank['radius_m']}m\n"
                     f"~{crescent_px}px in crescent  |  {SCALE}× zoom", fontsize=8)
        ax.axis("off")
    plt.tight_layout()
    fig.savefig(str(path), dpi=150, bbox_inches="tight")
    plt.close("all")

# ── Optical (Sentinel-2) ──────────────────────────────────────────────────────

def fetch_optical(bbox, start, end, tanks_subset, path_raw, path_overlay):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    w, h = _img_size(bbox, 10)
    request = SentinelHubRequest(
        evalscript=_EVALSCRIPT_S2_TRUE_COLOR,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S2_CDSE,
            time_interval=(start, end),
            other_args={"dataFilter": {"maxCloudCoverage": 20}},
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.PNG)],
        bbox=bbox,
        size=(w, h),
        config=sh_config,
    )
    try:
        data = request.get_data()
        if not data:
            print("  [optical] no S2 data returned")
            return
        img = data[0]
        plt.imsave(str(path_raw), img)

        fig, ax = plt.subplots(figsize=(12, 12))
        ax.imshow(img, origin="upper")
        for tank in tanks_subset:
            cx, cy = _lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)
            r_px = tank["radius_m"] / 10  # 10m/px for S2
            ax.add_patch(plt.Circle((cx, cy), r_px, color="red", fill=False, lw=1.0, alpha=0.8))
            ax.annotate(tank["id"], (cx, cy), color="red", fontsize=5, ha="center")
        ax.set_title("Sentinel-2 True Color + tank registry circles (red)", fontsize=9)
        ax.axis("off")
        plt.tight_layout(pad=0)
        fig.savefig(str(path_overlay), dpi=150, bbox_inches="tight")
        plt.close("all")
        print(f"  [optical] saved {path_raw.name} + overlay")
    except Exception as e:
        print(f"  [optical] failed: {e}")

# ── Statistics ────────────────────────────────────────────────────────────────

def _arr_stats(arr_linear):
    db = 10 * np.log10(np.clip(arr_linear, 1e-10, None))
    finite = db[np.isfinite(db)]
    total  = arr_linear.size
    nodata = int(np.sum(~np.isfinite(arr_linear) | (arr_linear <= 0)))
    if finite.size == 0:
        return {"min_db": None, "max_db": None, "mean_db": None, "std_db": None, "nodata_frac": 1.0}
    return {
        "min_db":     round(float(np.min(finite)), 3),
        "max_db":     round(float(np.max(finite)), 3),
        "mean_db":    round(float(np.mean(finite)), 3),
        "std_db":     round(float(np.std(finite)), 3),
        "nodata_frac": round(nodata / total, 4),
    }

def _tank_db(arr_linear, tank, bbox, w, h, los_az, width_m, res_m):
    mask = _crescent_mask(tank, bbox, w, h, los_az, width_m, res_m)
    pixels = arr_linear[mask]
    pixels = pixels[np.isfinite(pixels) & (pixels > 0)]
    if pixels.size == 0:
        return None, 0
    return round(float(10 * np.log10(np.mean(pixels))), 3), int(pixels.size)

def _crescent_px_count(tank, bbox, w, h, los_az, width_m, res_m):
    mask = _crescent_mask(tank, bbox, w, h, los_az, width_m, res_m)
    return int(np.sum(mask))

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("Ras Tanura SAR diagnostic")
    print(f"Period: {DIAG_START} → {DIAG_END}")
    print("=" * 70)

    south_tanks = [TANK_BY_ID[tid] for c in CLUSTERS if c["name"] == "south" for tid in c["tank_ids"] if tid in TANK_BY_ID]
    north_tanks = [TANK_BY_ID[tid] for c in CLUSTERS if c["name"] == "north" for tid in c["tank_ids"] if tid in TANK_BY_ID]
    all_tanks   = TANKS

    bbox_south = _bbox_for_tanks(south_tanks)
    bbox_north = _bbox_for_tanks(north_tanks)
    bbox_full  = _bbox_for_tanks(all_tanks)

    cluster_defs = [
        ("south", bbox_south, south_tanks),
        ("north", bbox_north, north_tanks),
        ("full",  bbox_full,  all_tanks),
    ]

    start_str = DIAG_START
    end_str   = DIAG_END

    # ── Query catalog ─────────────────────────────────────────────────────────
    print("\n[1/5] Querying Sentinel-1 catalog...")
    # Use south bbox as representative; orbit_state = both (we'll split ASC/DESC)
    desc_scenes = query_scenes(bbox_south, start_str, end_str, "descending")
    asc_scenes  = query_scenes(bbox_south, start_str, end_str, "ascending")
    all_scenes  = sorted(desc_scenes + asc_scenes, key=lambda s: s["datetime"])

    print(f"  descending: {len(desc_scenes)} scenes")
    print(f"  ascending:  {len(asc_scenes)} scenes")

    if len(desc_scenes) < MIN_SCENES_PER_CLUSTER:
        ext_start = (datetime.strptime(DIAG_START, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        print(f"  WARNING: fewer than {MIN_SCENES_PER_CLUSTER} descending scenes found.")
        print(f"  Expanding window to {ext_start} → {DIAG_END}")
        desc_scenes = query_scenes(bbox_south, ext_start, end_str, "descending")
        asc_scenes  = query_scenes(bbox_south, ext_start, end_str, "ascending")
        all_scenes  = sorted(desc_scenes + asc_scenes, key=lambda s: s["datetime"])
        print(f"  After expansion: {len(desc_scenes)} DESC, {len(asc_scenes)} ASC")

    # ── Sentinel-2 optical ─────────────────────────────────────────────────────
    print("\n[2/5] Fetching Sentinel-2 optical...")
    fetch_optical(
        bbox_full, start_str, end_str,
        south_tanks[:30],  # show first 30 south tanks for legibility
        OUT_DIR / "optical" / "s2_truecolor.png",
        OUT_DIR / "optical" / "s2_truecolor_overlay.png",
    )

    # ── Main download loop ────────────────────────────────────────────────────
    print(f"\n[3/5] Downloading SAR variants ({len(all_scenes)} scenes × clusters × resolutions)...")

    summary_rows = []
    # For side-by-side ASC/DESC, collect by (week, cluster)
    asc_desc_pairs = {}  # key=(week_str, cluster_name) → {asc: arr, desc: arr}

    for scene in all_scenes:
        dt   = scene["datetime"]
        orb  = scene["orbit_direction"]
        reln = scene["relative_orbit"]
        plat = scene["platform"]
        date_str = dt.strftime("%Y-%m-%d")
        week_str = dt.strftime("%Y-W%U")

        for cluster_name, bbox, tanks_subset in cluster_defs:
            for res_m in RESOLUTIONS:
                for pol in ["VV", "VH"]:
                    variant_tag = f"{date_str}_{orb}_{cluster_name}_{res_m}m_{pol}"
                    print(f"  {variant_tag} ... ", end="", flush=True)

                    w, h = _img_size(bbox, res_m)

                    arr = fetch_sar(bbox, dt, w, h, pol, orb.lower())
                    if arr is None:
                        print("no data")
                        continue
                    print(f"ok ({w}×{h})")

                    # Save GeoTIFF-like (numpy as .npy for now, float32)
                    raw_path = OUT_DIR / "raw" / f"{variant_tag}.npy"
                    np.save(str(raw_path), arr)

                    # PNG
                    png_path = OUT_DIR / "png" / f"{variant_tag}.png"
                    _to_png(arr, png_path)

                    # Overlay (only VV, res=5, prod params)
                    if pol == "VV" and res_m == PROD_RESOLUTION:
                        ov_path = OUT_DIR / "overlay" / f"{variant_tag}_overlay.png"
                        _draw_overlay(arr, bbox, w, h, tanks_subset,
                                      PROD_LOS_AZIMUTH, PROD_WIDTH_WIDE, res_m, ov_path)

                        # Collect for side-by-side
                        key = (week_str, cluster_name)
                        asc_desc_pairs.setdefault(key, {})
                        asc_desc_pairs[key][orb] = (arr, bbox, w, h, tanks_subset, date_str)

                    # Crescent pixel counts for reference tanks
                    ref_px_60 = ref_px_35 = ref_px_n = 0
                    ref_db_60 = ref_db_35 = ref_db_n = None
                    if pol == "VV" and res_m == PROD_RESOLUTION:
                        for ref_id, var_name in [
                            (REF_TANK_60M,   "ref_60"),
                            (REF_TANK_35M,   "ref_35"),
                            (REF_TANK_NORTH, "ref_north"),
                        ]:
                            if ref_id in TANK_BY_ID:
                                t = TANK_BY_ID[ref_id]
                                px = _crescent_px_count(t, bbox, w, h, PROD_LOS_AZIMUTH, PROD_WIDTH_WIDE, PROD_RESOLUTION)
                                db_val, _ = _tank_db(arr, t, bbox, w, h, PROD_LOS_AZIMUTH, PROD_WIDTH_WIDE, PROD_RESOLUTION)
                                if ref_id == REF_TANK_60M:
                                    ref_px_60, ref_db_60 = px, db_val
                                elif ref_id == REF_TANK_35M:
                                    ref_px_35, ref_db_35 = px, db_val
                                else:
                                    ref_px_n, ref_db_n = px, db_val

                    stats = _arr_stats(arr)

                    row = {
                        "scene_id":      f"{plat}_{date_str}_{reln}",
                        "date":          date_str,
                        "orbit_dir":     orb,
                        "relative_orbit": reln,
                        "platform":      plat,
                        "cluster":       cluster_name,
                        "resolution_m":  res_m,
                        "polarisation":  pol,
                        "img_w":         w,
                        "img_h":         h,
                        "min_db":        stats["min_db"],
                        "max_db":        stats["max_db"],
                        "mean_db":       stats["mean_db"],
                        "std_db":        stats["std_db"],
                        "nodata_frac":   stats["nodata_frac"],
                        "ref_60m_crescent_px":  ref_px_60,
                        "ref_35m_crescent_px":  ref_px_35,
                        "ref_north_crescent_px": ref_px_n,
                        "ref_60m_db":    ref_db_60,
                        "ref_35m_db":    ref_db_35,
                        "ref_north_db":  ref_db_n,
                        "prod_los_az":   PROD_LOS_AZIMUTH,
                        "prod_width_m":  PROD_WIDTH_WIDE,
                        "prod_backcoeff": PROD_BACK_COEFF,
                        "prod_orthorect": PROD_ORTHORECTIFY,
                        "prod_dem":      PROD_DEM,
                    }
                    summary_rows.append(row)

                    # Per-scene JSON
                    meta_path = OUT_DIR / "meta" / f"{variant_tag}.json"
                    with open(meta_path, "w") as f:
                        json.dump(row, f, indent=2, default=str)

                    time.sleep(0.3)  # rate-limit courtesy pause

    # ── Side-by-side ASC vs DESC ──────────────────────────────────────────────
    print("\n[4/5] Generating ASC/DESC side-by-side comparisons...")
    for (week_str, cluster_name), sides in asc_desc_pairs.items():
        if "ASCENDING" in sides and "DESCENDING" in sides:
            arr_a, bbox_a, w_a, h_a, tanks_a, date_a = sides["ASCENDING"]
            arr_d, bbox_d, w_d, h_d, tanks_d, date_d = sides["DESCENDING"]
            # Use descending bbox/dimensions as reference (both clusters same area)
            sbs_path = OUT_DIR / "overlay" / f"{week_str}_{cluster_name}_ASCvsDESC.png"
            _side_by_side(
                arr_a, f"ASC {date_a}", arr_d, f"DESC {date_d}",
                bbox_d, w_d, h_d, tanks_d,
                PROD_LOS_AZIMUTH, PROD_WIDTH_WIDE, PROD_RESOLUTION,
                sbs_path
            )
            print(f"  {sbs_path.name}")

    # ── Zoom crops ────────────────────────────────────────────────────────────
    print("\n[4b/5] Generating zoom crops for reference tanks...")
    # Use the most recent descending VV 5m south image
    ref_rows = [r for r in summary_rows
                if r["orbit_dir"] == "DESCENDING" and r["resolution_m"] == 5
                and r["polarisation"] == "VV" and r["cluster"] == "south"]
    if ref_rows:
        last = sorted(ref_rows, key=lambda r: r["date"])[-1]
        variant_tag = f"{last['date']}_DESCENDING_south_5m_VV"
        raw_path = OUT_DIR / "raw" / f"{variant_tag}.npy"
        if raw_path.exists():
            arr = np.load(str(raw_path))
            bbox = bbox_south
            w, h = last["img_w"], last["img_h"]
            crop_path = OUT_DIR / "crops" / f"{last['date']}_DESC_reference_tanks.png"
            _zoom_crops(arr, bbox, w, h,
                        [REF_TANK_60M, REF_TANK_35M, REF_TANK_NORTH],
                        PROD_RESOLUTION, PROD_LOS_AZIMUTH, PROD_WIDTH_WIDE,
                        crop_path)
            print(f"  {crop_path.name}")

    # ── Relative orbit grouping in summary ────────────────────────────────────
    # For ref tanks: mean dB per relative_orbit_number
    vv_5m_south = [r for r in summary_rows
                   if r["polarisation"] == "VV" and r["resolution_m"] == 5
                   and r["cluster"] == "south"]
    orbit_groups = {}
    for r in vv_5m_south:
        k = (r["orbit_dir"], r["relative_orbit"])
        orbit_groups.setdefault(k, []).append(r)

    # ── Save CSV ──────────────────────────────────────────────────────────────
    csv_path = OUT_DIR / "diagnostics_summary.csv"
    if summary_rows:
        fieldnames = list(summary_rows[0].keys())
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(summary_rows)
        print(f"\n[5/5] Saved {csv_path}")

    # ── Stdout summary table ──────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print(f"{'Date':<12} {'Orbit':<12} {'Cluster':<8} {'Res':>4} {'Pol':>4} "
          f"{'px@35m':>7} {'px@60m':>7} {'std_db':>7} {'mean_db':>8} {'ref60_db':>9} {'ref35_db':>9}")
    print("-" * 90)
    vv_rows = sorted(
        [r for r in summary_rows if r["polarisation"] == "VV" and r["cluster"] in ("south", "full")],
        key=lambda r: (r["date"], r["cluster"], r["resolution_m"])
    )
    for r in vv_rows:
        print(f"{r['date']:<12} {r['orbit_dir']:<12} {r['cluster']:<8} {r['resolution_m']:>4} "
              f"{r['polarisation']:>4} "
              f"{r['ref_35m_crescent_px']:>7} {r['ref_60m_crescent_px']:>7} "
              f"{str(r['std_db']):>7} {str(r['mean_db']):>8} "
              f"{str(r['ref_60m_db']):>9} {str(r['ref_35m_db']):>9}")

    # Orbit grouping
    if orbit_groups:
        print("\n── Mean dB per relative orbit (VV 5m south, ref tanks) ──")
        print(f"{'Orbit dir':<12} {'Rel.orbit':>10} {'N':>4} {'mean ref60':>11} {'mean ref35':>11} {'std ref35':>10}")
        print("-" * 60)
        for (od, rn), rows in sorted(orbit_groups.items()):
            db60 = [r["ref_60m_db"] for r in rows if r["ref_60m_db"] is not None]
            db35 = [r["ref_35m_db"] for r in rows if r["ref_35m_db"] is not None]
            m60  = round(np.mean(db60), 3) if db60 else "—"
            m35  = round(np.mean(db35), 3) if db35 else "—"
            s35  = round(np.std(db35),  3) if len(db35) > 1 else "—"
            print(f"{od:<12} {str(rn):>10} {len(rows):>4} {str(m60):>11} {str(m35):>11} {str(s35):>10}")

    print("\nDone. Output in:", OUT_DIR.resolve())
    print(f"Total variants saved: {len(summary_rows)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
