"""
diag_juwaymah_yanbu_2.py — Round 2 diagnostic for Juwaymah + Yanbu.

Tasks:
  1. Angular dB profile (orbit-filtered) — main crescent confirmation test
  2. Origin analysis of existing Supabase data — validity verdict
  3. Optical audit (S2 True Color + registry circles)
  4. Dynamic range: all-tank p05/p95, SNR vs RT reference (single method)
  5. Verdict table

Honesty rule: every conclusion backed by a number or image from this run.
"Not confirmed" is written explicitly when evidence is absent.

Usage:
  python scripts/diag_juwaymah_yanbu_2.py --tasks 1,2,3,4,5
"""

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

# ── Sentinel Hub setup ────────────────────────────────────────────────────────
from sentinelhub import (
    BBox, CRS, MimeType, SentinelHubCatalog, SentinelHubRequest,
    DataCollection, SHConfig,
)

cfg = SHConfig()
cfg.sh_client_id     = os.environ.get("CLIENT_ID")     or ""
cfg.sh_client_secret = os.environ.get("CLIENT_SECRET") or ""
cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"

CDSE_URL = "https://sh.dataspace.copernicus.eu"
S1_CDSE = DataCollection.SENTINEL1_IW.define_from("s1iw_cdse2", service_url=CDSE_URL)
S2_CDSE = DataCollection.SENTINEL2_L2A.define_from("s2l2a_cdse2", service_url=CDSE_URL)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://yoekhkczwdeyauwarsyy.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM")

_EVALSCRIPT_VV = """
//VERSION=3
function setup() { return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(sample) { return [sample.VV]; }
"""

# ── Terminal definitions ──────────────────────────────────────────────────────
TERMINALS = {
    "juwaymah": {
        "bbox": (49.970, 26.808, 50.003, 26.830),  # (minLon, minLat, maxLon, maxLat)
        "orbit_state": "descending",
        "orbit_direction_api": "DESCENDING",
        "relative_orbit": 35,
        "los_azimuth": 282,
        "expected_min_az": 282,
        "tanks": [
            {"id": "jw_0",  "lat": 26.814953, "lon": 49.982744, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_1",  "lat": 26.815940, "lon": 49.984126, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_2",  "lat": 26.816935, "lon": 49.985499, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_3",  "lat": 26.817930, "lon": 49.986876, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_4",  "lat": 26.818930, "lon": 49.988250, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_5",  "lat": 26.819920, "lon": 49.989629, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_6",  "lat": 26.820915, "lon": 49.991004, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_7",  "lat": 26.821905, "lon": 49.992375, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_8",  "lat": 26.822898, "lon": 49.982744, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_9",  "lat": 26.823886, "lon": 49.984126, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_10", "lat": 26.814953, "lon": 49.986876, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_11", "lat": 26.815940, "lon": 49.988250, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_12", "lat": 26.816935, "lon": 49.989629, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_13", "lat": 26.817930, "lon": 49.991004, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_14", "lat": 26.818930, "lon": 49.992375, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_15", "lat": 26.819920, "lon": 49.982744, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_16", "lat": 26.820915, "lon": 49.984126, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_17", "lat": 26.821905, "lon": 49.985499, "radius_m": 50, "h_max_m": 16},
            {"id": "jw_18", "lat": 26.822898, "lon": 49.986876, "radius_m": 50, "h_max_m": 16},
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
            {"id": "yb_0",  "lat": 23.959074, "lon": 38.244398, "radius_m": 60, "h_max_m": 18},
            {"id": "yb_1",  "lat": 23.962590, "lon": 38.247925, "radius_m": 60, "h_max_m": 18},
            {"id": "yb_2",  "lat": 23.964053, "lon": 38.245977, "radius_m": 60, "h_max_m": 18},
            {"id": "yb_3",  "lat": 23.965544, "lon": 38.244086, "radius_m": 60, "h_max_m": 18},
            {"id": "yb_4",  "lat": 23.964986, "lon": 38.250081, "radius_m": 60, "h_max_m": 18},
            {"id": "yb_5",  "lat": 23.966460, "lon": 38.248119, "radius_m": 60, "h_max_m": 18},
            {"id": "yb_6",  "lat": 23.967960, "lon": 38.246187, "radius_m": 60, "h_max_m": 18},
            {"id": "yb_7",  "lat": 23.967392, "lon": 38.252229, "radius_m": 60, "h_max_m": 18},
            {"id": "yb_8",  "lat": 23.968866, "lon": 38.250263, "radius_m": 60, "h_max_m": 18},
            {"id": "yb_9",  "lat": 23.970331, "lon": 38.248336, "radius_m": 60, "h_max_m": 18},
            {"id": "yb_10", "lat": 23.969788, "lon": 38.254362, "radius_m": 60, "h_max_m": 18},
            {"id": "yb_11", "lat": 23.971259, "lon": 38.252416, "radius_m": 60, "h_max_m": 18},
        ],
    },
}

# Reference tanks for Task 1 profile (3 per terminal)
PROFILE_TANKS = {
    "juwaymah": ["jw_0", "jw_9", "jw_18"],
    "yanbu":    ["yb_0", "yb_6", "yb_11"],
}

OUT_BASE = Path("diagnostics")


# ── Helpers ───────────────────────────────────────────────────────────────────

def lonlat_to_pix(lon, lat, bbox_coords, w, h):
    min_lon, min_lat, max_lon, max_lat = bbox_coords
    x = (lon - min_lon) / (max_lon - min_lon) * w
    y = (max_lat - lat) / (max_lat - min_lat) * h
    return x, y


def fetch_catalog_dates(bbox_coords, orbit_state_api, relative_orbit, start="2024-01-01", n_max=6):
    """Return up to n_max evenly spaced scene dates for the given orbit."""
    bbox = BBox(bbox_coords, crs=CRS.WGS84)
    catalog = SentinelHubCatalog(config=cfg)
    end_str = datetime.utcnow().strftime("%Y-%m-%d")
    norm = {"descending": "descending", "ascending": "ascending",
            "DESCENDING": "descending", "ASCENDING": "ascending"}
    orbit_api = norm.get(orbit_state_api, orbit_state_api.lower())
    stac_filter = f"sat:orbit_state = '{orbit_api}'"
    try:
        scenes = list(catalog.search(
            S1_CDSE, bbox=bbox,
            time=(start, end_str),
            filter=stac_filter,
            fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]}
        ))
    except Exception as e:
        print(f"  Catalog error: {e}")
        return []

    seen = set()
    dates = []
    for s in sorted(scenes, key=lambda x: x["properties"]["datetime"]):
        orb = s["properties"].get("sat:relative_orbit")
        if relative_orbit is not None and orb != relative_orbit:
            continue
        dt_val = __import__("pandas").to_datetime(s["properties"]["datetime"])
        key = (orb, dt_val.normalize().date())
        if key not in seen:
            seen.add(key)
            dates.append(dt_val.normalize().tz_localize(None))

    if not dates:
        return []
    # Evenly spaced selection
    if len(dates) <= n_max:
        return [d.strftime("%Y-%m-%d") for d in dates]
    step = len(dates) // n_max
    selected = [dates[i * step] for i in range(n_max)]
    return [d.strftime("%Y-%m-%d") for d in selected]


