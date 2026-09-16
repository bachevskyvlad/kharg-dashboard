"""
verify_orbit_fix_2.py — VERIFICATION round 2 for Ras Tanura orbit / azimuth analysis.

Five tasks:
  1. Resolve azimuth mask convention in prod code — quote code, derive formula,
     calculate where shadow should be for ASC vs DESC.
  2. Per-scene paired comparison (282° vs 78°) for 10 ASC 101 + 10 DESC 35 scenes,
     both tanks. Median aggregates. Angular sweep profile (360°, 30°-wide sector, 5° step)
     to find actual shadow position empirically.
  3. Overlay crops with BOTH masks colored (282=red, 78=blue), side-by-side ASC vs DESC.
  4. Kharg cross-check: 2 DESC 35 scenes, tanks t60_1 (full) + t55_5 (empty),
     paired test + angular profile.
  5. DESC 137 contamination — query terminal_observations (not raw_observations).

Output: diagnostics/ras_tanura/VERIFICATION_2.md + diagnostics/ras_tanura/task*/

No Supabase writes. No production code changes.
"""

import os, sys, math, json, time, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# ── Credentials ───────────────────────────────────────────────────────────────
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_BASE_URL      = "https://sh.dataspace.copernicus.eu"
SUPABASE_URL     = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY     = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

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
    "s1iw_cdse_v2", service_url="https://sh.dataspace.copernicus.eu"
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
    south_tanks = RT_TANKS[:5]  # fallback

KH_TANKS    = _kh["tanks"]
KH_BY_ID    = {t["id"]: t for t in KH_TANKS}

REF_RT_60 = "rt_0"    # radius 60m south cluster
REF_RT_35 = "rt_11"   # radius 35m
KH_FULL   = "t60_1"   # chronically full
KH_IDLE   = "t55_5"   # chronically idle / empty

OUT_ROOT = Path("diagnostics/ras_tanura")
TASK2    = OUT_ROOT / "task2"
TASK3    = OUT_ROOT / "task3"
TASK4    = OUT_ROOT / "task4"
for d in [TASK2, TASK3, TASK4]:
    d.mkdir(parents=True, exist_ok=True)

REPORT = []

# ── Geometry ──────────────────────────────────────────────────────────────────

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

def _crescent_db(arr, mask):
    px = arr[mask]
    px = px[np.isfinite(px) & (px > 0)]
    if px.size == 0:
        return None, 0
    return float(10 * np.log10(np.mean(px))), int(px.size)

def _angular_sweep(arr, tank, bbox, w, h, sector_deg=30, step_deg=5, res_m=5, width_m=15):
    """
    Compute mean dB in a sliding azimuthal sector around the full annulus ring.
    Returns (angles_deg_list, mean_db_list).
    """
    cx, cy = _pix(tank["lon"], tank["lat"], bbox, w, h)
    Y, X = np.indices((arr.shape[0], arr.shape[1]))
    dsq  = (X - cx)**2 + (Y - cy)**2
    ro   = tank["radius_m"] / res_m
    ri   = max(0, tank["radius_m"] - width_m) / res_m
    ring = (dsq <= ro**2) & (dsq >= ri**2)

    angles_deg = np.arange(0, 360, step_deg)
    dbs        = []
    for center_az in angles_deg:
        az_rad = math.radians(center_az)
        dx = math.sin(az_rad)
        dy = -math.cos(az_rad)
        dot = (X - cx) * dx + (Y - cy) * dy
        half = math.sin(math.radians(sector_deg / 2))
        # sector: dot > cos(sector/2) * r ≈ use angular test
        # compute pixel angle and compare
        theta = np.degrees(np.arctan2(X - cx, -(Y - cy))) % 360
        lo = (center_az - sector_deg / 2) % 360
        hi = (center_az + sector_deg / 2) % 360
        if lo < hi:
            sector_mask = ring & (theta >= lo) & (theta < hi)
        else:
            sector_mask = ring & ((theta >= lo) | (theta < hi))
        db, n = _crescent_db(arr, sector_mask)
        dbs.append(db if db is not None else np.nan)
    return angles_deg.tolist(), dbs

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
    fields  = {"include": ["properties.datetime","properties.sat:orbit_state",
                            "properties.sat:relative_orbit","properties.platform"]}
    results = []
    for item in catalog.search(S1_CDSE, bbox=bbox, time=(start, end), fields=fields):
        props = item.get("properties", {})
        if props.get("sat:orbit_state", "").lower() == orbit_state.lower():
            dt  = pd.to_datetime(props["datetime"]).normalize()
            rob = props.get("sat:relative_orbit")
            results.append({"date": dt, "relative_orbit": rob, "orbit_state": orbit_state})
    seen, uniq = set(), []
    for r in sorted(results, key=lambda x: x["date"], reverse=True):
        k = r["date"]
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 1 — Convention audit
# ═══════════════════════════════════════════════════════════════════════════════

