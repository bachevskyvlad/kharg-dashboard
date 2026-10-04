"""
stage_h1_db_norm.py — Этап 1 (H1): per-scene SAR normalization
===============================================================
For each terminal (al_flaw only for now — used as normalization polygon),
defines 3 desert reference patches (100×100 m each, confirmed open ground).
Per SAR scene: ref_db = median(db_mean of patches).
Output: db_norm = db_mean − ref_db appended to al_flaw series.

QC: inter-scene std per patch must be < 1 dB.

Rules (ТЗ):
  - Gate H1 on Kharg reference only (not al_flaw series itself)
  - Export anchors not opened
  - "вероятно" prohibited
"""

import os, sys, math, json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ── Credentials ───────────────────────────────────────────────────────────────
SB_URL = (os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or "sh-5215639c-a2e5-47db-8d46-7e28a3150cde"
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or "UyosmDOgQ4ejiHbrWpDCT0WzLYgvhfloa3VgOLTtLd7c050tvejLgIVCcXfr3eLXT2JWqfrqjQhBgewF3g6nGF"

SH_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PAGE_SIZE = 1000

# ── Reference patches — 3 open-ground patches near Al-Faw terminal ──────────
# Each patch: 100m × 100m box of desert/bare ground clear of tanks and roads.
# Coordinates verified against Esri imagery (open flat ground SW of tank field).
# patch_id, lat_ctr, lon_ctr, half_deg (100m ≈ 0.00090° lat / 0.00103° lon at 30°)
REF_PATCHES = [
    {"id": "ref_SW1",
     "lat_min": 29.9580, "lat_max": 29.9589,
     "lon_min": 48.4360, "lon_max": 48.4371},
    {"id": "ref_SW2",
     "lat_min": 29.9590, "lat_max": 29.9599,
     "lon_min": 48.4375, "lon_max": 48.4386},
    {"id": "ref_S3",
     "lat_min": 29.9575, "lat_max": 29.9584,
     "lon_min": 48.4420, "lon_max": 48.4431},
]

TERMINAL_ID = "al_flaw"
TANK_PREFIX = "af_"

SB_HEADERS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Content-Type":  "application/json",
}


# ── SH token ──────────────────────────────────────────────────────────────────
_sh_token_cache = {"token": None, "exp": 0}

def _get_sh_token():
    import time
    if _sh_token_cache["token"] and time.time() < _sh_token_cache["exp"] - 30:
        return _sh_token_cache["token"]
    r = requests.post(SH_TOKEN_URL,
                      data={"grant_type": "client_credentials",
                            "client_id": SH_CLIENT_ID,
                            "client_secret": SH_CLIENT_SECRET},
                      timeout=30)
    r.raise_for_status()
    d = r.json()
    _sh_token_cache["token"] = d["access_token"]
    _sh_token_cache["exp"] = time.time() + d.get("expires_in", 3600)
    return d["access_token"]


# ── Fetch SAR db_mean over a patch via SH Process API ─────────────────────────
def _fetch_patch_db(bbox, date_str, token):
    """
    Fetch mean VV backscatter (dB) for a 100×100m patch on a specific date.
    Returns float or None.
    bbox = [lon_min, lat_min, lon_max, lat_max]
    """
    evalscript = """
//VERSION=3
function setup(){
  return{input:[{bands:["VV"],units:"LINEAR_POWER"}],output:{bands:1,sampleType:"FLOAT32"}};
}
function evaluatePixel(s){
  var db = 10*Math.log10(Math.max(s.VV, 1e-10));
  return [db];
}"""
    body = {
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
            },
            "data": [{
                "type": "sentinel-1-grd",
                "dataFilter": {
                    "timeRange": {
                        "from": f"{date_str}T00:00:00Z",
                        "to":   f"{date_str}T23:59:59Z"
                    },
                    "acquisitionMode": "IW",
                    "polarization": "DV",
                    "orbitDirection": "DESCENDING",
                    "resolution": "HIGH"
                },
                "processing": {"backCoeff": "SIGMA0_ELLIPSOID", "orthorectify": True,
                                "demInstance": "COPERNICUS_30"}
            }]
        },
        "output": {
            "width": 20, "height": 20,
            "responses": [{"identifier": "default",
                           "format": {"type": "image/tiff"}}]
        },
        "evalscript": evalscript
    }
    try:
        from PIL import Image
        import io
        r = requests.post(SH_PROCESS_URL, json=body,
                          headers={"Authorization": f"Bearer {token}"},
                          timeout=60)
        if r.status_code != 200:
            return None
        img = Image.open(io.BytesIO(r.content))
        arr = np.array(img, dtype=np.float32).flatten()
        arr = arr[np.isfinite(arr) & (arr > -40) & (arr < 30)]
        if len(arr) == 0:
            return None
        return float(np.median(arr))
    except Exception:
        return None


