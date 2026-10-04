"""
reingest_alfaw_v2.py — Al-Faw re-ingest with v2 radii (gate B: radii-only)
===========================================================================
Re-fetches SAR db_mean for all historical al_flaw DESCENDING observations
using the updated r_fit radii from tank_registry.py.

Modes:
  --dry-run   (default) — show what would be re-ingested, no SH calls, no writes
  --force     — live run: fetch all scenes via SH Process API, upsert Supabase

After --force, run stage_h1_db_norm.py to compute norm baseline (H1 patch).

Usage:
  python reingest_alfaw_v2.py          # dry-run
  python reingest_alfaw_v2.py --force  # live (requires go from analyst)
"""

import os, sys, json, math, time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

DRY_RUN = "--force" not in sys.argv

SB_URL = (os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or "sh-5215639c-a2e5-47db-8d46-7e28a3150cde"
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or "UyosmDOgQ4ejiHbrWpDCT0WzLYgvhfloa3VgOLTtLd7c050tvejLgIVCcXfr3eLXT2JWqfrqjQhBgewF3g6nGF"

SH_TOKEN_URL   = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

TERMINAL_ID = "al_flaw"
ORBIT_DIR   = "DESCENDING"
PAGE_SIZE   = 1000
BARREL_PER_M3 = 6.2898
MAX_CAP_V2    = 8.5933  # Mn bbl, computed from r_fit (gate B run 4)
MAX_CAP_V1    = 10.6195 # Mn bbl, old r=40 uniform

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── v2 radii from gate B (old centres, r_fit per tank) ───────────────────────
R_FIT = {
    "af_0": 37.3, "af_1": 36.8, "af_2": 37.0, "af_3": 36.6, "af_4": 37.0,
    "af_5": 35.9, "af_6": 36.2, "af_7": 36.3, "af_8": 36.4, "af_9": 35.1,
    "af_10": 36.6,"af_11": 34.4,"af_12": 34.2,"af_13": 35.2,"af_14": 33.8,
    "af_15": 37.2,"af_16": 36.8,"af_17": 34.9,"af_18": 34.7,"af_19": 36.9,
    "af_20": 35.9,"af_21": 36.7,"af_22": 35.5,"af_23": 35.7,
}

# ── Supabase helpers ──────────────────────────────────────────────────────────
def sb_get(path, params=None):
    r = requests.get(f"{SB_URL}/rest/v1/{path}", params=params,
                     headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}, timeout=30)
    r.raise_for_status()
    return r.json()

def sb_upsert(table, rows):
    r = requests.post(
        f"{SB_URL}/rest/v1/{table}",
        json=rows,
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"},
        timeout=60)
    r.raise_for_status()

def fetch_all_observations():
    rows, offset = [], 0
    while True:
        chunk = sb_get("terminal_observations",
                       {"terminal_id": f"eq.{TERMINAL_ID}",
                        "orbit_direction": f"eq.{ORBIT_DIR}",
                        "select": "date,tank_id,lat,lon,radius_m,db_mean",
                        "order": "date.asc",
                        "offset": str(offset), "limit": str(PAGE_SIZE)})
        rows.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return pd.DataFrame(rows)

