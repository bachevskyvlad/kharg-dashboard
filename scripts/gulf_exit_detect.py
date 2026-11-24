"""
gulf_exit_detect.py — SAR ship detection for Hormuz exit zones.

Three zones (§B6):
  corridor  orbit 93  AOI (56.55, 25.90, 57.05, 26.60)  main shipping lane
  irlane    orbit 93  AOI (56.55, 26.50, 57.10, 27.00)  Iranian coast lane
  jask      orbit 57  AOI (57.10, 25.45, 57.75, 25.92)  Jask bypass

Detection formula (§C7):
  threshold = median + max(10 dB, 5σ)
  blobs 3–600 px; hull via PCA principal axis
  large ≥ 220 m, vlcc ≥ 300 m
  stay = large hull matched ≤1.5 km on previous same-zone pass
  moving = large − stay

Jask sub-zones (§B6):
  terminal:  (57.20, 25.74, 57.42, 25.92)
  anchorage: (57.45, 25.48, 57.75, 25.68)

Writes to gulf_exit_passes (unique: date, zone) via upsert.

Usage:
  python scripts/gulf_exit_detect.py              # last 14 days
  python scripts/gulf_exit_detect.py --days 30    # last 30 days
  python scripts/gulf_exit_detect.py --start 2026-11-01 --end 2026-11-20
  python scripts/gulf_exit_detect.py --dry-run
"""

import argparse
import math
import os
import sys
import json
import urllib.request
from datetime import date, datetime, timedelta

import numpy as np

# ── credentials ───────────────────────────────────────────────────────────────
SB_URL         = os.environ.get("SUPABASE_URL") or ""
SB_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or ""
PC_KEY         = os.environ.get("PC_SDK_SUBSCRIPTION_KEY", "")

# ── zone definitions (§B6) ────────────────────────────────────────────────────
ZONES = {
    "corridor": {"orbit": 93, "aoi": (56.55, 25.90, 57.05, 26.60)},
    "irlane":   {"orbit": 93, "aoi": (56.55, 26.50, 57.10, 27.00)},
    "jask":     {"orbit": 57, "aoi": (57.10, 25.45, 57.75, 25.92)},
}

JASK_TERMINAL_SUB  = (57.20, 25.74, 57.42, 25.92)
JASK_ANCHORAGE_SUB = (57.45, 25.48, 57.75, 25.68)

RES = 25.0  # metres per pixel

LARGE_M = 220
VLCC_M  = 300
STAY_KM = 1.5    # match radius for stay detection (km)


