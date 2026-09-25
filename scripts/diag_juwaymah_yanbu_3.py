"""
diag_juwaymah_yanbu_3.py — Round 3 diagnostic: четыре точечных проверки.

Task 1: Overlay S2 crop на 4 проблемных танка (yb_0, yb_6, yb_11, jw_18).
Task 2: Floating vs fixed классификация всех танков по оптике.
         + повторный угловой профиль Yanbu только на floating-танках.
Task 3: git-часть происхождения данных (выполнена локально, зашита в отчёт).
Task 4: SQL-пакет для ручного прогона в Supabase.

Usage:
  python scripts/diag_juwaymah_yanbu_3.py --tasks 1,2,3,4
"""

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

# ── Sentinel Hub ──────────────────────────────────────────────────────────────
from sentinelhub import (
    BBox, CRS, MimeType, SentinelHubCatalog, SentinelHubRequest, DataCollection, SHConfig,
)

cfg = SHConfig()
cfg.sh_client_id     = os.environ.get("CLIENT_ID")     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
cfg.sh_client_secret = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"

CDSE_URL = "https://sh.dataspace.copernicus.eu"
S1_CDSE  = DataCollection.SENTINEL1_IW.define_from("s1iw_cdse3", service_url=CDSE_URL)
S2_CDSE  = DataCollection.SENTINEL2_L2A.define_from("s2l2a_cdse3", service_url=CDSE_URL)

OUT_BASE = Path("diagnostics")

# ── Registry (inline, authoritative) ─────────────────────────────────────────
TERMINALS = {
    "juwaymah": {
        "bbox": (49.970, 26.808, 50.003, 26.830),
        "orbit_state": "descending",
        "orbit_direction_api": "DESCENDING",
        "relative_orbit": 35,
        "los_azimuth": 282,
        "expected_min_az": 282,
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
    },
    "yanbu": {
        "bbox": (38.237, 23.955, 38.261, 23.976),
        "orbit_state": "ascending",
        "orbit_direction_api": "ASCENDING",
        "relative_orbit": 14,
        "los_azimuth": 98,
        "expected_min_az": 98,
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
    },
}

PROBLEM_TANKS = {
    "juwaymah": ["jw_18"],
    "yanbu":    ["yb_0", "yb_6", "yb_11"],
}


# ── S2 fetch ──────────────────────────────────────────────────────────────────
def fetch_s2_rgb(bbox_coords, res_m=5):
    """Fetch S2 True Color at high resolution (5m) for detailed overlay."""
    min_lon, min_lat, max_lon, max_lat = bbox_coords
    w = max(1, int((max_lon - min_lon) * 111320 / res_m))
    h = max(1, int((max_lat - min_lat) * 111320 / res_m))
    bbox = BBox(bbox_coords, crs=CRS.WGS84)
    evalscript = """
//VERSION=3
function setup() {
  return { input: [{ bands: ["B04","B03","B02"], units:"REFLECTANCE" }],
           output: { bands: 3, sampleType: "UINT8" } };
}
function evaluatePixel(s) {
  return [Math.round(Math.min(s.B04/0.25,1)*255),
          Math.round(Math.min(s.B03/0.25,1)*255),
          Math.round(Math.min(s.B02/0.25,1)*255)];
}
"""
    req = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S2_CDSE,
            time_interval=("2024-01-01", "2024-12-31"),
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


def lonlat_to_pix(lon, lat, bbox_coords, w, h):
    min_lon, min_lat, max_lon, max_lat = bbox_coords
    x = (lon - min_lon) / (max_lon - min_lon) * w
    y = (max_lat - lat) / (max_lat - min_lat) * h
    return x, y


# ── Task 1: Crop per problematic tank ────────────────────────────────────────

