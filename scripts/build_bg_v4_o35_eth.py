"""
build_bg_v4_o35_eth.py — build BG_V4 for orbit-35 east_t_head.

Strategy: use orbit-35 scenes from Sept–Nov 2026 where the adjacent orbit-101
pass (±4 days) shows east_t_head = EMP.  Requires ≥5 scenes.

Output: diagnostics/berth_v4/bg_v4/bg_o35_east_t_head.npy
"""

import os, sys
import numpy as np
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from berth_detect_core import (
    RESOLUTION, BERTH_COORDS, berth_center, cell_bbox_for,
)

from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection, SentinelHubRequest,
    SentinelHubCatalog, MimeType, bbox_to_dimensions, MosaickingOrder,
)
from scipy.ndimage import zoom as nd_zoom

# ── Config ─────────────────────────────────────────────────────────────────
config = SHConfig()
config.sh_client_id     = os.environ.get("CLIENT_ID") or ""
config.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
config.sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
config.sh_base_url  = "https://sh.dataspace.copernicus.eu"
if not config.sh_client_id or not config.sh_client_secret:
    sys.exit("FATAL: CLIENT_ID / CLIENT_SECRET not set")

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_bgbuild", service_url="https://sh.dataspace.copernicus.eu")

ROOT   = Path(__file__).parent.parent
BG_V4  = ROOT / "diagnostics" / "berth_v4" / "bg_v4"
BG_V4.mkdir(parents=True, exist_ok=True)

TARGET_ORBIT  = 35
TARGET_BERTH  = "east_t_head"
MIN_SCENES    = 5

AOI_BBOX = BBox(bbox=[50.26, 29.17, 50.40, 29.31], crs=CRS.WGS84)

# Orbit-35 dates in Sept–Nov 2026 where adjacent orbit-101 east_t_head = EMP.
# Cross-validated from berth_v4_history run #45 (orbit-101 column Sept–Nov 2026
# shows continuous EMP for east_t_head).
KNOWN_EMP_DATES = [
    "2026-09-06", "2026-09-12", "2026-09-18", "2026-09-24", "2026-09-30",
    "2026-10-06", "2026-10-12", "2026-10-18", "2026-10-24", "2026-10-30",
    "2026-11-05", "2026-11-11",
]

EVALSCRIPT_S1 = """
//VERSION=3
function setup() { return { input: ["VV"], output: { bands:1, sampleType:"FLOAT32" } }; }
function evaluatePixel(s) { return [Math.max(s.VV, 1e-10)]; }
"""

def discover_scenes(dates):
    """Return catalog scene_dt for each (date, orbit=35) pair."""
    catalog = SentinelHubCatalog(config=config)
    date_min = dates[0] + "T00:00:00Z"
    date_max = dates[-1] + "T23:59:59Z"
    print(f"Searching catalog {date_min[:10]} → {date_max[:10]} orbit={TARGET_ORBIT} ...")
    all_scenes = list(catalog.search(
        S1_CDSE, bbox=AOI_BBOX,
        time=(date_min, date_max),
        fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
    ))
    grouped = {}
    for s in all_scenes:
        p = s.get("properties", {})
        orb = p.get("sat:relative_orbit")
        if orb != TARGET_ORBIT:
            continue
        dt_raw = p.get("datetime", "")
        try:
            dt = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.strptime(dt_raw[:19], "%Y-%m-%dT%H:%M:%S")
        date_str = dt.strftime("%Y-%m-%d")
        if date_str not in dates:
            continue
        if date_str not in grouped or dt_raw < grouped[date_str]:
            grouped[date_str] = dt_raw
    found = [(d, grouped[d]) for d in dates if d in grouped]
    print(f"  Found {len(found)}/{len(dates)} dates in catalog")
    return found

def fetch_linear(scene_dt, bbox_obj):
    sz = bbox_to_dimensions(bbox_obj, resolution=RESOLUTION)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S1,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(scene_dt[:19].replace("T", " "), scene_dt[:19].replace("T", " ")),
            other_args={"processing": {"backCoeff": "SIGMA0_ELLIPSOID", "orthorectify": True,
                                       "demInstance": "COPERNICUS"}},
            mosaicking_order=MosaickingOrder.MOST_RECENT,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox_obj, size=sz, config=config,
    )
    try:
        d = req.get_data()
        a = d[0]
        if a.ndim == 3:
            a = a[:, :, 0]
        return a.astype(np.float32)
    except Exception as e:
        print(f"  fetch err {scene_dt[:16]}: {e}")
        return None

def main():
    pts = BERTH_COORDS[TARGET_BERTH]
    bbox_obj = cell_bbox_for(*berth_center(pts))

    scenes = discover_scenes(KNOWN_EMP_DATES)
    if len(scenes) < MIN_SCENES:
        sys.exit(f"FATAL: only {len(scenes)} scenes found, need ≥{MIN_SCENES}")

    print(f"\nFetching {len(scenes)} orbit-{TARGET_ORBIT} EMP scenes for {TARGET_BERTH} ...")
    arrays = []
    for date_str, scene_dt in scenes:
        arr = fetch_linear(scene_dt, bbox_obj)
        if arr is not None:
            arrays.append(arr)
            print(f"  {date_str}  shape={arr.shape}  mean={arr.mean():.4f}")
        else:
            print(f"  {date_str}  SKIP (fetch error)")

    if len(arrays) < MIN_SCENES:
        sys.exit(f"FATAL: only {len(arrays)} arrays fetched, need ≥{MIN_SCENES}")

    # Align shapes to median shape (should all be identical, just safety)
    target_shape = arrays[len(arrays)//2].shape
    aligned = []
    for a in arrays:
        if a.shape == target_shape:
            aligned.append(a)
        else:
            aligned.append(nd_zoom(a, (target_shape[0]/a.shape[0], target_shape[1]/a.shape[1]), order=1))

    stack = np.stack(aligned, axis=0)
    bg = np.nanmedian(stack, axis=0).astype(np.float32)
    print(f"\nBackground shape={bg.shape}  scenes used={len(aligned)}")
    print(f"BG stats: min={bg.min():.4f} mean={bg.mean():.4f} max={bg.max():.4f}")

    out_path = BG_V4 / f"bg_o{TARGET_ORBIT}_{TARGET_BERTH}.npy"
    np.save(out_path, bg)
    print(f"\nSaved: {out_path}")
    print(f"bg_source label: bg_v4_o35_2026 ({len(aligned)} EMP scenes, orbit-35, Sept–Nov 2026)")

if __name__ == "__main__":
    main()
