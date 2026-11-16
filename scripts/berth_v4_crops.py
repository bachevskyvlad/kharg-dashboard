"""
berth_v4_crops.py — SAR crop images for acceptance gate g.

Reads diagnostics/berth_v4/history_v4/berth_v4_history.csv and generates
SAR ratio crops with hull boxes for:
  1. 5 random 2025 visits (arrived=True, 2025)  → crops/v2025_*.png
  2. All autumn 2026 visits (Sep-Nov 2026)       → crops/aut2026_*.png

Box colors: VLCC=#f87171, SuezAframax=#fbbf24, smaller=#94a3b8
Label format: "class · length · arrived/same"

Output: diagnostics/berth_v4/crops/
"""

import os, sys, csv
from pathlib import Path
from datetime import date, timedelta

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT   = Path(__file__).parent.parent
HIST   = ROOT / "diagnostics" / "berth_v4" / "history_v4" / "berth_v4_history.csv"
BG_V4  = ROOT / "diagnostics" / "berth_v4" / "bg_v4"
OUT    = ROOT / "diagnostics" / "berth_v4" / "crops"
OUT.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent))

from berth_detect_core import (
    RESOLUTION, BERTH_COORDS,
    build_geom, find_blobs, cell_bbox_for, berth_center,
)
from berth_pc_fetch import find_item_pc, fetch_s1_pc
from scipy.ndimage import zoom as nd_zoom
from sentinelhub import BBox, CRS

AOI_BBOX_LST = [50.26, 29.17, 50.40, 29.31]

V4_CFG = {
    "sea_island":     {"pier": 20, "water_i": 20, "water_o": 80,
                       "min_len": 120, "aspect_thr": 2.0, "merge_gap": 120},
    "east_t_head":    {"pier": 20, "water_i": 20, "water_o": 80,
                       "min_len": 150, "aspect_thr": 3.0, "merge_gap": 120},
    "southeast_pier": {"pier": 20, "water_i": 20, "water_o": 80,
                       "min_len": 150, "aspect_thr": 3.0, "merge_gap": 120},
}

BERTHS = BERTH_COORDS


def _classify(len_m):
    if len_m >= 300:   return "VLCC"
    if len_m >= 150:   return "SuezAframax"
    return "smaller"

CLASS_COLORS = {"VLCC": "#f87171", "SuezAframax": "#fbbf24", "smaller": "#94a3b8"}


def _load_bg(berth, orbit):
    p_v4 = BG_V4 / f"bg_{berth}_o{orbit}.npy"
    p_v3 = ROOT / "diagnostics" / "berth_v4" / "bg_v3" / f"bg_{berth}_o{orbit}.npy"
    for p in (p_v4, p_v3):
        if p.exists():
            return np.load(p)
    return None


def _draw_boxes(ax, blobs, ux, uy, csv_row=None):
    px_u, py_u = -uy, ux
    by_side: dict = {}
    for b in blobs:
        sk = 1 if b.get("perp_ctr", 0) >= 0 else -1
        by_side.setdefault(sk, []).append(b)
    for sk, side_blobs in by_side.items():
        side_blobs_s = sorted(side_blobs, key=lambda b: b["length_m"], reverse=True)
        for idx, b in enumerate(side_blobs_s):
            am, aM = b["axis_min"], b["axis_max"]
            pm = b.get("perp_min", b.get("perp_ctr", 0) - b.get("width_m", 20) / RESOLUTION / 2)
            pM = b.get("perp_max", b.get("perp_ctr", 0) + b.get("width_m", 20) / RESOLUTION / 2)
            cs = [am*ux + pm*px_u, aM*ux + pm*px_u,
                  aM*ux + pM*px_u, am*ux + pM*px_u, am*ux + pm*px_u]
            rs = [am*uy + pm*py_u, aM*uy + pm*py_u,
                  aM*uy + pM*py_u, am*uy + pM*py_u, am*uy + pm*py_u]
            cls = _classify(b["length_m"])
            clr = CLASS_COLORS.get(cls, "#94a3b8")
            ax.plot(cs, rs, "-", color=clr, lw=1.5, alpha=0.9)
            ctr_c = (am + aM) / 2 * ux + (pm + pM) / 2 * px_u
            ctr_r = (am + aM) / 2 * uy + (pm + pM) / 2 * py_u
            # Label: "class · length · arrived/same"
            arrived_str = ""
            if csv_row is not None:
                arrived_str = " · arrived" if str(csv_row.get("arrived", "")).lower() in ("true", "1") else " · same"
            lbl = f"{cls} · {b['length_m']:.0f}m{arrived_str}"
            ax.text(ctr_c, ctr_r, lbl,
                    color=clr, fontsize=5, ha="center", va="center", fontweight="bold",
                    bbox=dict(fc="black", alpha=0.5, lw=0, pad=0.5))


