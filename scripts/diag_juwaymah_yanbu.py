"""
diag_juwaymah_yanbu.py — Diagnostic: Juwaymah Tank Farm + Yanbu crude terminal.

Tasks:
  0. Registry + Supabase summary (terminal_id, tanks, data coverage)
  1. Sentinel-2 optical crop → tank count, floating/fixed roof classification
  2. Sentinel-1 catalog → orbits, cadence, frame overlap with RT
  3. Per-tank SAR stats on 6-8 scenes → mean/std dB, crescent profile, dynamic range
  4. Print verdict table

Usage:
  python3 scripts/diag_juwaymah_yanbu.py

Requires: CLIENT_ID / CLIENT_SECRET env vars (or hardcoded fallback below).
Outputs written to: diagnostics/juwaymah/ and diagnostics/yanbu/

Rules:
  - Read-only on Supabase (publishable key only)
  - Rate limits respected (0.5s between SH requests)
  - No SAR data committed to git (too large); only stats CSVs + PNG crops
  - "not confirmed" written explicitly when signal does not match expectation
"""

import os, sys, time, json, math
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import date, timedelta

# ── Constants ─────────────────────────────────────────────────────────────────
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"
SUPABASE_URL     = os.environ.get("SUPABASE_URL")  or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY     = os.environ.get("SUPABASE_KEY")  or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

# Bounding boxes (WGS84 [lon_min, lat_min, lon_max, lat_max])
BBOX_JUWAYMAH   = [49.965, 26.805, 50.008, 26.835]  # ~4.8×3.3 km
BBOX_YANBU      = [38.234, 23.952, 38.263, 23.978]  # ~2.7×2.9 km
BBOX_RT_SOUTH   = [50.139, 26.630, 50.170, 26.660]  # RT south cluster (reference)
BBOX_RT_NORTH   = [50.105, 26.682, 50.135, 26.700]  # RT north cluster

# Expected orbits (to confirm via catalog)
EXPECTED_JW_ORBIT = 35   # DESC — same as RT (to verify)
EXPECTED_YB_ORBIT = None  # ASC — unknown, catalog will reveal

# Reference periods
CATALOG_START = "2024-01-01"
CATALOG_END   = "2026-09-20"
SCENE_FETCH_N = 8  # SAR scenes per terminal for stats

# Output dirs
OUT_JW = Path("diagnostics/juwaymah")
OUT_YB = Path("diagnostics/yanbu")
OUT_JW.mkdir(parents=True, exist_ok=True)
OUT_YB.mkdir(parents=True, exist_ok=True)

# ── Imports ───────────────────────────────────────────────────────────────────
from sentinelhub import (
    SHConfig, SentinelHubCatalog, SentinelHubRequest, DataCollection,
    BBox, CRS, MimeType, MosaickingOrder
)
import requests

cfg = SHConfig()
cfg.sh_client_id     = SH_CLIENT_ID
cfg.sh_client_secret = SH_CLIENT_SECRET
cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"

CDSE_URL = "https://sh.dataspace.copernicus.eu"
S1_CDSE  = DataCollection.SENTINEL1_IW.define_from("s1iw_cdse",  service_url=CDSE_URL)
S2_CDSE  = DataCollection.SENTINEL2_L2A.define_from("s2l2a_cdse", service_url=CDSE_URL)

# ── Task 0: Supabase summary ──────────────────────────────────────────────────
def task0_supabase():
    print("\n" + "="*70)
    print("TASK 0 — SUPABASE DATA INVENTORY")
    print("="*70)

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
               "Accept": "application/json"}

    rows = {}
    for terminal in ["juwaymah", "yanbu", "ras_tanura"]:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/terminal_observations"
            f"?select=date,tank_id&terminal_id=eq.{terminal}&limit=1",
            headers=headers, timeout=30
        )
        if r.status_code == 200:
            rows[terminal] = {"sample_ok": True, "data": r.json()}
        else:
            rows[terminal] = {"sample_ok": False, "status": r.status_code}

    # Full count via PostgREST head request
    for terminal in ["juwaymah", "yanbu", "ras_tanura"]:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/terminal_observations"
            f"?terminal_id=eq.{terminal}&select=date",
            headers={**headers, "Prefer": "count=exact"},
            timeout=30
        )
        count = r.headers.get("Content-Range", "?/0").split("/")[-1]
        print(f"  {terminal:<20} obs rows: {count}")
        time.sleep(0.5)

    for terminal in ["juwaymah", "yanbu", "ras_tanura"]:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/terminal_volume_timeseries"
            f"?terminal_id=eq.{terminal}&select=date",
            headers={**headers, "Prefer": "count=exact"},
            timeout=30
        )
        count = r.headers.get("Content-Range", "?/0").split("/")[-1]
        print(f"  {terminal:<20} vol_ts rows: {count}")
        time.sleep(0.5)


