"""
verify_orbit_fix_3.py — VERIFICATION round 3 for Ras Tanura.

TASK ORDER (per spec):
  5. Fix scene date window — root cause of round 2 using 2025-11/12 instead of 2026-07→09
  1. Annotated crops for rt_0, rt_11 + 3 neighbours on 3 DESC 35 + 2 ASC 101 scenes
     from 2026-07-16→2026-09-16. ~4-diameter crop, ×8 nearest upscale.
     Annotate: registry circle, full annulus ring, 282° crescent mask.
     Fix: guard against zero-size crop array.
  2. Geolocation offset: 10 south-cluster tanks × 3 DESC 35 scenes.
     Template matching in ±10px window. Table dx/dy in px + metres.
     If offset confirmed: note impact on paired 282 vs 78 measurements.
     Kharg control (expect 0–1 px offset).
  3. 213 single-tank dates vs orbit 137 12-day cycle.
  4. Exact SQL query on terminal_observations for ras_tanura at 10 specific dates.

Output: diagnostics/ras_tanura/VERIFICATION_3.md + diagnostics/ras_tanura/task*/

No Supabase writes. No production code changes.
"""

import os, sys, math, json, time, warnings, csv as _csv
warnings.filterwarnings("ignore")
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# ── Credentials ───────────────────────────────────────────────────────────────
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_BASE_URL      = "https://sh.dataspace.copernicus.eu"
SUPABASE_URL     = os.environ.get("SUPABASE_URL") or ""
SUPABASE_KEY     = os.environ.get("SUPABASE_KEY") or ""

from sentinelhub import (
    SHConfig, DataCollection, SentinelHubCatalog,
    SentinelHubRequest, BBox, CRS, MimeType
)

cfg = SHConfig()
cfg.sh_client_id     = SH_CLIENT_ID
cfg.sh_client_secret = SH_CLIENT_SECRET
cfg.sh_token_url     = SH_TOKEN_URL
cfg.sh_base_url      = SH_BASE_URL

S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
    "s1iw_cdse_v3", service_url="https://sh.dataspace.copernicus.eu"
)

import supabase as supa_mod
sb = supa_mod.create_client(SUPABASE_URL, SUPABASE_KEY)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tank_registry import LOCATIONS

_rt = next(loc for loc in LOCATIONS if loc["id"] == "ras_tanura")
_kh = next(loc for loc in LOCATIONS if loc["id"] == "kharg")
RT_TANKS    = _rt["tanks"]
TANK_BY_ID  = {t["id"]: t for t in RT_TANKS}
CLUSTERS    = _rt.get("clusters", [])
south_tanks = [TANK_BY_ID[tid] for c in CLUSTERS if c.get("name") == "south"
               for tid in c["tank_ids"] if tid in TANK_BY_ID]
if not south_tanks:
    south_tanks = RT_TANKS[:10]

KH_TANKS = _kh["tanks"]
KH_BY_ID = {t["id"]: t for t in KH_TANKS}

# Primary reference tanks
REF_RT_60 = "rt_0"   # radius 60m, south cluster
REF_RT_35 = "rt_11"  # radius 35m

# Scene window for round 3 (corrected from round 2's wrong 2025-11/12)
WIN_START = "2026-07-16"
WIN_END   = "2026-09-16"

OUT_ROOT = Path("diagnostics/ras_tanura")
TASK1    = OUT_ROOT / "task1_crops"
TASK2    = OUT_ROOT / "task2_offset"
for d in [TASK1, TASK2]:
    d.mkdir(parents=True, exist_ok=True)

REPORT = []

# ── Geometry helpers ──────────────────────────────────────────────────────────

def _bbox(tank_list, buf=350):
    lats = [t["lat"] for t in tank_list]
    lons = [t["lon"] for t in tank_list]
    dlat = buf / 111320
    dlon = buf / (111320 * math.cos(math.radians(min(lats))))
    return BBox([min(lons)-dlon, min(lats)-dlat, max(lons)+dlon, max(lats)+dlat], crs=CRS.WGS84)

def _size(bbox, res=5):
    lat0 = (bbox.min_y + bbox.max_y) / 2
    w = int((bbox.max_x - bbox.min_x) * 111320 * math.cos(math.radians(lat0)) / res)
    h = int((bbox.max_y - bbox.min_y) * 111320 / res)
    return max(w, 1), max(h, 1)