# ── SH token ─────────────────────────────────────────────────────────────────
_token_cache = {}
def sh_token():
    if _token_cache.get("exp", 0) > time.time() + 60:
        return _token_cache["token"]
    r = requests.post(SH_TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": SH_CLIENT_ID, "client_secret": SH_CLIENT_SECRET})
    r.raise_for_status()
    d = r.json()
    _token_cache["token"] = d["access_token"]
    _token_cache["exp"]   = time.time() + d.get("expires_in", 3600)
    return d["access_token"]

# ── SH fetch db_mean ──────────────────────────────────────────────────────────
def fetch_db(lat_c, lon_c, r_m, date_str, token, retries=2):
    import io, tifffile
    dr_lat = r_m / 111320.0
    dr_lon = r_m / (111320.0 * math.cos(math.radians(lat_c)))
    bbox   = [lon_c - dr_lon, lat_c - dr_lat, lon_c + dr_lon, lat_c + dr_lat]

    evalscript = """
//VERSION=3
function setup(){
  return{input:[{bands:["VV"],units:"LINEAR_POWER"}],
         output:{bands:1,sampleType:"FLOAT32"}};
}
function evaluatePixel(s){
  return [10*Math.log10(Math.max(s.VV, 1e-10))];
}"""

    body = {
        "input": {
            "bounds": {"bbox": bbox,
                       "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{"type": "sentinel-1-grd", "dataFilter": {
                "timeRange": {"from": f"{date_str}T00:00:00Z", "to": f"{date_str}T23:59:59Z"},
                "acquisitionMode": "IW", "orbitDirection": ORBIT_DIR, "resolution": "HIGH"
            }, "processing": {"backCoeff": "SIGMA0_ELLIPSOID", "orthorectify": True,
                              "demInstance": "COPERNICUS_30"}}]
        },
        "output": {"width": 40, "height": 40,
                   "responses": [{"identifier": "default",
                                  "format": {"type": "image/tiff"}}]},
        "evalscript": evalscript
    }

    for attempt in range(retries + 1):
        try:
            resp = requests.post(SH_PROCESS_URL, json=body,
                                 headers={"Authorization": f"Bearer {token}"}, timeout=60)
            if resp.status_code == 429:
                time.sleep(2 * (attempt + 1)); continue
            if resp.status_code != 200 or len(resp.content) < 16:
                return None
            arr  = tifffile.imread(io.BytesIO(resp.content)).flatten().astype(np.float32)
            nw, nh = 40, 40
            lons_px = np.linspace(bbox[0], bbox[2], nw)
            lats_px = np.linspace(bbox[3], bbox[1], nh)
            lon_g, lat_g = np.meshgrid(lons_px, lats_px)
            dx = (lon_g - lon_c) * 111320.0 * math.cos(math.radians(lat_c))
            dy = (lat_g - lat_c) * 111320.0
            mask = (np.sqrt(dx**2 + dy**2).flatten() <= r_m)
            valid = arr[mask & np.isfinite(arr) & (arr > -40) & (arr < 30)]
            return float(np.median(valid)) if len(valid) >= 4 else None
        except Exception:
            if attempt < retries:
                time.sleep(2)
    return None

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=== Al-Faw re-ingest v2 (gate B: radii-only) ===")
    print(f"  mode: {'DRY-RUN' if DRY_RUN else 'LIVE --force'}")
    print(f"  cap v1={MAX_CAP_V1:.4f} Mn bbl  →  cap v2={MAX_CAP_V2:.4f} Mn bbl")
    print()

    print("[1] Fetching current observations from Supabase …")
    df = fetch_all_observations()
    print(f"  {len(df)} rows: {df['tank_id'].nunique()} tanks, "
          f"{df['date'].nunique()} dates")
    print(f"  date range: {df['date'].min()} – {df['date'].max()}")

    dates  = sorted(df["date"].unique())
    tanks  = sorted(df["tank_id"].unique())
    n_rows = len(df)

    print()
    print("[2] Dry-run summary:")
    print(f"  rows to re-ingest: {n_rows}")
    print(f"  SH API calls needed: {len(dates) * len(tanks)}")
    print(f"  estimated time @ 1 s/call: {len(dates)*len(tanks)/3600:.1f} h")
    print()
    print("  Radius delta (r_fit − 40):")
    for tid in sorted(R_FIT.keys()):
        delta = R_FIT[tid] - 40
        print(f"    {tid}: {R_FIT[tid]:.1f} m  ({delta:+.1f} m)")

    if DRY_RUN:
        print()
        print("DRY-RUN complete. Pass --force to execute.")
        # Write dry-run report
        report = {
            "mode": "dry_run",
            "rows_to_reingest": n_rows,
            "n_dates": len(dates),
            "n_tanks": len(tanks),
            "sh_calls_needed": len(dates) * len(tanks),
            "cap_v1_mn_bbl": MAX_CAP_V1,
            "cap_v2_mn_bbl": MAX_CAP_V2,
            "r_fit": R_FIT,
            "date_range": [dates[0], dates[-1]],
        }
        rpt_path = OUT_DIR / "reingest_dryrun.json"
        with open(rpt_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"  report → {rpt_path}")
        return

    # ── LIVE run ──────────────────────────────────────────────────────────────
    print("[3] Live re-ingest — fetching SH and upserting Supabase …")
    # Need lat/lon per tank — fetch from current Supabase rows
    tank_coords = (df.groupby("tank_id")[["lat", "lon"]]
                   .first().reset_index()
                   .set_index("tank_id")
                   .to_dict("index"))

    token    = sh_token()
    updated  = 0
    errors   = 0
    batch    = []
    BATCH_SZ = 100

    for i, date in enumerate(dates):
        date_rows = df[df["date"] == date]
        for _, row in date_rows.iterrows():
            tid = row["tank_id"]
            if tid not in R_FIT:
                continue
            r_new = R_FIT[tid]
            lat_c = tank_coords[tid]["lat"]
            lon_c = tank_coords[tid]["lon"]

            db_new = fetch_db(lat_c, lon_c, r_new, date, token)
            if db_new is None:
                errors += 1
                continue

            batch.append({"date": date, "tank_id": tid,
                          "terminal_id": TERMINAL_ID,
                          "orbit_direction": ORBIT_DIR,
                          "radius_m": r_new,
                          "db_mean": round(db_new, 4),
                          "geom_source": "esri_z18_2026-10"})
            updated += 1

        if len(batch) >= BATCH_SZ:
            sb_upsert("terminal_observations", batch)
            batch = []

        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(dates)} dates | updated={updated} errors={errors}")
        # Renew token if needed
        if (i + 1) % 50 == 0:
            token = sh_token()

    if batch:
        sb_upsert("terminal_observations", batch)

    print(f"\nDone. updated={updated}  errors={errors}  skipped={n_rows-updated-errors}")
    print("Next: run stage_h1_db_norm.py for norm baseline (H1 ref patches).")

if __name__ == "__main__":
    main()