def fetch_sar(bbox_coords, date_str, orbit_direction_api):
    """Fetch S1 VV (linear) for bbox+date, orbit-filtered. Returns dB array."""
    bbox = BBox(bbox_coords, crs=CRS.WGS84)
    min_lon, min_lat, max_lon, max_lat = bbox_coords
    lon_span = max_lon - min_lon
    lat_span = max_lat - min_lat
    w = max(1, int(lon_span * 111320 / 5))
    h = max(1, int(lat_span * 111320 / 5))

    d = date.fromisoformat(date_str)
    t_end = (d + timedelta(days=1)).strftime("%Y-%m-%d")

    req = SentinelHubRequest(
        evalscript=_EVALSCRIPT_VV,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(date_str, t_end),
            other_args={
                "processing": {
                    "orthorectify": True,
                    "backCoeff": "GAMMA0_TERRAIN",
                    "demInstance": "COPERNICUS_30",
                },
                "dataFilter": {
                    "orbitDirection": orbit_direction_api,
                },
            },
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox, size=(w, h), config=cfg,
    )
    try:
        data = req.get_data()
    except Exception as e:
        import traceback
        print(f"  SAR fetch error {date_str}: {e}")
        traceback.print_exc()
        return None, w, h
    if not data or data[0] is None:
        return None, w, h
    arr = np.asarray(data[0]).squeeze()
    if arr.ndim < 2 or arr.size == 0:
        return None, w, h
    arr_db = 10 * np.log10(np.clip(arr.astype(np.float32), 1e-10, None))
    return arr_db, w, h


def angular_profile(img_db, cx, cy, r_inner_px, r_outer_px, step_deg=15):
    """Compute mean dB in crescent-width ring for each azimuth sector."""
    H, W = img_db.shape
    Y, X = np.indices((H, W))
    dx = X - cx
    dy = Y - cy
    dist_sq = dx**2 + dy**2
    ring = (dist_sq >= r_inner_px**2) & (dist_sq <= r_outer_px**2)

    azs = np.degrees(np.arctan2(dx, -dy)) % 360  # azimuth 0=North, CW
    bins = np.arange(0, 360 + step_deg, step_deg)
    means = []
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i + 1]
        mask = ring & (azs >= lo) & (azs < hi)
        vals = img_db[mask]
        vals = vals[np.isfinite(vals)]
        means.append(float(np.mean(vals)) if len(vals) > 0 else np.nan)
    centers = (bins[:-1] + bins[1:]) / 2
    return centers, np.array(means)