def task1_convention():
    print("\n=== TASK 1: Azimuth mask convention ===")

    convention_text = """
## Task 1 — Azimuth Mask Convention in Prod Code

### Code (scripts/sar_fetch.py, build_crescent_masks, lines 128-166)

```python
az = los_azimuth if los_azimuth is not None else LOS_AZIMUTH   # 282° default
dx = np.sin(np.deg2rad(az))
dy = -np.cos(np.deg2rad(az))
dot_product = (X_grid - cx) * dx + (Y_grid - cy) * dy
crescent = ... & (dot_product > 0)
```

### Coordinate system

Image axes: X increases EAST, Y increases SOUTH (standard raster — origin top-left,
`dy = -cos(az)` flips the North/South sense back to geographic).

Direction vector `(dx, dy) = (sin(az), -cos(az))` in geographic East/North:
- az=0°   → (0, 1)  = **North**
- az=90°  → (1, 0)  = **East**
- az=180° → (0, -1) = **South**
- az=270° → (-1, 0) = **West**
- az=282° → (-0.978, 0.208) ≈ **WNW** (between West and NW)

`dot_product > 0` selects pixels that lie IN THE DIRECTION of `az` from centre.

### Conclusion: `los_azimuth` = direction TO the shadow (far side)

Docstring says "far-side half-plane (pixels whose dot product with the LOS direction
vector is positive)". The crescent is placed in the direction the azimuth points.

**`los_azimuth = 282°`** → mask placed at WNW → shadow at WNW.

### Shadow geometry for Sentinel-1

Sentinel-1 is right-looking (radar beam points RIGHT of flight direction).
The shadow falls on the FAR side from the satellite (opposite to range direction).

**Descending orbit** (satellite flies NW→SE, heading ≈ 170-190° relative to North
when travelling south; range direction ≈ ENE from the satellite → shadow at WNW):
- Satellite range azimuth to target: ~282° FROM target TO satellite = ~102°
- Shadow at: ~282° ✓ — `los_azimuth=282` is correct for DESC.

**Ascending orbit** (satellite flies SE→NW, heading ≈ 350-10°; range direction ≈ ENE
= satellite is at WNW of target → shadow falls at ENE):
- Satellite range azimuth from target: ~78-102° (ENE)
- Shadow at: ~78-102°
- `los_azimuth=282` places mask at WNW = OPPOSITE the shadow → **incorrect for ASC**.

### Expected behaviour (summary)

| Orbit | Correct los_azimuth | Mask position | Comment |
|-------|-------------------|---------------|---------|
| DESC (35, 137) | 282° | WNW | Current prod default ✓ |
| ASC (101) | ~78-102° | ENE | Docstring note: "Use ~98 for ascending orbit" |

Current prod config has no `orbit_state` for Ras Tanura (defaults to "descending").
ASC 101 scenes are **not ingested at all** — DESC 137 is the contamination source.
"""

    REPORT.append(convention_text)
    print(convention_text)
    print("Task 1 complete.")


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 2 — Per-scene paired comparison + angular sweep
# ═══════════════════════════════════════════════════════════════════════════════

