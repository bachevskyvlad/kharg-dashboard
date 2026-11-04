"""
v1_tank_centers.py — V1: averaged SAR stack tank centre + r/h fitting.

Method:
  1. Download N_STACK (default 30) most recent scenes on the terminal's working
     orbit via SentinelHub ProcessingAPI (linear VV power).
  2. Average the linear power stack → mean_lin image.  Averaging in linear
     reduces speckle by √N (→ ~0.5 dB residual for N=30).
  3. NCC shift per tank using the averaged image.  Search window ±SEARCH_PX.
  4. r_fit: ring-energy maximisation — sweep r ∈ [r_reg−20m, r_reg+20m] in 1m
     steps on the averaged image, pick the radius that maximises annulus mean dB.
  5. h_fit: NOT fitted from SAR directly (requires shadow length and solar
     geometry or InSAR). Output column filled with registry h_max_m value.
  6. Flags: >10m, >25m, >50m based on offset_m.
  7. PNG: averaged stack with registry circle (red) and fitted circle (green).

Output (per terminal):
  diagnostics/research/2026-10/v1/<terminal>/v1_centers.csv
  diagnostics/research/2026-10/v1/<terminal>/stack_<tank_id>.png  (sampled tanks)
  diagnostics/research/2026-10/v1/v1_summary.csv  (all terminals combined)

Usage (called by research_auto.yml Section 8):
  python scripts/research/v1_tank_centers.py --terminals kharg,ras_tanura,yanbu,al_flaw,jask
"""

import argparse
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from scipy.ndimage import gaussian_filter

# ---------------------------------------------------------------------------
# path setup
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)

from config import config as sh_config, S1_CDSE
from locations import raw_tanks, TERMINALS
from sar_fetch import get_all_scene_dates
from sentinelhub import BBox, CRS

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
RESOLUTION  = 5       # m/px
WIDTH_WIDE  = 15      # m crescent annulus
SEARCH_PX   = 12      # ±12 px = ±60 m search window
N_STACK     = 30      # target number of scenes to average
NCC_OK      = 0.30    # lowered vs 2-scene (averaged stack is less noisy)
NCC_LOW     = 0.15
BUFFER_M    = 300     # bbox buffer around tanks
R_SWEEP_M   = 20      # r_fit sweep ±20 m around registry radius

OUT_BASE = os.path.join(SCRIPTS_DIR, "..", "diagnostics", "research", "2026-10", "v1")

# ---------------------------------------------------------------------------
# terminal config (working orbit per terminal)
# ---------------------------------------------------------------------------
TERMINAL_ORBITS = {
    "kharg":      {"direction": "DESC", "rel_orbit": 35,  "los_az": 282},
    "ras_tanura": {"direction": "DESC", "rel_orbit": 35,  "los_az": 282},
    "yanbu":      {"direction": "ASC",  "rel_orbit": 14,  "los_az": 98},
    "al_flaw":    {"direction": "DESC", "rel_orbit": None, "los_az": 282},
    "jask":       {"direction": "DESC", "rel_orbit": 93,  "los_az": 282},
}

# ---------------------------------------------------------------------------
# evalscript — raw linear VV power
# ---------------------------------------------------------------------------
_EVALSCRIPT = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _get_tanks(terminal_id):
    if terminal_id == "kharg":
        return [{"id": t["id"], "lat": t["lat"], "lon": t["lon"],
                 "radius_m": t["radius"], "h_max_m": t.get("h_max", 14)}
                for t in raw_tanks]
    for loc in TERMINALS:
        if loc["id"] == terminal_id:
            return [{"id": t["id"], "lat": t["lat"], "lon": t["lon"],
                     "radius_m": t["radius"], "h_max_m": t.get("h_max", 14)}
                    for t in loc["tanks"] if t.get("enabled", True)]
    return []


def _get_bbox(tanks, buffer_m=BUFFER_M):
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buffer_m / 111320
    dlon = buffer_m / (111320 * np.cos(np.deg2rad(min(lats))))
    return BBox([min(lons)-dlon, min(lats)-dlat, max(lons)+dlon, max(lats)+dlat], crs=CRS.WGS84)


