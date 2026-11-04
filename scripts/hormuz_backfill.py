"""
hormuz_backfill.py — Historical ship detection backfill for Strait of Hormuz.

Fetches all S1 scenes in BBOX from 2017-01-01 to 2025-08-31 (all orbits),
runs the same CFAR detector as hormuz_ships.py on each scene, writes results
to data/hormuz_timeseries_history.csv.

Columns: date, scene_id, rel_orbit, orbit_direction, platform, n_ships, status
"""

import os, sys, csv, traceback
from datetime import datetime, timedelta, date
import numpy as np

SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""

if not SH_CLIENT_ID or not SH_CLIENT_SECRET:
    print("FATAL: CLIENT_ID / CLIENT_SECRET not set"); sys.exit(1)

from scipy.ndimage import label, uniform_filter
from sentinelhub import (
    SentinelHubRequest, SentinelHubCatalog, MimeType,
    BBox, CRS, SHConfig, DataCollection,
)

cfg = SHConfig()
cfg.sh_client_id     = SH_CLIENT_ID
cfg.sh_client_secret = SH_CLIENT_SECRET
cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"
cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

S1_CDSE = DataCollection.SENTINEL1.define_from(
    "S1_CDSE",
    service_url="https://sh.dataspace.copernicus.eu",
)

BBOX       = BBox([56.0, 26.15, 57.1, 26.75], crs=CRS.WGS84)
RESOLUTION = 40

lat_mid  = (BBOX.min_y + BBOX.max_y) / 2
width_m  = (BBOX.max_x - BBOX.min_x) * 111320 * np.cos(np.deg2rad(lat_mid))
height_m = (BBOX.max_y - BBOX.min_y) * 111320
IMG_W = min(int(width_m  / RESOLUTION), 2400)
IMG_H = min(int(height_m / RESOLUTION), 2400)

WINDOW_SIZE  = 15
SIGMA        = 4
MIN_SHIP_PX  = 3
MAX_SHIP_PX  = 150

START_DATE = date(2017, 1, 1)
END_DATE   = date(2025, 8, 31)
OUT_PATH   = os.path.join(os.path.dirname(__file__), "..", "data", "hormuz_timeseries_history.csv")

EVALSCRIPT = """
//VERSION=3
function setup() {
  return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) { return [s.VV]; }
"""


