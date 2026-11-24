"""
jask_ship_detect.py -- Jask terminal + anchorage SAR ship detection.

Orbit 57, descending. AOI covers the Jask port area and anchorage
along the Iranian coast of the Gulf of Oman.

Outputs data/jask_ship_obs.csv with one row per detected ship per pass.

Usage:
  python scripts/jask_ship_detect.py              # full run
  python scripts/jask_ship_detect.py --incremental  # only new passes
"""

import argparse
import csv
import math
import os
import sys
from datetime import date, datetime, timedelta

import numpy as np

# ---- geometry ----------------------------------------------------------------
AOI    = (57.00, 25.40, 58.20, 26.20)   # Jask terminal + anchorage
ORBIT  = 57
RES    = 20.0
W = int((AOI[2] - AOI[0]) * 111320 * math.cos(math.radians((AOI[1]+AOI[3])/2)) / RES)
H = int((AOI[3] - AOI[1]) * 111320 / RES)

OUT_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "jask_ship_obs.csv")
FIELDNAMES = ["date", "orbit", "time_utc", "sat", "lat", "lon", "length_m", "n_ships_pass"]

# ---- credentials -------------------------------------------------------------
PC_KEY = os.environ.get("PC_SDK_SUBSCRIPTION_KEY", "")


def _fatal(msg):
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


# ---- SAR fetch ---------------------------------------------------------------
def _open_catalog():
    try:
        import pystac_client
        import planetary_computer as pc
    except ImportError as e:
        _fatal(f"Missing dependency: {e}  --  pip install pystac_client planetary_computer")

    kwargs = {}
    if PC_KEY:
        kwargs["headers"] = {"Ocp-Apim-Subscription-Key": PC_KEY}
    return pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=pc.sign_inplace,
        **kwargs,
    )


def _detect_item(item):
    """Detect ships in one STAC item. Returns list of (lat, lon, length_m) or None on skip."""
    try:
        import rasterio
        from rasterio.vrt import WarpedVRT
        from rasterio.enums import Resampling
        from rasterio.transform import from_bounds
        from scipy import ndimage
    except ImportError as e:
        _fatal(f"Missing dependency: {e}")

    T = from_bounds(*AOI, W, H)
    try:
        with rasterio.open(item.assets["vv"].href) as src:
            with WarpedVRT(src, crs="EPSG:4326", transform=T, width=W, height=H,
                           resampling=Resampling.average, nodata=np.nan) as v:
                a = v.read(1)
    except Exception as e:
        print(f"  [READ-ERR] {item.id}: {e}")
        return None

    valid = np.isfinite(a) & (a > 0)
    if valid.mean() < 0.80:
        return None  # less than 80% coverage

    db  = 10 * np.log10(np.where(valid, a, 1e-6))
    sea = valid
    med = np.median(db[sea])
    sd  = np.std(db[sea][db[sea] < med + 6])
    thr = med + max(8.0, 4.5 * sd)
    if not np.isfinite(thr):
        return None

    lab, n = ndimage.label((db > thr) & sea)
    ships = []
    for i in range(1, n + 1):
        ys, xs = np.where(lab == i)
        if len(ys) < 3 or len(ys) > 600:
            continue
        cy, cx = ys.mean(), xs.mean()
        q = np.c_[xs, ys].astype(float)
        q -= q.mean(0)
        _, evecs = np.linalg.eigh(np.cov(q.T))
        length = int((np.ptp(q @ evecs[:, -1]) + 1) * RES)
        lat = round(float(AOI[3] - (cy + .5) / H * (AOI[3] - AOI[1])), 5)
        lon = round(float(AOI[0] + (cx + .5) / W * (AOI[2] - AOI[0])), 5)
        ships.append((lat, lon, length))

    return ships


# ---- incremental state -------------------------------------------------------
def _last_date_in_csv(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            rows = list(csv.DictReader(f))
        if rows:
            return max(r["date"] for r in rows)
    except Exception:
        pass
    return None


# ---- main --------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incremental", action="store_true")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end",   default=date.today().isoformat())
    args = ap.parse_args()

    start = args.start
    end   = args.end

    if args.incremental:
        last = _last_date_in_csv(OUT_CSV)
        if last:
            start = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            print(f"Incremental: start={start} (last CSV date={last})")
        else:
            print("Incremental: no existing CSV, running full range")

    if start > end:
        print("Nothing to do.")
        return

    cat = _open_catalog()
    items = sorted(
        cat.search(
            collections=["sentinel-1-rtc"],
            bbox=list(AOI),
            datetime=f"{start}/{end}",
            max_items=2000,
        ).items(),
        key=lambda x: x.datetime,
    )
    print(f"Found {len(items)} STAC items")

    seen, new_rows = set(), []
    for item in items:
        orb = item.properties.get("sat:relative_orbit")
        if orb != ORBIT:
            continue
        key = item.datetime.strftime("%Y-%m-%d")
        if key in seen:
            continue
        seen.add(key)
        time_utc = item.datetime.strftime("%H:%M")
        sat      = item.id[:3]
        print(f"  Detecting {key} orbit={orb} ...", end=" ", flush=True)
        ships = _detect_item(item)
        if ships is None:
            print("SKIP")
            continue
        print(f"n={len(ships)}")
        for lat, lon, length in ships:
            new_rows.append({
                "date":         key,
                "orbit":        orb,
                "time_utc":     time_utc,
                "sat":          sat,
                "lat":          lat,
                "lon":          lon,
                "length_m":     length,
                "n_ships_pass": len(ships),
            })

    out_path = os.path.abspath(OUT_CSV)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    file_exists = os.path.exists(out_path)
    write_header = not file_exists

    if not new_rows:
        print("No new ships detected.")
        if not file_exists:
            with open(out_path, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        return

    with open(out_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            w.writeheader()
        w.writerows(new_rows)

    print(f"Wrote {len(new_rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
