"""
reingest_alfaw_retry.py — error analysis + catalog verify + retry for 1485 failed (tank,date) pairs
====================================================================================================
Pipeline:
  Step 1  Identify failures: fetch Supabase, find rows where radius_m != r_fit (never set → SH failed)
  Step 2  Error table: failures per tank × per year  (always printed + saved)
  Step 3  Show 3 example failing requests with bbox, date, SH response for diagnosis
  Step 4  SH catalog query — for each failed date confirm scene existence in catalog
  Step 5  Retry only catalog-confirmed scenes (transient SH errors), backoff 3–5 attempts
  Step 6  Print completion criterion: updated = T − N (N = catalog-confirmed absent dates)

Modes:
  --analyze   (default) — steps 1-4 only, no writes
  --retry     — steps 1-5-6, live: catalog + retry + upsert

Usage:
  python reingest_alfaw_retry.py             # analyze (identify failures, catalog query, error table)
  python reingest_alfaw_retry.py --retry     # live retry of catalog-confirmed transient failures
"""

import os, sys, json, math, time, io
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import requests

MODE = "retry" if "--retry" in sys.argv else "analyze"

SB_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or ""
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""

SH_TOKEN_URL   = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
SH_CATALOG_URL = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"

TERMINAL_ID = "al_flaw"
ORBIT_DIR   = "DESCENDING"
PAGE_SIZE   = 1000

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

R_FIT = {
    "af_0": 37.3, "af_1": 36.8, "af_2": 37.0, "af_3": 36.6, "af_4": 37.0,
    "af_5": 35.9, "af_6": 36.2, "af_7": 36.3, "af_8": 36.4, "af_9": 35.1,
    "af_10": 36.6,"af_11": 34.4,"af_12": 34.2,"af_13": 35.2,"af_14": 33.8,
    "af_15": 37.2,"af_16": 36.8,"af_17": 34.9,"af_18": 34.7,"af_19": 36.9,
    "af_20": 35.9,"af_21": 36.7,"af_22": 35.5,"af_23": 35.7,
}

TANK_COORDS = {
    "af_0":  {"lat": 29.976037712391882, "lon": 48.44195552300743},
    "af_1":  {"lat": 29.97446023561727,  "lon": 48.44041073298363},
    "af_2":  {"lat": 29.973482237683964, "lon": 48.44170682926327},
    "af_3":  {"lat": 29.97506237979022,  "lon": 48.443256604287775},
    "af_4":  {"lat": 29.972052466075166, "lon": 48.44360540262179},
    "af_5":  {"lat": 29.973613605327138, "lon": 48.44517136039961},
    "af_6":  {"lat": 29.971043737749962, "lon": 48.44490239092673},
    "af_7":  {"lat": 29.972645241700267, "lon": 48.44646203210755},
    "af_8":  {"lat": 29.970602225788706, "lon": 48.44914349229505},
    "af_9":  {"lat": 29.96964506640903,  "lon": 48.45042448972437},
    "af_10": {"lat": 29.969053578374012, "lon": 48.44757323726147},
    "af_11": {"lat": 29.968074369587153, "lon": 48.44888119527686},
    "af_12": {"lat": 29.967562585491844, "lon": 48.44613477210609},
    "af_13": {"lat": 29.966616705980595, "lon": 48.44740303358956},
    "af_14": {"lat": 29.965160381102958, "lon": 48.449324515383225},
    "af_15": {"lat": 29.966613187227228, "lon": 48.45079062045803},
    "af_16": {"lat": 29.964164829146835, "lon": 48.450625111425616},
    "af_17": {"lat": 29.965658494437534, "lon": 48.45208349893114},
    "af_18": {"lat": 29.961779354173064, "lon": 48.45380166725049},
    "af_19": {"lat": 29.96274476143991,  "lon": 48.45253449582525},
    "af_20": {"lat": 29.964218037290507, "lon": 48.45399437056702},
    "af_21": {"lat": 29.96326674821542,  "lon": 48.45527466098349},
    "af_22": {"lat": 29.964822804270934, "lon": 48.45681522152186},
    "af_23": {"lat": 29.96580178961507,  "lon": 48.45553712042206},
}