def _fetch_scene(bbox, date_str, direction, rel_orbit):
    from sentinelhub import SentinelHubRequest, MimeType
    from datetime import date as _date, timedelta
    d = _date.fromisoformat(date_str)
    w = max(10, int((bbox.max_x - bbox.min_x) * 111320
                    * np.cos(np.deg2rad(bbox.min_y)) / RESOLUTION))
    h = max(10, int((bbox.max_y - bbox.min_y) * 111320 / RESOLUTION))
    dir_str = {"DESC": "DESCENDING", "ASC": "ASCENDING"}.get(direction.upper(), direction.upper())
    other_args = {
        "processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN",
                       "demInstance": "COPERNICUS_30"},
        "dataFilter": {"orbitDirection": dir_str},
        "mosaicking": "ORBIT",
    }
    if rel_orbit is not None:
        other_args["dataFilter"]["relativeOrbit"] = rel_orbit
    try:
        req = SentinelHubRequest(
            evalscript=_EVALSCRIPT,
            input_data=[SentinelHubRequest.input_data(
                data_collection=S1_CDSE,
                time_interval=(date_str, (d + timedelta(days=1)).isoformat()),
                other_args=other_args,
            )],
            responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
            bbox=bbox, size=(w, h), config=sh_config,
        )
        data = req.get_data()
        if not data:
            return None, w, h
        arr = np.array(data[0], dtype=np.float32)
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        if arr.std() == 0 or np.all(arr == 0):
            return None, w, h
        return arr, w, h
    except Exception as e:
        print(f"    [WARN] fetch {date_str}: {e}")
        return None, w, h