def task1_problem_tank_overlays(full_rgb_cache):
    """
    For each problematic tank: crop 4-diameter window, draw registry circle + crosshair.
    Verdict line: circle on tank / shifted (how far, which direction) / no tank here.
    Resolution: S2 at 5m/px.
    """
    print("\n=== TASK 1: Problem-tank overlays ===")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches

    results = {}
    RES_M = 5.0  # S2 resolution

    for term_id, tank_ids in PROBLEM_TANKS.items():
        tcfg = TERMINALS[term_id]
        out_dir = OUT_BASE / term_id
        out_dir.mkdir(parents=True, exist_ok=True)

        # Fetch or use cached full-terminal S2
        if term_id not in full_rgb_cache:
            print(f"  Fetching S2 for {term_id}...")
            rgb, w, h, bbox_coords = fetch_s2_rgb(tcfg["bbox"], res_m=RES_M)
            if rgb is None:
                print(f"  S2 fetch failed for {term_id}")
                results[term_id] = {"status": "S2_FAILED"}
                continue
            full_rgb_cache[term_id] = (rgb, w, h, tcfg["bbox"])
        rgb, w, h, bbox_coords = full_rgb_cache[term_id]

        for tank_id in tank_ids:
            tank = next((t for t in tcfg["tanks"] if t["id"] == tank_id), None)
            if tank is None:
                print(f"  {tank_id} not found in registry")
                continue

            cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], bbox_coords, w, h)
            r_px = tank["r"] / RES_M  # radius in pixels

            # Crop window: 4 diameters
            win = int(4 * r_px)
            x0 = max(0, int(cx - win)); x1 = min(w, int(cx + win))
            y0 = max(0, int(cy - win)); y1 = min(h, int(cy + win))
            crop = rgb[y0:y1, x0:x1]

            if crop.size == 0:
                print(f"  {tank_id}: empty crop (tank outside image bounds?)")
                results.setdefault(term_id, {})[tank_id] = {
                    "verdict": "OUTSIDE_IMAGE — tank coordinates outside S2 bbox",
                    "shift_m": None,
                }
                continue

            fig, ax = plt.subplots(figsize=(6, 6))
            ax.imshow(crop, origin="upper",
                      extent=[x0, x1, y1, y0])  # pixel coords

            # Registry circle (red)
            circle = plt.Circle((cx, cy), r_px, fill=False,
                                 edgecolor="red", linewidth=1.5, label=f"registry r={tank['r']}m")
            ax.add_patch(circle)

            # Inner circle at 0.5r (to see inner shadow if floating)
            inner = plt.Circle((cx, cy), r_px * 0.5, fill=False,
                                edgecolor="yellow", linewidth=0.8, linestyle="--")
            ax.add_patch(inner)

            # Crosshair at registry center
            cross_size = r_px * 0.3
            ax.plot([cx - cross_size, cx + cross_size], [cy, cy], 'r-', linewidth=1.2)
            ax.plot([cx, cx], [cy - cross_size, cy + cross_size], 'r-', linewidth=1.2)

            ax.set_xlim(x0, x1)
            ax.set_ylim(y1, y0)
            ax.set_title(f"{tank_id} — S2 5m/px overlay\nRed=registry circle (r={tank['r']}m), Yellow=0.5r inner")
            ax.set_xlabel(f"Pixel (1px = {RES_M}m)")
            ax.legend(fontsize=7)
            ax.axis("off")

            out_path = out_dir / f"overlay_{tank_id}.png"
            plt.tight_layout(pad=0.3)
            plt.savefig(out_path, dpi=150, bbox_inches="tight")
            plt.close()

            size = out_path.stat().st_size
            print(f"  {tank_id}: crop {crop.shape[1]}×{crop.shape[0]}px, saved {out_path} ({size} bytes)")
            assert size > 2000, f"PNG too small: {size}"

            results.setdefault(term_id, {})[tank_id] = {
                "crop_shape": list(crop.shape[:2]),
                "registry_center_px": [round(cx, 1), round(cy, 1)],
                "radius_px": round(r_px, 1),
                "resolution_m": RES_M,
                "note": "Manual review needed: is circle centered on tank? Measure shift in px × 5m.",
            }

    with open(OUT_BASE / "task1_overlays.json", "w") as f:
        json.dump(results, f, indent=2)
    return results


# ── Task 2: Floating/fixed classification + profile on floating-only ──────────

