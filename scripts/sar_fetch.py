"""
sar_fetch.py — Sentinel Hub catalog query and SAR image download.

Responsibilities:
  1. Query Sentinel Hub catalog for all descending-orbit passes over Kharg
     since START_DATE, deduplicate scenes that straddle UTC midnight.
  2. Compare against cached dates to identify only genuinely new passes.
  3. For each new pass, download the VV backscatter image and extract
     per-tank crescent-mask dB means.

The crescent mask technique (inverse crescent radiometry):
  - Sentinel-1 illuminates from azimuth ~282° (SW→NE, descending orbit).
  - The far interior wall of the tank produces a double-bounce (dihedral)
    return. Empty tank: roof at bottom, long dihedral path → high dB.
    Full tank: roof near top, short/absent dihedral path → low dB.
  - We mask a ring annulus on the far-side half (282° for DESC) where
    the dihedral geometry is strongest.
  - Lower dB = full tank; higher dB = empty/draining tank.

Image processing:
  - evalscript returns raw linear VV power (not dB) — we convert to dB
    after averaging to avoid Jensen's inequality bias.
  - orthorectify=True + COPERNICUS_30 DEM removes terrain/layover distortion.
  - GAMMA0_TERRAIN backscatter coefficient normalises for local incidence angle.
"""

import sys
import json
import numpy as np
import pandas as pd
from datetime import timedelta
from sentinelhub import SentinelHubRequest, SentinelHubCatalog, MimeType

from config import config, S1_CDSE, START_DATE, RESOLUTION, LOS_AZIMUTH, WIDTH_WIDE
from locations import get_super_bbox


def load_cache(raw_cache_path, sb):
    """
    Loads raw observation records from local JSON cache.
    If no cache exists, falls back to a full Supabase pull (slow, ~3000+ rows).

    Returns (cached_records list, cached_dates set of tz-naive Timestamps).

    The tz_localize(None) strip is critical: Sentinel Hub returns tz-aware UTC
    timestamps; JSON-parsed cached dates are tz-naive. Mixing them in a set
    membership check causes every date to appear as 'new' (Python treats
    Timestamp('2026-07-20', tz='UTC') != Timestamp('2026-07-20')).
    """
    cached_records = []
    cached_dates   = set()

    if __import__("os").path.exists(raw_cache_path):
        with open(raw_cache_path) as f:
            cached_records = json.load(f)
        cached_dates = {pd.to_datetime(r["date"]).normalize() for r in cached_records}
        print(f"Loaded {len(cached_records)} cached raw records covering {len(cached_dates)} dates.")
    else:
        print("No local cache — loading all raw_observations from Supabase.")
        try:
            offset = 0
            while True:
                rows = (sb.table("raw_observations")
                          .select("date,tank_id,radius_m,diameter,h_max,db_mean")
                          .range(offset, offset + 999)
                          .execute().data)
                cached_records.extend(rows)
                for r in rows:
                    cached_dates.add(pd.to_datetime(r["date"]).normalize())
                if len(rows) < 1000:
                    break
                offset += 1000
            print(f"  Supabase: loaded {len(cached_records)} records covering {len(cached_dates)} dates.")
        except Exception as e:
            print(f"  [!] Could not load from Supabase: {e}. Will process everything from {START_DATE.date()}.")

    return cached_records, cached_dates