def task2_paired_comparison():
    print("\n=== TASK 2: Per-scene paired comparison + angular sweep ===")

    tank_60 = TANK_BY_ID.get(REF_RT_60) or south_tanks[0]
    tank_35 = TANK_BY_ID.get(REF_RT_35)
    if tank_35 is None:
        cands = [t for t in RT_TANKS if abs(t.get("radius_m", 0) - 35) < 5]
        tank_35 = cands[0] if cands else RT_TANKS[1]

    tanks_pair = [tank_60, tank_35]
    all_results = {}   # orbit_key -> list of per-scene rows
    sweep_profiles = {}  # (tank_id, orbit_key, date) -> (angles, dbs)

    for orbit_state, rel_orbit, orbit_key in [
        ("ascending",  101, "ASC_101"),
        ("descending", 35,  "DESC_35"),
    ]:
        print(f"\n  Fetching catalog for {orbit_key}...")
        bbox_all = _bbox(tanks_pair, buf=500)
        scenes = _catalog(bbox_all, "2024-01-01", "2025-12-31", orbit_state=orbit_state)
        # filter to target relative orbit
        scenes = [s for s in scenes if s["relative_orbit"] == rel_orbit]
        # take 10 most recent
        scenes = scenes[:10]
        print(f"  Found {len(scenes)} scenes for {orbit_key}")

        rows = []
        sweep_this = {}

        for sc in scenes:
            date_str = sc["date"].strftime("%Y-%m-%d")
            print(f"    {orbit_key} {date_str}")

            for tk in tanks_pair:
                bbox_tk = _bbox([tk], buf=350)
                w, h    = _size(bbox_tk)
                arr     = _fetch(bbox_tk, date_str, w, h)
                if arr is None:
                    print(f"      {tk['id']}: no data")
                    continue

                db282, n282 = _crescent_db(arr, _crescent_mask(tk, bbox_tk, w, h, 282))
                db78,  n78  = _crescent_db(arr, _crescent_mask(tk, bbox_tk, w, h, 78))

                winner = None
                if db282 is not None and db78 is not None:
                    winner = "282" if db282 < db78 else ("78" if db78 < db282 else "tie")

                rows.append({
                    "orbit": orbit_key,
                    "date":  date_str,
                    "tank":  tk["id"],
                    "radius_m": tk.get("radius_m", "?"),
                    "db_282": round(db282, 3) if db282 is not None else None,
                    "db_78":  round(db78,  3) if db78  is not None else None,
                    "n_282":  n282,
                    "n_78":   n78,
                    "winner": winner,
                })

                # Angular sweep profile (3 scenes: first, middle, last)
                scene_idx = scenes.index(sc)
                if scene_idx in (0, len(scenes)//2, len(scenes)-1):
                    angles, dbs = _angular_sweep(arr, tk, bbox_tk, w, h)
                    sweep_this[(tk["id"], date_str)] = (angles, dbs)

            time.sleep(0.3)

        all_results[orbit_key] = rows
        sweep_profiles[orbit_key] = sweep_this

    # Save raw CSV
    all_rows = []
    for key, rows in all_results.items():
        all_rows.extend(rows)

    csv_path = TASK2 / "paired_comparison.csv"
    if all_rows:
        import csv as _csv
        with open(csv_path, "w", newline="") as f:
            writer = _csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"  Saved {csv_path}")

    # Save angular sweep profiles as JSON
    sweep_export = {}
    for orbit_key, profiles in sweep_profiles.items():
        sweep_export[orbit_key] = {}
        for (tank_id, date_str), (angles, dbs) in profiles.items():
            sweep_export[orbit_key][f"{tank_id}_{date_str}"] = {
                "angles": angles, "dbs": dbs
            }
    with open(TASK2 / "angular_sweeps.json", "w") as f:
        json.dump(sweep_export, f, indent=2)

    # Generate plots
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        for orbit_key, profiles in sweep_profiles.items():
            for (tank_id, date_str), (angles, dbs) in profiles.items():
                fig, ax = plt.subplots(figsize=(8, 4))
                ax.plot(angles, dbs, "b-", lw=1.5)
                ax.axvline(282, color="red", ls="--", lw=1, label="282° (prod)")
                ax.axvline(78,  color="green", ls="--", lw=1, label="78° (ASC cand.)")
                min_idx = int(np.nanargmin(dbs)) if not all(np.isnan(dbs)) else 0
                ax.axvline(angles[min_idx], color="orange", ls=":", lw=1.5,
                           label=f"min at {angles[min_idx]}°")
                ax.set_xlabel("Sector centre azimuth (°)")
                ax.set_ylabel("Mean dB")
                ax.set_title(f"Angular sweep — {tank_id} {date_str} {orbit_key}")
                ax.legend(fontsize=8)
                ax.grid(True, alpha=0.3)
                fname = TASK2 / f"sweep_{orbit_key}_{tank_id}_{date_str}.png"
                fig.savefig(fname, dpi=120, bbox_inches="tight")
                plt.close(fig)
        print("  Angular sweep plots saved.")
    except Exception as e:
        print(f"  Plot generation failed: {e}")

    # Build report section
    lines = ["\n## Task 2 — Per-Scene Paired Comparison (282° vs 78°) + Angular Sweep\n"]

    for orbit_key in ("ASC_101", "DESC_35"):
        rows = all_results.get(orbit_key, [])
        if not rows:
            lines.append(f"\n### {orbit_key}: no scenes downloaded\n")
            continue
        lines.append(f"\n### {orbit_key} — per-scene results\n")
        lines.append("| date | tank | db_282 | db_78 | winner |")
        lines.append("|------|------|--------|-------|--------|")
        for r in rows:
            lines.append(f"| {r['date']} | {r['tank']} | {r['db_282']} | {r['db_78']} | {r['winner']} |")

        # Medians per tank
        df = pd.DataFrame(rows)
        for tank_id, grp in df.groupby("tank"):
            v282 = grp["db_282"].dropna()
            v78  = grp["db_78"].dropna()
            if len(v282) and len(v78):
                med282 = round(v282.median(), 3)
                med78  = round(v78.median(), 3)
                diff   = round(med282 - med78, 3)
                winner = "282 lower" if med282 < med78 else "78 lower"
                lines.append(f"\n**{orbit_key} {tank_id} MEDIAN**: 282°={med282} dB, 78°={med78} dB, "
                              f"diff={diff:+.3f} dB → **{winner}**")

        # Angular sweep summary
        if orbit_key in sweep_profiles and sweep_profiles[orbit_key]:
            lines.append(f"\n#### {orbit_key} Angular Sweep Minima\n")
            lines.append("| tank | date | min_az° | min_db |")
            lines.append("|------|------|---------|--------|")
            for (tank_id, date_str), (angles, dbs) in sweep_profiles[orbit_key].items():
                valid = [(a, d) for a, d in zip(angles, dbs) if not np.isnan(d)]
                if valid:
                    min_az, min_db = min(valid, key=lambda x: x[1])
                    lines.append(f"| {tank_id} | {date_str} | {min_az} | {round(min_db, 3)} |")

    lines.append("\n**Angular sweep = main empirical arbiter.** "
                 "Shadow is at the minimum-dB azimuth.\n"
                 "For DESC: expect min near 282°. For ASC: expect min near 78-102°.")

    section = "\n".join(lines)
    REPORT.append(section)
    print(section)
    print("\nTask 2 complete.")


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 3 — Overlay crops: 282=red, 78=blue, side-by-side ASC vs DESC
# ═══════════════════════════════════════════════════════════════════════════════

