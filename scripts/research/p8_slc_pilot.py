"""
p8_slc_pilot.py — Task 1.3: SLC burst API for Kharg DESC-35

20 dates (8 episodic + 12 additional covering full date range):
  2019-04-21, 2019-05-03, 2019-06-08, 2019-07-12, 2019-09-06,
  2024-09-21, 2024-10-03, 2024-10-15, 2024-11-08, 2025-01-01,
  2026-04-14, 2026-05-02, 2026-05-14, 2026-06-07, 2026-06-19,
  2026-06-26, 2026-07-08, 2026-07-20, 2026-08-01, 2026-08-13

Method: CDSE STAC → burst geometry → xarray-sentinel GRD SLC tiff download
        (no SNAP; sarsen optional for RTC backscatter)
Resolution: 4m range × 14m azimuth native SLC → oversampled to 5m grid via
            scipy.ndimage.zoom or GDAL warp.

Outputs (diagnostics/mega/):
  slc_kharg.csv.gz       — (tank_id, date, ring15_far, ring15_near, R_pos_m,
                             R_max_dB, peak_width_m, pixel_valid_frac)
  slc_profiles.npz       — range profile through each tank centre, 1m step

Secrets: CLIENT_ID, CLIENT_SECRET (GHA secrets)
"""

import csv, gzip, io, json, math, os, sys
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

SH_CLIENT_ID     = os.environ["CLIENT_ID"]
SH_CLIENT_SECRET = os.environ["CLIENT_SECRET"]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR   = REPO_ROOT / "diagnostics" / "mega"

TERMINAL   = "kharg"
REL_ORBIT  = 35
DIRECTION  = "DESCENDING"
LOS_AZ     = 282

# 20 dates: 8 episodic + 12 additional
DATES = [
    "2019-04-21","2019-05-03","2019-06-08","2019-07-12","2019-09-06",
    "2024-09-21","2024-10-03","2024-10-15","2024-11-08","2025-01-01",
    "2026-04-14","2026-05-02","2026-05-14","2026-06-07","2026-06-19",
    "2026-06-26","2026-07-08","2026-07-20","2026-08-01","2026-08-13",
]

BUFFER_M   = 300
RING_IN_M  = 15
RING_OUT_M = 30
SECTOR_HALF = 45

SLC_EVALSCRIPT = """
//VERSION=3
function setup(){
  return{
    input:[{bands:["VV","VH"],units:"DN"}],
    output:{bands:2,sampleType:"FLOAT32"}
  };
}
function evaluatePixel(s){
  return[s.VV, s.VH];
}
"""


# ── Auth ─────────────────────────────────────────────────────────────────────