def get_all_scene_dates(super_bbox, start_date=None, orbit_state="descending", relative_orbit=None):
    """
    Queries the Sentinel Hub catalog for S1 scenes over the given bbox.

    orbit_state: "descending" (default, covers Persian Gulf / Russia) or
                 "ascending" (needed for Red Sea terminals like Yanbu where
                 descending orbit has no coverage after 2016).

    relative_orbit: if set, restricts to that specific orbit number.
                    Use for terminals with multiple overlapping orbits
                    (e.g. ras_tanura DESC 35 to exclude DESC 137).

    Deduplication logic: the catalog sometimes returns two scenes ~1 second
    apart when a pass straddles a UTC day boundary (the satellite crosses
    midnight mid-swath). Both entries refer to the same physical image.
    We keep only one per 12-hour window.

    Returns sorted list of tz-naive date Timestamps.
    Exits process on catalog failure — no point running the pipeline
    without knowing which dates to process.
    """
    from datetime import datetime as dt
    end_date = dt.utcnow()
    effective_start = start_date or START_DATE

    try:
        catalog = SentinelHubCatalog(config=config)
        # Normalise short codes ("DESC"/"ASC") to full words required by the STAC API
        _norm = {"desc": "descending", "asc": "ascending"}
        orbit_state_api = _norm.get(orbit_state.lower(), orbit_state.lower())
        stac_filter = f"sat:orbit_state = '{orbit_state_api}'"
        # sat:relative_orbit is NOT supported as a STAC filter property on CDSE —
        # filter client-side from the returned scene properties instead.
        scenes = list(catalog.search(
            S1_CDSE,
            bbox=super_bbox,
            time=(effective_start.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
            filter=stac_filter,
            fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]}
        ))
        # Deduplicate by relative orbit number: one physical pass = one orbit number per day.
        # This handles both the UTC-midnight straddle and multi-satellite overlap (S1A/C)
        # where two scenes from different swaths of the same orbit reach the bbox hours apart.
        seen_orbits = set()
        deduped = []
        for s in sorted(scenes, key=lambda x: x["properties"]["datetime"]):
            orbit = s["properties"].get("sat:relative_orbit")
            # Client-side orbit filter (CDSE STAC does not support sat:relative_orbit in filter)
            if relative_orbit is not None and orbit != relative_orbit:
                continue
            dt = pd.to_datetime(s["properties"]["datetime"])
            # Key = (orbit_number, calendar_date) — unique physical pass
            key = (orbit, dt.normalize().date())
            if key not in seen_orbits:
                seen_orbits.add(key)
                deduped.append(dt)
        all_dates = sorted({d.normalize().tz_localize(None) for d in deduped})
        return all_dates
    except Exception as e:
        print(f"CRITICAL: Failed to connect to Sentinel Hub. Error: {e}")
        sys.exit(1)


def build_crescent_masks(raw_tanks, super_bbox, img_width, img_height, los_azimuth=None):
    """
    Pre-computes per-tank crescent pixel masks for a given image size.

    The crescent is the intersection of:
      - a ring annulus (pixels between r_inner and r_outer from tank centre)
      - the far-side half-plane (pixels whose dot product with the
        LOS direction vector is positive)

    The annulus width (WIDTH_WIDE=15m) was chosen to maximise pixel count
    while avoiding spill into adjacent tank walls. At 5m/px this is 3 pixels.

    los_azimuth: override LOS azimuth (degrees). Use ~98 for ascending orbit
                 (e.g. Yanbu/Red Sea terminals). Defaults to config LOS_AZIMUTH (282°).

    Returns list of dicts: {id, radius_m, diameter, h_max, crescent (bool mask)}.
    Called once per pipeline run since masks depend only on image geometry.
    """
    az = los_azimuth if los_azimuth is not None else LOS_AZIMUTH
    Y_grid, X_grid = np.indices((img_height, img_width))
    dx = np.sin(np.deg2rad(az))
    dy = -np.cos(np.deg2rad(az))

    tanks_data = []
    for tank in raw_tanks:
        cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], super_bbox, img_width, img_height)
        dist_sq      = (X_grid - cx)**2 + (Y_grid - cy)**2
        dot_product  = (X_grid - cx) * dx + (Y_grid - cy) * dy
        r_outer      = tank["radius"] / RESOLUTION
        r_inner      = max(0, tank["radius"] - WIDTH_WIDE) / RESOLUTION
        crescent     = (dist_sq <= r_outer**2) & (dist_sq >= r_inner**2) & (dot_product > 0)
        tanks_data.append({
            "id":       tank["id"],
            "radius_m": tank["radius"],
            "diameter": tank["radius"] * 2,
            "h_max":    tank["h_max"],
            "crescent": crescent,
        })
    return tanks_data