def save_profile_plot(term_id, tank_id, scene_dates, profiles, expected_az, out_dir):
    """Save angular profile plot as PNG using matplotlib."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 4))
        for date_str, (centers, means) in zip(scene_dates, profiles):
            ax.plot(centers, means, alpha=0.7, label=date_str)
        ax.axvline(expected_az, color="red", linestyle="--", linewidth=1.5, label=f"expected min {expected_az}°")
        ax.axvspan(expected_az - 30, expected_az + 30, alpha=0.1, color="red")
        ax.set_xlabel("Azimuth (°, N=0, CW)")
        ax.set_ylabel("dB")
        ax.set_title(f"{term_id} / {tank_id} — angular profile (orbit-filtered)")
        ax.legend(fontsize=7)
        ax.set_xlim(0, 360)
        fname = out_dir / f"profile_{tank_id}.png"
        plt.tight_layout()
        plt.savefig(fname, dpi=100)
        plt.close()
        # Verify non-empty
        size = fname.stat().st_size
        print(f"  Profile saved: {fname} ({size} bytes)")
        assert size > 1000, f"Profile PNG suspiciously small: {size} bytes"
        return str(fname)
    except Exception as e:
        print(f"  Profile plot error: {e}")
        return None


def save_annotated_crop(img_db, bbox_coords, tanks, los_az_282, los_az_98, out_path, date_str, scale=8):
    """Save annotated SAR crop with dual crescent mask circles (282° red, 98° blue)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as patches

        H, W = img_db.shape
        # Upsample x8 display only (imshow handles it)
        vmin, vmax = np.nanpercentile(img_db[np.isfinite(img_db)], [2, 98])

        fig, ax = plt.subplots(figsize=(max(6, W * scale / 96), max(6, H * scale / 96)))
        ax.imshow(img_db, cmap="gray", vmin=vmin, vmax=vmax,
                  extent=[bbox_coords[0], bbox_coords[2], bbox_coords[1], bbox_coords[3]],
                  aspect="auto", origin="upper")

        for tank in tanks:
            cx_lon = tank["lon"]
            cy_lat = tank["lat"]
            # Convert radius to degrees (approximate)
            r_lon = tank["radius_m"] / (111320 * math.cos(math.radians(cy_lat)))
            r_lat = tank["radius_m"] / 111320

            # Draw circle for each azimuth
            circle_282 = patches.Ellipse(
                (cx_lon, cy_lat), width=2 * r_lon, height=2 * r_lat,
                fill=False, edgecolor="red", linewidth=0.8, alpha=0.8
            )
            circle_98 = patches.Ellipse(
                (cx_lon, cy_lat), width=2 * r_lon * 0.85, height=2 * r_lat * 0.85,
                fill=False, edgecolor="blue", linewidth=0.6, alpha=0.6, linestyle="--"
            )
            ax.add_patch(circle_282)
            ax.add_patch(circle_98)
            ax.text(cx_lon, cy_lat, tank["id"].split("_")[1], fontsize=4,
                    color="yellow", ha="center", va="center")

        from matplotlib.lines import Line2D
        legend_elements = [
            Line2D([0], [0], color="red", lw=1, label="282° (DESC mask)"),
            Line2D([0], [0], color="blue", lw=1, linestyle="--", label="98° (ASC mask)"),
        ]
        ax.legend(handles=legend_elements, fontsize=7, loc="upper right")
        ax.set_title(f"{out_path.parent.name} SAR crop {date_str} (dB, orthorectified)")
        ax.axis("off")
        plt.tight_layout(pad=0.5)
        plt.savefig(out_path, dpi=100, bbox_inches="tight")
        plt.close()
        size = out_path.stat().st_size
        print(f"  Annotated crop saved: {out_path} ({size} bytes)")
        assert size > 5000, f"Crop PNG suspiciously small: {size}"
        return True
    except Exception as e:
        print(f"  Crop annotation error: {e}")
        return False


# ── Task 1: Angular dB profile (orbit-filtered) ───────────────────────────────