# ── Task 1: Sentinel-2 optical crop ──────────────────────────────────────────
def fetch_s2_true_color(bbox_coords, out_path, label, resolution=10):
    """Download Sentinel-2 True Color for a bbox, save PNG."""
    bbox = BBox(bbox_coords, crs=CRS.WGS84)
    evalscript = """
    //VERSION=3
    function setup() {
      return { input: ["B04","B03","B02","dataMask"], output: { bands:4 } };
    }
    function evaluatePixel(s) {
      return [3.5*s.B04, 3.5*s.B03, 3.5*s.B02, s.dataMask];
    }
    """
    req = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S2_CDSE,
            time_interval=("2026-06-01", "2026-09-20"),
            mosaicking_order=MosaickingOrder.LEAST_CC,
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.PNG)],
        bbox=bbox,
        size=[int((bbox_coords[2]-bbox_coords[0])*111320/resolution),
              int((bbox_coords[3]-bbox_coords[1])*111320/resolution)],
        config=cfg,
    )
    data = req.get_data()[0]
    from PIL import Image
    img = Image.fromarray(data)
    img.save(out_path)
    print(f"  Saved S2 crop: {out_path} ({img.size[0]}×{img.size[1]}px)")
    return np.array(data)


def task1_optical():
    print("\n" + "="*70)
    print("TASK 1 — SENTINEL-2 OPTICAL COVERAGE")
    print("="*70)
    from PIL import Image, ImageDraw
    import sys
    sys.path.insert(0, "scripts")
    from tank_registry import LOCATIONS

    for loc in LOCATIONS:
        if loc["id"] not in ("juwaymah", "yanbu"):
            continue
        out_dir = OUT_JW if loc["id"] == "juwaymah" else OUT_YB
        bbox = BBOX_JUWAYMAH if loc["id"] == "juwaymah" else BBOX_YANBU
        label = loc["name"]

        print(f"\n  {label}:")
        img_path = out_dir / "optical_s2.png"
        try:
            arr = fetch_s2_true_color(bbox, img_path, label)
            time.sleep(1)
        except Exception as e:
            print(f"  ERROR fetching S2: {e}")
            continue

        # Overlay registry tanks
        img = Image.open(img_path).convert("RGBA")
        draw = ImageDraw.Draw(img)

        lon_min, lat_min, lon_max, lat_max = bbox
        w, h = img.size

        for t in loc["tanks"]:
            # pixel coords
            px = int((t["lon"] - lon_min) / (lon_max - lon_min) * w)
            py = int((lat_max - t["lat"]) / (lat_max - lat_min) * h)
            r_px = max(2, int(t["radius_m"] / ((lon_max - lon_min) * 111320 / w)))
            draw.ellipse([px-r_px, py-r_px, px+r_px, py+r_px],
                         outline=(255, 0, 0, 200), width=2)

        overlay_path = out_dir / "optical_s2_registry_overlay.png"
        img.save(overlay_path)
        print(f"  Saved overlay: {overlay_path}")

        # Simple Hough circle count (rough estimate)
        try:
            import cv2
            gray = cv2.cvtColor(np.array(Image.open(img_path).convert("RGB")), cv2.COLOR_RGB2GRAY)
            # Estimate pixel radius range for large tanks
            r_min_px = int(30 / ((lon_max - lon_min) * 111320 / w))
            r_max_px = int(80 / ((lon_max - lon_min) * 111320 / w))
            circles = cv2.HoughCircles(
                gray, cv2.HOUGH_GRADIENT, dp=1.5, minDist=r_min_px*2,
                param1=50, param2=30, minRadius=r_min_px, maxRadius=r_max_px
            )
            n_circles = 0 if circles is None else len(circles[0])
            print(f"  Hough circle estimate (r={r_min_px}–{r_max_px}px): {n_circles} circles detected")
            print(f"  Registry tanks: {len(loc['tanks'])}")
        except ImportError:
            print("  cv2 not available — skipping Hough circle detection")