def _fatal(msg):
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def _sb_headers():
    return {
        "apikey": SB_SERVICE_KEY,
        "Authorization": f"Bearer {SB_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }


def _sb_upsert(rows, dry_run=False):
    if dry_run or not rows:
        return
    payload = json.dumps(rows).encode()
    url = f"{SB_URL}/rest/v1/gulf_exit_passes?on_conflict=date,zone"
    req = urllib.request.Request(url, data=payload, headers=_sb_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status = r.status
        print(f"  upsert {len(rows)} rows → HTTP {status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        _fatal(f"Supabase upsert failed: {e.code} {body[:300]}")


def _fetch_existing(zone, start, end):
    """Fetch existing gulf_exit_passes rows for zone in [start, end]."""
    if not SB_URL or not SB_SERVICE_KEY:
        return {}
    url = (f"{SB_URL}/rest/v1/gulf_exit_passes"
           f"?zone=eq.{zone}&date=gte.{start}&date=lte.{end}"
           f"&select=date,large")
    req = urllib.request.Request(url, headers=_sb_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        return {row["date"]: row for row in data}
    except Exception as e:
        print(f"  [warn] fetch_existing {zone}: {e}")
        return {}


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def _open_catalog():
    try:
        import pystac_client
        import planetary_computer as pc
    except ImportError as e:
        _fatal(f"Missing: {e}  — pip install pystac_client planetary_computer")
    kwargs = {}
    if PC_KEY:
        kwargs["headers"] = {"Ocp-Apim-Subscription-Key": PC_KEY}
    return pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=pc.sign_inplace,
        **kwargs,
    )


def _detect_ships(item, aoi, res=RES):
    """Detect large ships in one STAC item within aoi. Returns list of (lat, lon, length_m)."""
    try:
        import rasterio
        from rasterio.vrt import WarpedVRT
        from rasterio.enums import Resampling
        from rasterio.transform import from_bounds
        from scipy import ndimage
    except ImportError as e:
        _fatal(f"Missing: {e}")

    w = int((aoi[2] - aoi[0]) * 111320 * math.cos(math.radians((aoi[1]+aoi[3])/2)) / res)
    h = int((aoi[3] - aoi[1]) * 111320 / res)
    T = from_bounds(*aoi, w, h)

    try:
        with rasterio.open(item.assets["vv"].href) as src:
            with WarpedVRT(src, crs="EPSG:4326", transform=T, width=w, height=h,
                           resampling=Resampling.average, nodata=np.nan) as vrt:
                a = vrt.read(1)
    except Exception as e:
        print(f"  [READ-ERR] {item.id}: {e}")
        return None

    valid = np.isfinite(a) & (a > 0)
    if valid.mean() < 0.80:
        return None  # less than 80% valid pixels — skip partial scene

    db  = 10 * np.log10(np.where(valid, a, 1e-6))
    sea = valid
    med = np.median(db[sea])
    sd  = np.std(db[sea][db[sea] < med + 6])
    thr = med + max(10.0, 5.0 * sd)   # §C7 threshold
    if not np.isfinite(thr):
        return None

    lab, n = ndimage.label((db > thr) & sea)
    ships = []
    for i in range(1, n + 1):
        ys, xs = np.where(lab == i)
        if len(ys) < 3 or len(ys) > 600:
            continue
        q = np.c_[xs, ys].astype(float)
        q -= q.mean(0)
        _, evecs = np.linalg.eigh(np.cov(q.T))
        length = int((np.ptp(q @ evecs[:, -1]) + 1) * res)
        if length < 150 or length > 450:   # §C7 hull filter
            continue
        cy, cx = ys.mean(), xs.mean()
        lat = round(float(aoi[3] - (cy + .5) / h * (aoi[3] - aoi[1])), 5)
        lon = round(float(aoi[0] + (cx + .5) / w * (aoi[2] - aoi[0])), 5)
        ships.append((lat, lon, length))

    return ships


def _in_box(lat, lon, box):
    return box[1] <= lat <= box[3] and box[0] <= lon <= box[2]


def _compute_pass_row(zone_name, dt_str, ships, prev_ships):
    """Aggregate per-pass ship detections into a gulf_exit_passes row."""
    large  = [(lat, lon, l) for lat, lon, l in ships if l >= LARGE_M]
    vlcc_n = sum(1 for _, _, l in large if l >= VLCC_M)

    # Stay: each large ship that has a matching large ship ≤1.5 km on prev pass
    stay = 0
    if prev_ships:
        prev_large = [(lat, lon, l) for lat, lon, l in prev_ships if l >= LARGE_M]
        for lat, lon, _ in large:
            matched = any(_haversine_km(lat, lon, plat, plon) <= STAY_KM
                         for plat, plon, _ in prev_large)
            if matched:
                stay += 1

    moving = len(large) - stay

    row = {
        "date":    dt_str,
        "zone":    zone_name,
        "large":   len(large),
        "vlcc":    vlcc_n,
        "moving":  moving,
        "stay":    stay,
        "terminal_n":    None,
        "terminal_vlcc": None,
        "anchorage_n":   None,
        "anchorage_vlcc": None,
    }

    if zone_name == "jask":
        t_ships = [(lat, lon, l) for lat, lon, l in ships if _in_box(lat, lon, JASK_TERMINAL_SUB)]
        a_ships = [(lat, lon, l) for lat, lon, l in ships if _in_box(lat, lon, JASK_ANCHORAGE_SUB)]
        row["terminal_n"]    = sum(1 for _, _, l in t_ships if l >= LARGE_M)
        row["terminal_vlcc"] = sum(1 for _, _, l in t_ships if l >= VLCC_M)
        row["anchorage_n"]   = sum(1 for _, _, l in a_ships if l >= LARGE_M)
        row["anchorage_vlcc"]= sum(1 for _, _, l in a_ships if l >= VLCC_M)

    return row


def run_zone(cat, zone_name, start, end, dry_run=False):
    cfg  = ZONES[zone_name]
    aoi  = cfg["aoi"]
    orbit = cfg["orbit"]

    items = sorted(
        cat.search(
            collections=["sentinel-1-rtc"],
            bbox=list(aoi),
            datetime=f"{start}/{end}",
            max_items=500,
        ).items(),
        key=lambda x: x.datetime,
    )

    # Filter to correct orbit
    items = [i for i in items if i.properties.get("sat:relative_orbit") == orbit]
    print(f"  [{zone_name}] {len(items)} items (orbit {orbit})")

    # Group by date (take first item per date)
    date_items = {}
    for item in items:
        d = item.datetime.strftime("%Y-%m-%d")
        if d not in date_items:
            date_items[d] = item

    rows = []
    prev_ships = None  # ships from prior date for stay tracking

    for dt_str in sorted(date_items):
        item = date_items[dt_str]
        print(f"    {dt_str} ...", end=" ", flush=True)
        ships = _detect_ships(item, aoi)
        if ships is None:
            print("SKIP (coverage)")
            prev_ships = None
            continue
        print(f"n={len(ships)} total  large={sum(1 for _,_,l in ships if l>=LARGE_M)}")
        row = _compute_pass_row(zone_name, dt_str, ships, prev_ships)
        rows.append(row)
        prev_ships = ships

    if not rows:
        print(f"  [{zone_name}] no rows to write")
        return

    if dry_run:
        print(f"  [{zone_name}] DRY-RUN: would upsert {len(rows)} rows")
        for r in rows[-3:]:
            print("   ", r)
        return

    _sb_upsert(rows, dry_run=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days",    type=int, default=14,
                    help="Number of days to look back (default 14)")
    ap.add_argument("--start",   default=None)
    ap.add_argument("--end",     default=None)
    ap.add_argument("--zone",    choices=list(ZONES), default=None,
                    help="Run one zone only")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not SB_URL:
        _fatal("SUPABASE_URL not set")
    if not SB_SERVICE_KEY and not args.dry_run:
        _fatal("SUPABASE_SERVICE_KEY not set")

    end   = args.end   or date.today().isoformat()
    start = args.start or (date.today() - timedelta(days=args.days)).isoformat()

    print(f"gulf_exit_detect: {start} → {end}  dry_run={args.dry_run}")

    cat = _open_catalog()

    zones_to_run = [args.zone] if args.zone else list(ZONES)
    for zone_name in zones_to_run:
        run_zone(cat, zone_name, start, end, dry_run=args.dry_run)

    print("Done.")


if __name__ == "__main__":
    main()