def catalog_scenes():
    """Fetch all scenes in date range, all orbits. Returns list of dicts."""
    catalog = SentinelHubCatalog(config=cfg)
    all_scenes = []
    # Search in 6-month chunks to avoid catalog pagination limits
    cur = START_DATE
    while cur <= END_DATE:
        chunk_end = min(date(cur.year + (cur.month // 6), ((cur.month - 1 + 6) % 12) + 1, 1) - timedelta(days=1), END_DATE)
        # safer: just 90-day windows
        chunk_end = min(cur + timedelta(days=89), END_DATE)
        print(f"  Catalog search {cur} → {chunk_end} ...", flush=True)
        try:
            results = list(catalog.search(
                S1_CDSE, bbox=BBOX,
                time=(cur.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")),
                fields={"include": [
                    "properties.datetime",
                    "properties.eo:cloud_cover",
                ]},
            ))
            for r in results:
                props = r.get("properties", {})
                dt_str = props.get("datetime", "")[:10]
                scene_id = r.get("id", "")
                # Extract orbit info from scene id or properties
                rel_orbit = props.get("sat:relative_orbit", props.get("relativeOrbitNumber", ""))
                orbit_dir = props.get("sat:orbit_state", props.get("orbitDirection", ""))
                platform  = props.get("platform", props.get("constellation", "sentinel-1"))
                all_scenes.append({
                    "date": dt_str,
                    "scene_id": scene_id,
                    "rel_orbit": str(rel_orbit) if rel_orbit != "" else "",
                    "orbit_direction": str(orbit_dir).upper() if orbit_dir else "",
                    "platform": str(platform),
                })
        except Exception as e:
            print(f"  [!] Catalog chunk {cur}→{chunk_end} failed: {e}", flush=True)
        cur = chunk_end + timedelta(days=1)

    # Deduplicate by (date, rel_orbit, orbit_direction) — keep distinct passes
    seen = set()
    unique = []
    for s in sorted(all_scenes, key=lambda x: (x["date"], x["rel_orbit"], x["orbit_direction"])):
        key = (s["date"], s["rel_orbit"], s["orbit_direction"])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    print(f"  Total unique scenes: {len(unique)}", flush=True)
    return unique


def detect_ships(scene_date: str) -> int:
    """Run CFAR detector on scene_date. Returns n_ships or raises."""
    request = SentinelHubRequest(
        evalscript=EVALSCRIPT,
        input_data=[SentinelHubRequest.input_data(
            data_collection=S1_CDSE,
            time_interval=(
                scene_date,
                (datetime.strptime(scene_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"),
            ),
            other_args={"processing": {
                "orthorectify": True,
                "backCoeff": "GAMMA0_TERRAIN",
                "demInstance": "COPERNICUS_30",
            }},
        )],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=BBOX,
        size=(IMG_W, IMG_H),
        config=cfg,
    )
    data = request.get_data()
    if not data or data[0] is None:
        raise ValueError("No image data returned")

    img = data[0].squeeze().astype(np.float32)
    img_db = 10 * np.log10(np.clip(img, 1e-10, None))
    valid_mask = np.isfinite(img_db) & (img > 0)

    if valid_mask.sum() < 1000:
        raise ValueError(f"Too few valid pixels: {valid_mask.sum()}")

    local_mean  = uniform_filter(img_db, size=WINDOW_SIZE, mode='reflect')
    local_mean2 = uniform_filter(img_db**2, size=WINDOW_SIZE, mode='reflect')
    local_std   = np.sqrt(np.maximum(local_mean2 - local_mean**2, 0))

    water_mask = local_mean < -8.0

    lon_range_deg = BBOX.max_x - BBOX.min_x
    lat_range_deg = BBOX.max_y - BBOX.min_y
    excl_col = int((56.35 - BBOX.min_x) / lon_range_deg * IMG_W)
    excl_row = int((BBOX.max_y - 26.45) / lat_range_deg * IMG_H)
    anchorage_mask = np.ones(img_db.shape, dtype=bool)
    anchorage_mask[excl_row:, :excl_col] = False

    threshold  = local_mean + SIGMA * local_std
    detections = (img_db > threshold) & valid_mask & water_mask & anchorage_mask

    struct = np.ones((3, 3), dtype=int)
    labeled, n_clusters = label(detections, structure=struct)

    n_ships = sum(
        1 for i in range(1, n_clusters + 1)
        if MIN_SHIP_PX <= (labeled == i).sum() <= MAX_SHIP_PX
    )
    return n_ships


def main():
    print(f"Hormuz backfill {START_DATE} → {END_DATE}", flush=True)
    print(f"Output: {OUT_PATH}", flush=True)

    scenes = catalog_scenes()
    if not scenes:
        print("No scenes found — exiting"); sys.exit(1)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    # Load already-processed dates to allow resume
    done = set()
    rows_existing = []
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows_existing.append(row)
                if row.get("status") == "ok":
                    done.add((row["date"], row.get("rel_orbit",""), row.get("orbit_direction","")))
        print(f"  Resume: {len(done)} scenes already done", flush=True)

    errors = 0
    processed = 0
    rows_new = []

    for s in scenes:
        key = (s["date"], s["rel_orbit"], s["orbit_direction"])
        if key in done:
            continue
        try:
            n_ships = detect_ships(s["date"])
            status  = "ok"
            print(f"  {s['date']} orbit={s['rel_orbit']} {s['orbit_direction']} → {n_ships} ships", flush=True)
        except Exception as e:
            n_ships = ""
            status  = f"error: {str(e)[:80]}"
            errors += 1
            print(f"  [!] {s['date']}: {e}", flush=True)
            traceback.print_exc()

        rows_new.append({
            "date":            s["date"],
            "scene_id":        s["scene_id"],
            "rel_orbit":       s["rel_orbit"],
            "orbit_direction": s["orbit_direction"],
            "platform":        s["platform"],
            "n_ships":         n_ships,
            "status":          status,
        })
        processed += 1

        # Write checkpoint every 10 scenes
        if processed % 10 == 0:
            _write_csv(rows_existing + rows_new)
            print(f"  Checkpoint: {processed} processed, {errors} errors", flush=True)

    all_rows = rows_existing + rows_new
    _write_csv(all_rows)

    # Summary
    ok_rows = [r for r in all_rows if r.get("status") == "ok"]
    dates_ok = sorted(r["date"] for r in ok_rows)
    print("\n" + "="*60)
    print(f"BACKFILL COMPLETE")
    print(f"  Total scenes processed : {processed}")
    print(f"  OK                     : {len(ok_rows)}")
    print(f"  Errors                 : {errors}")
    print(f"  Error rate             : {errors/max(processed,1)*100:.1f}%")
    if dates_ok:
        print(f"  First date             : {dates_ok[0]}")
        print(f"  Last date              : {dates_ok[-1]}")

    # By year
    from collections import Counter
    by_year = Counter(r["date"][:4] for r in ok_rows)
    print("\n  By year:")
    for yr in sorted(by_year):
        print(f"    {yr}: {by_year[yr]} scenes")

    # By orbit direction
    by_orbit = Counter(r.get("orbit_direction","?") for r in ok_rows)
    print("\n  By orbit direction:")
    for d, c in sorted(by_orbit.items()):
        print(f"    {d}: {c}")
    print("="*60)


def _write_csv(rows):
    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date","scene_id","rel_orbit","orbit_direction","platform","n_ships","status"
        ])
        writer.writeheader()
        for r in sorted(rows, key=lambda x: x["date"]):
            writer.writerow(r)


if __name__ == "__main__":
    main()