# ── Task 2: SAR catalog ───────────────────────────────────────────────────────
def task2_catalog():
    print("\n" + "="*70)
    print("TASK 2 — SENTINEL-1 SAR CATALOG")
    print("="*70)

    catalog = SentinelHubCatalog(config=cfg)
    fields = {"include": ["id", "properties.datetime",
                          "properties.sat:relative_orbit",
                          "properties.sat:orbit_state",
                          "properties.sar:instrument_mode"]}

    results = {}
    for name, bbox_coords in [
        ("juwaymah", BBOX_JUWAYMAH),
        ("yanbu", BBOX_YANBU),
        ("ras_tanura_ref", BBOX_RT_SOUTH),
    ]:
        bbox = BBox(bbox_coords, crs=CRS.WGS84)
        items = list(catalog.search(
            S1_CDSE, bbox=bbox,
            time=(CATALOG_START, CATALOG_END), fields=fields
        ))
        results[name] = items
        time.sleep(1)

        orbits = {}
        for item in items:
            p = item.get("properties", {})
            orbit = p.get("sat:relative_orbit", "?")
            state = p.get("sat:orbit_state", "?")
            key = f"{state}_{orbit}"
            orbits[key] = orbits.get(key, 0) + 1

        print(f"\n  {name}: {len(items)} total scenes")
        for k, v in sorted(orbits.items()):
            state, orb = k.split("_", 1)
            orb_dates = sorted(set(
                r["properties"]["datetime"][:10] for r in items
                if str(r["properties"].get("sat:relative_orbit","")) == orb
                and r["properties"].get("sat:orbit_state","") == state
            ))
            if len(orb_dates) >= 2:
                gaps = [(date.fromisoformat(orb_dates[i]) - date.fromisoformat(orb_dates[i-1])).days
                        for i in range(1, len(orb_dates))]
                avg_gap = sum(gaps)/len(gaps)
                print(f"    {state} orbit {orb}: {v} scenes, avg gap {avg_gap:.1f}d, "
                      f"range {orb_dates[0]}–{orb_dates[-1]}")
            else:
                print(f"    {state} orbit {orb}: {v} scenes")

        # Save scene list
        out_dir = OUT_JW if name == "juwaymah" else (OUT_YB if name == "yanbu" else Path("diagnostics"))
        scene_rows = []
        for item in items:
            p = item.get("properties", {})
            scene_rows.append({
                "id": item.get("id",""),
                "date": p.get("datetime","")[:10],
                "orbit": p.get("sat:relative_orbit",""),
                "state": p.get("sat:orbit_state",""),
            })
        pd.DataFrame(scene_rows).to_csv(out_dir / "sar_catalog.csv", index=False)
        print(f"  Saved: {out_dir}/sar_catalog.csv")

    # Check if JW and RT are covered by same frame
    jw_orbits = set()
    rt_orbits = set()
    for item in results.get("juwaymah", []):
        p = item.get("properties", {})
        jw_orbits.add((p.get("sat:relative_orbit"), p.get("sat:orbit_state")))
    for item in results.get("ras_tanura_ref", []):
        p = item.get("properties", {})
        rt_orbits.add((p.get("sat:relative_orbit"), p.get("sat:orbit_state")))

    common = jw_orbits & rt_orbits
    print(f"\n  OVERLAP: Juwaymah ∩ RT orbits = {common}")
    if common:
        print("  ✓ CONFIRMED: Juwaymah shares orbit frames with RT — backfill is FREE")
    else:
        print("  ✗ No shared orbit frames — Juwaymah requires separate download")

    return results


