"""
stage_h1_db_norm.py — H1 reference patches → db_norm column for al_flaw
========================================================================
Three reference patches chosen over stable, non-tank desert/sand surfaces
adjacent to the Al-Faw terminal. Each patch must pass: inter-scene std < 1 dB
across all DESCENDING observations. Fails with exit 1 and replacement guidance
if any patch fails.

db_norm(tank, date) = db_mean(tank, date) − scene_bias(date)
scene_bias(date) = median of the three ref patches on that date

Modes:
  --dry-run   (default) — fetch patches, validate std, print stats; no write
  --force     — compute db_norm for all al_flaw rows, upsert Supabase

Output: column db_norm added to terminal_observations (upsert on terminal_id,date,tank_id)

Usage:
  python stage_h1_db_norm.py          # dry-run: validate patches
  python stage_h1_db_norm.py --force  # live
"""

import os, sys, json, math, time, io
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

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

STD_THRESHOLD_DB = 1.0  # dB — inter-scene std limit per patch

# ── Reference patches: stable desert/sand near Al-Faw ────────────────────────
# These are flat, dry ground surfaces SE of the tank cluster, away from water
# and roads. 50m radius each. Replace any that fail std criterion.
REF_PATCHES = {
    "ref_A": {"lat": 29.956, "lon": 48.460, "r_m": 50,
              "desc": "desert flat SE cluster"},
    "ref_B": {"lat": 29.953, "lon": 48.468, "r_m": 50,
              "desc": "sand plain E of terminal"},
    "ref_C": {"lat": 29.962, "lon": 48.473, "r_m": 50,
              "desc": "bare ground NE berm"},
}

# ── Supabase helpers ──────────────────────────────────────────────────────────
def sb_get(path, params=None):
    r = requests.get(f"{SB_URL}/rest/v1/{path}", params=params,
                     headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}, timeout=30)
    r.raise_for_status()
    return r.json()

def sb_upsert(table, rows):
    r = requests.post(
        f"{SB_URL}/rest/v1/{table}?on_conflict=terminal_id,date,tank_id",
        json=rows,
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"},
        timeout=60)
    if not r.ok:
        print(f"  [upsert error] {r.status_code}: {r.text[:500]}")
    r.raise_for_status()

def fetch_all_observations():
    rows, offset = [], 0
    while True:
        chunk = sb_get("terminal_observations",
                       {"terminal_id": f"eq.{TERMINAL_ID}",
                        "orbit_direction": f"eq.{ORBIT_DIR}",
                        "select": "date,tank_id,db_mean",
                        "order": "date.asc",
                        "offset": str(offset), "limit": str(PAGE_SIZE)})
        rows.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return pd.DataFrame(rows)

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