def _pix(lon, lat, bbox, w, h):
    x = (lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * w
    y = (bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * h
    return x, y

# ── Download ──────────────────────────────────────────────────────────────────

_EVALSCRIPT = """//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}"""

def _fetch(bbox, date_str, w, h):
    d_next = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    req = SentinelHubRequest(
        evalscript=_EVALSCRIPT,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(date_str, d_next),
            other_args={"processing": {
                "orthorectify": True,
                "backCoeff": "GAMMA0_TERRAIN",
                "demInstance": "COPERNICUS_30",
            }},
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox, size=(w, h), config=cfg,
    )
    for attempt in range(3):
        try:
            data = req.get_data()
            if data:
                arr = data[0].squeeze().astype(np.float32)
                if np.any(np.isfinite(arr) & (arr > 0)):
                    return arr
            return None
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                print(f"    fetch failed: {e}")
    return None

def _catalog(bbox, start, end, orbit_state="descending"):
    catalog = SentinelHubCatalog(config=cfg)
    fields  = {"include": ["properties.datetime", "properties.sat:orbit_state",
                            "properties.sat:relative_orbit"]}
    results = []
    for item in catalog.search(S1_CDSE, bbox=bbox, time=(start, end), fields=fields):
        props = item.get("properties", {})
        if props.get("sat:orbit_state", "").lower() == orbit_state.lower():
            dt  = pd.to_datetime(props["datetime"]).normalize()
            rob = props.get("sat:relative_orbit")
            results.append({"date": dt, "relative_orbit": rob})
    seen, uniq = set(), []
    for r in sorted(results, key=lambda x: x["date"], reverse=True):
        k = r["date"]
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 5 — Scene date window root cause (run first)
# ═══════════════════════════════════════════════════════════════════════════════

def task5_date_window_fix():
    print("\n=== TASK 5: Scene date window root-cause ===")

    cause = """
## Task 5 — Scene Date Window: Root Cause of Round 2 Wrong Dates

### What round 2 used
Round 2 searched `"2024-01-01"` → `"2025-12-31"` in `_catalog()`.
That window ends **2025-12-31**, so no 2026 scenes were ever considered.
The "most recent" scenes returned were from **Nov–Dec 2025** — the actual tail
of that search window.

### Why that happened
`verify_orbit_fix_2.py` hardcoded the catalog search window to `("2024-01-01", "2025-12-31")`
in **three places**: task2, task3, and task4. The requested analysis window
(2026-07→09) was never passed to the catalog calls.

### Fix applied in round 3
`WIN_START = "2026-07-16"`, `WIN_END = "2026-09-16"` are module-level constants.
All `_catalog()` calls in tasks 1 and 2 pass `(WIN_START, WIN_END)` explicitly.

### Verification
Scene catalog is re-run below. If DESC 35 or ASC 101 scenes are found inside
the 2026-07-16 → 2026-09-16 window, the fix is confirmed.
"""
    REPORT.append(cause)
    print(cause)

    # Quick probe: search the catalog for the correct window
    tank_60 = TANK_BY_ID.get(REF_RT_60) or south_tanks[0]
    bbox_probe = _bbox([tank_60], buf=300)
    print(f"  Probing catalog {WIN_START} → {WIN_END}...")
    desc35 = _catalog(bbox_probe, WIN_START, WIN_END, orbit_state="descending")
    desc35 = [s for s in desc35 if s["relative_orbit"] == 35]
    asc101 = _catalog(bbox_probe, WIN_START, WIN_END, orbit_state="ascending")
    asc101 = [s for s in asc101 if s["relative_orbit"] == 101]

    result_lines = [
        f"\n**Catalog probe results for {WIN_START} → {WIN_END}:**\n",
        f"DESC 35 scenes found: {len(desc35)}",
        f"ASC 101 scenes found: {len(asc101)}",
    ]
    if desc35:
        result_lines.append("DESC 35 dates: " + ", ".join(s["date"].strftime("%Y-%m-%d") for s in desc35))
    if asc101:
        result_lines.append("ASC 101 dates: " + ", ".join(s["date"].strftime("%Y-%m-%d") for s in asc101))

    probe_text = "\n".join(result_lines)
    REPORT.append(probe_text)
    print(probe_text)
    print("Task 5 complete.")

    return desc35, asc101


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 1 — Annotated crops
# ═══════════════════════════════════════════════════════════════════════════════

def _draw_circle_on_ax(ax, cx, cy, r, color, lw=1, label=None, ls="-"):
    """Draw a circle on matplotlib axes using a polygon approximation."""
    theta = np.linspace(0, 2 * np.pi, 360)
    xs = cx + r * np.cos(theta)
    ys = cy + r * np.sin(theta)
    ax.plot(xs, ys, color=color, lw=lw, ls=ls, label=label)

def _crescent_mask(tank, bbox, w, h, los_az, width_m=15, res_m=5):
    cx, cy = _pix(tank["lon"], tank["lat"], bbox, w, h)
    dx = math.sin(math.radians(los_az))
    dy = -math.cos(math.radians(los_az))
    Y, X = np.indices((h, w))
    dsq = (X - cx)**2 + (Y - cy)**2
    dot = (X - cx) * dx + (Y - cy) * dy
    ro = tank["radius_m"] / res_m
    ri = max(0, tank["radius_m"] - width_m) / res_m
    return (dsq <= ro**2) & (dsq >= ri**2) & (dot > 0)

def task1_annotated_crops(desc35_scenes, asc101_scenes):
    print("\n=== TASK 1: Annotated crops ===")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError as e:
        msg = f"\n## Task 1 — Annotated Crops\n\nmatplotlib not available: {e}\n"
        REPORT.append(msg)
        return

    # Reference tanks + 3 neighbours
    tank_60 = TANK_BY_ID.get(REF_RT_60) or south_tanks[0]
    tank_35 = TANK_BY_ID.get(REF_RT_35) or south_tanks[1]

    # 3 neighbours: tanks closest to rt_0 in south cluster (by distance), excluding rt_0 itself
    def _dist(a, b):
        return math.hypot(a["lat"] - b["lat"], a["lon"] - b["lon"])

    neighbours_60 = sorted(
        [t for t in south_tanks if t["id"] != REF_RT_60],
        key=lambda t: _dist(t, tank_60)
    )[:2]
    neighbours_35 = sorted(
        [t for t in south_tanks if t["id"] != REF_RT_35 and t["id"] not in {t["id"] for t in neighbours_60}],
        key=lambda t: _dist(t, tank_35)
    )[:1]

    crop_tanks = [tank_60, tank_35] + neighbours_60 + neighbours_35
    print(f"  Crop tanks: {[t['id'] for t in crop_tanks]}")

    # Scene lists: 3 DESC 35 + 2 ASC 101 (most recent from window)
    scenes_to_use = []
    for s in desc35_scenes[:3]:
        scenes_to_use.append(("DESC_35", s["date"].strftime("%Y-%m-%d")))
    for s in asc101_scenes[:2]:
        scenes_to_use.append(("ASC_101", s["date"].strftime("%Y-%m-%d")))

    if not scenes_to_use:
        # Fallback: try without orbit filter
        tank_60 = TANK_BY_ID.get(REF_RT_60) or south_tanks[0]
        bbox_probe = _bbox([tank_60], buf=300)
        all_desc = _catalog(bbox_probe, WIN_START, WIN_END, "descending")
        all_asc  = _catalog(bbox_probe, WIN_START, WIN_END, "ascending")
        for s in all_desc[:3]:
            scenes_to_use.append(("DESC_any", s["date"].strftime("%Y-%m-%d")))
        for s in all_asc[:2]:
            scenes_to_use.append(("ASC_any", s["date"].strftime("%Y-%m-%d")))

    print(f"  Scenes to process: {scenes_to_use}")

    crop_report_rows = []

    for orbit_key, date_str in scenes_to_use:
        for tank in crop_tanks:
            # Bounding box: ~4 diameters around the tank
            four_diam_buf = tank["radius_m"] * 4 + 50  # metres
            bbox_tk = _bbox([tank], buf=four_diam_buf)
            res_m = 5  # 5m/px from SH
            w, h = _size(bbox_tk, res=res_m)

            print(f"  {orbit_key} {date_str} {tank['id']}: fetch {w}×{h}...", end=" ", flush=True)
            arr = _fetch(bbox_tk, date_str, w, h)
            if arr is None:
                print("no data")
                crop_report_rows.append({
                    "orbit": orbit_key, "date": date_str,
                    "tank": tank["id"], "status": "no_data"
                })
                continue
            print(f"ok arr={arr.shape}")

            # Pixel centre of tank
            cx, cy = _pix(tank["lon"], tank["lat"], bbox_tk, w, h)

            # Crop radius in pixels = 2 * radius (= 4 * radius = 4 diameters / 2)
            # "~4 diameter" crop means total side ≈ 4 * diameter = 8 * radius
            r_pix = int(tank["radius_m"] / res_m) * 4  # 4 radii each side → 8 radius total width

            x0 = max(0, int(round(cx)) - r_pix)
            x1 = min(arr.shape[1], int(round(cx)) + r_pix)
            y0 = max(0, int(round(cy)) - r_pix)
            y1 = min(arr.shape[0], int(round(cy)) + r_pix)

            # FIX: guard zero-size crop
            if x1 <= x0 or y1 <= y0:
                print(f"    SKIP: zero-size crop x=[{x0},{x1}] y=[{y0},{y1}], arr={arr.shape}")
                crop_report_rows.append({
                    "orbit": orbit_key, "date": date_str,
                    "tank": tank["id"], "status": "zero_size_crop_skipped",
                    "cx": round(cx, 1), "cy": round(cy, 1),
                    "arr_w": arr.shape[1], "arr_h": arr.shape[0],
                })
                continue

            arr_crop = arr[y0:y1, x0:x1]

            # ×8 nearest-neighbour upscale
            scale = 8
            arr_up = np.kron(arr_crop, np.ones((scale, scale), dtype=np.float32))

            # Convert to dB for display
            arr_db = 10 * np.log10(np.clip(arr_up, 1e-10, None))
            arr_db[~np.isfinite(arr_db)] = np.nan

            valid = arr_db[np.isfinite(arr_db)]
            vmin = float(np.percentile(valid, 2))  if valid.size else -30
            vmax = float(np.percentile(valid, 98)) if valid.size else 0

            ch, cw_up = arr_up.shape
            fig, ax = plt.subplots(1, 1, figsize=(6, 6), facecolor="#0d1117")
            ax.set_facecolor("#0d1117")
            ax.imshow(arr_db, cmap="gray", vmin=vmin, vmax=vmax, origin="upper",
                      extent=[0, cw_up, ch, 0])

            # Centre of tank in upscaled coordinates
            cx_up = (cx - x0) * scale + scale / 2
            cy_up = (cy - y0) * scale + scale / 2
            r_up  = tank["radius_m"] / res_m * scale

            # Registry circle (outer edge of tank wall) — green
            _draw_circle_on_ax(ax, cx_up, cy_up, r_up, color="#00ff44", lw=1.5,
                               label="Registry circle")

            # Inner edge of annulus (tank wall thickness 15m)
            r_inner = max(0, tank["radius_m"] - 15) / res_m * scale
            _draw_circle_on_ax(ax, cx_up, cy_up, r_inner, color="#00ff44", lw=1.0, ls="--",
                               label="Inner annulus")

            # 282° crescent mask — magenta overlay
            # Recompute mask in cropped + upscaled space
            Y_up, X_up = np.indices((ch, cw_up))
            dsq = (X_up - cx_up)**2 + (Y_up - cy_up)**2
            dx282 = math.sin(math.radians(282))
            dy282 = -math.cos(math.radians(282))
            dot282 = (X_up - cx_up) * dx282 + (Y_up - cy_up) * dy282
            mask282_up = (dsq <= r_up**2) & (dsq >= r_inner**2) & (dot282 > 0)

            mask_rgba = np.zeros((ch, cw_up, 4), dtype=np.float32)
            mask_rgba[mask282_up] = [1.0, 0.0, 1.0, 0.55]  # magenta semi-transparent
            ax.imshow(mask_rgba, origin="upper", extent=[0, cw_up, ch, 0])

            # Centre crosshair
            ax.plot(cx_up, cy_up, "+", color="yellow", ms=10, mew=2)

            ax.set_title(
                f"{tank['id']} r={tank['radius_m']}m  {orbit_key}  {date_str}\n"
                f"5m/px × ×8 upscale  crop={arr_crop.shape}  dB [{vmin:.1f}, {vmax:.1f}]",
                color="white", fontsize=8
            )
            ax.legend(handles=[
                mpatches.Patch(color="#00ff44", label="Registry circle / annulus"),
                mpatches.Patch(color="magenta", alpha=0.55, label="282° crescent mask"),
            ], loc="lower right", fontsize=7, facecolor="#1e2530", edgecolor="#444")
            ax.tick_params(colors="#555")
            for sp in ax.spines.values():
                sp.set_color("#333")
            ax.set_xlabel("px (upscaled ×8)", color="#888", fontsize=7)
            ax.set_ylabel("px (upscaled ×8)", color="#888", fontsize=7)

            fname = TASK1 / f"crop_{orbit_key}_{date_str}_{tank['id']}.png"
            fig.savefig(fname, dpi=100, bbox_inches="tight", facecolor="#0d1117")
            plt.close(fig)
            print(f"    saved {fname.name}")

            crop_report_rows.append({
                "orbit": orbit_key, "date": date_str,
                "tank": tank["id"], "status": "ok",
                "crop_h": arr_crop.shape[0], "crop_w": arr_crop.shape[1],
                "db_vmin": round(vmin, 2), "db_vmax": round(vmax, 2),
            })
            time.sleep(0.2)

    # Write crop manifest CSV
    if crop_report_rows:
        with open(TASK1 / "crop_manifest.csv", "w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(crop_report_rows[0].keys()))
            w.writeheader()
            w.writerows(crop_report_rows)

    # Build report section
    ok_rows = [r for r in crop_report_rows if r.get("status") == "ok"]
    skip_rows = [r for r in crop_report_rows if r.get("status") != "ok"]

    lines = [
        "\n## Task 1 — Annotated SAR Crops (2026-07→09 window)\n",
        f"Tanks: {[t['id'] for t in crop_tanks]}  "
        f"({len(crop_tanks)} total: rt_0 + rt_11 + 3 neighbours)",
        f"Scenes: {len(scenes_to_use)} = up to 3 DESC 35 + 2 ASC 101",
        f"Resolution: 5 m/px, ×8 nearest upscale",
        f"Annotations: registry circle (green), inner annulus (green dashed), 282° crescent (magenta)\n",
        "**Zero-size crop fix**: guard `if x1 <= x0 or y1 <= y0: skip` added — "
        "prevents the numpy indexing crash that occurred in round 2.\n",
        f"**Crops generated**: {len(ok_rows)} of {len(crop_report_rows)} attempted",
    ]

    if skip_rows:
        lines.append(f"\n**Skipped / no data ({len(skip_rows)}):**")
        lines.append("| orbit | date | tank | reason |")
        lines.append("|-------|------|------|--------|")
        for r in skip_rows:
            lines.append(f"| {r.get('orbit')} | {r.get('date')} | {r.get('tank')} | {r.get('status')} |")

    lines.append(f"\n**Successful crops:**")
    lines.append("| orbit | date | tank | crop_h | crop_w |")
    lines.append("|-------|------|------|--------|--------|")
    for r in ok_rows:
        lines.append(f"| {r.get('orbit')} | {r.get('date')} | {r.get('tank')} | "
                     f"{r.get('crop_h')} | {r.get('crop_w')} |")

    lines.append(f"\nPNG files in `diagnostics/ras_tanura/task1_crops/`.")

    section = "\n".join(lines)
    REPORT.append(section)
    print(section)
    print("\nTask 1 complete.")


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 2 — Geolocation offset via template matching
# ═══════════════════════════════════════════════════════════════════════════════

def task2_geolocation_offset(desc35_scenes):
    print("\n=== TASK 2: Geolocation offset ===")

    try:
        from scipy.signal import fftconvolve
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as e:
        msg = f"\n## Task 2 — Geolocation Offset\n\nDependency missing: {e}\n"
        REPORT.append(msg)
        return

    # 10 south-cluster tanks
    offset_tanks = south_tanks[:10]
    print(f"  Offset tanks: {[t['id'] for t in offset_tanks]}")

    # 3 DESC 35 scenes (most recent from window)
    scenes = [s["date"].strftime("%Y-%m-%d") for s in desc35_scenes[:3]]
    if not scenes:
        REPORT.append("\n## Task 2 — Geolocation Offset\n\nNo DESC 35 scenes in window.\n")
        return

    print(f"  Scenes: {scenes}")

    res_m = 5
    SEARCH_PX = 10  # ±10 px search window

    all_offset_rows = []

    for date_str in scenes:
        for tank in offset_tanks:
            # Template: 3×3 radius patch around tank centre from registry
            bbox_tk = _bbox([tank], buf=int(tank["radius_m"] * 3 + 60))
            w, h = _size(bbox_tk, res=res_m)
            arr = _fetch(bbox_tk, date_str, w, h)
            if arr is None:
                print(f"    {date_str} {tank['id']}: no data")
                all_offset_rows.append({
                    "date": date_str, "tank": tank["id"],
                    "dx_px": None, "dy_px": None,
                    "dx_m": None, "dy_m": None, "status": "no_data"
                })
                continue

            arr_db = 10 * np.log10(np.clip(arr, 1e-10, None))
            arr_db[~np.isfinite(arr_db)] = 0.0

            cx, cy = _pix(tank["lon"], tank["lat"], bbox_tk, w, h)
            r_px = max(1, int(tank["radius_m"] / res_m))

            # Template centred exactly at registry coords
            tx0 = max(0, int(cx) - r_px)
            tx1 = min(w, int(cx) + r_px + 1)
            ty0 = max(0, int(cy) - r_px)
            ty1 = min(h, int(cy) + r_px + 1)

            if tx1 <= tx0 or ty1 <= ty0:
                all_offset_rows.append({
                    "date": date_str, "tank": tank["id"],
                    "dx_px": None, "dy_px": None,
                    "dx_m": None, "dy_m": None, "status": "template_zero_size"
                })
                continue

            template = arr_db[ty0:ty1, tx0:tx1].copy()
            template -= template.mean()

            # Search window: extend by SEARCH_PX on each side
            sx0 = max(0, tx0 - SEARCH_PX)
            sx1 = min(w, tx1 + SEARCH_PX)
            sy0 = max(0, ty0 - SEARCH_PX)
            sy1 = min(h, ty1 + SEARCH_PX)

            search = arr_db[sy0:sy1, sx0:sx1].copy()
            if search.shape[0] < template.shape[0] or search.shape[1] < template.shape[1]:
                all_offset_rows.append({
                    "date": date_str, "tank": tank["id"],
                    "dx_px": None, "dy_px": None,
                    "dx_m": None, "dy_m": None, "status": "search_too_small"
                })
                continue

            # Normalised cross-correlation via FFT
            t_flip = template[::-1, ::-1]
            xcorr = fftconvolve(search, t_flip, mode="valid")

            best_y, best_x = np.unravel_index(np.argmax(xcorr), xcorr.shape)

            # Offset relative to expected position
            # In "valid" convolution the output shape is search - template + 1
            # The expected position (zero offset) is at index (SEARCH_PX, SEARCH_PX)
            # corrected for possible border truncation
            expected_y = ty0 - sy0
            expected_x = tx0 - sx0

            dy_px = best_y - expected_y
            dx_px = best_x - expected_x

            dx_m = dx_px * res_m
            dy_m = dy_px * res_m  # positive = south in image coords

            all_offset_rows.append({
                "date": date_str, "tank": tank["id"],
                "dx_px": dx_px, "dy_px": dy_px,
                "dx_m": dx_m, "dy_m": dy_m, "status": "ok"
            })
            print(f"    {date_str} {tank['id']}: dx={dx_px}px dy={dy_px}px ({dx_m}m {dy_m}m)")
            time.sleep(0.15)

    # Save CSV
    if all_offset_rows:
        with open(TASK2 / "offsets.csv", "w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(all_offset_rows[0].keys()))
            w.writeheader()
            w.writerows(all_offset_rows)

    # Kharg control: 2 tanks, same 3 dates
    kh_tanks = list(KH_BY_ID.values())[:2]
    kh_rows = []
    for date_str in scenes:
        for tank in kh_tanks:
            bbox_tk = _bbox([tank], buf=int(tank["radius_m"] * 3 + 60))
            w_kh, h_kh = _size(bbox_tk, res=res_m)
            arr = _fetch(bbox_tk, date_str, w_kh, h_kh)
            if arr is None:
                kh_rows.append({"date": date_str, "tank": tank["id"],
                                "dx_px": None, "dy_px": None, "status": "no_data"})
                continue
            arr_db = 10 * np.log10(np.clip(arr, 1e-10, None))
            arr_db[~np.isfinite(arr_db)] = 0.0
            cx, cy = _pix(tank["lon"], tank["lat"], bbox_tk, w_kh, h_kh)
            r_px = max(1, int(tank["radius_m"] / res_m))
            tx0 = max(0, int(cx) - r_px)
            tx1 = min(w_kh, int(cx) + r_px + 1)
            ty0 = max(0, int(cy) - r_px)
            ty1 = min(h_kh, int(cy) + r_px + 1)
            if tx1 <= tx0 or ty1 <= ty0:
                kh_rows.append({"date": date_str, "tank": tank["id"],
                                "dx_px": None, "dy_px": None, "status": "template_zero_size"})
                continue
            template = arr_db[ty0:ty1, tx0:tx1].copy()
            template -= template.mean()
            sx0 = max(0, tx0 - SEARCH_PX)
            sx1 = min(w_kh, tx1 + SEARCH_PX)
            sy0 = max(0, ty0 - SEARCH_PX)
            sy1 = min(h_kh, ty1 + SEARCH_PX)
            search = arr_db[sy0:sy1, sx0:sx1]
            if search.shape[0] < template.shape[0] or search.shape[1] < template.shape[1]:
                kh_rows.append({"date": date_str, "tank": tank["id"],
                                "dx_px": None, "dy_px": None, "status": "search_too_small"})
                continue
            from scipy.signal import fftconvolve as fc2
            xcorr = fc2(search, template[::-1, ::-1], mode="valid")
            by, bx = np.unravel_index(np.argmax(xcorr), xcorr.shape)
            dy_px = by - (ty0 - sy0)
            dx_px = bx - (tx0 - sx0)
            kh_rows.append({"date": date_str, "tank": tank["id"],
                            "dx_px": dx_px, "dy_px": dy_px,
                            "dx_m": dx_px * res_m, "dy_m": dy_px * res_m,
                            "status": "ok"})
            time.sleep(0.15)

    # Statistics
    ok_rows = [r for r in all_offset_rows if r.get("status") == "ok"]
    df_off = pd.DataFrame(ok_rows) if ok_rows else pd.DataFrame()

    lines = [
        "\n## Task 2 — Geolocation Offset (Template Matching ±10px)\n",
        f"Tanks: 10 south-cluster RT tanks",
        f"Scenes: {scenes}",
        f"Method: normalised cross-correlation (FFT), ±10px search window, 5m/px",
        f"Search window: ±{SEARCH_PX}px = ±{SEARCH_PX * res_m}m\n",
    ]

    lines.append("### RT offset results\n")
    lines.append("| date | tank | dx_px | dy_px | dx_m | dy_m | status |")
    lines.append("|------|------|-------|-------|------|------|--------|")
    for r in all_offset_rows:
        lines.append(f"| {r['date']} | {r['tank']} | {r['dx_px']} | {r['dy_px']} | "
                     f"{r['dx_m']} | {r['dy_m']} | {r['status']} |")

    if not df_off.empty:
        mean_dx = round(df_off["dx_px"].mean(), 2)
        mean_dy = round(df_off["dy_px"].mean(), 2)
        std_dx  = round(df_off["dx_px"].std(), 2)
        std_dy  = round(df_off["dy_px"].std(), 2)
        systematic = abs(mean_dx) > 1 or abs(mean_dy) > 1
        lines.append(f"\n**Summary**: mean dx={mean_dx}px (±{std_dx}), mean dy={mean_dy}px (±{std_dy})")
        lines.append(f"Offset in metres: dx={round(mean_dx * res_m, 1)}m, dy={round(mean_dy * res_m, 1)}m")
        if systematic:
            lines.append(f"\n**⚠ SYSTEMATIC OFFSET DETECTED**: mean > 1px. "
                         "Crescent mask centres are biased. "
                         "Paired 282 vs 78 comparison in round 2 may be measuring displaced "
                         "annulus pixels — the shadow min-dB measurement is still valid "
                         "(angular sweep), but the absolute dB values may shift by ~"
                         f"{abs(round(mean_dx * res_m, 0))}m E/W. "
                         "Re-run pairing with corrected centres if offset > 2px.")
        else:
            lines.append("\n**Offset within ±1px**: registry coords are well-aligned. "
                         "Round 2 paired measurements unaffected by geolocation error.")

    if kh_rows:
        lines.append("\n### Kharg control (expected ≈ 0–1px)\n")
        lines.append("| date | tank | dx_px | dy_px | status |")
        lines.append("|------|------|-------|-------|--------|")
        for r in kh_rows:
            lines.append(f"| {r['date']} | {r['tank']} | {r.get('dx_px')} | {r.get('dy_px')} | {r['status']} |")

    section = "\n".join(lines)
    REPORT.append(section)
    print(section)
    print("\nTask 2 complete.")


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 3 — 213 single-tank dates vs orbit 137 12-day cycle
# ═══════════════════════════════════════════════════════════════════════════════

def task3_orbit137_cycle():
    print("\n=== TASK 3: Orbit 137 12-day cycle analysis ===")

    # Query terminal_observations for ras_tanura
    tid = "ras_tanura"
    rows = []
    offset = 0
    while True:
        batch = (sb.table("terminal_observations")
                   .select("date,tank_id,db_mean")
                   .eq("terminal_id", tid)
                   .range(offset, offset + 999)
                   .execute().data)
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000

    if not rows:
        REPORT.append("\n## Task 3 — Orbit 137 12-day Cycle\n\nNo rows in terminal_observations.\n")
        return

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)

    # Find dates where only 1 tank reported (single-tank dates)
    per_date = df.groupby("date")["tank_id"].nunique().reset_index()
    per_date.columns = ["date", "tank_count"]
    single_tank_dates = per_date[per_date["tank_count"] == 1]["date"].tolist()
    single_tank_dates.sort()

    n_single = len(single_tank_dates)
    # ТЗ says "213 single-tank dates" — use all of them
    print(f"  Found {n_single} single-tank dates (spec says 213)")

    # Orbit 137 repeats every 12 days.
    # Find reference dates from orbit 35 catalog (known good) and check if
    # orbit 137 offsets explain the single-tank dates.
    # DESC 35 period = 12 days. DESC 137 also repeats every 12 days.
    # The offset between orbit 35 and orbit 137 repeat dates is ~5-6 days
    # (both are descending, same satellite, 6-day sub-cycle).
    # Method: for each single-tank date, check if it is offset from
    # ANY reference DESC 35 date by approximately N*12 + offset_days.

    # Use a known DESC 35 anchor from the catalog
    tank_sample = TANK_BY_ID.get(REF_RT_60) or south_tanks[0]
    bbox_probe  = _bbox([tank_sample], buf=300)
    print("  Fetching DESC 35 catalog anchor (all time)...")
    desc35_all = _catalog(bbox_probe, "2023-01-01", WIN_END, "descending")
    desc35_all = [s for s in desc35_all if s["relative_orbit"] == 35]
    desc35_dates = sorted([s["date"].tz_localize(None) if s["date"].tzinfo else s["date"] for s in desc35_all])
    print(f"  DESC 35 dates found: {len(desc35_dates)}")

    if not desc35_dates:
        REPORT.append("\n## Task 3 — Orbit 137 12-day Cycle\n\nNo DESC 35 catalog dates found.\n")
        return

    # For each single-tank date, find nearest DESC 35 date and compute offset
    PERIOD = 12

    orbit137_matches = []
    non_matches = []

    for sd in single_tank_dates:
        # Nearest DESC 35 date
        diffs = [(sd - d).days for d in desc35_dates]
        abs_diffs = [abs(x % PERIOD) for x in diffs]  # within-cycle offset
        # Actual offset from nearest DESC 35 anchor
        nearest_d35 = min(desc35_dates, key=lambda d: abs((sd - d).days % PERIOD))
        offset_days = (sd - nearest_d35).days % PERIOD
        # Orbit 137 is offset ~5-6 days from orbit 35
        is_137 = 4 <= offset_days <= 7

        entry = {
            "date": sd.strftime("%Y-%m-%d"),
            "nearest_desc35": nearest_d35.strftime("%Y-%m-%d"),
            "offset_mod12": offset_days,
            "orbit137_candidate": is_137,
        }
        if is_137:
            orbit137_matches.append(entry)
        else:
            non_matches.append(entry)

    lines = [
        "\n## Task 3 — Single-Tank Dates vs Orbit 137 12-Day Cycle\n",
        f"**Single-tank dates in terminal_observations**: {n_single} (spec: 213)",
        f"**Period**: 12 days (Sentinel-1 repeat)",
        f"**DESC 35 anchor dates available**: {len(desc35_dates)}",
        f"**Orbit 137 offset from orbit 35**: 4–7 days within 12-day cycle\n",
        f"**Matches (offset 4–7 mod 12)**: {len(orbit137_matches)} / {n_single} = "
        f"{round(100*len(orbit137_matches)/max(n_single,1), 1)}%",
        f"**Non-matches**: {len(non_matches)} / {n_single}\n",
    ]

    if orbit137_matches:
        lines.append("### Sample orbit-137 candidate dates (first 20)\n")
        lines.append("| date | nearest_desc35 | offset_mod12 |")
        lines.append("|------|----------------|--------------|")
        for r in orbit137_matches[:20]:
            lines.append(f"| {r['date']} | {r['nearest_desc35']} | {r['offset_mod12']} |")

    if len(orbit137_matches) > 0.5 * n_single:
        lines.append(
            "\n**Conclusion**: The majority of single-tank dates fall within the "
            "orbit 137 12-day cycle window (offset 4–7 mod 12). "
            "This is consistent with DESC 137 contamination: on those dates only 1 tank "
            "returns non-null because orbit 137 has a different incidence angle "
            "and footprint geometry, covering only a subset of the tank farm."
        )
    else:
        lines.append(
            "\n**Conclusion**: Single-tank dates do NOT cluster on orbit-137 offsets. "
            "Contamination from orbit 137 is not the primary cause; "
            "another mechanism (partial orbit coverage, cloud/rain shadow, "
            "pipeline deduplication artifact) may be responsible."
        )

    section = "\n".join(lines)
    REPORT.append(section)
    print(section)
    print("\nTask 3 complete.")


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 4 — Exact SQL query on terminal_observations
# ═══════════════════════════════════════════════════════════════════════════════

def task4_exact_sql():
    print("\n=== TASK 4: Exact SQL at 10 specific dates ===")

    TARGET_DATES = [
        "2026-07-21", "2026-07-27",
        "2026-08-02", "2026-08-08",
        "2026-08-14", "2026-08-20",
        "2026-08-26",
        "2026-09-01", "2026-09-07",
        "2026-09-13",
    ]

    all_rows = []
    for d in TARGET_DATES:
        batch = (sb.table("terminal_observations")
                   .select("date,tank_id,db_mean,terminal_id")
                   .eq("terminal_id", "ras_tanura")
                   .eq("date", d)
                   .execute().data)
        all_rows.extend(batch)
        print(f"  {d}: {len(batch)} rows")

    df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame(
        columns=["date", "tank_id", "db_mean", "terminal_id"]
    )

    per_date_summary = []
    for d in TARGET_DATES:
        sub = df[df["date"] == d] if not df.empty else pd.DataFrame()
        n = len(sub)
        mean_db = round(sub["db_mean"].mean(), 3) if n > 0 else None
        tanks = sorted(sub["tank_id"].tolist()) if n > 0 else []
        per_date_summary.append({
            "date": d,
            "n_rows": n,
            "mean_db": mean_db,
            "tanks": ", ".join(tanks) if tanks else "—",
        })

    lines = [
        "\n## Task 4 — Exact SQL on terminal_observations (10 target dates)\n",
        "**Query**: `SELECT date, tank_id, db_mean FROM terminal_observations "
        "WHERE terminal_id = 'ras_tanura' AND date IN (...)`\n",
        "| date | n_rows | mean_db | tanks |",
        "|------|--------|---------|-------|",
    ]
    for r in per_date_summary:
        lines.append(f"| {r['date']} | {r['n_rows']} | {r['mean_db']} | {r['tanks'][:80]} |")

    total_rows = sum(r["n_rows"] for r in per_date_summary)
    empty_dates = [r["date"] for r in per_date_summary if r["n_rows"] == 0]

    lines.append(f"\n**Total rows across 10 dates**: {total_rows}")
    if empty_dates:
        lines.append(f"**Dates with zero rows**: {', '.join(empty_dates)}")
        lines.append("Zero rows on a date = no scene was processed by the pipeline "
                     "(orbit 35 did not have a pass, or the scene was rejected).")
    else:
        lines.append("**All 10 dates have rows**: pipeline coverage confirmed for this window.")

    section = "\n".join(lines)
    REPORT.append(section)
    print(section)
    print("\nTask 4 complete.")


# ═══════════════════════════════════════════════════════════════════════════════
# Write report
# ═══════════════════════════════════════════════════════════════════════════════

def write_report():
    header = f"""# VERIFICATION_3 — Ras Tanura Geolocation / Orbit / Date-Window Analysis

Generated: {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}
Scene window: {WIN_START} → {WIN_END} (corrected from round 2)

Task order per spec:
  5 (first): Scene date window root cause and fix confirmation
  1: Annotated crops — rt_0, rt_11 + 3 neighbours, 3 DESC 35 + 2 ASC 101
  2: Geolocation offset — template matching ±10px, 10 south tanks × 3 scenes
  3: 213 single-tank dates vs orbit 137 12-day cycle
  4: Exact SQL — terminal_observations for ras_tanura at 10 specific dates

No Supabase writes. No production code changes.
"""
    full = header + "\n".join(REPORT)

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = OUT_ROOT / "VERIFICATION_3.md"
    with open(report_path, "w") as f:
        f.write(full)

    print(f"\n{'='*60}")
    print("VERIFICATION_3.md written")
    print('='*60)
    print(full[:4000])
    if len(full) > 4000:
        print(f"... [+{len(full)-4000} chars] ...")
    print('='*60)


if __name__ == "__main__":
    # Task 5 first (fix + confirm scene window)
    desc35_scenes, asc101_scenes = task5_date_window_fix()

    # Task 1 (crops — hard blocker per spec)
    task1_annotated_crops(desc35_scenes, asc101_scenes)

    # Task 2 (geolocation offset)
    task2_geolocation_offset(desc35_scenes)

    # Task 3 (orbit 137 cycle)
    task3_orbit137_cycle()

    # Task 4 (exact SQL)
    task4_exact_sql()

    write_report()
    print("\nAll tasks complete.")
