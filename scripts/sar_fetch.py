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
  - The floating roof casts a radar shadow on the FAR wall of the tank.
  - We mask a ring annulus on the far-side half of each tank.
  - Mean linear backscatter in that crescent → convert to dB → fill proxy.
  - Lower dB = full tank (more oil = smoother surface = less radar return).

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


def get_all_scene_dates(super_bbox):
    """
    Queries the Sentinel Hub catalog for every descending-orbit S1 scene
    over the Kharg bounding box from START_DATE to now.

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

    try:
        catalog = SentinelHubCatalog(config=config)
        scenes = list(catalog.search(
            S1_CDSE,
            bbox=super_bbox,
            time=(START_DATE.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
            filter="sat:orbit_state = 'descending'",
            fields={"include": ["properties.datetime"]}
        ))
        raw_dates = sorted(pd.to_datetime(s["properties"]["datetime"]) for s in scenes)
        deduped = []
        for d in raw_dates:
            if not deduped or (d - deduped[-1]).total_seconds() > 43200:
                deduped.append(d)
        all_dates = sorted({d.normalize().tz_localize(None) for d in deduped})
        return all_dates
    except Exception as e:
        print(f"CRITICAL: Failed to connect to Sentinel Hub. Error: {e}")
        sys.exit(1)


def build_crescent_masks(raw_tanks, super_bbox, img_width, img_height):
    """
    Pre-computes per-tank crescent pixel masks for a given image size.

    The crescent is the intersection of:
      - a ring annulus (pixels between r_inner and r_outer from tank centre)
      - the far-side half-plane (pixels whose dot product with the
        LOS direction vector is positive)

    The annulus width (WIDTH_WIDE=15m) was chosen to maximise pixel count
    while avoiding spill into adjacent tank walls. At 5m/px this is 3 pixels.

    Returns list of dicts: {id, radius_m, diameter, h_max, crescent (bool mask)}.
    Called once per pipeline run since masks depend only on image geometry.
    """
    Y_grid, X_grid = np.indices((img_height, img_width))
    dx = np.sin(np.deg2rad(LOS_AZIMUTH))
    dy = -np.cos(np.deg2rad(LOS_AZIMUTH))

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


def fetch_new_records(new_dates, tanks_data, super_bbox, img_width, img_height):
    """
    Downloads SAR imagery for each date in new_dates and extracts per-tank dB means.

    For each date:
      - Requests a 1-day time window (the pass is a single scene, so this is safe)
      - Applies the crescent mask to the VV linear power image
      - Filters NaN/Inf pixels (can occur at image edges or layover zones)
      - Computes mean linear → convert to dB
      - Stores as a record per (date, tank_id)

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
                pixels = img_linear[tank["crescent"]]
                # Filter NaN/Inf pixels (can appear at image edges or layover zones)
                pixels = pixels[np.isfinite(pixels)]
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
