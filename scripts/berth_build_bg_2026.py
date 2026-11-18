"""
berth_build_bg_2026.py — build east_t_head o101 2026 background.

Uses o101 scenes where east_t_head is optically/logically EMPTY:
  2026-09-28  (optics confirmed EMP)
  2026-10-28  (optics confirmed EMP)
  all o101 candidate passes Oct–Nov 2026 (Oct-Nov 2026 = 0 visits confirmed)
Median of linear-scale patches → bg_o101_east_t_head_2026.npy

This BG is used for east_t_head o101 passes from 2026-08-15 onward, absorbing
the persistent artifact at pos~250m and ~700m that is invisible in the 2024-2025 BG.
"""

import os, sys
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
ORBIT        = 101
OUT_PATH     = BG_V4 / "bg_o101_east_t_head_2026.npy"

# o101 candidate dates where east_t_head is empty.
# 2026-09-28 and 2026-10-28 confirmed by optics; all Oct-Nov 2026 o101 passes
# are empty (0 visits confirmed for Oct-Nov 2026).
# Sentinel-1 orbit-101 revisit ~6 days; list all candidates, fetch will skip
# dates where no scene exists.
EMPTY_DATES = [
    "2026-09-28",   # optics confirmed EMP
    "2026-10-04",
    "2026-10-10",
    "2026-10-16",
    "2026-10-22",
    "2026-10-28",   # optics confirmed EMP
    "2026-11-03",
    "2026-11-09",
    "2026-11-15",
    "2026-11-21",
    "2026-11-27",
]

def main():
    pts = BERTH_COORDS[BERTH]
    bbox_obj = cell_bbox_for(*berth_center(pts))

    arrays = []
    for date_str in EMPTY_DATES:
        item = find_item_pc(date_str, ORBIT, AOI_BBOX_LST)
        if item is None:
            print(f"  {date_str}: no PC item — skipped")
            continue
        arr = fetch_s1_pc(item, bbox_obj)
        if arr is None:
            print(f"  {date_str}: fetch returned None — skipped")
            continue
        print(f"  {date_str} OK shape={arr.shape}")
        arrays.append(arr)

    if len(arrays) < 2:
        sys.exit(f"FATAL: only {len(arrays)} arrays fetched — need ≥2 for median BG")

    # Align to most common shape
    shapes = [a.shape for a in arrays]
    target = max(set(shapes), key=shapes.count)
    aligned = []
    for a in arrays:
        if a.shape != target:
            a = nd_zoom(a, (target[0]/a.shape[0], target[1]/a.shape[1]), order=1)
        aligned.append(a)

    stack = np.stack(aligned, axis=0)
    bg_arr = np.median(stack, axis=0).astype(np.float32)
    np.save(OUT_PATH, bg_arr)
    print(f"\nSaved {OUT_PATH.name}  shape={bg_arr.shape}  "
          f"from {len(arrays)} dates: {', '.join(EMPTY_DATES[:len(arrays)])}")


if __name__ == "__main__":
    main()