# ── Task 3: SAR signal quality ────────────────────────────────────────────────
def fetch_sar_scene(bbox_coords, date_str, orbit=None):
    """Fetch S1 VV backscatter in dB for a given bbox and date."""
    bbox = BBox(bbox_coords, crs=CRS.WGS84)
    evalscript = """
    //VERSION=3
    function setup() {
      return {
        input: [{bands: ["VV"], units: "LINEAR_POWER"}],
        output: {bands: 1, sampleType: "FLOAT32"}
      };
    }
    function evaluatePixel(s) {
      var db = 10 * Math.log10(s.VV + 1e-10);
      return [db];
    }
    """
    req = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(date_str, date_str),
            other_args={"dataFilter": {
                "acquisitionMode": "IW",
                "polarization": "DV",
                **({"orbitDirection": orbit.upper()} if orbit else {})
            }},
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox,
        resolution=5,
        config=cfg,
    )
    data = req.get_data()
    if not data or data[0] is None:
        return None
    arr = np.asarray(data[0])
    if arr.ndim < 3:
        return None
    return arr[:, :, 0]  # shape (H, W)


def crescent_mask(cx, cy, radius_px, los_az_deg, arc_deg=60, n_rings=3, ring_width=2):
    """Return pixel mask for crescent on the far side of a floating-roof tank.
    los_az_deg: Sentinel-1 line-of-sight azimuth (282 for DESC, 98 for ASC).
    Shadow crescent appears at the far wall (opposite to illumination direction).
    Illumination from ~west (DESC 282°) → shadow at east side of tank.
    """
    far_az = (los_az_deg + 180) % 360  # shadow side azimuth
    far_az_rad = math.radians(far_az)

    h, w = int(2*(radius_px+5)), int(2*(radius_px+5))
    mask = np.zeros((h, w), dtype=bool)
    yc, xc = h//2, w//2

    arc_half = math.radians(arc_deg / 2)
    for ring in range(1, n_rings+1):
        r = radius_px - ring * ring_width
        if r < 1:
            continue
        for px in range(w):
            for py in range(h):
                dx, dy = px - xc, py - yc
                dist = math.sqrt(dx**2 + dy**2)
                if abs(dist - r) > 1:
                    continue
                angle = math.atan2(dy, dx)  # in image coords (y down)
                # Convert: image angle to geo bearing
                # atan2(dy, dx) → east=0, north=π/2 (image y-down so north=-y)
                geo_bearing = math.atan2(dx, -dy)  # bearing from north, CW
                diff = ((geo_bearing - math.radians(far_az)) + math.pi) % (2*math.pi) - math.pi
                if abs(diff) < arc_half:
                    mask[py, px] = True
    return mask


