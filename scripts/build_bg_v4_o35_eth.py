"""
build_bg_v4_o35_eth.py — build BG_V4 for orbit-35 east_t_head via Planetary Computer.

Uses 8 optically-confirmed empty passes (orbit-35, Oct–Nov 2026): adjacent orbit-101
passes all show EMP, confirmed by optical imagery.

Fetches S1-RTC from Planetary Computer (same source as berth_v4_history.py)
to guarantee radiometric consistency.

Output: diagnostics/berth_v4/bg_v4/bg_o35_east_t_head.npy
"""

import sys
import numpy as np
from pathlib import Path
from scipy.ndimage import zoom as nd_zoom

sys.path.insert(0, str(Path(__file__).parent))

from berth_detect_core import RESOLUTION, BERTH_COORDS, berth_center, cell_bbox_for
from berth_pc_fetch import find_item_pc, fetch_s1_pc

ROOT   = Path(__file__).parent.parent
BG_V4  = ROOT / "diagnostics" / "berth_v4" / "bg_v4"
BG_V4.mkdir(parents=True, exist_ok=True)

TARGET_ORBIT = 35
TARGET_BERTH = "east_t_head"
MIN_SCENES   = 5

AOI_BBOX_LST = [50.26, 29.17, 50.40, 29.31]

# Orbit-35 dates where adjacent orbit-101 passes show east_t_head = EMP.
# Cross-validated from berth_v4_history run #45 (Oct–Nov 2026).
KNOWN_EMP_DATES = [
    "2026-10-06", "2026-10-12", "2026-10-18", "2026-10-24", "2026-10-30",
    "2026-11-05", "2026-11-11", "2026-11-17",
]


def main():
    pts      = BERTH_COORDS[TARGET_BERTH]
    bbox_obj = cell_bbox_for(*berth_center(pts))

    print(f"Fetching {len(KNOWN_EMP_DATES)} orbit-{TARGET_ORBIT} EMP scenes "
          f"for {TARGET_BERTH} via Planetary Computer ...")

    arrays = []
    for date_str in KNOWN_EMP_DATES:
        item = find_item_pc(date_str, TARGET_ORBIT, AOI_BBOX_LST)
        if item is None:
            print(f"  {date_str}  SKIP (no PC item)")
            continue
        arr = fetch_s1_pc(item, bbox_obj)
        if arr is None:
            print(f"  {date_str}  SKIP (fetch error)")
            continue
        arrays.append(arr)
        print(f"  {date_str}  shape={arr.shape}  mean={arr.mean():.4f}")

    if len(arrays) < MIN_SCENES:
        sys.exit(f"FATAL: only {len(arrays)} arrays fetched, need ≥{MIN_SCENES}")

    target_shape = arrays[len(arrays) // 2].shape
    aligned = []
    for a in arrays:
        if a.shape == target_shape:
            aligned.append(a)
        else:
            aligned.append(nd_zoom(a,
                (target_shape[0] / a.shape[0], target_shape[1] / a.shape[1]), order=1))

    stack = np.stack(aligned, axis=0)
    bg    = np.nanmedian(stack, axis=0).astype(np.float32)

    db = 10 * np.log10(np.maximum(bg, 1e-10))
    print(f"\nBackground shape={bg.shape}  scenes={len(aligned)}")
    print(f"BG stats: min={bg.min():.4f} mean={bg.mean():.4f} max={bg.max():.4f}")
    print(f"BG dB:    min={db.min():.2f}  mean={db.mean():.2f}  max={db.max():.2f}")

    out_path = BG_V4 / f"bg_o{TARGET_ORBIT}_{TARGET_BERTH}.npy"
    np.save(out_path, bg)
    print(f"\nSaved: {out_path}")
    print(f"bg_source label: bg_v4_o35_2026 "
          f"({len(aligned)} EMP scenes, orbit-35, Oct–Nov 2026, optically confirmed, PC)")


if __name__ == "__main__":
    main()