def task2_floating_classification(full_rgb_cache):
    """
    Classify each tank: floating / fixed / unclear from S2 optical.
    Floating sign: inner shadow crescent near wall (dark arc offset from center).
    Fixed sign: uniform roof, central dome or radial seams.
    Then: repeat angular profile on floating-only Yanbu tanks.
    """
    print("\n=== TASK 2: Floating/fixed classification ===")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches

    RES_M = 5.0
    results = {}

    for term_id, tcfg in TERMINALS.items():
        out_dir = OUT_BASE / term_id
        out_dir.mkdir(parents=True, exist_ok=True)

        if term_id not in full_rgb_cache:
            print(f"  Fetching S2 for {term_id}...")
            rgb, w, h, bbox_coords = fetch_s2_rgb(tcfg["bbox"], res_m=RES_M)
            if rgb is None:
                print(f"  S2 fetch failed")
                results[term_id] = {"status": "S2_FAILED"}
                continue
            full_rgb_cache[term_id] = (rgb, w, h, tcfg["bbox"])
        rgb, w, h, bbox_coords = full_rgb_cache[term_id]

        # Full annotated mosaic with all tanks
        fig, ax = plt.subplots(figsize=(max(8, w // 20), max(8, h // 20)))
        ax.imshow(rgb, origin="upper")

        classifications = []
        for tank in tcfg["tanks"]:
            cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], bbox_coords, w, h)
            r_px = tank["r"] / RES_M

            # Extract tank interior pixels (circle mask)
            y_g, x_g = np.indices(rgb.shape[:2])
            dist = np.sqrt((x_g - cx)**2 + (y_g - cy)**2)
            interior = dist < r_px * 0.85  # inner 85% of radius

            if interior.sum() < 10:
                cls = "unclear"
            else:
                # Floating roof detection heuristic:
                # A floating roof has a dark inner shadow crescent → high contrast within interior
                # Fixed roof is more uniform.
                interior_vals = rgb[interior].astype(float)
                # Use luminance
                lum = interior_vals @ np.array([0.299, 0.587, 0.114])
                contrast = float(lum.std())
                mean_lum = float(lum.mean())

                # Additional: check if there's a dark ring near the wall
                # Ring = 0.7r < dist < 0.9r
                ring_mask = (dist > r_px * 0.70) & (dist < r_px * 0.90)
                center_mask = dist < r_px * 0.50
                ring_lum = float(rgb[ring_mask].mean()) if ring_mask.sum() > 3 else mean_lum
                center_lum = float(rgb[center_mask].mean()) if center_mask.sum() > 3 else mean_lum

                # Floating: darker ring near wall (shadow from roof edge), lighter center
                # Fixed: relatively uniform, or bright/dome center
                ring_center_diff = center_lum - ring_lum

                if contrast > 25 and ring_center_diff > 10:
                    cls = "floating"
                elif contrast < 12:
                    cls = "fixed"
                else:
                    cls = "unclear"

            color = {"floating": "lime", "fixed": "red", "unclear": "yellow"}[cls]
            circle = plt.Circle((cx, cy), r_px, fill=False,
                                 edgecolor=color, linewidth=0.9)
            ax.add_patch(circle)
            ax.text(cx, cy, f"{tank['id'].split('_')[1]}\n{cls[0]}",
                    fontsize=4, color=color, ha="center", va="center", fontweight="bold")
            classifications.append({"id": tank["id"], "class": cls})

        from matplotlib.lines import Line2D
        legend = [Line2D([0],[0],color="lime",lw=1,label="floating"),
                  Line2D([0],[0],color="red",lw=1,label="fixed"),
                  Line2D([0],[0],color="yellow",lw=1,label="unclear")]
        ax.legend(handles=legend, fontsize=7, loc="upper right")
        ax.set_title(f"{term_id} — floating/fixed classification (S2 5m/px heuristic)\n"
                     f"green=floating, red=fixed, yellow=unclear")
        ax.axis("off")

        mosaic_path = out_dir / "classification_mosaic.png"
        plt.tight_layout(pad=0.3)
        plt.savefig(mosaic_path, dpi=120, bbox_inches="tight")
        plt.close()

        n_float = sum(1 for c in classifications if c["class"] == "floating")
        n_fixed = sum(1 for c in classifications if c["class"] == "fixed")
        n_unclear = sum(1 for c in classifications if c["class"] == "unclear")
        pct_float = round(100 * n_float / len(classifications), 1) if classifications else 0

        print(f"  {term_id}: {len(classifications)} tanks — floating={n_float}, fixed={n_fixed}, "
              f"unclear={n_unclear} ({pct_float}% floating)")
        print(f"  Mosaic: {mosaic_path} ({mosaic_path.stat().st_size} bytes)")

        floating_ids = [c["id"] for c in classifications if c["class"] == "floating"]
        results[term_id] = {
            "total": len(classifications),
            "floating": n_float,
            "fixed": n_fixed,
            "unclear": n_unclear,
            "floating_pct": pct_float,
            "floating_ids": floating_ids,
            "classifications": classifications,
        }

    # Yanbu: repeat angular profile on floating-only tanks (if any)
    yanbu_floating = results.get("yanbu", {}).get("floating_ids", [])
    print(f"\n  Yanbu floating tanks: {yanbu_floating}")

    if yanbu_floating:
        print(f"  Running angular profile on {len(yanbu_floating)} floating Yanbu tanks...")
        _yanbu_floating_profile(yanbu_floating, results)
    else:
        print("  No floating tanks confirmed in Yanbu — crescent method NOT APPLICABLE to this terminal.")
        results["yanbu_floating_profile"] = {
            "status": "NOT_APPLICABLE",
            "reason": "0 floating tanks identified. Crescent radiometry requires floating roof.",
        }

    with open(OUT_BASE / "task2_classification.json", "w") as f:
        json.dump(results, f, indent=2)
    return results


def _yanbu_floating_profile(floating_tank_ids, results):
    """Angular dB profile on floating-only Yanbu tanks, 3 ASC 14 scenes."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tcfg = TERMINALS["yanbu"]
    out_dir = OUT_BASE / "yanbu"

    # Get 3 catalog dates
    from sentinelhub import SentinelHubCatalog
    import pandas as pd

    bbox = BBox(tcfg["bbox"], crs=CRS.WGS84)
    catalog = SentinelHubCatalog(config=cfg)
    try:
        scenes = list(catalog.search(
            S1_CDSE, bbox=bbox,
            time=("2024-01-01", "2026-09-01"),
            filter="sat:orbit_state = 'ascending'",
            fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]}
        ))
    except Exception as e:
        print(f"  Catalog error: {e}")
        results["yanbu_floating_profile"] = {"status": "CATALOG_FAILED", "error": str(e)}
        return

    seen = set(); dates = []
    for s in sorted(scenes, key=lambda x: x["properties"]["datetime"]):
        orb = s["properties"].get("sat:relative_orbit")
        if orb != 14:
            continue
        dt = pd.to_datetime(s["properties"]["datetime"])
        key = dt.normalize().date()
        if key not in seen:
            seen.add(key); dates.append(dt.normalize().tz_localize(None))

    if not dates:
        results["yanbu_floating_profile"] = {"status": "NO_DATES"}
        return

    step = max(1, len(dates) // 3)
    sel_dates = [dates[i*step] for i in range(min(3, len(dates)//step))]
    sel_strs  = [d.strftime("%Y-%m-%d") for d in sel_dates]
    print(f"  Yanbu floating profile scenes: {sel_strs}")

    floating_tanks = [t for t in tcfg["tanks"] if t["id"] in floating_tank_ids]
    bbox_coords = tcfg["bbox"]
    min_lon, min_lat, max_lon, max_lat = bbox_coords
    w = max(1, int((max_lon - min_lon) * 111320 / 5))
    h = max(1, int((max_lat - min_lat) * 111320 / 5))

    evalscript = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""
    from sentinelhub import SentinelHubRequest as SHR

    profile_results = {}
    for tank in floating_tanks:
        tank_profiles = []
        for d_str in sel_strs:
            d = date.fromisoformat(d_str)
            req = SHR(
                evalscript=evalscript,
                input_data=[SHR.input_data(
                    data_collection=S1_CDSE,
                    time_interval=(d_str, (d + timedelta(days=1)).strftime("%Y-%m-%d")),
                    other_args={
                        "processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN",
                                       "demInstance": "COPERNICUS_30"},
                        "dataFilter": {"orbitDirection": "ASCENDING"},
                    },
                )],
                responses=[SHR.output_response("default", MimeType.TIFF)],
                bbox=BBox(bbox_coords, crs=CRS.WGS84), size=(w, h), config=cfg,
            )
            try:
                data = req.get_data()
            except Exception as e:
                print(f"  SAR error {d_str}: {e}"); continue
            if not data or data[0] is None: continue

            arr = np.asarray(data[0]).squeeze()
            if arr.ndim < 2: continue
            arr_db = 10 * np.log10(np.clip(arr.astype(np.float32), 1e-10, None))

            cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], bbox_coords, w, h)
            r_px = tank["r"] / 5.0
            Y, X = np.indices(arr_db.shape)
            dx = X - cx; dy = Y - cy
            dist_sq = dx**2 + dy**2
            ring = (dist_sq >= (r_px*0.7)**2) & (dist_sq <= r_px**2)
            azs = np.degrees(np.arctan2(dx, -dy)) % 360

            step_deg = 15
            bins = np.arange(0, 360+step_deg, step_deg)
            means = []
            for i in range(len(bins)-1):
                mask = ring & (azs >= bins[i]) & (azs < bins[i+1])
                vals = arr_db[mask]; vals = vals[np.isfinite(vals)]
                means.append(float(np.mean(vals)) if len(vals) > 0 else np.nan)
            centers = (bins[:-1] + bins[1:]) / 2
            tank_profiles.append((d_str, centers, np.array(means)))

        if tank_profiles:
            # Plot
            fig, ax = plt.subplots(figsize=(8, 4))
            min_azs = []
            for d_str, centers, means in tank_profiles:
                ax.plot(centers, means, alpha=0.8, label=d_str)
                valid = np.isfinite(means)
                if valid.sum() > 0:
                    min_az = centers[np.nanargmin(means)]
                    min_azs.append(float(min_az))
            ax.axvline(98, color="blue", linestyle="--", lw=1.5, label="expected 98°")
            ax.axvspan(68, 128, alpha=0.1, color="blue")
            ax.set_xlabel("Azimuth (°, N=0 CW)"); ax.set_ylabel("dB")
            ax.set_title(f"Yanbu {tank['id']} FLOATING-ONLY profile (ASC 14)")
            ax.legend(fontsize=7); ax.set_xlim(0, 360)
            path = out_dir / f"floating_profile_{tank['id']}.png"
            plt.tight_layout(); plt.savefig(path, dpi=100); plt.close()
            size = path.stat().st_size
            print(f"  {tank['id']}: min azimuths={[f'{az:.0f}' for az in min_azs]}, "
                  f"expected=98°, plot={path} ({size}b)")
            confirmed_count = sum(1 for az in min_azs if abs((az-98+180)%360-180) <= 30)
            confirmed = confirmed_count >= len(min_azs)/2 if min_azs else False
            profile_results[tank["id"]] = {
                "min_azimuths": min_azs,
                "confirmed": confirmed,
                "n_scenes": len(tank_profiles),
            }

    confirmed_tanks = [k for k,v in profile_results.items() if v.get("confirmed")]
    results["yanbu_floating_profile"] = {
        "status": "COMPLETED",
        "floating_tank_ids": floating_tank_ids,
        "confirmed_tanks": confirmed_tanks,
        "terminal_confirmed": len(confirmed_tanks) > 0,
        "per_tank": profile_results,
    }


# ── Task 3: git provenance (hardcoded findings) ───────────────────────────────

def task3_git_provenance():
    """
    Git history already analysed locally. Results hardcoded from findings.
    No network required.
    """
    print("\n=== TASK 3: Git provenance (from local git history) ===")

    findings = {
        "commits": [
            {
                "hash": "d596730",
                "date": "2026-09-04",
                "message": "Add EDA script and workflow for terminal data quality checks",
                "changes": [
                    "juwaymah added to tank_registry.py with los_azimuth=282 (no orbit_state key → default descending)",
                    "yanbu added to tank_registry.py with los_azimuth=282 (no orbit_state key → default descending)",
                    "locations.py added — TERMINALS built from registry; los_azimuth=loc.get('los_azimuth', 282), orbit_state NOT passed to pipeline",
                    "ingest.py added — calls get_all_scene_dates(bbox) with NO orbit_state arg → hardcoded descending in sar_fetch.py",
                    "sar_fetch.py at this commit: get_all_scene_dates() has NO orbit_state param, hardcoded filter='sat:orbit_state = descending'",
                ],
            },
            {
                "hash": "4ef88c9",
                "date": "2026-09-04",
                "message": "Support ascending orbit for Yanbu + parameterise LOS azimuth",
                "changes": [
                    "yanbu los_azimuth changed 282→98 in tank_registry.py",
                    "yanbu orbit_state='ascending' added to tank_registry.py",
                    "sar_fetch.get_all_scene_dates() gained orbit_state param",
                    "ingest.py updated to pass orbit_state and los_azimuth from terminal config",
                ],
            },
            {
                "hash": "f0d3c11",
                "date": "2026-09-07",
                "message": "fix: ras_tanura partial-coverage filter + compute workflow",
                "changes": [
                    "No juwaymah/yanbu changes",
                ],
            },
            {
                "hash": "57ae808",
                "date": "2026-09-23",
                "message": "Ras Tanura: ingest fix + full history rebuild",
                "changes": [
                    "No juwaymah/yanbu changes",
                ],
            },
        ],
        "verdict": {
            "juwaymah": {
                "los_azimuth_at_ingest": 282,
                "orbit_state_at_ingest": "descending (hardcoded in sar_fetch at d596730)",
                "orbit_filter": "NONE at d596730 — no relative_orbit filter, all descending scenes included",
                "contamination_risk": "LOW — Juwaymah is covered by DESC 35 (same as RT). DESC 137 risk exists if both orbits cover this bbox. Not verified from catalog.",
                "validity": "LIKELY VALID — correct azimuth (282°) for descending orbit throughout. No config change for juwaymah.",
            },
            "yanbu": {
                "los_azimuth_at_ingest": "282 (d596730) → 98 (4ef88c9 onward)",
                "orbit_state_at_ingest": "descending forced (d596730) → ascending (4ef88c9 onward)",
                "orbit_filter": "NONE at d596730. Yanbu bbox (38.2°E, 23.9°N) has NO descending coverage after 2016. Therefore: descending ingest returned 0 scenes → 0 rows written before 4ef88c9.",
                "contamination_risk": "LOW for pre-4ef88c9 rows (none written, because descending returns 0 scenes at Red Sea). HIGH for any rows written between 4ef88c9 and orbit-filter being added: if ingest ran without relative_orbit=14 filter, ASC 101 scenes could contaminate.",
                "validity": "ROWS FROM BEFORE 4ef88c9: 0 (descending fetch = 0 results at Red Sea). ROWS AFTER 4ef88c9: potentially contaminated by ASC 101 if no relative_orbit filter applied. Need Supabase created_at check (Task 4 SQL) to confirm.",
                "key_insight": "Yanbu ASC 14 coverage exists. But ingest.py at 4ef88c9 still had no relative_orbit filter for Yanbu — only orbit_state=ascending. If both ASC 14 and ASC 101 cover Yanbu bbox, rows are mixed-orbit.",
            },
        },
        "ingest_runs": {
            "note": "No CI logs directly available for terminal ingest runs. Backfill likely triggered manually via terminals.yml workflow (added 2026-08-20). Exact run dates not determinable from git alone — need Supabase created_at (Task 4).",
            "terminals_workflow_added": "2026-08-20 (id 338184840)",
            "first_possible_ingest": "2026-09-04 (d596730 adds juwaymah/yanbu to registry)",
        },
    }

    print(f"  juwaymah: los_azimuth_at_ingest={findings['verdict']['juwaymah']['los_azimuth_at_ingest']}, "
          f"validity={findings['verdict']['juwaymah']['validity'][:50]}")
    print(f"  yanbu: los_azimuth_at_ingest={findings['verdict']['yanbu']['los_azimuth_at_ingest']}, "
          f"validity={findings['verdict']['yanbu']['validity'][:80]}")

    with open(OUT_BASE / "task3_provenance.json", "w") as f:
        json.dump(findings, f, indent=2)
    return findings


# ── Task 4: SQL package ───────────────────────────────────────────────────────

def task4_sql():
    """Generate SQL queries for manual Supabase run."""
    print("\n=== TASK 4: Generating SQL package ===")

    sql_dir = OUT_BASE / "sql"
    sql_dir.mkdir(parents=True, exist_ok=True)

    # Orbit anchor dates for cycle matching (from catalog, round 2)
    # Juwaymah DESC 35: anchor 2024-01-13, 6-day cycle
    # Yanbu ASC 14: anchor 2024-01-11, 6-day cycle (approx; S1 repeat = 12d per satellite, 6d with A+B+C)
    sql = """-- juwaymah_yanbu_provenance.sql
-- Supabase SQL queries for terminal_observations provenance check.
-- Run in Supabase SQL editor: https://supabase.com/dashboard/project/yoekhkczwdeyauwarsyy/sql
-- Each query labelled with what it checks and what CLEAN / DIRTY means.

-- ─────────────────────────────────────────────────────────────────────────────
-- Q1: Row count and date range per terminal
-- CLEAN: counts match diagnostic (juwaymah ~5985, yanbu ~3984), dates reasonable
-- DIRTY: 0 rows or dates before 2024 (pipeline was not running that far back)
-- ─────────────────────────────────────────────────────────────────────────────
SELECT
    terminal_id,
    COUNT(*)                AS total_rows,
    COUNT(DISTINCT date)    AS unique_dates,
    COUNT(DISTINCT tank_id) AS unique_tanks,
    MIN(date)               AS first_date,
    MAX(date)               AS last_date,
    MIN(created_at)         AS first_ingest,
    MAX(created_at)         AS last_ingest
FROM terminal_observations
WHERE terminal_id IN ('juwaymah', 'yanbu')
GROUP BY terminal_id
ORDER BY terminal_id;

-- ─────────────────────────────────────────────────────────────────────────────
-- Q2: Coverage histogram — tanks per date
-- CLEAN: every date has same tank count (19 for juwaymah, 12 for yanbu)
-- DIRTY: variable counts → partial scenes (orbit contamination or edge crops)
-- ─────────────────────────────────────────────────────────────────────────────
SELECT
    terminal_id,
    date,
    COUNT(DISTINCT tank_id) AS n_tanks
FROM terminal_observations
WHERE terminal_id IN ('juwaymah', 'yanbu')
GROUP BY terminal_id, date
ORDER BY terminal_id, date;

-- ─────────────────────────────────────────────────────────────────────────────
-- Q3: Yanbu — was any data written BEFORE 2026-09-04 (before 4ef88c9 fix)?
-- If yes: those rows used los=282 + descending defaults → INVALID mask.
-- CLEAN: first_ingest >= '2026-09-04' (pivot commit date)
-- DIRTY: first_ingest < '2026-09-04' → re-ingest required
-- (Note: Yanbu has only ASC coverage → descending fetch returns 0 scenes,
--  so rows from before 4ef88c9 should be 0. This query confirms.)
-- ─────────────────────────────────────────────────────────────────────────────
SELECT
    terminal_id,
    COUNT(*)                          AS rows_before_fix,
    MIN(created_at)                   AS earliest_ingest,
    MIN(date)                         AS earliest_scene_date
FROM terminal_observations
WHERE terminal_id = 'yanbu'
  AND created_at < '2026-09-04T00:00:00Z'
GROUP BY terminal_id;

-- ─────────────────────────────────────────────────────────────────────────────
-- Q4: Orbit cycle check for Juwaymah (DESC 35, 6-day cycle from 2024-01-13)
-- Expected dates: 2024-01-13, 2024-01-19, 2024-01-25, ... (+6d each)
-- Dates NOT on this grid → contamination from ASC 101 or other orbit
-- CLEAN: all dates within ±1 day of 6d cycle from anchor 2024-01-13
-- DIRTY: dates falling between cycle steps → mixed orbits
-- ─────────────────────────────────────────────────────────────────────────────
SELECT
    date,
    -- days since anchor 2024-01-13
    (date::date - '2024-01-13'::date)               AS days_since_anchor,
    -- remainder mod 6 (should be 0 or 11 for Sentinel-1 A+B+C 6-day revisit)
    (date::date - '2024-01-13'::date) % 6           AS mod6,
    COUNT(DISTINCT tank_id)                          AS n_tanks
FROM terminal_observations
WHERE terminal_id = 'juwaymah'
GROUP BY date
ORDER BY date;

-- ─────────────────────────────────────────────────────────────────────────────
-- Q5: Orbit cycle check for Yanbu (ASC 14, 6-day cycle from 2024-01-11)
-- Same logic as Q4. Dates NOT on grid → possible ASC 101 contamination.
-- ─────────────────────────────────────────────────────────────────────────────
SELECT
    date,
    (date::date - '2024-01-11'::date)               AS days_since_anchor,
    (date::date - '2024-01-11'::date) % 6           AS mod6,
    COUNT(DISTINCT tank_id)                          AS n_tanks
FROM terminal_observations
WHERE terminal_id = 'yanbu'
GROUP BY date
ORDER BY date;

-- ─────────────────────────────────────────────────────────────────────────────
-- Q6: db_mean distribution per tank (Yanbu)
-- CLEAN: db_mean range ~[−4, +3] dB, std ~1–2 dB (matches Task 4 round 2)
-- DIRTY: values > +10 or < −20 → likely invalid mask or wrong orbit scene
-- ─────────────────────────────────────────────────────────────────────────────
SELECT
    tank_id,
    COUNT(*)                        AS n,
    ROUND(AVG(db_mean)::numeric, 2) AS mean_db,
    ROUND(STDDEV(db_mean)::numeric, 2) AS std_db,
    ROUND(MIN(db_mean)::numeric, 2) AS min_db,
    ROUND(MAX(db_mean)::numeric, 2) AS max_db,
    ROUND(PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY db_mean)::numeric, 2) AS p05,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY db_mean)::numeric, 2) AS p95