# ── Fetch al_flaw terminal_observations from Supabase ─────────────────────────
def fetch_alfaw_obs():
    rows, offset = [], 0
    while True:
        url = (f"{SB_URL}/rest/v1/terminal_observations"
               f"?terminal_id=eq.{TERMINAL_ID}"
               f"&select=id,date,tank_id,db_mean,orbit_direction"
               f"&order=date.asc&offset={offset}&limit={PAGE_SIZE}")
        r = requests.get(url, headers=SB_HEADERS, timeout=60)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=== H1 db_norm — Al-Faw reference patches ===")

    # 1. Fetch al_flaw observations
    print("\nFetching al_flaw observations…")
    rows = fetch_alfaw_obs()
    df = pd.DataFrame(rows)
    print(f"  {len(df)} rows, {df['date'].nunique()} dates, {df['tank_id'].nunique()} tanks")

    if df.empty:
        print("FATAL: no observations")
        sys.exit(1)

    df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")

    # 2. Get SH token
    print("\nGetting SH token…")
    try:
        token = _get_sh_token()
        print("  token OK")
        sh_available = True
    except Exception as e:
        print(f"  SH token failed: {e}")
        sh_available = False

    # 3. For each unique scene date, fetch patch db values
    dates = sorted(df["date"].unique())
    print(f"\nFetching ref_db for {len(dates)} scene dates…")

    patch_records = []   # date × patch → db
    ref_db_by_date = {}  # date → median(patches)

    if sh_available:
        for i, d in enumerate(dates):
            dbs = []
            for p in REF_PATCHES:
                bbox = [p["lon_min"], p["lat_min"], p["lon_max"], p["lat_max"]]
                db_val = _fetch_patch_db(bbox, d, token)
                patch_records.append({
                    "date": d, "patch_id": p["id"],
                    "db_patch": db_val if db_val is not None else float("nan")
                })
                if db_val is not None:
                    dbs.append(db_val)

            if dbs:
                ref_db_by_date[d] = float(np.median(dbs))
            else:
                ref_db_by_date[d] = float("nan")

            if (i + 1) % 20 == 0 or (i + 1) == len(dates):
                print(f"  {i+1}/{len(dates)} dates processed")
    else:
        print("  SH unavailable — db_norm will be NaN (run again in GHA)")
        for d in dates:
            ref_db_by_date[d] = float("nan")
            for p in REF_PATCHES:
                patch_records.append({"date": d, "patch_id": p["id"], "db_patch": float("nan")})

    # 4. QC: inter-scene std per patch
    print("\n[QC] Inter-scene std per patch:")
    patch_df = pd.DataFrame(patch_records)
    for pid in patch_df["patch_id"].unique():
        sub = patch_df[patch_df["patch_id"] == pid]["db_patch"].dropna()
        if len(sub) > 1:
            std_val = sub.std()
            status = "PASS" if std_val < 1.0 else "FAIL"
            print(f"  {pid}: std={std_val:.3f} dB → {status}")
        else:
            print(f"  {pid}: insufficient data")

    # 5. Append db_norm to df
    df["ref_db"]  = df["date"].map(ref_db_by_date)
    df["db_norm"] = df["db_mean"] - df["ref_db"]

    # 6. Save outputs
    patch_csv = OUT_DIR / "ref_patches_db.csv"
    patch_df.to_csv(patch_csv, index=False)
    print(f"\nPatch time-series → {patch_csv}")

    series_csv = OUT_DIR / "alfaw_series_with_norm.csv"
    out_df = df[["date", "tank_id", "db_mean", "ref_db", "db_norm", "orbit_direction"]].copy()
    out_df.to_csv(series_csv, index=False)
    print(f"Series with db_norm → {series_csv}")

    print(f"\ndb_norm coverage: {df['db_norm'].notna().sum()} / {len(df)} rows")
    print(f"ref_db range: {df['ref_db'].min():.2f} – {df['ref_db'].max():.2f} dB")
    print(f"db_norm range: {df['db_norm'].dropna().min():.2f} – {df['db_norm'].dropna().max():.2f} dB")

    # 7. Save patch config as JSON (for stage7 / stage_h1 integration)
    config_path = OUT_DIR / "ref_patches_config.json"
    with open(config_path, "w") as f:
        json.dump({
            "terminal_id": TERMINAL_ID,
            "patches": REF_PATCHES,
            "orbit_direction": "DESC",
            "generated_utc": datetime.now(timezone.utc).isoformat(),
        }, f, indent=2)
    print(f"Patch config → {config_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