def fetch_db_patch(lat_c, lon_c, r_m, date_str, token, retries=2):
    """Fetch median VV dB over a circular patch."""
    try:
        import tifffile
    except ImportError:
        raise ImportError("tifffile required: pip install tifffile")

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
        "output": {"width": 30, "height": 30,
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
                if attempt < retries:
                    time.sleep(2)
                    continue
                return None
            arr = tifffile.imread(io.BytesIO(resp.content)).flatten().astype(np.float32)
            nw, nh = 30, 30
            lons_px = np.linspace(bbox[0], bbox[2], nw)
            lats_px = np.linspace(bbox[3], bbox[1], nh)
            lon_g, lat_g = np.meshgrid(lons_px, lats_px)
            dx = (lon_g - lon_c) * 111320.0 * math.cos(math.radians(lat_c))
            dy = (lat_g - lat_c) * 111320.0
            mask = np.sqrt(dx**2 + dy**2).flatten() <= r_m
            valid = arr[mask & np.isfinite(arr) & (arr > -30) & (arr < 30)]
            return float(np.median(valid)) if len(valid) >= 4 else None
        except Exception:
            if attempt < retries:
                time.sleep(2)
    return None


def main():
    print("=== Al-Faw H1 db_norm ===")
    print(f"  mode: {'DRY-RUN' if DRY_RUN else 'LIVE --force'}")
    print()

    print("[1] Fetching dates from Supabase …")
    df = fetch_all_observations()
    df = df.drop_duplicates(subset=["date", "tank_id"])
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")
    dates = sorted(df["date"].unique())
    print(f"  {len(df)} rows, {len(dates)} dates, {df['tank_id'].nunique()} tanks")
    print(f"  range: {dates[0]} – {dates[-1]}")
    print()

    token = sh_token()

    # ── Fetch ref patches for all dates ──────────────────────────────────────
    print("[2] Fetching ref patches for all dates …")
    patch_records = {pid: {} for pid in REF_PATCHES}

    for i, date in enumerate(dates):
        for pid, pc in REF_PATCHES.items():
            val = fetch_db_patch(pc["lat"], pc["lon"], pc["r_m"], date, token)
            if val is not None:
                patch_records[pid][date] = val
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(dates)} dates …")
        if (i + 1) % 100 == 0:
            token = sh_token()

    print()

    # ── Validate patches: inter-scene std ────────────────────────────────────
    print("[3] Patch validation (inter-scene std, threshold=1.0 dB):")
    patch_fail = False
    patch_stats = {}
    for pid, pc in REF_PATCHES.items():
        vals = [v for v in patch_records[pid].values() if v is not None]
        n    = len(vals)
        std  = float(np.std(vals)) if n > 1 else 999.0
        mean = float(np.mean(vals)) if n > 0 else float("nan")
        coverage = n / len(dates) * 100
        status   = "OK" if std < STD_THRESHOLD_DB else "FAIL"
        patch_stats[pid] = {"std": std, "mean": mean, "n": n, "coverage_pct": coverage}
        print(f"  {pid} ({pc['desc']}): mean={mean:.2f} dB  std={std:.3f} dB  "
              f"n={n}/{len(dates)} ({coverage:.0f}%)  [{status}]")
        if std >= STD_THRESHOLD_DB:
            patch_fail = True
            print(f"    REPLACE: std={std:.3f} >= {STD_THRESHOLD_DB} — find flatter surface "
                  f"near lat={pc['lat']:.4f} lon={pc['lon']:.4f}")

    if patch_fail:
        print("\n[PATCH FAIL] One or more patches exceed std threshold.")
        print("  Action: replace failing patch coordinates in REF_PATCHES dict and rerun.")
        sys.exit(1)

    print("  All patches PASS.")
    print()

    # ── Compute scene_bias and db_norm ────────────────────────────────────────
    print("[4] Computing scene_bias and db_norm …")
    scene_bias = {}
    for date in dates:
        vals = [patch_records[pid].get(date) for pid in REF_PATCHES
                if patch_records[pid].get(date) is not None]
        scene_bias[date] = float(np.median(vals)) if len(vals) >= 2 else None

    bias_missing = sum(1 for v in scene_bias.values() if v is None)
    if bias_missing > 0:
        print(f"  WARN: {bias_missing} dates have <2 valid patches → db_norm=NULL for those dates")

    df["scene_bias"] = df["date"].map(scene_bias)
    df["db_norm"]    = df["db_mean"] - df["scene_bias"]

    n_normed = df["db_norm"].notna().sum()
    print(f"  db_norm computed for {n_normed}/{len(df)} rows")
    print(f"  db_norm range: [{df['db_norm'].min():.3f}, {df['db_norm'].max():.3f}] dB")
    print()

    # Save bias table
    bias_df = pd.DataFrame([{"date": d, "scene_bias": v} for d, v in scene_bias.items()])
    bias_path = OUT_DIR / "h1_scene_bias.csv"
    bias_df.to_csv(bias_path, index=False)
    print(f"  scene_bias → {bias_path}")

    patch_path = OUT_DIR / "h1_patch_stats.json"
    with open(patch_path, "w") as f:
        json.dump(patch_stats, f, indent=2)
    print(f"  patch_stats → {patch_path}")

    if DRY_RUN:
        print("\nDRY-RUN complete. Pass --force to write db_norm to Supabase.")
        return

    # ── LIVE write ────────────────────────────────────────────────────────────
    print("[5] Writing db_norm to Supabase …")
    normed_df = df[df["db_norm"].notna()].copy()
    batch = []
    written = 0

    for date, grp in normed_df.groupby("date"):
        for _, row in grp.iterrows():
            batch.append({
                "terminal_id": TERMINAL_ID,
                "orbit_direction": ORBIT_DIR,
                "date": row["date"],
                "tank_id": row["tank_id"],
                "db_norm": round(float(row["db_norm"]), 4),
            })

        sb_upsert("terminal_observations", batch)
        written += len(batch)
        batch = []

        if written % 500 == 0:
            print(f"  written {written}/{n_normed} …")

    print(f"\nDone. db_norm written for {written} rows.")
    print("Next: series_v2.py to compute terminal daily volumes (raw + norm).")


if __name__ == "__main__":
    main()