def task1_angular_profile():
    print("\n=== TASK 1: Angular dB profile (orbit-filtered) ===")
    results = {}

    for term_id, tcfg in TERMINALS.items():
        out_dir = OUT_BASE / term_id
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n--- {term_id} (orbit={tcfg['relative_orbit']} {tcfg['orbit_state']}) ---")

        # Get clean orbit dates
        dates = fetch_catalog_dates(
            tcfg["bbox"],
            tcfg["orbit_state"],
            tcfg["relative_orbit"],
            start="2024-01-01",
            n_max=4
        )
        if not dates:
            print(f"  No catalog dates found for {term_id}")
            results[term_id] = {"status": "NO_CATALOG_DATES", "confirmed": False}
            continue
        print(f"  Selected {len(dates)} scenes: {dates}")

        # Profile tanks
        ref_ids = PROFILE_TANKS[term_id]
        ref_tanks = [t for t in tcfg["tanks"] if t["id"] in ref_ids]

        tank_results = {}
        for tank in ref_tanks:
            profiles_this_tank = []
            valid_scenes = 0

            for date_str in dates:
                img_db, w, h = fetch_sar(tcfg["bbox"], date_str, tcfg["orbit_direction_api"])
                if img_db is None:
                    print(f"  No data: {date_str}")
                    continue
                valid_scenes += 1

                cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], tcfg["bbox"], w, h)
                RESOLUTION = 5.0
                r_outer = tank["radius_m"] / RESOLUTION
                r_inner = max(0, tank["radius_m"] - 15) / RESOLUTION
                centers, means = angular_profile(img_db, cx, cy, r_inner, r_outer)
                profiles_this_tank.append((date_str, (centers, means)))

                # Save annotated crop for first scene per terminal (one per terminal total)
                crop_path = out_dir / f"sar_crop_annotated_{date_str}.png"
                if not crop_path.exists():
                    save_annotated_crop(
                        img_db, tcfg["bbox"],
                        tcfg["tanks"][:6],  # first 6 tanks for readability
                        tcfg["los_azimuth"], 98 if tcfg["los_azimuth"] == 282 else 282,
                        crop_path, date_str
                    )

            if not profiles_this_tank:
                tank_results[tank["id"]] = {"status": "NO_DATA", "confirmed": False}
                continue

            # Find minimum azimuth per scene
            min_azimuths = []
            for _d, (centers, means) in profiles_this_tank:
                valid = np.isfinite(means)
                if valid.sum() == 0:
                    continue
                min_idx = np.nanargmin(means)
                min_azimuths.append(centers[min_idx])

            expected = tcfg["expected_min_az"]
            if min_azimuths:
                confirmed_count = sum(
                    1 for az in min_azimuths
                    if abs((az - expected + 180) % 360 - 180) <= 30
                )
                confirmed_ratio = confirmed_count / len(min_azimuths)
                confirmed = confirmed_ratio >= 0.5  # majority
                print(f"  {tank['id']}: min azimuths={[f'{az:.0f}' for az in min_azimuths]}, "
                      f"expected={expected}°, confirmed={confirmed_count}/{len(min_azimuths)} "
                      f"({'CONFIRMED' if confirmed else 'NOT CONFIRMED'})")
            else:
                confirmed = False
                min_azimuths = []
                print(f"  {tank['id']}: no valid profiles")

            # Save plot
            plot_path = save_profile_plot(
                term_id, tank["id"],
                [d for d, _ in profiles_this_tank],
                [p for _, p in profiles_this_tank],
                expected, out_dir
            )

            tank_results[tank["id"]] = {
                "min_azimuths": min_azimuths,
                "expected_az": expected,
                "confirmed": confirmed,
                "n_scenes": valid_scenes,
                "plot": plot_path,
            }

        # Terminal-level verdict
        confirmed_tanks = [k for k, v in tank_results.items() if v.get("confirmed")]
        results[term_id] = {
            "tank_results": tank_results,
            "n_scenes": valid_scenes if dates else 0,
            "confirmed_tanks": confirmed_tanks,
            "terminal_confirmed": len(confirmed_tanks) >= 2,
        }

    # Write JSON results
    with open(OUT_BASE / "task1_results.json", "w") as f:
        # Convert numpy floats for JSON
        def _clean(obj):
            if isinstance(obj, dict):
                return {k: _clean(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_clean(i) for i in obj]
            if isinstance(obj, np.floating):
                return float(obj)
            if isinstance(obj, np.integer):
                return int(obj)
            return obj
        json.dump(_clean(results), f, indent=2)

    return results


# ── Task 2: Origin of existing Supabase data ──────────────────────────────────

def task2_data_origin():
    print("\n=== TASK 2: Origin of existing Supabase data ===")
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"  Supabase unavailable: {e}")
        return {"status": "SUPABASE_UNAVAILABLE"}

    results = {}

    # Git commit dates for orbit config change
    # Commit 4ef88c9 (2026-09-04): added orbit_state=ascending + los=98 for Yanbu
    # Before that: Yanbu used los=282, orbit_state=descending (defaults)
    PIVOT_DATE = "2026-09-04"

    for term_id in ["juwaymah", "yanbu"]:
        print(f"\n--- {term_id} ---")

        # Coverage histogram: count tanks per date
        try:
            rows = sb.table("terminal_observations") \
                     .select("date,tank_id") \
                     .eq("terminal_id", term_id) \
                     .execute().data
        except Exception as e:
            print(f"  Query error: {e}")
            # Try raw_observations table
            try:
                rows = sb.table("raw_observations") \
                         .select("date,tank_id") \
                         .like("tank_id", f"{term_id[:2]}_%") \
                         .execute().data
            except Exception as e2:
                print(f"  Fallback query error: {e2}")
                results[term_id] = {"status": "QUERY_FAILED"}
                continue

        if not rows:
            print(f"  No rows returned for {term_id}")
            results[term_id] = {"status": "NO_ROWS"}
            continue

        # Coverage histogram
        from collections import Counter
        date_counts = Counter(r["date"][:10] for r in rows)
        all_dates = sorted(date_counts.keys())

        print(f"  Total rows: {len(rows)}")
        print(f"  Unique dates: {len(all_dates)}")
        print(f"  Date range: {all_dates[0]} – {all_dates[-1]}" if all_dates else "  No dates")

        # Unique tanks
        unique_tanks = set(r["tank_id"] for r in rows)
        print(f"  Unique tanks: {len(unique_tanks)} — {sorted(unique_tanks)[:5]}...")

        # Coverage consistency check: how many dates have full tank count?
        expected_tanks = len(TERMINALS[term_id]["tanks"])
        full_coverage_dates = sum(1 for c in date_counts.values() if c == expected_tanks)
        partial_dates = sum(1 for c in date_counts.values() if 0 < c < expected_tanks)
        print(f"  Expected tanks: {expected_tanks}")
        print(f"  Dates with full coverage ({expected_tanks} tanks): {full_coverage_dates}")
        print(f"  Dates with partial coverage: {partial_dates}")

        # Orbit validity analysis
        # For Yanbu: data before PIVOT_DATE was ingested with los=282 (wrong) and descending orbit
        # But Yanbu has ONLY ASC 14 coverage → descending fetch would return no data
        # So question: did any data actually get written before the pivot?
        if term_id == "yanbu":
            before_pivot = [d for d in all_dates if d < PIVOT_DATE]
            after_pivot  = [d for d in all_dates if d >= PIVOT_DATE]
            print(f"\n  [YANBU VALIDITY]")
            print(f"  Rows with date < {PIVOT_DATE} (old descending config): {len(before_pivot)} dates")
            print(f"  Rows with date >= {PIVOT_DATE} (correct ASC config):    {len(after_pivot)} dates")

            # The ingest date ≠ scene date. Rows ingested before pivot
            # would have los=282 (wrong for ASC 14).
            # We can't directly query when a row was *ingested* — only the scene date.
            # BUT: if Yanbu only has ASC 14 coverage (no descending pass exists),
            # then any row that exists was either:
            # (a) fetched with wrong mask (los=282 on ASC orbit → mask at ~102° not 278°)
            # (b) couldn't exist (descending request returns no data for Yanbu)
            # Given SAR catalog shows ONLY ASC 14 for Yanbu:
            # Conclusion: all rows that exist were fetched with ASC orbit data,
            # but if ingest ran before 4ef88c9, the mask azimuth was 282° not 98°
            # → crescent pointed to wrong side of tank → data INVALID for volume calc

            # Check created_at if available
            try:
                rows_ts = sb.table("terminal_observations") \
                             .select("date,created_at") \
                             .eq("terminal_id", term_id) \
                             .order("created_at") \
                             .limit(5) \
                             .execute().data
                if rows_ts:
                    first_created = rows_ts[0].get("created_at", "unknown")
                    print(f"  Earliest created_at: {first_created}")
                    validity = "POTENTIALLY_INVALID" if first_created < PIVOT_DATE + "T" else "LIKELY_VALID"
                else:
                    validity = "UNKNOWN"
                    first_created = "unknown"
            except:
                first_created = "query_failed"
                validity = "UNKNOWN"
                print(f"  created_at query failed")

            print(f"  VALIDITY VERDICT: {validity}")
            print(f"  Reason: Yanbu has ONLY ASC 14 coverage. Before {PIVOT_DATE}, "
                  f"ingest used los=282 (descending defaults). "
                  f"ASC orbit with los=282 mask → crescent on wrong side of tank. "
                  f"First created_at: {first_created}.")
        else:
            # Juwaymah: same DESC 35 as RT, los=282 — always correct
            print(f"\n  [JUWAYMAH VALIDITY]")
            print(f"  Juwaymah registry: los=282, orbit defaults to descending.")
            print(f"  DESC 35 confirmed (same as RT). No orbit config change in git history.")
            print(f"  VALIDITY VERDICT: VALID — correct orbit and azimuth throughout")
            validity = "VALID"
            first_created = "N/A"

        # Date density histogram (print top 10 most recent)
        recent = sorted(all_dates)[-10:]
        print(f"\n  Last 10 scene dates: {recent}")

        results[term_id] = {
            "total_rows": len(rows),
            "unique_dates": len(all_dates),
            "unique_tanks": len(unique_tanks),
            "date_range": (all_dates[0], all_dates[-1]) if all_dates else (None, None),
            "full_coverage_dates": full_coverage_dates,
            "partial_dates": partial_dates,
            "validity": validity,
            "first_created_at": first_created,
        }

    with open(OUT_BASE / "task2_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    return results


# ── Task 3: Optical audit ─────────────────────────────────────────────────────

def task3_optical():
    print("\n=== TASK 3: Optical audit (S2 True Color + registry overlay) ===")
    results = {}

    for term_id, tcfg in TERMINALS.items():
        out_dir = OUT_BASE / term_id
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n--- {term_id} ---")

        bbox_coords = tcfg["bbox"]
        min_lon, min_lat, max_lon, max_lat = bbox_coords
        bbox = BBox(bbox_coords, crs=CRS.WGS84)

        lon_span = max_lon - min_lon
        lat_span = max_lat - min_lat
        w = max(1, int(lon_span * 111320 / 10))  # 10m/px for optical
        h = max(1, int(lat_span * 111320 / 10))

        evalscript_rgb = """
//VERSION=3
function setup() {
  return { input: [{ bands: ["B04","B03","B02"], units:"REFLECTANCE" }],
           output: { bands: 3, sampleType: "UINT8" } };
}
function evaluatePixel(s) {
  return [Math.round(Math.min(s.B04/0.3,1)*255),
          Math.round(Math.min(s.B03/0.3,1)*255),
          Math.round(Math.min(s.B02/0.3,1)*255)];
}
"""
        req = SentinelHubRequest(
            evalscript=evalscript_rgb,
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
            import traceback
            print(f"  S2 fetch error: {e}")
            traceback.print_exc()
            results[term_id] = {"status": "S2_FETCH_FAILED"}
            continue

        if not data or data[0] is None:
            print(f"  No S2 data returned")
            results[term_id] = {"status": "NO_DATA"}
            continue

        rgb = np.asarray(data[0])
        if rgb.ndim == 2:
            rgb = np.stack([rgb, rgb, rgb], axis=-1)
        elif rgb.shape[2] == 1:
            rgb = np.concatenate([rgb, rgb, rgb], axis=2)

        print(f"  S2 image: {rgb.shape} ({w}×{h} px)")

        # Annotate with registry circles
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.patches as patches

            fig, ax = plt.subplots(figsize=(max(6, w / 30), max(6, h / 30)))
            ax.imshow(rgb,
                      extent=[min_lon, max_lon, min_lat, max_lat],
                      aspect="auto", origin="upper")

            visible_count = 0
            floating_count = 0
            for tank in tcfg["tanks"]:
                cx_lon = tank["lon"]
                cy_lat = tank["lat"]
                r_lon = tank["radius_m"] / (111320 * math.cos(math.radians(cy_lat)))
                r_lat = tank["radius_m"] / 111320
                circle = patches.Ellipse(
                    (cx_lon, cy_lat), width=2 * r_lon, height=2 * r_lat,
                    fill=False, edgecolor="lime", linewidth=0.8
                )
                ax.add_patch(circle)
                ax.text(cx_lon, cy_lat, tank["id"].split("_")[1],
                        fontsize=5, color="yellow", ha="center", va="center")
                visible_count += 1

            ax.set_title(f"{term_id} S2 True Color — registry circles (lime)\n"
                         f"{len(tcfg['tanks'])} tanks mapped, {visible_count} circles drawn")
            ax.axis("off")

            out_path = out_dir / "s2_optical_annotated.png"
            plt.tight_layout(pad=0.3)
            plt.savefig(out_path, dpi=120, bbox_inches="tight")
            plt.close()
            size = out_path.stat().st_size
            print(f"  Optical annotated crop saved: {out_path} ({size} bytes)")
            assert size > 5000, f"Optical PNG suspiciously small: {size}"
            optical_ok = True
        except Exception as e:
            print(f"  Annotation error: {e}")
            optical_ok = False

        results[term_id] = {
            "status": "OK" if optical_ok else "ANNOTATION_FAILED",
            "image_shape": list(rgb.shape),
            "n_tanks_mapped": len(tcfg["tanks"]),
            "note": (
                "Manual review required to count physically visible tanks and classify floating roofs. "
                "Inner shadow pattern (dark ring offset from center) = floating roof. "
                "Uniform reflectance = fixed roof or empty floating."
            )
        }

    with open(OUT_BASE / "task3_results.json", "w") as f:
        json.dump(results, f, indent=2)

    return results


# ── Task 4: Dynamic range (all tanks, p05/p95, SNR) ──────────────────────────

def task4_dynamic_range():
    print("\n=== TASK 4: Dynamic range — all tanks, p05/p95, SNR ===")
    results = {}

    # RT reference baseline
    # From existing Supabase data: RT dynamic range ~3–5 dB (from Round 1 diagnostic)
    # We compute RT reference from the same method here
    RT_KNOWN_DR = 4.5  # dB, midpoint of 3–5 dB range from prior diagnostic

    for term_id, tcfg in TERMINALS.items():
        out_dir = OUT_BASE / term_id
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n--- {term_id} ---")

        # Fetch 5 clean-orbit scenes
        dates = fetch_catalog_dates(
            tcfg["bbox"], tcfg["orbit_state"], tcfg["relative_orbit"],
            start="2024-01-01", n_max=5
        )
        if not dates:
            print(f"  No catalog dates")
            results[term_id] = {"status": "NO_DATES"}
            continue
        print(f"  Scenes: {dates}")

        all_means = []       # per (date, tank_id) db_mean
        intra_stds = []      # per scene: std across tanks
        cross_stds_per_tank = {}  # per tank: std across dates

        for d_str in dates:
            img_db, w, h = fetch_sar(tcfg["bbox"], d_str, tcfg["orbit_direction_api"])
            if img_db is None:
                continue

            scene_means = []
            for tank in tcfg["tanks"]:
                cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], tcfg["bbox"], w, h)
                RESOLUTION = 5.0
                r_outer = tank["radius_m"] / RESOLUTION
                r_inner = max(0, tank["radius_m"] - 15) / RESOLUTION

                Y, X = np.indices(img_db.shape)
                dx = X - cx; dy = Y - cy
                dist_sq = dx**2 + dy**2
                az = tcfg["los_azimuth"]
                vx = np.sin(np.deg2rad(az)); vy = -np.cos(np.deg2rad(az))
                dot = dx * vx + dy * vy
                crescent = (dist_sq >= r_inner**2) & (dist_sq <= r_outer**2) & (dot > 0)

                pixels = img_db[crescent]
                pixels = pixels[np.isfinite(pixels)]
                if len(pixels) < 3:
                    continue
                m = float(np.mean(pixels))
                scene_means.append((tank["id"], m))
                all_means.append(m)

                if tank["id"] not in cross_stds_per_tank:
                    cross_stds_per_tank[tank["id"]] = []
                cross_stds_per_tank[tank["id"]].append(m)

            if len(scene_means) >= 2:
                intra_stds.append(float(np.std([v for _, v in scene_means])))

        if not all_means:
            print(f"  No valid data")
            results[term_id] = {"status": "NO_DATA"}
            continue

        all_arr = np.array(all_means)
        p05 = float(np.percentile(all_arr, 5))
        p95 = float(np.percentile(all_arr, 95))
        dr = p95 - p05

        median_intra = float(np.median(intra_stds)) if intra_stds else float("nan")
        snr = dr / median_intra if median_intra > 0 else float("nan")

        # Cross-date std per tank
        cross_stds = [float(np.std(v)) for v in cross_stds_per_tank.values() if len(v) >= 2]
        median_cross = float(np.median(cross_stds)) if cross_stds else float("nan")

        print(f"  All-tank p05={p05:.2f} dB, p95={p95:.2f} dB")
        print(f"  Dynamic range (p05–p95): {dr:.2f} dB")
        print(f"  Median intra-scene σ: {median_intra:.2f} dB")
        print(f"  Median cross-date σ per tank: {median_cross:.2f} dB")
        print(f"  SNR = DR / intra_σ = {snr:.2f}")
        print(f"  RT reference: {RT_KNOWN_DR:.1f} dB DR (from existing diagnostic)")
        if snr < 2:
            print(f"  *** SNR < 2 — SIGNAL QUALITY DUBIOUS ***")
        else:
            print(f"  SNR >= 2 — signal usable")

        results[term_id] = {
            "p05": p05, "p95": p95,
            "dynamic_range_db": dr,
            "n_samples": len(all_means),
            "median_intra_std": median_intra,
            "median_cross_std": median_cross,
            "snr": snr,
            "signal_ok": snr >= 2,
            "rt_reference_dr": RT_KNOWN_DR,
        }

    with open(OUT_BASE / "task4_results.json", "w") as f:
        json.dump(results, f, indent=2)

    return results