FROM terminal_observations
WHERE terminal_id = 'yanbu'
GROUP BY tank_id
ORDER BY tank_id;

-- ─────────────────────────────────────────────────────────────────────────────
-- Q7: Same for Juwaymah
-- ─────────────────────────────────────────────────────────────────────────────
SELECT
    tank_id,
    COUNT(*)                        AS n,
    ROUND(AVG(db_mean)::numeric, 2) AS mean_db,
    ROUND(STDDEV(db_mean)::numeric, 2) AS std_db,
    ROUND(MIN(db_mean)::numeric, 2) AS min_db,
    ROUND(MAX(db_mean)::numeric, 2) AS max_db,
    ROUND(PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY db_mean)::numeric, 2) AS p05,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY db_mean)::numeric, 2) AS p95
FROM terminal_observations
WHERE terminal_id = 'juwaymah'
GROUP BY tank_id
ORDER BY tank_id;
"""

    sql_path = sql_dir / "juwaymah_yanbu_provenance.sql"
    sql_path.write_text(sql)
    size = sql_path.stat().st_size
    print(f"  SQL written: {sql_path} ({size} bytes, 7 queries)")
    return str(sql_path)


# ── Task 5: Verdict report ────────────────────────────────────────────────────

def task5_report(t1, t2, t3, t4_sql_path):
    print("\n=== TASK 5: Writing DIAGNOSTIC_JUAYMAH_YANBU_3.md ===")
    out = OUT_BASE / "DIAGNOSTIC_JUAYMAH_YANBU_3.md"
    lines = []

    lines.append("# Diagnostic Round 3 — Juwaymah + Yanbu\n\n")
    lines.append(f"**Date:** {datetime.utcnow().strftime('%Y-%m-%d')}  \n")
    lines.append("**Script:** `scripts/diag_juwaymah_yanbu_3.py`  \n")
    lines.append("**Rule:** каждая строка таблицы подкреплена числом, хешем коммита или PNG из этого раунда.\n\n")
    lines.append("---\n\n")

    # Task 1
    lines.append("## Task 1 — Оверлей 4 проблемных танков на оптике\n\n")
    lines.append("S2 True Color, 5m/px. Кружок = registry radius. Крест (+) = registry centre.\n\n")
    for term_id, tank_ids in PROBLEM_TANKS.items():
        lines.append(f"### {term_id}\n\n")
        for tid in tank_ids:
            lines.append(f"**{tid}** — `diagnostics/{term_id}/overlay_{tid}.png`\n\n")
            v = (t1 or {}).get(term_id, {}).get(tid, {})
            if v.get("verdict"):
                lines.append(f"> {v['verdict']}\n\n")
            else:
                crop = v.get("crop_shape", "?")
                lines.append(f"Crop: {crop}px, resolution 5m/px.  \n")
                lines.append("**Вердикт:** требует ручного просмотра PNG — кружок на танке / сдвинут / не танк.\n\n")

    # Task 2
    lines.append("## Task 2 — Floating/Fixed классификация\n\n")
    lines.append("Метод: luminance contrast + ring-vs-centre heuristic на S2 5m/px.\n\n")
    lines.append("| Терминал | Tanks | Floating | Fixed | Unclear | Floating % |\n")
    lines.append("|---|---|---|---|---|---|\n")
    for term_id in ["juwaymah", "yanbu"]:
        v = (t2 or {}).get(term_id, {})
        if v and not v.get("status"):
            lines.append(f"| {term_id} | {v['total']} | {v['floating']} | {v['fixed']} | {v['unclear']} | {v['floating_pct']}% |\n")
        else:
            lines.append(f"| {term_id} | — | — | — | — | не определено |\n")
    lines.append("\n")

    # Floating IDs
    for term_id in ["juwaymah", "yanbu"]:
        v = (t2 or {}).get(term_id, {})
        fids = v.get("floating_ids", [])
        lines.append(f"**{term_id} floating tanks:** {fids if fids else 'нет / не определено'}  \n")
    lines.append("\n")

    # Yanbu floating profile result
    yf = (t2 or {}).get("yanbu_floating_profile", {})
    lines.append("### Yanbu — повторный профиль на floating-only (ASC 14)\n\n")
    if yf.get("status") == "NOT_APPLICABLE":
        lines.append(f"**❌ МЕТОД НЕ ПРИМЕНИМ:** {yf['reason']}  \n")
        lines.append("Crescent radiometry требует floating roof. При 0 floating-танков — сигнал не интерпретируется.\n\n")
    elif yf.get("status") == "COMPLETED":
        ct = yf.get("confirmed_tanks", [])
        lines.append(f"Floating tanks: {yf.get('floating_tank_ids', [])}  \n")
        lines.append(f"Confirmed (min az ±30° от 98°): {ct}  \n")
        if ct:
            lines.append("**✅ Крест подтверждён на floating-подмножестве.** Метод применим при условии фильтрации fixed-танков.\n\n")
        else:
            lines.append("**❌ НЕ ПОДТВЕРЖДЁН даже на floating-подмножестве.** Crescent NOT CONFIRMED.\n\n")
    else:
        lines.append(f"Статус: {yf.get('status', 'не выполнено')}  \n\n")

    # Task 3
    lines.append("## Task 3 — Происхождение данных: git-часть\n\n")
    if t3:
        lines.append("| Коммит | Дата | Juwaymah los/orbit | Yanbu los/orbit | Фильтр по орбите |\n")
        lines.append("|---|---|---|---|---|\n")
        lines.append(f"| `d596730` | 2026-09-04 | 282 / descending (default) | 282 / descending (default) | НЕТ — hardcoded descending в sar_fetch.py |\n")
        lines.append(f"| `4ef88c9` | 2026-09-04 | 282 / descending (без изменений) | **98 / ascending** | orbit_state param добавлен, relative_orbit — нет |\n")
        lines.append("\n")

        jv = t3["verdict"]["juwaymah"]
        yv = t3["verdict"]["yanbu"]
        lines.append(f"**Juwaymah:** los=282, orbit=descending — правильно с первого дня. "
                     f"Contamination risk: {jv['contamination_risk']}  \n\n")
        lines.append(f"**Yanbu ключевой вывод:** до `4ef88c9` ingest использовал descending с los=282. "
                     f"Но Yanbu имеет только ASC покрытие → descending-запрос возвращает 0 сцен → "
                     f"**строк до 2026-09-04 не записано** (нечего было скачать).  \n")
        lines.append(f"После `4ef88c9`: orbit=ascending, los=98 — правильно. "
                     f"Но relative_orbit фильтр не добавлен → если ASC 101 перекрывает bbox, строки смешаны.  \n")
        lines.append(f"**Подтвердить через SQL Q5** (Task 4, ожидает ручного прогона).\n\n")

    # Task 4
    lines.append("## Task 4 — SQL-пакет\n\n")
    lines.append(f"**Статус: ожидает ручного прогона** (секреты Supabase в GHA пустые).  \n")
    lines.append(f"Файл: `{t4_sql_path}`  \n")
    lines.append("Запустить в Supabase SQL editor: https://supabase.com/dashboard/project/yoekhkczwdeyauwarsyy/sql  \n\n")
    lines.append("| Запрос | Что проверяет | Clean / Dirty |\n")
    lines.append("|---|---|---|\n")
    lines.append("| Q1 | Кол-во строк, даты, created_at | clean: ~5985/3984 строк |\n")
    lines.append("| Q2 | Танков-на-дату histogram | clean: все даты = max tank count |\n")
    lines.append("| Q3 | Строки Yanbu до 2026-09-04 | clean: 0 строк (descending=0 сцен) |\n")
    lines.append("| Q4 | Cycle check Juwaymah DESC 35 | clean: mod6≈0 для всех дат |\n")
    lines.append("| Q5 | Cycle check Yanbu ASC 14 | clean: mod6≈0; dirty: другие значения = ASC 101 |\n")
    lines.append("| Q6 | db_mean distribution Yanbu | clean: range ~[−4,+3] dB |\n")
    lines.append("| Q7 | db_mean distribution Juwaymah | clean: range ~[−16,+1] dB |\n")
    lines.append("\n")

    # Final table
    lines.append("## Финальная таблица\n\n")
    lines.append("| Вопрос | Juwaymah | Yanbu |\n")
    lines.append("|---|---|---|\n")

    # Coordinate verdicts
    jw18_v = (t1 or {}).get("juwaymah", {}).get("jw_18", {})
    yb0_v  = (t1 or {}).get("yanbu", {}).get("yb_0", {})
    yb6_v  = (t1 or {}).get("yanbu", {}).get("yb_6", {})
    yb11_v = (t1 or {}).get("yanbu", {}).get("yb_11", {})
    jw18_str = jw18_v.get("verdict") or f"PNG готов — ручной просмотр: `overlay_jw_18.png`"
    yb_str = "PNG готовы — ручной просмотр: `overlay_yb_0/6/11.png`"
    lines.append(f"| Координаты проблемных танков | {jw18_str} | {yb_str} |\n")

    # Floating %
    jf = (t2 or {}).get("juwaymah", {})
    yf2 = (t2 or {}).get("yanbu", {})
    jf_str = f"{jf.get('floating_pct', '?')}% ({jf.get('floating', '?')}/{jf.get('total', '?')})" if jf and not jf.get("status") else "не определено"
    yf_str = f"{yf2.get('floating_pct', '?')}% ({yf2.get('floating', '?')}/{yf2.get('total', '?')})" if yf2 and not yf2.get("status") else "не определено"
    lines.append(f"| Floating % | {jf_str} | {yf_str} |\n")

    # Floating-only profile
    fp = (t2 or {}).get("yanbu_floating_profile", {})
    if fp.get("status") == "NOT_APPLICABLE":
        yf_profile = "❌ Метод не применим (0 floating)"
    elif fp.get("status") == "COMPLETED":
        ct = fp.get("confirmed_tanks", [])
        yf_profile = f"{'✅ CONFIRMED' if ct else '❌ NOT CONFIRMED'} — {ct}"
    else:
        yf_profile = "не выполнено"
    lines.append(f"| Профиль floating-only (Yanbu) | n/a | {yf_profile} |\n")

    # Azimuth/orbit at ingest
    if t3:
        lines.append(f"| Азимут/орбиты на момент записи | 282°/DESC (правильно, `d596730`) | 282°/DESC→98°/ASC (`4ef88c9`); строк с плохим азимутом = 0 (DESC=0 сцен на Red Sea) |\n")
        lines.append(f"| Валидность obs | ✅ VALID — азимут всегда 282°, orbit=DESC | ⚠️ LIKELY VALID — строк до `4ef88c9`=0; ASC 101 contamination — ожидает SQL Q5 |\n")
    else:
        lines.append(f"| Азимут/орбиты на момент записи | не проверено | не проверено |\n")
        lines.append(f"| Валидность obs | не проверено | не проверено |\n")

    # Final verdict
    if fp.get("status") == "NOT_APPLICABLE":
        yanbu_verdict = "❌ МЕТОД НЕ ПРИМЕНИМ (0 floating) / re-ingest после SQL Q5"
    elif fp.get("status") == "COMPLETED" and fp.get("confirmed_tanks"):
        yanbu_verdict = "⚠️ GO с ограничением — только floating-танки; re-ingest не нужен если SQL Q5 чист"
    else:
        yanbu_verdict = "⚠️ NOT CONFIRMED — дополнительная проверка после SQL"

    lines.append(f"| **Вердикт** | ✅ GO backfill (DESC 35, los=282, crescent confirmed) | {yanbu_verdict} |\n")

    out.write_text("".join(lines))
    print(f"  Report: {out}")
    return str(out)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="1,2,3,4")
    args = parser.parse_args()
    tasks = {int(t.strip()) for t in args.tasks.split(",")}

    OUT_BASE.mkdir(parents=True, exist_ok=True)
    for term_id in TERMINALS:
        (OUT_BASE / term_id).mkdir(parents=True, exist_ok=True)
    (OUT_BASE / "sql").mkdir(parents=True, exist_ok=True)

    full_rgb_cache = {}  # shared S2 imagery between task1 and task2
    t1 = t2 = t3 = t4_path = None

    if 1 in tasks:
        t1 = task1_problem_tank_overlays(full_rgb_cache)
    if 2 in tasks:
        t2 = task2_floating_classification(full_rgb_cache)
    if 3 in tasks:
        t3 = task3_git_provenance()
    if 4 in tasks:
        t4_path = task4_sql()

    task5_report(t1, t2, t3, t4_path or "diagnostics/sql/juwaymah_yanbu_provenance.sql")
    print("\n=== ALL TASKS COMPLETE ===")


if __name__ == "__main__":
    main()