def render_crop(date_str, orbit, berth, tag, idx, csv_row=None):
    cfg = V4_CFG[berth]
    pts = BERTHS[berth]
    cell_bbox = cell_bbox_for(*berth_center(pts))

    item = find_item_pc(date_str, int(orbit), AOI_BBOX_LST)
    if item is None:
        print(f"  No item — skip")
        return False

    linear = fetch_s1_pc(item, cell_bbox)
    if linear is None:
        print(f"  Fetch failed — skip")
        return False

    bg = _load_bg(berth, int(orbit))
    if bg is None:
        print(f"  No BG — skip")
        return False

    if bg.shape != linear.shape:
        bg = nd_zoom(bg, (linear.shape[0] / bg.shape[0],
                          linear.shape[1] / bg.shape[1]), order=1)

    ratio = np.where((linear > 0) & (bg > 0),
                     10 * np.log10(linear / bg), np.nan).astype(np.float32)

    geom = build_geom(pts, cell_bbox, linear.shape,
                      cfg["pier"], cfg["water_i"], cfg["water_o"])

    blobs, best, detected = find_blobs(
        ratio, geom["water_mask"], geom["axis_uvec"],
        merge_gap_m=cfg.get("merge_gap", 0),
        min_len_m=cfg["min_len"],
        min_aspect=cfg.get("aspect_thr", 3.0),
        return_all=True,
    )

    ux, uy = geom["axis_uvec"]

    fig, ax = plt.subplots(1, 1, figsize=(4, 3), facecolor="#111")
    ax.set_facecolor("#111")

    fin = ratio[np.isfinite(ratio)]
    vmin = np.percentile(fin, 2) if fin.size > 0 else -10
    vmax = np.percentile(fin, 98) if fin.size > 0 else 10
    ax.imshow(ratio, cmap="gray", vmin=vmin, vmax=vmax,
              interpolation="nearest", origin="upper")

    # water contour
    if geom.get("water_mask") is not None:
        try:
            ax.contour(geom["water_mask"].astype(float), levels=[0.5],
                       colors=["#3498db"], linewidths=0.5, alpha=0.6)
        except Exception:
            pass

    if blobs:
        _draw_boxes(ax, blobs, ux, uy, csv_row=csv_row)

    det_str = "OCC" if detected else "EMP"
    n_hulls = len(blobs) if blobs else 0
    hull_summary = ""
    if blobs:
        top = sorted(blobs, key=lambda b: b["length_m"], reverse=True)[:2]
        hull_summary = "  ".join(
            f"{_classify(b['length_m'])} {b['length_m']:.0f}m asp{b.get('aspect', 0):.1f}"
            for b in top)

    ann = f"{date_str} o{orbit}\n{berth}\ndet={det_str}  hulls={n_hulls}\n{hull_summary}"
    ax.text(0.02, 0.98, ann, color="white", fontsize=5, ha="left", va="top",
            transform=ax.transAxes, fontweight="bold",
            bbox=dict(boxstyle="square,pad=0.2", fc="black", alpha=0.75, lw=0))

    ax.set_title(f"[{tag}] {date_str} o{orbit} — {berth}",
                 color="white", fontsize=6, pad=2)
    ax.axis("off")

    fname = OUT / f"{tag}_{idx:02d}_{date_str}_o{orbit}_{berth}.png"
    fig.savefig(fname, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    kb = fname.stat().st_size // 1024
    print(f"  Saved: {fname.name} ({kb} KB)  det={det_str} hulls={n_hulls}")
    return True


def main():
    if not HIST.exists():
        sys.exit(f"FATAL: {HIST} not found — run berth_v4_history.py first (step 2)")

    rows = []
    with open(HIST) as f:
        for r in csv.DictReader(f):
            rows.append(r)
    print(f"Loaded {len(rows)} hull rows from {HIST.name}")

    import random as _random
    _random.seed(42)

    # --- Gate g case 1: 5 random 2025 visits (arrived=True, 2025) ---
    # Dedup by visit_id (or by date/orbit/berth if no visit_id), arrived=True
    v2025_seen: set = set()
    v2025_pool = []
    for r in rows:
        is_arrived = str(r.get("arrived", r.get("new_load", ""))).lower() in ("true", "1")
        is_2025 = r.get("date", "").startswith("2025")
        is_static = str(r.get("static", "0")) == "1"
        if is_arrived and is_2025 and not is_static:
            vid = r.get("visit_id") or f"{r['date']}_{r['orbit']}_{r['berth']}"
            if vid not in v2025_seen:
                v2025_seen.add(vid)
                v2025_pool.append(r)
    v2025_pool = sorted(v2025_pool, key=lambda r: r["date"])
    v2025_cases = _random.sample(v2025_pool, min(5, len(v2025_pool)))
    v2025_cases = sorted(v2025_cases, key=lambda r: r["date"])

    # --- Gate g case 2: all autumn 2026 visits (Sep-Nov 2026) ---
    aut2026_seen: set = set()
    aut2026_cases = []
    for r in rows:
        d = r.get("date", "")
        is_static = str(r.get("static", "0")) == "1"
        if d >= "2026-09-01" and d <= "2026-11-30" and not is_static:
            is_arrived = str(r.get("arrived", r.get("new_load", ""))).lower() in ("true", "1")
            if is_arrived:
                vid = r.get("visit_id") or f"{r['date']}_{r['orbit']}_{r['berth']}"
                if vid not in aut2026_seen:
                    aut2026_seen.add(vid)
                    aut2026_cases.append(r)
    aut2026_cases = sorted(aut2026_cases, key=lambda r: r["date"])

    print(f"\n2025 visits pool: {len(v2025_pool)}, sampling {len(v2025_cases)}")
    for r in v2025_cases:
        print(f"  {r['date']} o{r['orbit']} {r['berth']} "
              f"cls={r.get('class','')} len={r.get('len_m','')}m vid={r.get('visit_id','')}")

    print(f"\nAutumn 2026 visits: {len(aut2026_cases)}")
    for r in aut2026_cases:
        print(f"  {r['date']} o{r['orbit']} {r['berth']} "
              f"cls={r.get('class','')} len={r.get('len_m','')}m vid={r.get('visit_id','')}")

    if not v2025_cases and not aut2026_cases:
        print("Nothing to render — done.")
        return

    print("\n=== Rendering crops ===")

    for idx, r in enumerate(v2025_cases):
        print(f"\nv2025 [{idx+1}/{len(v2025_cases)}]: {r['date']} o{r['orbit']} {r['berth']}")
        render_crop(r["date"], r["orbit"], r["berth"], "v2025", idx + 1, csv_row=r)

    for idx, r in enumerate(aut2026_cases):
        print(f"\naut2026 [{idx+1}/{len(aut2026_cases)}]: {r['date']} o{r['orbit']} {r['berth']}")
        render_crop(r["date"], r["orbit"], r["berth"], "aut2026", idx + 1, csv_row=r)

    pngs = list(OUT.glob("*.png"))
    print(f"\nTotal crops saved: {len(pngs)} in {OUT}")


if __name__ == "__main__":
    main()