def sh_token():
    import requests
    r = requests.post(
        "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token",
        data={"grant_type":"client_credentials","client_id":SH_CLIENT_ID,"client_secret":SH_CLIENT_SECRET},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


# ── CDSE STAC search for SLC burst IDs ───────────────────────────────────────

def find_slc_scene(date_str, bbox_deg):
    """
    Query CDSE STAC for S1 SLC scene on this date over this bbox.
    Returns (scene_id, burst_ids) or (None, []).
    Uses SH Catalog API: /catalog/1.0.0/search with type=sentinel-1-slc
    """
    import requests
    url = "https://services.sentinel-hub.com/api/v1/catalog/1.0.0/search"
    # Token via client_credentials already obtained in caller — pass via header
    payload = {
        "bbox": bbox_deg,
        "datetime": f"{date_str}T00:00:00Z/{date_str}T23:59:59Z",
        "collections": ["sentinel-1-slc"],
        "filter": {
            "op": "and",
            "args": [
                {"op": "=", "args": [{"property": "s1:relative_orbit_number"}, REL_ORBIT]},
                {"op": "=", "args": [{"property": "sat:orbit_state"}, DIRECTION.lower()]},
            ],
        },
        "limit": 5,
    }
    return payload  # return for diagnostic even before auth


def sh_catalog_search(token, date_str, bbox_deg):
    import requests
    url = "https://services.sentinel-hub.com/api/v1/catalog/1.0.0/search"
    payload = {
        "bbox": bbox_deg,
        "datetime": f"{date_str}T00:00:00Z/{date_str}T23:59:59Z",
        "collections": ["sentinel-1-slc"],
        "filter": {
            "op": "and",
            "args": [
                {"op": "=", "args": [{"property": "s1:relative_orbit_number"}, REL_ORBIT]},
                {"op": "=", "args": [{"property": "sat:orbit_state"}, DIRECTION.lower()]},
            ],
        },
        "limit": 5,
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(url, json=payload, headers=headers, timeout=60)
    if r.status_code != 200:
        print(f"    catalog {r.status_code}: {r.text[:300]}", flush=True)
        return []
    items = r.json().get("features", [])
    return items


# ── SLC Process API (SH) ─────────────────────────────────────────────────────

def sh_slc_request(token, bbox, date_str, resolution=5):
    """
    Download SLC-derived intensity (|VV|²) via SH Process API at fine resolution.
    SH resamples from SLC complex to intensity before delivering tiff.
    """
    import requests, tifffile
    url = "https://services.sentinel-hub.com/api/v1/process"
    payload = {
        "input": {
            "bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{
                "type": "sentinel-1-slc",
                "dataFilter": {
                    "timeRange": {"from": f"{date_str}T00:00:00Z", "to": f"{date_str}T23:59:59Z"},
                    "acquisitionMode": "IW",
                    "polarization": "DV",
                    "orbitDirection": DIRECTION,
                    "relativeOrbit": REL_ORBIT,
                },
                "processing": {
                    "orthorectify": True,
                    "demInstance": "COPERNICUS",
                    "backCoeff": "GAMMA0_ELLIPSOID",
                    "upsampling": "BILINEAR",
                    "downsampling": "BILINEAR",
                },
            }],
        },
        "evalscript": SLC_EVALSCRIPT,
        "output": {
            "resx": resolution / 111320,
            "resy": resolution / 111320,
            "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}],
        },
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(url, json=payload, headers=headers, timeout=180)
    if resp.status_code != 200:
        print(f"    SLC SH {resp.status_code}: {resp.text[:300]}", flush=True)
        return None
    arr = tifffile.imread(io.BytesIO(resp.content))
    arr = arr.astype(np.float32)
    # extract VV band (band 0 if shape is (H,W,2) or (H,W))
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    return arr


# ── Tank registry ─────────────────────────────────────────────────────────────

import subprocess

def load_tanks():
    r = subprocess.run(
        ["python3", "-c",
         "import sys; sys.path.insert(0,'scripts'); from tank_registry import TERMINALS; "
         "t=[x for x in TERMINALS if x['id']=='kharg'][0]; "
         "[print(tk['id'],tk['lat'],tk['lon'],tk['radius_m'],tk['h_max_m']) for tk in t['tanks']]"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    tanks = []
    for line in r.stdout.strip().splitlines():
        parts = line.split()
        tanks.append({
            "id": parts[0], "lat": float(parts[1]), "lon": float(parts[2]),
            "radius_m": float(parts[3]), "h_max_m": float(parts[4]),
        })
    return tanks


# ── Geometry ──────────────────────────────────────────────────────────────────

def bbox_for_tanks(tanks, buffer_m=BUFFER_M):
    lats = [t["lat"] for t in tanks]; lons = [t["lon"] for t in tanks]
    buf  = buffer_m / 111320
    return [min(lons)-buf, min(lats)-buf, max(lons)+buf, max(lats)+buf]


def range_profile(arr, tank, bbox, res_m, step_m=1.0):
    """Radial range profile through tank centre toward far-side (los_az+180), 1m step."""
    x0, y0, x1, y1 = bbox
    cx = (tank["lon"] - x0) / (x1 - x0) * arr.shape[1]
    cy = (y1 - tank["lat"]) / (y1 - y0) * arr.shape[0]
    far_az_rad = math.radians((LOS_AZ + 180) % 360)
    ax = math.sin(far_az_rad)
    ay = -math.cos(far_az_rad)
    r_max = tank["radius_m"] * 2
    rs  = np.arange(0, r_max + step_m, step_m)
    vals = []
    for r in rs:
        px = cx + ax * r / res_m
        py = cy + ay * r / res_m
        pr = int(round(py)); pc = int(round(px))
        if 0 <= pr < arr.shape[0] and 0 <= pc < arr.shape[1]:
            v = float(arr[pr, pc])
            vals.append(v if v > 0 else float("nan"))
        else:
            vals.append(float("nan"))
    return rs, np.array(vals, dtype=np.float32)


def ring_metrics(arr, tank, bbox, res_m):
    x0, y0, x1, y1 = bbox
    cx = (tank["lon"] - x0) / (x1 - x0) * arr.shape[1]
    cy = (y1 - tank["lat"]) / (y1 - y0) * arr.shape[0]
    r_in  = RING_IN_M  / res_m
    r_out = RING_OUT_M / res_m
    far_az  = (LOS_AZ + 180) % 360
    near_az = LOS_AZ % 360

    def ring_mean_db(az_center):
        vals = []
        imax = int(r_out) + 2
        for dy in range(-imax, imax+1):
            for dx in range(-imax, imax+1):
                dist = math.sqrt(dx*dx + dy*dy)
                if not (r_in <= dist <= r_out):
                    continue
                az = math.degrees(math.atan2(dx, -dy)) % 360
                if abs((az - az_center + 180) % 360 - 180) > SECTOR_HALF:
                    continue
                pr, pc = int(cy+dy), int(cx+dx)
                if 0 <= pr < arr.shape[0] and 0 <= pc < arr.shape[1]:
                    v = arr[pr, pc]
                    if np.isfinite(v) and v > 0:
                        vals.append(float(v))
        if not vals:
            return float("nan")
        return 10 * math.log10(float(np.mean(vals)))

    rf  = ring_mean_db(far_az)
    rn  = ring_mean_db(near_az)

    rs, prof = range_profile(arr, tank, bbox, res_m, step_m=1.0)
    valid = np.isfinite(prof)
    pvf = float(valid.mean()) if len(valid) else float("nan")

    if valid.sum() < 3:
        return rf, rn, float("nan"), float("nan"), float("nan"), pvf, rs, prof

    db_prof = 10 * np.log10(np.where(valid, prof, np.nan))
    peak_idx = int(np.nanargmax(db_prof))
    R_pos    = float(rs[peak_idx])
    R_max    = float(db_prof[peak_idx])
    half     = R_max - 3
    above    = np.where(np.isfinite(db_prof) & (db_prof >= half))[0]
    pw       = float(rs[above[-1]] - rs[above[0]]) if len(above) >= 2 else float("nan")

    return rf, rn, R_pos, R_max, pw, pvf, rs, prof


def _f(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return f"{v:.4f}"


# ── Main ──────────────────────────────────────────────────────────────────────

OUT_COLS = [
    "tank_id","date",
    "ring15_far","ring15_near","R_pos_m","R_max_dB","peak_width_m","pixel_valid_frac",
]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M UTC")
    print(f"=== P8 SLC pilot — Kharg DESC-35 — {ts} ===\n", flush=True)

    os.system("pip install tifffile scipy scikit-image -q")

    print("Auth SentinelHub...", flush=True)
    token = sh_token()
    print("  OK\n", flush=True)

    tanks = load_tanks()
    if not tanks:
        print("[FATAL] no tanks loaded for kharg", flush=True)
        sys.exit(1)
    print(f"Tanks: {len(tanks)}", flush=True)

    bbox = bbox_for_tanks(tanks, BUFFER_M)
    print(f"BBox: {bbox}\n", flush=True)

    # Assess disk space first
    import shutil
    free_gb = shutil.disk_usage("/").free / 1e9
    print(f"Disk free: {free_gb:.1f} GB  (20 scenes × ~80MB SLC each = ~1.6 GB)", flush=True)
    if free_gb < 2.0:
        print("[STOP] Insufficient disk space for SLC download — reporting assessment only", flush=True)
        print("  Required: ~2 GB; Available: {free_gb:.1f} GB", flush=True)
        print("  Recommendation: use 5m GRD (p7) for filter comparison; SLC needs dedicated runner", flush=True)
        return

    # First, try STAC catalog to confirm SLC product availability
    print("CDSE STAC catalog probe (one date)...", flush=True)
    items = sh_catalog_search(token, DATES[2], bbox)
    if items:
        print(f"  Found {len(items)} SLC item(s) for {DATES[2]}", flush=True)
        item0 = items[0]
        print(f"  id={item0.get('id','?')}  date={item0.get('properties',{}).get('datetime','?')[:10]}", flush=True)
        props = item0.get("properties", {})
        print(f"  orbit={props.get('s1:relative_orbit_number','?')}  state={props.get('sat:orbit_state','?')}", flush=True)
    else:
        print(f"  No SLC items found for {DATES[2]} — SLC collection may not be available on this SH account", flush=True)
        print("  Falling back to SH Process API (sentinel-1-slc) direct request for assessment", flush=True)

    out_rows  = []
    profiles  = {}

    for di, date in enumerate(DATES):
        print(f"[{di+1}/{len(DATES)}] {date}", flush=True)

        arr = sh_slc_request(token, bbox, date, resolution=5)
        if arr is None:
            print(f"  no SLC data", flush=True)
            continue

        print(f"  arr shape={arr.shape}  min={arr.min():.4f}  max={arr.max():.4f}", flush=True)

        for tank in tanks:
            rf, rn, rp, rm, pw, pvf, rs, prof = ring_metrics(arr, tank, bbox, res_m=5)
            out_rows.append(dict(
                tank_id=tank["id"], date=date,
                ring15_far=_f(rf), ring15_near=_f(rn), R_pos_m=_f(rp),
                R_max_dB=_f(rm), peak_width_m=_f(pw), pixel_valid_frac=_f(pvf),
            ))
            profiles[f"{tank['id']}__{date}__r"] = rs.astype(np.float32)
            profiles[f"{tank['id']}__{date}__db"] = (10*np.log10(np.where(prof>0, prof, np.nan))).astype(np.float32)

    # ── Save outputs ──────────────────────────────────────────────────────────
    csv_path = OUT_DIR / "slc_kharg.csv.gz"
    print(f"\nSaving {csv_path.name} ({len(out_rows)} rows)...", flush=True)
    with gzip.open(csv_path, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLS)
        w.writeheader()
        w.writerows(out_rows)

    npz_path = OUT_DIR / "slc_profiles.npz"
    print(f"Saving {npz_path.name} ({len(profiles)} arrays)...", flush=True)
    np.savez_compressed(npz_path, **{k.replace("/","_"): v for k, v in profiles.items()})

    # Quality report
    nn  = sum(1 for r in out_rows if r["ring15_far"] not in ("","nan"))
    tot = len(out_rows)
    print(f"\n=== Quality report ===", flush=True)
    print(f"  kharg  SLC  ring15_far: {nn}/{tot} ({nn/tot:.3f})" if tot else "  no rows", flush=True)
    print(f"\nDONE", flush=True)


if __name__ == "__main__":
    main()