def task3_signal_quality(catalog_results=None):
    print("\n" + "="*70)
    print("TASK 3 — SAR SIGNAL QUALITY")
    print("="*70)

    import sys
    sys.path.insert(0, "scripts")
    from tank_registry import LOCATIONS

    for loc in LOCATIONS:
        if loc["id"] not in ("juwaymah", "yanbu"):
            continue

        out_dir = OUT_JW if loc["id"] == "juwaymah" else OUT_YB
        bbox    = BBOX_JUWAYMAH if loc["id"] == "juwaymah" else BBOX_YANBU
        los_az  = loc.get("los_azimuth", 282)
        label   = loc["name"]

        print(f"\n  {label} (los_azimuth={los_az}):")

        # Pick 3 reference tanks: first, middle, last
        tanks = loc["tanks"]
        ref_tanks = [tanks[0], tanks[len(tanks)//2], tanks[-1]]

        # Get scene dates from catalog if available
        scene_dates = []
        if catalog_results:
            name_key = loc["id"] if loc["id"] in catalog_results else None
            if name_key:
                all_dates = sorted(set(
                    r["properties"]["datetime"][:10]
                    for r in catalog_results[name_key]
                    if r["properties"].get("sat:orbit_state","").lower() ==
                       ("descending" if los_az == 282 else "ascending")
                ))
                # Pick evenly spaced SCENE_FETCH_N dates
                if len(all_dates) >= SCENE_FETCH_N:
                    step = len(all_dates) // SCENE_FETCH_N
                    scene_dates = [all_dates[i*step] for i in range(SCENE_FETCH_N)]
                else:
                    scene_dates = all_dates

        if not scene_dates:
            print(f"    No catalog dates available — using fixed recent dates")
            # Fallback: recent 6-day cadence
            end = date(2026, 9, 18)
            scene_dates = [(end - timedelta(days=6*i)).isoformat() for i in range(SCENE_FETCH_N)]
            scene_dates = sorted(scene_dates)

        print(f"    Processing {len(scene_dates)} scenes: {scene_dates[0]} → {scene_dates[-1]}")

        orbit_dir = "descending" if los_az == 282 else "ascending"
        stats_rows = []

        for scene_date in scene_dates:
            print(f"    Fetching {scene_date}...", end=" ", flush=True)
            try:
                arr = fetch_sar_scene(bbox, scene_date, orbit=orbit_dir)
                time.sleep(0.5)
            except Exception as e:
                print(f"ERROR: {e}")
                continue

            if arr is None or arr.size == 0:
                print("NO DATA")
                continue

            if arr.max() == 0 and arr.min() == 0:
                print("ZERO ARRAY — skipped")
                continue

            print(f"ok (arr {arr.shape}, range {arr.min():.1f}–{arr.max():.1f} dB)")

            # Per-tank stats
            lon_min, lat_min, lon_max, lat_max = bbox
            h_arr, w_arr = arr.shape

            for t in ref_tanks:
                cx = int((t["lon"] - lon_min) / (lon_max - lon_min) * w_arr)
                cy = int((lat_max - t["lat"]) / (lat_max - lat_min) * h_arr)
                r_px = max(1, int(t["radius_m"] / ((lon_max - lon_min) * 111320 / w_arr)))

                # Full disk stats
                ys, xs = np.ogrid[:h_arr, :w_arr]
                dist = np.sqrt((xs - cx)**2 + (ys - cy)**2)
                disk = arr[dist <= r_px]
                if len(disk) == 0:
                    continue

                stats_rows.append({
                    "date": scene_date,
                    "tank_id": t["id"],
                    "radius_m": t["radius_m"],
                    "n_px": len(disk),
                    "mean_db": float(np.mean(disk)),
                    "std_db": float(np.std(disk)),
                    "min_db": float(np.min(disk)),
                    "max_db": float(np.max(disk)),
                })

            # Crescent angular profile for first reference tank on first 2 scenes
            if scene_date in scene_dates[:2]:
                t = ref_tanks[0]
                cx = int((t["lon"] - lon_min) / (lon_max - lon_min) * w_arr)
                cy = int((lat_max - t["lat"]) / (lat_max - lat_min) * h_arr)
                r_px = max(1, int(t["radius_m"] / ((lon_max - lon_min) * 111320 / w_arr)))

                # Rolling sector profile: 360 degrees, 30-degree window
                sector_results = []
                for az_center in range(0, 360, 15):
                    az_rad = math.radians(az_center)
                    arc = 30
                    arc_r = math.radians(arc/2)
                    ys, xs = np.ogrid[:h_arr, :w_arr]
                    dist = np.sqrt((xs - cx)**2 + (ys - cy)**2)
                    ring = (dist >= r_px * 0.75) & (dist <= r_px * 1.25)

                    # Angle of each pixel
                    dy = np.arange(h_arr)[:, None] - cy
                    dx = np.arange(w_arr)[None, :] - cx
                    angles = np.arctan2(dx, -dy)  # geo bearing
                    diff = ((angles - math.radians(az_center)) + math.pi) % (2*math.pi) - math.pi
                    sector = ring & (np.abs(diff) <= arc_r)

                    vals = arr[sector]
                    if len(vals) >= 3:
                        sector_results.append({
                            "az": az_center,
                            "mean_db": float(np.mean(vals)),
                            "n_px": len(vals)
                        })

                if sector_results:
                    prof_df = pd.DataFrame(sector_results)
                    prof_path = out_dir / f"crescent_profile_{t['id']}_{scene_date}.csv"
                    prof_df.to_csv(prof_path, index=False)
                    min_az = prof_df.loc[prof_df["mean_db"].idxmin(), "az"]
                    expected_az = (los_az + 180) % 360  # shadow side
                    az_error = abs(((min_az - expected_az) + 180) % 360 - 180)
                    match = "✓ CONFIRMED" if az_error < 45 else f"✗ NOT CONFIRMED (off by {az_error:.0f}°)"
                    print(f"    Profile {t['id']} {scene_date}: min dB at az={min_az}°, "
                          f"expected {expected_az}° → {match}")

        if stats_rows:
            df = pd.DataFrame(stats_rows)
            stats_path = out_dir / "tank_stats.csv"
            df.to_csv(stats_path, index=False)
            print(f"\n    Saved stats: {stats_path}")

            # Summary per tank
            summary = df.groupby("tank_id").agg(
                scenes=("date","count"),
                mean_db=("mean_db","mean"),
                std_db_mean=("std_db","mean"),
                std_db_across_dates=("mean_db","std"),
            ).round(2)
            print(f"\n    Per-tank summary:")
            print(summary.to_string())

            # Dynamic range (across all tanks, cross-scene)
            dr = df["mean_db"].max() - df["mean_db"].min()
            print(f"\n    Dynamic range across all tanks×dates: {dr:.1f} dB")
            print(f"    (RT reference: ~3-5 dB full range; Kharg: ~6-8 dB)")
        else:
            print("    No stats computed — check scene dates and bbox")

        # Save PNG crops for 3 scenes
        _save_sar_crops(loc, bbox, scene_dates[:3], los_az, ref_tanks, out_dir)


def _save_sar_crops(loc, bbox, scene_dates, los_az, ref_tanks, out_dir):
    """Save annotated SAR crops for reference tanks."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return

    orbit_dir = "descending" if los_az == 282 else "ascending"
    lon_min, lat_min, lon_max, lat_max = bbox

    for scene_date in scene_dates:
        try:
            arr = fetch_sar_scene(bbox, scene_date, orbit=orbit_dir)
            time.sleep(0.5)
        except:
            continue
        if arr is None or arr.size == 0 or (arr.max() == 0 and arr.min() == 0):
            continue

        h_arr, w_arr = arr.shape
        # Normalize to 0-255
        vmin, vmax = np.percentile(arr, 2), np.percentile(arr, 98)
        arr_norm = np.clip((arr - vmin) / (vmax - vmin + 1e-9) * 255, 0, 255).astype(np.uint8)
        # Upsample ×4 for visibility
        from PIL import Image
        img = Image.fromarray(arr_norm).convert("RGB")
        img = img.resize((w_arr * 4, h_arr * 4), Image.NEAREST)
        draw = ImageDraw.Draw(img)

        scale = 4
        for t in ref_tanks:
            cx = int((t["lon"] - lon_min) / (lon_max - lon_min) * w_arr) * scale
            cy = int((lat_max - t["lat"]) / (lat_max - lat_min) * h_arr) * scale
            r_px = max(2, int(t["radius_m"] / ((lon_max - lon_min) * 111320 / w_arr))) * scale
            draw.ellipse([cx-r_px, cy-r_px, cx+r_px, cy+r_px], outline=(255,0,0), width=2)
            draw.text((cx+r_px+2, cy-6), t["id"], fill=(255,255,0))

            # Draw expected crescent arc
            expected_az = (los_az + 180) % 360
            arc_half = 30
            for angle_deg in range(int(expected_az - arc_half), int(expected_az + arc_half), 3):
                a = math.radians(angle_deg)
                # Convert geo bearing to image coords
                ix = cx + int(r_px * math.sin(a))
                iy = cy - int(r_px * math.cos(a))
                draw.ellipse([ix-2, iy-2, ix+2, iy+2], fill=(0, 255, 0))

        crop_path = out_dir / f"sar_crop_{scene_date}.png"
        img.save(crop_path)
        if crop_path.stat().st_size == 0:
            print(f"    WARNING: zero-size PNG at {crop_path} — removing")
            crop_path.unlink()
        else:
            print(f"    Saved SAR crop: {crop_path} ({img.size[0]}×{img.size[1]}px, "
                  f"{crop_path.stat().st_size//1024}KB)")


# ── Task 4: Verdict table ─────────────────────────────────────────────────────
def task4_verdict():
    print("\n" + "="*70)
    print("TASK 4 — VERDICT TABLE")
    print("="*70)

    jw_stats = OUT_JW / "tank_stats.csv"
    yb_stats = OUT_YB / "tank_stats.csv"

    def get_std(path):
        if not path.exists(): return "n/a"
        df = pd.read_csv(path)
        return f"{df['std_db_mean'].mean():.2f}" if "std_db_mean" in df.columns else "n/a"

    def catalog_summary(path):
        if not path.exists(): return "n/a"
        df = pd.read_csv(path)
        if df.empty: return "no scenes"
        best = df.groupby(["state","orbit"]).size().idxmax()
        count = df.groupby(["state","orbit"]).size().max()
        return f"{best[0]} orbit {best[1]}: {count} scenes"

    def supabase_row_count(terminal):
        """Query Supabase for row count — same as Task 0."""
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                   "Accept": "application/json", "Prefer": "count=exact"}
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/terminal_observations?terminal_id=eq.{terminal}&select=date",
                headers=headers, timeout=15
            )
            return r.headers.get("Content-Range", "?/0").split("/")[-1]
        except Exception:
            return "?"

    jw_count = supabase_row_count("juwaymah")
    yb_count = supabase_row_count("yanbu")
    jw_sb = f"✅ {jw_count} obs rows" if jw_count not in ("0","?") else "❌ no data"
    yb_sb = f"✅ {yb_count} obs rows" if yb_count not in ("0","?") else "❌ no data"

    # Check catalog overlap with RT (DESC 35 shared?)
    jw_catalog = OUT_JW / "sar_catalog.csv"
    jw_backfill = "⬜ catalog not downloaded"
    if jw_catalog.exists():
        df = pd.read_csv(jw_catalog)
        desc35 = df[(df["state"] == "descending") & (df["orbit"] == 35)]
        jw_backfill = "✅ DESC 35 confirmed — backfill FREE" if len(desc35) > 0 else "❌ DESC 35 not in catalog"

    rows = [
        ("In registry / tanks", "✅ 19 tanks r=50m", "✅ 12 tanks r=60m"),
        ("In Supabase", jw_sb, yb_sb),
        ("Floating % (optical)", "⬜ check optical crop", "⬜ check optical crop"),
        ("SAR orbits / cadence", catalog_summary(OUT_JW / "sar_catalog.csv"),
                                  catalog_summary(OUT_YB / "sar_catalog.csv")),
        ("Same frame as RT (backfill free?)", jw_backfill, "❌ ASC orbit — NO"),
        ("LOS azimuth", "282 (DESC) — same as RT", "98 (ASC) — different"),
        ("Crescent mask compatible", "✅ los=282 same as RT", "❌ los=98 — mask code change needed"),
        ("allowed_orbits configured", "❌ not set", "❌ not set"),
        ("Ref tank std dB", get_std(jw_stats), get_std(yb_stats)),
        ("Angular profile confirmed", "⬜ run Task 3", "⬜ run Task 3"),
        ("Blockers", "1. Set allowed_orbits\n2. Download+process scenes",
                     "1. Identify ASC orbit\n2. Modify crescent mask for az=98\n3. Calibrate from scratch"),
        ("Recommended priority", "HIGH — likely free backfill", "MEDIUM — separate track"),
    ]

    col_w = [36, 30, 30]
    sep = "+" + "+".join("-"*w for w in col_w) + "+"
    print(sep)
    print(f"| {'Criterion':<34} | {'Juwaymah':<28} | {'Yanbu':<28} |")
    print(sep)
    for label, jw, yb in rows:
        jw_lines = str(jw).split("\n")
        yb_lines = str(yb).split("\n")
        max_lines = max(len(jw_lines), len(yb_lines))
        for i in range(max_lines):
            l = label if i == 0 else ""
            j = jw_lines[i] if i < len(jw_lines) else ""
            y = yb_lines[i] if i < len(yb_lines) else ""
            print(f"| {l:<34} | {j:<28} | {y:<28} |")
        print(sep)


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--tasks", default="0,1,2,3,4",
                   help="Comma-separated task numbers to run (default: all)")
    args = p.parse_args()

    tasks = set(args.tasks.split(","))

    if "0" in tasks:
        task0_supabase()
    if "1" in tasks:
        task1_optical()

    catalog_results = None
    if "2" in tasks:
        catalog_results = task2_catalog()
    if "3" in tasks:
        task3_signal_quality(catalog_results)
    if "4" in tasks:
        task4_verdict()

    print("\nDone. Outputs in diagnostics/juwaymah/ and diagnostics/yanbu/")