# ── helpers ───────────────────────────────────────────────────────────────────
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

def fetch_all_with_radius():
    """Fetch date, tank_id, radius_m for all al_flaw DESCENDING rows."""
    rows, offset = [], 0
    while True:
        chunk = sb_get("terminal_observations",
                       {"terminal_id": f"eq.{TERMINAL_ID}",
                        "orbit_direction": f"eq.{ORBIT_DIR}",
                        "select": "date,tank_id,radius_m",
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

def tank_bbox(tid):
    c = TANK_COORDS[tid]
    lat_c, lon_c = c["lat"], c["lon"]
    r_m = R_FIT[tid]
    dr_lat = r_m / 111320.0
    dr_lon = r_m / (111320.0 * math.cos(math.radians(lat_c)))
    return [lon_c - dr_lon, lat_c - dr_lat, lon_c + dr_lon, lat_c + dr_lat]

def catalog_has_scene(date_str, bbox, token):
    """Return True if SH catalog has at least one IW DESCENDING scene for this date+bbox.
    Uses simple bbox+datetime query (no OGC filter) and filters orbit/mode on client side.
    """
    body = {
        "bbox": bbox,
        "datetime": f"{date_str}T00:00:00Z/{date_str}T23:59:59Z",
        "collections": ["sentinel-1-grd"],
        "limit": 10
    }
    try:
        r = requests.post(SH_CATALOG_URL, json=body,
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"}, timeout=30)
        if r.status_code != 200:
            return False, r.status_code, r.text[:300]
        features = r.json().get("features", [])
        # Client-side filter: DESCENDING orbit + IW mode
        for f in features:
            props = f.get("properties", {})
            orbit = str(props.get("sat:orbit_state", "")).lower()
            mode  = str(props.get("sar:instrument_mode", "")).upper()
            if orbit == "descending" and mode == "IW":
                return True, r.status_code, f"OK orbit={orbit} mode={mode}"
        # No matching scene found among returned features
        return False, r.status_code, f"no IW/DESC in {len(features)} features"
    except Exception as e:
        return False, -1, str(e)

def fetch_db_with_response(lat_c, lon_c, r_m, date_str, token, retries=4):
    """Like fetch_db but returns (db_value_or_None, status_code, response_snippet)."""
    try:
        import tifffile
    except ImportError:
        return None, -1, "tifffile not installed"

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
                wait = 2 * (attempt + 1)
                time.sleep(wait)
                continue
            if resp.status_code != 200 or len(resp.content) < 16:
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                return None, resp.status_code, resp.text[:300]
            arr  = tifffile.imread(io.BytesIO(resp.content)).flatten().astype(np.float32)
            nw, nh = 40, 40
            lons_px = np.linspace(bbox[0], bbox[2], nw)
            lats_px = np.linspace(bbox[3], bbox[1], nh)
            lon_g, lat_g = np.meshgrid(lons_px, lats_px)
            dx = (lon_g - TANK_COORDS.get(None, {}).get("lon", lon_c) if False
                  else (lon_g - lon_c)) * 111320.0 * math.cos(math.radians(lat_c))
            dy = (lat_g - lat_c) * 111320.0
            mask = (np.sqrt(dx**2 + dy**2).flatten() <= r_m)
            valid = arr[mask & np.isfinite(arr) & (arr > -40) & (arr < 30)]
            if len(valid) >= 4:
                return float(np.median(valid)), resp.status_code, f"OK pixels={len(valid)}"
            return None, resp.status_code, f"insufficient pixels: {len(valid)}"
        except Exception as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                return None, -1, str(e)
    return None, -1, "exhausted retries"


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=== Al-Faw retry / error analysis ===")
    print(f"  mode: {MODE.upper()}")
    print()

    # Step 1: identify failures
    print("[1] Fetching current Supabase state (radius_m) …")
    df = fetch_all_with_radius()
    df = df.drop_duplicates(subset=["date", "tank_id"])
    print(f"  {len(df)} rows fetched")

    # Failed = radius_m is null or doesn't match r_fit
    def expected_r(tid):
        return R_FIT.get(tid)

    df["r_expected"] = df["tank_id"].map(R_FIT)
    df["radius_m"]   = pd.to_numeric(df["radius_m"], errors="coerce")

    TOLERANCE = 0.05  # meters, float comparison
    failed_mask = (
        df["r_expected"].notna() &
        (df["radius_m"].isna() | (abs(df["radius_m"] - df["r_expected"]) > TOLERANCE))
    )
    failed_df = df[failed_mask].copy()
    n_failed  = len(failed_df)
    n_total   = len(df[df["r_expected"].notna()])
    print(f"  failed (radius_m != r_fit): {n_failed}  /  {n_total}")
    print()

    # Step 2: error table per tank × per year
    print("[2] Error table — failures per tank × per year:")
    failed_df["year"] = failed_df["date"].str[:4]
    pivot = (failed_df.groupby(["tank_id", "year"])
             .size()
             .unstack(fill_value=0)
             .sort_index())
    pivot.loc["TOTAL"] = pivot.sum()
    pivot["TOTAL"] = pivot.sum(axis=1)
    print(pivot.to_string())
    print()

    errors_by_year = failed_df.groupby("year").size().to_dict()
    print("  Per year totals:")
    for yr, cnt in sorted(errors_by_year.items()):
        print(f"    {yr}: {cnt}")
    print()

    # Save failed pairs
    failed_path = OUT_DIR / "reingest_v2_failed_pairs.csv"
    failed_df[["date","tank_id"]].to_csv(failed_path, index=False)
    print(f"  failed pairs → {failed_path}  ({n_failed} rows)")

    # Step 3: show 3 example failing requests with catalog check
    print("[3] 3 example failing requests — catalog verification:")
    token = sh_token()
    samples = failed_df[["date","tank_id"]].drop_duplicates("date").head(3)
    example_results = []
    for _, row in samples.iterrows():
        tid, date = row["tank_id"], row["date"]
        bbox = tank_bbox(tid)
        in_catalog, cat_status, cat_resp = catalog_has_scene(date, bbox, token)
        print(f"  tank={tid}  date={date}")
        print(f"    bbox={[round(x,6) for x in bbox]}")
        print(f"    catalog → status={cat_status}  in_catalog={in_catalog}")
        print(f"    catalog resp snippet: {cat_resp[:120]}")
        print()
        example_results.append({
            "tank_id": tid, "date": date, "bbox": bbox,
            "in_catalog": in_catalog, "cat_status": cat_status
        })

    # Step 4: catalog-verify ALL failed pairs (by unique date+tank)
    print("[4] Catalog verification for all failed pairs …")
    unique_dates = sorted(failed_df["date"].unique())
    print(f"  {len(unique_dates)} unique dates to check …")

    catalog_status = {}  # date → bool (has_any_scene)
    for i, date in enumerate(unique_dates):
        # Use af_0 bbox as representative (all tanks in same ~2km cluster)
        bbox = tank_bbox("af_0")
        in_cat, _, _ = catalog_has_scene(date, bbox, token)
        catalog_status[date] = in_cat
        if (i + 1) % 20 == 0:
            print(f"  checked {i+1}/{len(unique_dates)} dates …")
        if (i + 1) % 50 == 0:
            token = sh_token()  # refresh token

    absent_dates  = sorted(d for d, v in catalog_status.items() if not v)
    present_dates = sorted(d for d, v in catalog_status.items() if v)
    n_absent = len(failed_df[failed_df["date"].isin(absent_dates)])
    n_transient = len(failed_df[failed_df["date"].isin(present_dates)])

    print(f"\n  Catalog result:")
    print(f"    absent in catalog: {len(absent_dates)} dates → {n_absent} (tank,date) pairs — no retry needed")
    print(f"    present in catalog: {len(present_dates)} dates → {n_transient} (tank,date) pairs — transient, retry")
    print()

    N = n_absent
    T = n_total
    print(f"  Completion criterion: updated = {T} − {N} = {T - N}")
    print(f"  Currently updated = {T - n_failed}")
    print(f"  Remaining to retry: {n_transient}")
    print()
    if absent_dates:
        print(f"  Absent dates ({len(absent_dates)}):")
        for d in absent_dates[:50]:
            n_tanks = len(failed_df[failed_df["date"] == d])
            print(f"    {d}  ({n_tanks} tanks)")
        if len(absent_dates) > 50:
            print(f"    … and {len(absent_dates)-50} more")

    # Save catalog results
    cat_report = {
        "n_failed": n_failed,
        "n_absent_catalog": len(absent_dates),
        "n_transient": n_transient,
        "N_absent_pairs": n_absent,
        "completion_criterion": f"updated = {T} − {N} = {T - N}",
        "absent_dates": absent_dates,
        "present_dates_sample": present_dates[:20],
    }
    cat_path = OUT_DIR / "reingest_v2_catalog_report.json"
    with open(cat_path, "w") as f:
        json.dump(cat_report, f, indent=2)
    print(f"\n  catalog report → {cat_path}")

    if MODE == "analyze":
        print("\nAnalysis complete. Run --retry to execute transient retries.")
        return

    # Step 5: retry transient failures
    print("\n[5] Retrying transient failures (catalog-confirmed, 4 backoff attempts) …")
    retry_pairs = failed_df[failed_df["date"].isin(present_dates)][["date","tank_id"]].copy()
    retry_pairs = retry_pairs.sort_values("date")

    token = sh_token()
    updated, still_failed = 0, 0
    batch = []

    dates_to_retry = sorted(retry_pairs["date"].unique())
    for i, date in enumerate(dates_to_retry):
        date_rows = retry_pairs[retry_pairs["date"] == date]
        for _, row in date_rows.iterrows():
            tid = row["tank_id"]
            lat_c = TANK_COORDS[tid]["lat"]
            lon_c = TANK_COORDS[tid]["lon"]
            r_new = R_FIT[tid]
            db_new, sh_status, sh_resp = fetch_db_with_response(lat_c, lon_c, r_new, date, token, retries=4)
            if db_new is None:
                still_failed += 1
            else:
                batch.append({"date": date, "tank_id": tid,
                              "terminal_id": TERMINAL_ID,
                              "orbit_direction": ORBIT_DIR,
                              "radius_m": r_new,
                              "db_mean": round(db_new, 4)})
                updated += 1

        if batch:
            sb_upsert("terminal_observations", batch)
            batch = []

        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(dates_to_retry)} dates | updated={updated} still_failed={still_failed}")
        if (i + 1) % 50 == 0:
            token = sh_token()

    if batch:
        sb_upsert("terminal_observations", batch)

    total_updated = (T - n_failed) + updated
    print(f"\nRetry done. updated={updated}  still_failed={still_failed}")
    print(f"Total updated: {total_updated}  /  {T}")
    print(f"Completion criterion: target={T - N}  achieved={total_updated}  remaining={T - N - total_updated}")

    if still_failed > 0:
        print(f"\nWARN: {still_failed} pairs still failed after retry — may be edge-catalog or SH data gaps")

    print("\nNext: run stage_h1_db_norm.py for norm baseline (H1 ref patches).")


if __name__ == "__main__":
    main()