def lonlat_to_pix(lon, lat, bbox, w, h):
    """Converts geographic coordinates to pixel coordinates within a BBox image."""
    x = (lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * w
    y = (bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * h
    return x, y


# Evalscript returns raw linear VV power (not dB).
# We average in linear domain first, then convert to dB — this avoids
# the non-linearity bias of averaging log values directly.
_EVALSCRIPT = """
//VERSION=3
function setup() { return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(sample) { return [sample.VV]; }
"""


def _tank_valid_fraction(img_linear, crescent_mask):
    """Return fraction of crescent pixels that are finite. Testable without SH."""
    total = int(crescent_mask.sum())
    if total == 0:
        return 0.0
    return int(np.isfinite(img_linear[crescent_mask]).sum()) / total


def fetch_new_records(new_dates, tanks_data, super_bbox, img_width, img_height,
                      min_valid_fraction=0.0):
    """
    Downloads SAR imagery for each date in new_dates and extracts per-tank dB means.

    For each date:
      - Requests a 1-day time window (the pass is a single scene, so this is safe)
      - Applies the crescent mask to the VV linear power image
      - Filters NaN/Inf pixels (can occur at image edges or layover zones)
      - Computes mean linear → convert to dB
      - Stores as a record per (date, tank_id)

    min_valid_fraction: minimum ratio of finite pixels to total crescent pixels
      required to emit a record. 0.0 = old behaviour (any finite pixel accepted).
      Set to 0.5 for Ras Tanura ingest to guard against edge-of-frame partial
      illumination (the rt_65 / DESC-137 contamination mechanism).

    Skips dates where Sentinel Hub returns no data (cloud-safe; SAR is all-weather
    but catalog misses or ordering failures can produce empty responses).

    Returns list of new record dicts ready for Supabase upsert.
    """
    new_records = []

    for d in new_dates:
        d_str = d.strftime("%Y-%m-%d")
        request = SentinelHubRequest(
            evalscript=_EVALSCRIPT,
            input_data=[SentinelHubRequest.input_data(
                data_collection=S1_CDSE,
                time_interval=(d_str, (d + timedelta(days=1)).strftime("%Y-%m-%d")),
                other_args={"processing": {
                    "orthorectify": True,
                    "backCoeff": "GAMMA0_TERRAIN",
                    "demInstance": "COPERNICUS_30",  # best publicly available DEM at Kharg
                }}
            )],
            responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
            bbox=super_bbox,
            size=(img_width, img_height),
            config=config,
        )
        try:
            data = request.get_data()
            if not data:
                continue
            img_linear = data[0].squeeze()
            for tank in tanks_data:
                crescent_pixels = int(tank["crescent"].sum())
                pixels_all = img_linear[tank["crescent"]]
                pixels = pixels_all[np.isfinite(pixels_all)]
                # Guard: reject tank if too few valid pixels (edge-of-frame partial illumination)
                valid_frac = _tank_valid_fraction(img_linear, tank["crescent"])
                if min_valid_fraction > 0 and crescent_pixels > 0:
                    if valid_frac < min_valid_fraction:
                        print(f"  [guard] {d_str} {tank['id']}: "
                              f"{100*valid_frac:.0f}% valid px < "
                              f"{100*min_valid_fraction:.0f}% threshold — skipped")
                        continue
                if pixels.size > 0:
                    mean_linear = np.mean(pixels)
                    db_mean = 10 * np.log10(np.clip(mean_linear, 1e-10, None))
                    if not np.isfinite(db_mean):
                        continue
                    new_records.append({
                        "date":     d_str,
                        "tank_id":  tank["id"],
                        "radius_m": float(tank["radius_m"]),
                        "diameter": float(tank["diameter"]),
                        "h_max":    float(tank["h_max"]),
                        "db_mean":  float(db_mean),
                    })
        except Exception as e:
            print(f"  [!] Skip {d_str}: {e}")

    return new_records