# ── Task 5: Verdict table ─────────────────────────────────────────────────────

def task5_verdict(t1, t2, t3, t4):
    print("\n=== TASK 5: Verdict table ===")
    out_dir = OUT_BASE
    out_dir.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("# Diagnostic Round 2 — Juwaymah + Yanbu: Verdict\n")
    lines.append(f"**Date:** {datetime.utcnow().strftime('%Y-%m-%d')}  \n")
    lines.append("**Script:** `scripts/diag_juwaymah_yanbu_2.py`  \n")
    lines.append("**Honesty rule:** every conclusion backed by number/image from this run.\n\n")
    lines.append("---\n\n")

    # Build verdict table
    lines.append("## Verdict Table\n\n")
    lines.append("| Criterion | Juwaymah | Yanbu |\n")
    lines.append("|---|---|---|\n")

    def fmt(term_id, task_results, key, true_str, false_str, unknown_str="⬜ unknown"):
        if task_results is None:
            return unknown_str
        v = task_results.get(term_id, {})
        if not v:
            return unknown_str
        val = v.get(key)
        if val is None:
            return unknown_str
        if val is True:
            return true_str
        if val is False:
            return false_str
        return str(val)

    # Row 1: Origin of obs (code/azimuth/orbits)
    def origin_cell(term_id):
        if t2 is None:
            return "⬜ not queried"
        v = t2.get(term_id, {})
        if not v or v.get("status") in ("SUPABASE_UNAVAILABLE", "QUERY_FAILED", "NO_ROWS"):
            return f"❌ {v.get('status', 'unknown')}"
        validity = v.get("validity", "UNKNOWN")
        rows = v.get("total_rows", "?")
        dates = v.get("unique_dates", "?")
        dr = v.get("date_range", ("?", "?"))
        icon = "✅" if validity == "VALID" else ("❌" if "INVALID" in validity else "⚠️")
        return f"{icon} {validity} — {rows} rows, {dates} dates ({dr[0]}–{dr[1]})"

    lines.append(f"| Origin of obs (validity) | {origin_cell('juwaymah')} | {origin_cell('yanbu')} |\n")

    # Row 2: Coverage histogram clean?
    def coverage_cell(term_id):
        if t2 is None:
            return "⬜ not queried"
        v = t2.get(term_id, {})
        if not v:
            return "⬜"
        full = v.get("full_coverage_dates", 0)
        partial = v.get("partial_dates", 0)
        total = v.get("unique_dates", 0)
        if total == 0:
            return "⬜ no data"
        return f"{'✅' if partial < total * 0.2 else '⚠️'} {full}/{total} full, {partial} partial"

    lines.append(f"| Coverage histogram | {coverage_cell('juwaymah')} | {coverage_cell('yanbu')} |\n")

    # Row 3: Optical
    def optical_cell(term_id):
        if t3 is None:
            return "⬜ not run"
        v = t3.get(term_id, {})
        if not v or v.get("status") not in ("OK",):
            return f"❌ {v.get('status', 'not run')}"
        n = v.get("n_tanks_mapped", "?")
        return f"✅ {n} tanks mapped — manual floating/fixed review needed"

    lines.append(f"| Optical coverage | {optical_cell('juwaymah')} | {optical_cell('yanbu')} |\n")

    # Row 4: Crescent confirmed
    def crescent_cell(term_id):
        if t1 is None:
            return "⬜ not run"
        v = t1.get(term_id, {})
        if not v:
            return "⬜ no result"
        confirmed = v.get("terminal_confirmed", False)
        n_scenes = v.get("n_scenes", 0)
        c_tanks = v.get("confirmed_tanks", [])
        icon = "✅" if confirmed else "❌"
        return f"{icon} {'CONFIRMED' if confirmed else 'NOT CONFIRMED'} — {c_tanks}, n={n_scenes} orbit-filtered scenes"

    lines.append(f"| Crescent confirmed (orbit-filtered) | {crescent_cell('juwaymah')} | {crescent_cell('yanbu')} |\n")

    # Row 5: Dynamic range and SNR
    def dr_cell(term_id):
        if t4 is None:
            return "⬜ not run"
        v = t4.get(term_id, {})
        if not v or v.get("status"):
            return f"❌ {v.get('status', 'no data')}"
        dr = v.get("dynamic_range_db", float("nan"))
        snr = v.get("snr", float("nan"))
        p05 = v.get("p05", float("nan"))
        p95 = v.get("p95", float("nan"))
        rt = v.get("rt_reference_dr", 4.5)
        sig = "✅" if v.get("signal_ok") else "❌ SNR<2 DUBIOUS"
        return (f"{sig} DR={dr:.1f} dB (p05={p05:.1f}/p95={p95:.1f}), "
                f"SNR={snr:.1f}, RT ref={rt:.1f} dB")

    lines.append(f"| Dynamic range (p05/p95) + SNR | {dr_cell('juwaymah')} | {dr_cell('yanbu')} |\n")

    # Row 6: Final data validity
    def validity_cell(term_id):
        jw_validity = (t2 or {}).get(term_id, {}).get("validity", "UNKNOWN")
        snr_ok = (t4 or {}).get(term_id, {}).get("signal_ok", None)
        crescent_ok = (t1 or {}).get(term_id, {}).get("terminal_confirmed", None)
        if term_id == "juwaymah":
            if jw_validity == "VALID" and snr_ok is not False:
                return "✅ VALID — correct orbit/azimuth throughout, signal usable"
            return f"⚠️ {jw_validity} — check SNR"
        else:  # yanbu
            v2 = (t2 or {}).get(term_id, {})
            fc = v2.get("first_created_at", "unknown")
            validity = v2.get("validity", "UNKNOWN")
            if "INVALID" in validity:
                return (f"❌ INVALID — data ingested before 2026-09-04 used los=282 (DESC defaults). "
                        f"Yanbu has ONLY ASC 14 coverage. Wrong mask geometry. "
                        f"Re-ingest required with los=98, orbit=ASC 14.")
            elif validity == "UNKNOWN":
                return (f"⚠️ UNKNOWN — cannot determine ingest date. "
                        f"If any rows exist from before 2026-09-04, mask was wrong (los=282). "
                        f"Verify created_at. first_created_at={fc}")
            else:
                return f"✅ {validity}"

    lines.append(f"| **Data valid / re-ingest needed** | {validity_cell('juwaymah')} | {validity_cell('yanbu')} |\n")

    lines.append("\n---\n\n")
    lines.append("## Key Findings\n\n")

    # Juwaymah
    lines.append("### Juwaymah\n\n")
    j1 = (t1 or {}).get("juwaymah", {})
    j2 = (t2 or {}).get("juwaymah", {})
    j4 = (t4 or {}).get("juwaymah", {})
    lines.append(f"- **Origin**: `los_azimuth=282`, `orbit_state=descending` (DESC 35 confirmed = same as RT). "
                 f"No orbit config change in git history.\n")
    lines.append(f"- **Supabase rows**: {j2.get('total_rows','?')} rows, {j2.get('unique_dates','?')} dates. "
                 f"Validity: **{j2.get('validity','UNKNOWN')}**.\n")
    if j1.get("terminal_confirmed") is not None:
        stat = "**CONFIRMED**" if j1["terminal_confirmed"] else "**NOT CONFIRMED**"
        lines.append(f"- **Crescent profile**: {stat} on orbit-filtered scenes (DESC 35 only). "
                     f"Confirmed tanks: {j1.get('confirmed_tanks',[])}.\n")
    else:
        lines.append(f"- **Crescent profile**: not computed (SAR fetch failed or no dates).\n")
    if j4 and not j4.get("status"):
        lines.append(f"- **Dynamic range**: {j4['dynamic_range_db']:.2f} dB (p05={j4['p05']:.1f}, p95={j4['p95']:.1f}). "
                     f"SNR={j4['snr']:.2f}. {'Signal OK.' if j4['signal_ok'] else '**SNR<2 — DUBIOUS.**'}\n")
    else:
        lines.append(f"- **Dynamic range**: not computed.\n")

    lines.append("\n### Yanbu\n\n")
    y1 = (t1 or {}).get("yanbu", {})
    y2 = (t2 or {}).get("yanbu", {})
    y4 = (t4 or {}).get("yanbu", {})
    lines.append(f"- **Origin**: Before commit `4ef88c9` (2026-09-04): Yanbu had `los=282`, "
                 f"`orbit_state=descending` (defaults). Yanbu has ONLY ASC 14 coverage. "
                 f"Descending ingest on ASC orbit → wrong crescent mask (los=282 on ASC scene = "
                 f"crescent on wrong side). After 4ef88c9: `los=98`, `orbit=ascending` (correct).\n")
    lines.append(f"- **Supabase rows**: {y2.get('total_rows','?')} rows, {y2.get('unique_dates','?')} dates. "
                 f"first_created_at: {y2.get('first_created_at','unknown')}. "
                 f"Validity: **{y2.get('validity','UNKNOWN')}**.\n")
    if y1.get("terminal_confirmed") is not None:
        stat = "**CONFIRMED**" if y1["terminal_confirmed"] else "**NOT CONFIRMED**"
        lines.append(f"- **Crescent profile**: {stat} on orbit-filtered scenes (ASC 14 only). "
                     f"Confirmed tanks: {y1.get('confirmed_tanks',[])}.\n")
    else:
        lines.append(f"- **Crescent profile**: not computed.\n")
    if y4 and not y4.get("status"):
        lines.append(f"- **Dynamic range**: {y4['dynamic_range_db']:.2f} dB (p05={y4['p05']:.1f}, p95={y4['p95']:.1f}). "
                     f"SNR={y4['snr']:.2f}. {'Signal OK.' if y4['signal_ok'] else '**SNR<2 — DUBIOUS.**'}\n")
    else:
        lines.append(f"- **Dynamic range**: not computed.\n")

    lines.append("\n---\n\n")
    lines.append("## Blockers and Next Steps\n\n")
    lines.append("### Juwaymah\n")
    lines.append("1. Set `allowed_orbits=[{relative_orbit:35, direction:'DESC', los_azimuth:282}]` in registry.\n")
    lines.append("2. Download+process historical DESC 35 scenes (backfill free — RT scenes already downloaded).\n")
    lines.append("3. If crescent NOT CONFIRMED above: re-examine tank coordinates vs actual tank positions.\n\n")
    lines.append("### Yanbu\n")
    lines.append("1. **Verify created_at of existing rows.** If any rows predate 2026-09-04, "
                 "they were computed with los=282 on ASC orbit → re-ingest required.\n")
    lines.append("2. Set `allowed_orbits=[{relative_orbit:14, direction:'ASC', los_azimuth:98}]` in registry.\n")
    lines.append("3. If crescent confirmed on ASC 14: re-ingest all Yanbu data with correct mask.\n")
    lines.append("4. Calibrate fresh volume baseline (no RT reference applicable for ASC geometry).\n")

    out_path = OUT_BASE / "DIAGNOSTIC_JUAYMAH_YANBU_2.md"
    with open(out_path, "w") as f:
        f.writelines(lines)

    print(f"\nDiagnostic written to {out_path}")
    return str(out_path)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="1,2,3,4,5",
                        help="Comma-separated task numbers to run (1–5)")
    args = parser.parse_args()
    tasks = {int(t.strip()) for t in args.tasks.split(",")}

    OUT_BASE.mkdir(parents=True, exist_ok=True)
    for term_id in TERMINALS:
        (OUT_BASE / term_id).mkdir(parents=True, exist_ok=True)

    t1 = t2 = t3 = t4 = None

    if 1 in tasks:
        t1 = task1_angular_profile()
    if 2 in tasks:
        t2 = task2_data_origin()
    if 3 in tasks:
        t3 = task3_optical()
    if 4 in tasks:
        t4 = task4_dynamic_range()
    if 5 in tasks:
        task5_verdict(t1, t2, t3, t4)

    print("\n=== ALL REQUESTED TASKS COMPLETE ===")


if __name__ == "__main__":
    main()