def task3_overlay_crops():
    print("\n=== TASK 3: Overlay crops with both masks ===")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        tank_60 = TANK_BY_ID.get(REF_RT_60) or south_tanks[0]
        tank_35 = TANK_BY_ID.get(REF_RT_35)
        if tank_35 is None:
            cands = [t for t in RT_TANKS if abs(t.get("radius_m", 0) - 35) < 5]
            tank_35 = cands[0] if cands else RT_TANKS[1]

        scene_pairs = {}  # orbit_key -> (date_str, arr, bbox, w, h)

        for orbit_state, rel_orbit, orbit_key in [
            ("ascending",  101, "ASC_101"),
            ("descending", 35,  "DESC_35"),
        ]:
            bbox_tk = _bbox([tank_60], buf=300)
            w, h    = _size(bbox_tk)
            scenes = _catalog(bbox_tk, "2024-06-01", "2025-12-31", orbit_state=orbit_state)
            scenes = [s for s in scenes if s["relative_orbit"] == rel_orbit]
            if not scenes:
                print(f"  No {orbit_key} scenes found")
                continue
            # take the most recent scene with valid data
            for sc in scenes[:5]:
                date_str = sc["date"].strftime("%Y-%m-%d")
                arr = _fetch(bbox_tk, date_str, w, h)
                if arr is not None:
                    scene_pairs[orbit_key] = (date_str, arr, bbox_tk, w, h)
                    print(f"  {orbit_key}: using {date_str}")
                    break
            time.sleep(0.3)

        if not scene_pairs:
            print("  No scenes downloaded for task 3")
            REPORT.append("\n## Task 3 — Overlay Crops\n\nNo scenes downloaded.\n")
            return

        for tank_label, tank in [("rt_60m", tank_60), ("rt_35m", tank_35)]:
            if tank_35 is None and tank_label == "rt_35m":
                continue

            n_panels = len(scene_pairs)
            if n_panels == 0:
                continue
            fig, axes = plt.subplots(1, n_panels, figsize=(6 * n_panels, 6))
            if n_panels == 1:
                axes = [axes]

            for ax, (orbit_key, (date_str, arr, bbox_tk, w, h)) in zip(axes, scene_pairs.items()):
                mask282 = _crescent_mask(tank, bbox_tk, w, h, 282)
                mask78  = _crescent_mask(tank, bbox_tk, w, h, 78)

                # Crop around tank
                cx, cy = _pix(tank["lon"], tank["lat"], bbox_tk, w, h)
                r_pix  = int(tank.get("radius_m", 60) / 5) + 20
                x0, x1 = max(0, int(cx) - r_pix), min(w, int(cx) + r_pix)
                y0, y1 = max(0, int(cy) - r_pix), min(h, int(cy) + r_pix)

                arr_crop = arr[y0:y1, x0:x1]
                # normalize for display
                valid = arr_crop[arr_crop > 0]
                vmin = np.percentile(valid, 5)  if valid.size else 0
                vmax = np.percentile(valid, 95) if valid.size else 1

                ax.imshow(arr_crop, cmap="gray", vmin=vmin, vmax=vmax, origin="upper")

                # overlay 282=red, 78=blue
                m282_crop = mask282[y0:y1, x0:x1]
                m78_crop  = mask78[y0:y1, x0:x1]

                red_overlay  = np.zeros((*arr_crop.shape, 4), dtype=np.float32)
                blue_overlay = np.zeros((*arr_crop.shape, 4), dtype=np.float32)
                red_overlay[m282_crop] = [1, 0, 0, 0.5]
                blue_overlay[m78_crop] = [0, 0.4, 1, 0.5]

                ax.imshow(red_overlay,  origin="upper")
                ax.imshow(blue_overlay, origin="upper")

                # arrows for satellite direction
                # DESC: satellite from ENE (az ≈ 102° from target), shadow at 282°
                # ASC:  satellite from WNW (az ≈ 282° from target), shadow at 78°
                sat_az    = 78 if "ASC" in orbit_key else 282
                shadow_az = (sat_az + 180) % 360
                mx, my = r_pix, r_pix
                arrow_len = r_pix * 0.7
                for az_deg, color, label in [(sat_az, "yellow", "SAT"), (shadow_az, "cyan", "SHADOW")]:
                    az_rad = math.radians(az_deg)
                    ax.annotate("", xy=(mx + arrow_len * math.sin(az_rad),
                                        my - arrow_len * math.cos(az_rad)),
                                xytext=(mx, my),
                                arrowprops=dict(arrowstyle="->", color=color, lw=2))
                    ax.text(mx + (arrow_len + 5) * math.sin(az_rad),
                            my - (arrow_len + 5) * math.cos(az_rad),
                            label, color=color, fontsize=7, ha="center", va="center")

                ax.set_title(f"{orbit_key} {date_str}\n{tank['id']}", fontsize=9)
                ax.axis("off")

            # legend
            fig.legend(handles=[
                mpatches.Patch(color="red",  alpha=0.5, label="282° mask (prod default)"),
                mpatches.Patch(color="blue", alpha=0.5, label="78° mask (ASC candidate)"),
            ], loc="lower center", ncol=2, fontsize=9, bbox_to_anchor=(0.5, -0.02))

            fname = TASK3 / f"overlay_{tank_label}.png"
            fig.savefig(fname, dpi=130, bbox_inches="tight")
            plt.close(fig)
            print(f"  Saved {fname}")

        REPORT.append("\n## Task 3 — Overlay Crops (282°=red, 78°=blue)\n\n"
                      "Crops show both masks overlaid on the actual SAR image.\n"
                      "Red = 282° mask (prod default for DESC); Blue = 78° mask (ASC candidate).\n"
                      "Satellite direction (yellow arrow) and expected shadow direction (cyan)\n"
                      "annotated for each orbit. See task3/ for PNG files.\n")
        print("Task 3 complete.")

    except Exception as e:
        print(f"  Task 3 error: {e}")
        import traceback; traceback.print_exc()
        REPORT.append(f"\n## Task 3 — Overlay Crops\n\nError: {e}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 4 — Kharg cross-check
# ═══════════════════════════════════════════════════════════════════════════════

def task4_kharg_crosscheck():
    print("\n=== TASK 4: Kharg cross-check ===")

    t_full  = KH_BY_ID.get(KH_FULL)
    t_empty = KH_BY_ID.get(KH_IDLE)
    if t_full is None or t_empty is None:
        REPORT.append("\n## Task 4 — Kharg Cross-check\n\nKharg tanks not found in registry.\n")
        return

    all_rows      = []
    sweep_results = {}

    for tk, label in [(t_full, "t60_1_FULL"), (t_empty, "t55_5_IDLE")]:
        bbox_tk = _bbox([tk], buf=350)
        w, h    = _size(bbox_tk)

        # Catalog DESC 35 for Kharg
        scenes = _catalog(bbox_tk, "2024-01-01", "2025-12-31", orbit_state="descending")
        scenes = [s for s in scenes if s.get("relative_orbit") == 35][:5]

        if not scenes:
            print(f"  No DESC 35 scenes for Kharg {tk['id']}")
            continue

        print(f"  Kharg {tk['id']}: {len(scenes)} scenes")
        for sc in scenes[:2]:
            date_str = sc["date"].strftime("%Y-%m-%d")
            arr = _fetch(bbox_tk, date_str, w, h)
            if arr is None:
                continue
            db282, n282 = _crescent_db(arr, _crescent_mask(tk, bbox_tk, w, h, 282))
            db78,  n78  = _crescent_db(arr, _crescent_mask(tk, bbox_tk, w, h, 78))
            winner = None
            if db282 is not None and db78 is not None:
                winner = "282" if db282 < db78 else ("78" if db78 < db282 else "tie")
            all_rows.append({
                "tank": tk["id"], "label": label, "date": date_str,
                "db_282": round(db282, 3) if db282 is not None else None,
                "db_78":  round(db78,  3) if db78  is not None else None,
                "n_282": n282, "n_78": n78, "winner": winner,
            })

            # Angular sweep
            angles, dbs = _angular_sweep(arr, tk, bbox_tk, w, h)
            sweep_results[f"{tk['id']}_{date_str}"] = (angles, dbs)
            time.sleep(0.3)

    # Angular sweep plots
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        for key, (angles, dbs) in sweep_results.items():
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(angles, dbs, "b-", lw=1.5)
            ax.axvline(282, color="red", ls="--", lw=1, label="282° (prod)")
            ax.axvline(78,  color="green", ls="--", lw=1, label="78°")
            valid = [(a, d) for a, d in zip(angles, dbs) if not np.isnan(d)]
            if valid:
                min_az, min_db = min(valid, key=lambda x: x[1])
                ax.axvline(min_az, color="orange", ls=":", lw=1.5, label=f"min at {min_az}°")
            ax.set_xlabel("Sector centre azimuth (°)")
            ax.set_ylabel("Mean dB")
            ax.set_title(f"Kharg angular sweep — {key}")
            ax.legend(fontsize=8)
            ax.grid(True, alpha=0.3)
            fig.savefig(TASK4 / f"sweep_kharg_{key}.png", dpi=120, bbox_inches="tight")
            plt.close(fig)
        print(f"  Kharg sweep plots saved.")
    except Exception as e:
        print(f"  Plot error: {e}")

    # Report section
    lines = ["\n## Task 4 — Kharg Cross-Check (DESC 35)\n",
             "Expectation: 282° wins clearly on Kharg (it's pure DESC 35, known-good signal).\n",
             "| tank | label | date | db_282 | db_78 | winner |",
             "|------|-------|------|--------|-------|--------|"]
    for r in all_rows:
        lines.append(f"| {r['tank']} | {r['label']} | {r['date']} | "
                     f"{r['db_282']} | {r['db_78']} | {r['winner']} |")

    lines.append("\n**Angular sweep minima (Kharg DESC 35):**\n")
    lines.append("| key | min_az° | min_db |")
    lines.append("|-----|---------|--------|")
    for key, (angles, dbs) in sweep_results.items():
        valid = [(a, d) for a, d in zip(angles, dbs) if not np.isnan(d)]
        if valid:
            min_az, min_db = min(valid, key=lambda x: x[1])
            lines.append(f"| {key} | {min_az} | {round(min_db, 3)} |")

    if all_rows:
        df = pd.DataFrame(all_rows)
        for tank_id, grp in df.groupby("tank"):
            v282 = grp["db_282"].dropna()
            v78  = grp["db_78"].dropna()
            if len(v282) and len(v78):
                med282 = round(v282.median(), 3)
                med78  = round(v78.median(), 3)
                winner = "282 lower ✓" if med282 < med78 else "78 lower — UNEXPECTED"
                lines.append(f"\n**Kharg {tank_id} MEDIAN**: 282°={med282}, 78°={med78} → {winner}")

    section = "\n".join(lines)
    REPORT.append(section)
    print(section)
    print("\nTask 4 complete.")


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 5 — DESC 137 in terminal_observations
# ═══════════════════════════════════════════════════════════════════════════════

def task5_desc137_contamination():
    print("\n=== TASK 5: DESC 137 in terminal_observations ===")

    tid = "ras_tanura"
    rows = []
    offset = 0
    while True:
        batch = (sb.table("terminal_observations")
                   .select("date,tank_id,db_mean,terminal_id")
                   .eq("terminal_id", tid)
                   .range(offset, offset + 999)
                   .execute().data)
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000

    if not rows:
        REPORT.append("\n## Task 5 — DESC 137 Contamination in terminal_observations\n\n"
                      "No rows found for terminal_id='ras_tanura'.\n")
        print("  No rows in terminal_observations for ras_tanura")
        return

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    total_rows  = len(df)
    date_count  = df["date"].nunique()
    date_min    = df["date"].min().strftime("%Y-%m-%d")
    date_max    = df["date"].max().strftime("%Y-%m-%d")

    # Identify potential DESC 137 scenes by looking at dates that fall in a 12-day
    # orbit cycle relative to DESC 35 dates.
    # DESC 35 has its own 12-day cycle; DESC 137 would appear at offset ~5-6 days.
    # Simple heuristic: group by week, then within each week if >1 distinct date → likely mixing.
    df["week"] = df["date"].dt.to_period("W")
    weekly = df.groupby("week")["date"].nunique()
    multi_date_weeks = weekly[weekly > 1]

    # db_mean distribution
    db_vals = df["db_mean"].dropna()
    db_stats = {
        "count": len(db_vals),
        "mean":  round(db_vals.mean(), 3),
        "std":   round(db_vals.std(), 3),
        "p5":    round(db_vals.quantile(0.05), 3),
        "p25":   round(db_vals.quantile(0.25), 3),
        "p50":   round(db_vals.median(), 3),
        "p75":   round(db_vals.quantile(0.75), 3),
        "p95":   round(db_vals.quantile(0.95), 3),
    }

    # Per-year row count and date count to see historical scale
    df["year"] = df["date"].dt.year
    yearly = df.groupby("year").agg(
        rows=("db_mean", "count"),
        dates=("date", "nunique"),
    ).reset_index()

    lines = [
        "\n## Task 5 — DESC 137 Contamination in terminal_observations\n",
        f"**Table**: terminal_observations | **terminal_id**: ras_tanura",
        f"**Total rows**: {total_rows}",
        f"**Unique dates**: {date_count}",
        f"**Date range**: {date_min} → {date_max}",
        f"\n**db_mean distribution** (all rows):",
        f"| stat | value |",
        f"|------|-------|",
    ]
    for k, v in db_stats.items():
        lines.append(f"| {k} | {v} |")

    lines.append(f"\n**Per-year summary:**")
    lines.append("| year | rows | unique_dates |")
    lines.append("|------|------|--------------|")
    for _, r in yearly.iterrows():
        lines.append(f"| {int(r['year'])} | {int(r['rows'])} | {int(r['dates'])} |")

    lines.append(f"\n**Weeks with >1 distinct date (likely multi-orbit mixing)**:")
    if len(multi_date_weeks):
        lines.append(f"Count: {len(multi_date_weeks)} weeks out of {len(weekly)}")
        lines.append("\nSample weeks with multiple dates:")
        lines.append("| week | distinct_dates |")
        lines.append("|------|----------------|")
        for w, cnt in multi_date_weeks.head(10).items():
            lines.append(f"| {w} | {cnt} |")
    else:
        lines.append("None — no week has >1 distinct acquisition date. No DESC 137 mixing detected.")

    # Show sample dates with db_mean for context
    lines.append("\n**Sample rows (10 most recent):**")
    lines.append("| date | tank_id | db_mean |")
    lines.append("|------|---------|---------|")
    for _, r in df.tail(10).iterrows():
        lines.append(f"| {r['date'].strftime('%Y-%m-%d')} | {r.get('tank_id','?')} | {round(r['db_mean'],3) if pd.notna(r['db_mean']) else 'null'} |")

    section = "\n".join(lines)
    REPORT.append(section)
    print(section)
    print("\nTask 5 complete.")


# ═══════════════════════════════════════════════════════════════════════════════
# Write report
# ═══════════════════════════════════════════════════════════════════════════════

def write_report():
    header = f"""# VERIFICATION_2 — Ras Tanura Orbit / Azimuth Analysis

Generated: {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}

Five tasks:
1. Convention audit — what does los_azimuth=282 mean geometrically?
2. Per-scene paired comparison (282° vs 78°) + angular sweep, 10 ASC + 10 DESC scenes
3. Visual overlay crops — 282=red, 78=blue, side-by-side ASC vs DESC
4. Kharg cross-check — DESC 35 only, expectation: 282° wins
5. DESC 137 contamination in terminal_observations (not raw_observations)

No Supabase writes. No production changes.
"""
    full = header + "\n".join(REPORT)

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = OUT_ROOT / "VERIFICATION_2.md"
    with open(report_path, "w") as f:
        f.write(full)

    print(f"\n{'='*60}")
    print("VERIFICATION_2.md")
    print('='*60)
    print(full)
    print('='*60)


if __name__ == "__main__":
    task1_convention()
    task2_paired_comparison()
    task3_overlay_crops()
    task4_kharg_crosscheck()
    task5_desc137_contamination()
    write_report()
    print("\nAll tasks complete.")