def _lonlat_to_pix(lon, lat, bbox, w, h):
    cx = (lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * w
    cy = (bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * h
    return cx, cy


def _ring_mask(radius_m, width_m=WIDTH_WIDE, res=RESOLUTION):
    r_out = radius_m / res
    r_in  = max(0.0, radius_m - width_m) / res
    patch = int(r_out * 2 + 6)
    Y, X  = np.mgrid[-patch:patch+1, -patch:patch+1]
    dist  = np.sqrt(X**2 + Y**2)
    return ((dist <= r_out) & (dist >= r_in)).astype(np.float32), patch


def _ncc_shift(img_lin, cx, cy, radius_m, search=SEARCH_PX):
    img_db = 10 * np.log10(np.clip(img_lin, 1e-6, None))
    img_db = gaussian_filter(img_db, sigma=1.5)
    h, w   = img_db.shape

    tmpl, _  = _ring_mask(radius_m)
    th, tw   = tmpl.shape
    ph, pw   = th + 2 * search, tw + 2 * search

    r0, c0 = int(cy) - ph // 2, int(cx) - pw // 2
    r1, c1 = r0 + ph, c0 + pw
    if r0 < 0 or c0 < 0 or r1 > h or c1 > w:
        return 0.0, 0.0, 0.0

    patch     = img_db[r0:r1, c0:c1]
    tmpl_flat = tmpl.flatten()
    tmpl_mu   = tmpl_flat.mean()
    tmpl_sig  = tmpl_flat.std()
    if tmpl_sig < 1e-9:
        return 0.0, 0.0, 0.0

    n = 2 * search + 1
    ncc_grid = np.full((n, n), -1.0, dtype=np.float64)
    for idy, dy in enumerate(range(-search, search + 1)):
        for idxi, dx in enumerate(range(-search, search + 1)):
            sub = patch[search+dy:search+dy+th, search+dx:search+dx+tw].flatten()
            if sub.shape != tmpl_flat.shape:
                continue
            sub_sig = sub.std()
            if sub_sig < 1e-9:
                continue
            ncc_grid[idy, idxi] = ((sub - sub.mean()) * (tmpl_flat - tmpl_mu)).mean() / (sub_sig * tmpl_sig)

    pidy, pidx = np.unravel_index(np.argmax(ncc_grid), ncc_grid.shape)
    best_ncc   = float(ncc_grid[pidy, pidx])
    dy_int, dx_int = pidy - search, pidx - search

    sub_dx = sub_dy = 0.0
    if 0 < pidx < n - 1:
        fm, f0, fp = ncc_grid[pidy, pidx-1], ncc_grid[pidy, pidx], ncc_grid[pidy, pidx+1]
        d_ = 2*f0 - fm - fp
        if abs(d_) > 1e-12:
            sub_dx = (fm - fp) / (2 * d_)
    if 0 < pidy < n - 1:
        fm, f0, fp = ncc_grid[pidy-1, pidx], ncc_grid[pidy, pidx], ncc_grid[pidy+1, pidx]
        d_ = 2*f0 - fm - fp
        if abs(d_) > 1e-12:
            sub_dy = (fm - fp) / (2 * d_)

    return float(dx_int + sub_dx), float(dy_int + sub_dy), best_ncc


def _r_fit(img_lin, cx, cy, r_reg_m, sweep_m=R_SWEEP_M, res=RESOLUTION):
    """Sweep radius ±sweep_m, return radius_m that maximises mean annulus dB."""
    img_db = 10 * np.log10(np.clip(img_lin, 1e-6, None))
    h, w   = img_db.shape
    best_r, best_e = r_reg_m, -np.inf
    for r in range(max(5, r_reg_m - sweep_m), r_reg_m + sweep_m + 1):
        mask, patch_r = _ring_mask(r)
        iy0 = int(cy) - patch_r - 1
        ix0 = int(cx) - patch_r - 1
        iy1 = iy0 + mask.shape[0]
        ix1 = ix0 + mask.shape[1]
        if iy0 < 0 or ix0 < 0 or iy1 > h or ix1 > w:
            continue
        crop = img_db[iy0:iy1, ix0:ix1]
        if crop.shape != mask.shape:
            continue
        e = float(np.mean(crop[mask > 0]))
        if e > best_e:
            best_e, best_r = e, r
    return best_r


def _save_stack_png(stack_lin, cx_reg, cy_reg, cx_fit, cy_fit, r_reg, r_fit, tank_id, outpath):
    img_db = 10 * np.log10(np.clip(stack_lin, 1e-6, None))
    r_reg_px, r_fit_px = r_reg / RESOLUTION, r_fit / RESOLUTION

    crop_r = int(r_reg_px * 3) + 10
    cx_i, cy_i = int(round(cx_fit)), int(round(cy_fit))
    h, w = img_db.shape
    iy0 = max(0, cy_i - crop_r)
    ix0 = max(0, cx_i - crop_r)
    iy1 = min(h, cy_i + crop_r)
    ix1 = min(w, cx_i + crop_r)
    crop = img_db[iy0:iy1, ix0:ix1]

    fig, ax = plt.subplots(figsize=(5, 5))
    vmin, vmax = np.nanpercentile(crop, 5), np.nanpercentile(crop, 95)
    ax.imshow(crop, cmap="gray", vmin=vmin, vmax=vmax, origin="upper")

    # registry circle (red)
    ax.add_patch(mpatches.Circle(
        (cx_reg - ix0, cy_reg - iy0), r_reg_px,
        fill=False, edgecolor="red", linewidth=1.5, linestyle="--", label="registry"
    ))
    # fitted circle (green)
    ax.add_patch(mpatches.Circle(
        (cx_fit - ix0, cy_fit - iy0), r_fit_px,
        fill=False, edgecolor="lime", linewidth=1.5, label="fitted"
    ))
    ax.set_title(tank_id, fontsize=8)
    ax.legend(fontsize=7, loc="upper right")
    ax.axis("off")
    plt.tight_layout(pad=0.3)
    plt.savefig(outpath, dpi=100, bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------------------
# main per-terminal processing
# ---------------------------------------------------------------------------

def process_terminal(terminal_id, n_stack=N_STACK):
    orb = TERMINAL_ORBITS.get(terminal_id)
    if orb is None:
        print(f"  [SKIP] {terminal_id}: no orbit config")
        return []

    tanks = _get_tanks(terminal_id)
    if not tanks:
        print(f"  [SKIP] {terminal_id}: no tanks")
        return []

    direction  = orb["direction"]
    rel_orbit  = orb["rel_orbit"]

    print(f"\n{'='*60}")
    print(f"V1: {terminal_id}  orbit={direction}-{rel_orbit}  n_tanks={len(tanks)}")

    # get scene dates
    bbox = _get_bbox(tanks)
    all_dates = get_all_scene_dates(bbox, orbit_state=direction.lower(),
                                     relative_orbit=rel_orbit)
    if not all_dates:
        print(f"  [!] no CDSE scenes for {terminal_id}")
        return []

    scene_dates = sorted(all_dates)[-n_stack:]
    print(f"  scenes available: {len(all_dates)}, using last {len(scene_dates)}: "
          f"{scene_dates[0].strftime('%Y-%m-%d')} – {scene_dates[-1].strftime('%Y-%m-%d')}")

    # fetch + build stack
    stack_layers = []
    for d in scene_dates:
        d_str = d.strftime("%Y-%m-%d")
        arr, w, h = _fetch_scene(bbox, d_str, direction, rel_orbit)
        if arr is not None:
            stack_layers.append(arr)
            print(f"  + {d_str}  ({len(stack_layers)}/{len(scene_dates)})")
        else:
            print(f"  - {d_str}  (no data)")

    if len(stack_layers) < 5:
        print(f"  [!] only {len(stack_layers)} valid scenes — skipping {terminal_id}")
        return []

    w = stack_layers[0].shape[1]
    h = stack_layers[0].shape[0]
    stack_arr = np.array(stack_layers, dtype=np.float32)
    mean_lin  = np.mean(stack_arr, axis=0)
    print(f"  stack: {len(stack_layers)} scenes → mean image {h}×{w}")

    # per-tank NCC + r_fit
    out_dir = os.path.join(OUT_BASE, terminal_id)
    os.makedirs(out_dir, exist_ok=True)

    rows = []
    png_count = 0
    for tank in tanks:
        tid      = tank["id"]
        r_reg    = tank["radius_m"]
        h_reg    = tank["h_max_m"]
        cx_reg, cy_reg = _lonlat_to_pix(tank["lon"], tank["lat"], bbox, w, h)

        dx, dy, ncc = _ncc_shift(mean_lin, cx_reg, cy_reg, r_reg)
        cx_fit = cx_reg + dx
        cy_fit = cy_reg + dy
        r_fit  = _r_fit(mean_lin, cx_fit, cy_fit, r_reg)

        offset_m = float(np.sqrt(dx**2 + dy**2) * RESOLUTION)
        azimuth  = float(np.degrees(np.arctan2(-dy, dx)) % 360)  # deg from East, CW

        if ncc >= NCC_OK:
            quality = "OK"
        elif ncc >= NCC_LOW:
            quality = "LOW"
        else:
            quality = "EXCL"

        row = {
            "terminal_id": terminal_id,
            "tank_id":     tid,
            "n_scenes":    len(stack_layers),
            "ncc":         round(ncc, 4),
            "quality":     quality,
            "dx_px":       round(dx, 2),
            "dy_px":       round(dy, 2),
            "offset_m":    round(offset_m, 1),
            "azimuth_deg": round(azimuth, 1),
            "r_reg_m":     r_reg,
            "r_fit_m":     r_fit,
            "dr_m":        r_fit - r_reg,
            "h_reg_m":     h_reg,
            "h_fit_m":     h_reg,   # SAR-only fit not available; placeholder
            "flag_10m":    offset_m > 10,
            "flag_25m":    offset_m > 25,
            "flag_50m":    offset_m > 50,
        }
        rows.append(row)

        db_diff = float(np.nanmean(10 * np.log10(np.clip(
            mean_lin[max(0,int(cy_fit)-3):int(cy_fit)+4,
                     max(0,int(cx_fit)-3):int(cx_fit)+4], 1e-6, None)))
            - np.nanmean(10 * np.log10(np.clip(
            mean_lin[max(0,int(cy_reg)-3):int(cy_reg)+4,
                     max(0,int(cx_reg)-3):int(cx_reg)+4], 1e-6, None))))
        row["delta_db"] = round(db_diff, 3)

        # save PNG for tanks with significant shift or sampled set
        if quality != "EXCL" and (offset_m > 5 or png_count < 8):
            png_path = os.path.join(out_dir, f"stack_{tid}.png")
            _save_stack_png(mean_lin, cx_reg, cy_reg, cx_fit, cy_fit, r_reg, r_fit, tid, png_path)
            png_count += 1

        flag_str = (">" + "+".join(
            [s for s, f in [("10m", offset_m>10), ("25m", offset_m>25), ("50m", offset_m>50)] if f]
        ) if any([offset_m>10, offset_m>25, offset_m>50]) else "ok")
        print(f"  {tid:12s}  offset={offset_m:5.1f}m  az={azimuth:5.1f}°  "
              f"r_reg={r_reg}m→r_fit={r_fit}m  ncc={ncc:.3f}  {quality}  {flag_str}")

    # write per-terminal CSV
    import csv
    csv_path = os.path.join(out_dir, "v1_centers.csv")
    if rows:
        with open(csv_path, "w", newline="") as f:
            w_ = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w_.writeheader()
            w_.writerows(rows)
        print(f"  → {csv_path}  ({len(rows)} rows)")

    # summary stats
    ok_rows = [r for r in rows if r["quality"] == "OK"]
    if ok_rows:
        offsets = [r["offset_m"] for r in ok_rows]
        print(f"  SUMMARY OK tanks={len(ok_rows)}/{len(rows)}  "
              f"median_offset={np.median(offsets):.1f}m  max={max(offsets):.1f}m  "
              f">10m={sum(r['flag_10m'] for r in ok_rows)}  "
              f">25m={sum(r['flag_25m'] for r in ok_rows)}  "
              f">50m={sum(r['flag_50m'] for r in ok_rows)}")

    return rows


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminals", default="kharg,ras_tanura",
                        help="comma-separated terminal ids")
    parser.add_argument("--n-stack", type=int, default=N_STACK)
    args = parser.parse_args()

    terminals = [t.strip() for t in args.terminals.split(",")]
    os.makedirs(OUT_BASE, exist_ok=True)

    all_rows = []
    for tid in terminals:
        rows = process_terminal(tid, n_stack=args.n_stack)
        all_rows.extend(rows)

    if all_rows:
        import csv
        summary_path = os.path.join(OUT_BASE, "v1_summary.csv")
        with open(summary_path, "w", newline="") as f:
            w_ = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            w_.writeheader()
            w_.writerows(all_rows)
        print(f"\n{'='*60}")
        print(f"V1 COMPLETE: {len(all_rows)} tanks across {len(terminals)} terminals")
        print(f"Summary: {summary_path}")

        # print table
        print(f"\n{'terminal':12s} {'tank':12s} {'offset_m':>8s} {'az':>6s} {'r_reg':>6s} {'r_fit':>6s} {'ncc':>6s} {'quality':8s} flags")
        for r in sorted(all_rows, key=lambda x: -x["offset_m"]):
            flags = "+".join([s for s, f in [("10m", r["flag_10m"]), ("25m", r["flag_25m"]), ("50m", r["flag_50m"])] if f]) or "ok"
            print(f"{r['terminal_id']:12s} {r['tank_id']:12s} {r['offset_m']:8.1f} "
                  f"{r['azimuth_deg']:6.1f} {r['r_reg_m']:6d} {r['r_fit_m']:6d} "
                  f"{r['ncc']:6.3f} {r['quality']:8s} {flags}")


if __name__ == "__main__":
    main()
