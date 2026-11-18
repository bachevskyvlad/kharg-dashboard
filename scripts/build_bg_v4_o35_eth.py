"""
build_bg_v4_o35_eth.py — build 2026 BG for orbit-35 east_t_head.

Uses ONLY orbit-35 scenes where optics confirm east_t_head = EMP:
  2026-10-06, 10-12, 10-18, 10-24, 10-30, 11-05, 11-11, 11-17

Output: diagnostics/berth_v4/bg_v4/bg_o35_east_t_head_2026.npy
"""

import sys
import numpy as np
from pathlib import Path
from scipy.ndimage import zoom as nd_zoom

sys.path.insert(0, str(Path(__file__).parent))
from berth_detect_core import BERTH_COORDS, berth_center, cell_bbox_for
from berth_pc_fetch import find_item_pc, fetch_s1_pc

ROOT   = Path(__file__).parent.parent
BG_V4  = ROOT / "diagnostics" / "berth_v4" / "bg_v4"
BG_V4.mkdir(parents=True, exist_ok=True)

AOI_BBOX_LST = [50.26, 29.17, 50.40, 29.31]
BERTH        = "east_t_head"
ORBIT        = 35
OUT_PATH     = BG_V4 / "bg_o35_east_t_head_2026.npy"
MIN_SCENES   = 4

# o35 dates where optics confirm east_t_head = EMP (Oct-Nov 2026 only).
# Excludes 2026-09-06 and 2026-09-30 — confirmed false-positive dates.
KNOWN_EMP_DATES = [
    "2026-10-06",
    "2026-10-12",
    "2026-10-18",
    "2026-10-24",
    "2026-10-30",
    "2026-11-05",
    "2026-11-11",
    "2026-11-17",
]


def main():
    pts = BERTH_COORDS[BERTH]
    bbox_obj = cell_bbox_for(*berth_center(pts))

    arrays = []
    for date_str in KNOWN_EMP_DATES:
        item = find_item_pc(date_str, ORBIT, AOI_BBOX_LST)
        if item is None:
            print(f"  {date_str}: no PC item — skipped")
            continue
        arr = fetch_s1_pc(item, bbox_obj)
        if arr is None:
            print(f"  {date_str}: fetch returned None — skipped")
            continue
        print(f"  {date_str} OK shape={arr.shape} mean={arr.mean():.4f}")
        arrays.append(arr)

    if len(arrays) < MIN_SCENES:
        sys.exit(f"FATAL: only {len(arrays)} arrays fetched — need ≥{MIN_SCENES}")

    # Align to most common shape
    shapes = [a.shape for a in arrays]
    target = max(set(shapes), key=shapes.count)
    aligned = []
    for a in arrays:
        if a.shape != target:
            a = nd_zoom(a, (target[0]/a.shape[0], target[1]/a.shape[1]), order=1)
        aligned.append(a)

    stack  = np.stack(aligned, axis=0)
    bg_arr = np.nanmedian(stack, axis=0).astype(np.float32)
    np.save(OUT_PATH, bg_arr)
    print(f"\nSaved {OUT_PATH.name}  shape={bg_arr.shape}  "
          f"from {len(arrays)} scenes: {', '.join(KNOWN_EMP_DATES[:len(arrays)])}")


if __name__ == "__main__":
    main()
