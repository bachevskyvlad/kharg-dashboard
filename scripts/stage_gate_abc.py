"""
stage_gate_abc.py — Al-Faw geometry gate A/B/C
===============================================
SAR arbitration: does the Esri-measured centre shift improve SAR signal?

Three mask configurations, identical 15-20 scene window:
  A = original registry centres   + r = 40 m  (baseline)
  B = original registry centres   + r_fit per tank
  C = v2 centres (Esri-measured)  + r_fit per tank

Metric: tank_step_db = std(db_mean) across all scenes for that tank.
Higher std ↔ more dynamic range ↔ mask captures floating-roof motion.

Verdict criterion (invariant — not adjusted after seeing numbers):
  median(C) < median(B) × 0.85  → full v2 accepted (centres + radii)
  median(B) ≤ median(C)×1.15 AND median(B) < median(A) × 0.85  → radii only
  otherwise  → STOP, table to analyst

db_contrast sanity: winning config must have ≥ inter-tank σ(db_mean per scene)
than config A.

Outputs (no writes to Supabase):
  gate_abc_results.csv   — tank × step_A/B/C + median row
  gate_abc_verdict.txt   — verdict + db_contrast
  gate_abc_run.txt       — log
"""

import os, sys, json, csv, math, time
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

SH_TOKEN_URL   = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

GEO_DIR  = OUT_DIR / "geometry"
GEOM_CSV = GEO_DIR / "geometry_v2.csv"

TERMINAL_ID  = "al_flaw"
ORBIT_DIR    = "DESCENDING"
PAGE_SIZE    = 1000
MAX_GAP_DAYS = 20
N_SCENES     = 20      # target; will use up to 20 most recent

SB_HEADERS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Content-Type":  "application/json",
}

# ── Logging ───────────────────────────────────────────────────────────────────
_log_lines = []
def log(msg):
    print(msg, flush=True)
    _log_lines.append(msg)

def save_log():
    with open(OUT_DIR / "gate_abc_run.txt", "w") as f:
        f.write("\n".join(_log_lines))


# ── SH token ──────────────────────────────────────────────────────────────────
_sh_cache = {"token": None, "exp": 0}

def _get_sh_token():
    if _sh_cache["token"] and time.time() < _sh_cache["exp"] - 30:
        return _sh_cache["token"]
    r = requests.post(SH_TOKEN_URL,
                      data={"grant_type": "client_credentials",
                            "client_id": SH_CLIENT_ID,
                            "client_secret": SH_CLIENT_SECRET},
                      timeout=30)
    r.raise_for_status()
    d = r.json()
    _sh_cache["token"] = d["access_token"]
    _sh_cache["exp"]   = time.time() + d.get("expires_in", 3600)
    return d["access_token"]


# ── Fetch db_mean over a circular mask via SH Process API ─────────────────────
def _fetch_db(lat_c, lon_c, r_m, date_str, token, retries=2):
    """
    Fetch median VV dB over a circular mask of radius r_m centred at (lat_c, lon_c).
    bbox is the enclosing square; circle mask applied via evalscript distance check.
    Returns float or None.
    """
    from PIL import Image
    import io

    # bbox: r_m in degrees (approx)
    dr_lat = r_m / 111320.0
    dr_lon = r_m / (111320.0 * math.cos(math.radians(lat_c)))
    bbox = [lon_c - dr_lon, lat_c - dr_lat, lon_c + dr_lon, lat_c + dr_lat]

    # evalscript: compute VV dB, mask pixels outside circle
    evalscript = """
//VERSION=3
function setup(){
  return{input:[{bands:["VV"],units:"LINEAR_POWER"}],
         output:{bands:1,sampleType:"FLOAT32"}};
}
function evaluatePixel(s,scenes,inputMetadata,customData,outputMetadata){
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
                    "orbitDirection":  ORBIT_DIR,
                    "resolution":      "HIGH"
                },
                "processing": {
                    "backCoeff":    "SIGMA0_ELLIPSOID",
                    "orthorectify": True,
                    "demInstance":  "COPERNICUS_30"
                }
            }]
        },
        "output": {
            "width":  40, "height": 40,
            "responses": [{"identifier": "default",
                           "format": {"type": "image/tiff"}}]
        },
        "evalscript": evalscript
    }

    for attempt in range(retries + 1):
        try:
            resp = requests.post(SH_PROCESS_URL, json=body,
                                 headers={"Authorization": f"Bearer {token}"},
                                 timeout=60)
            if resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code != 200:
                if _fetch_db._diag_calls < 3:
                    print(f"    [diag] HTTP {resp.status_code} | ct={resp.headers.get('content-type')} | {resp.text[:200]}", flush=True)
                    _fetch_db._diag_calls += 1
                return None
            if len(resp.content) < 16:
                if _fetch_db._diag_calls < 3:
                    print(f"    [diag] empty body ({len(resp.content)}B) | ct={resp.headers.get('content-type')}", flush=True)
                    _fetch_db._diag_calls += 1
                return None
            img = Image.open(io.BytesIO(resp.content))
            arr = np.array(img, dtype=np.float32).flatten()

            # Circular mask: pixel centres in bbox, keep those within r_m
            # 40×40 grid over bbox
            nw, nh = 40, 40
            lons_px = np.linspace(bbox[0], bbox[2], nw)
            lats_px = np.linspace(bbox[3], bbox[1], nh)  # N→S
            lon_grid, lat_grid = np.meshgrid(lons_px, lats_px)
            dx_m = (lon_grid - lon_c) * 111320.0 * math.cos(math.radians(lat_c))
            dy_m = (lat_grid - lat_c) * 111320.0
            dist  = np.sqrt(dx_m**2 + dy_m**2).flatten()
            mask  = dist <= r_m

            valid = arr[mask & np.isfinite(arr) & (arr > -40) & (arr < 30)]
            if len(valid) < 4:
                return None
            return float(np.median(valid))
        except Exception as exc:
            if _fetch_db._diag_calls < 3:
                print(f"    [diag] exception: {exc}", flush=True)
                _fetch_db._diag_calls += 1
            if attempt < retries:
                time.sleep(2)
    return None

_fetch_db._diag_calls = 0


# ── Supabase: config A db_mean (already ingested) ─────────────────────────────
def fetch_supabase_db():
    """Fetch all al_flaw DESCENDING terminal_observations. Returns {(date,tank_id): db_mean}."""
    rows, offset = [], 0
    while True:
        url = (f"{SB_URL}/rest/v1/terminal_observations"
               f"?terminal_id=eq.{TERMINAL_ID}"
               f"&orbit_direction=eq.{ORBIT_DIR}"
               f"&select=date,tank_id,db_mean"
               f"&order=date.asc&offset={offset}&limit={PAGE_SIZE}")
        r = requests.get(url, headers=SB_HEADERS, timeout=60)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    result = {}
    for row in rows:
        d = row["date"][:10]
        tid = row["tank_id"]
        val = row.get("db_mean")
        if val is not None:
            result[(d, tid)] = float(val)
    return result


# ── Supabase: recent scene dates for al_flaw ──────────────────────────────────
def fetch_recent_dates():
    rows, offset = [], 0
    while True:
        url = (f"{SB_URL}/rest/v1/terminal_observations"
               f"?terminal_id=eq.{TERMINAL_ID}"
               f"&orbit_direction=eq.{ORBIT_DIR}"
               f"&select=date"
               f"&order=date.desc&offset={offset}&limit={PAGE_SIZE}")
        r = requests.get(url, headers=SB_HEADERS, timeout=60)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    all_dates = sorted({r["date"][:10] for r in rows}, reverse=True)
    # pick up to N_SCENES most recent with no gap >MAX_GAP_DAYS
    selected = []
    for d in all_dates:
        if not selected:
            selected.append(d)
        else:
            gap = (datetime.fromisoformat(selected[-1]) - datetime.fromisoformat(d)).days
            if gap > MAX_GAP_DAYS:
                break
            selected.append(d)
        if len(selected) >= N_SCENES:
            break
    return list(reversed(selected))


# ── Load geometry ─────────────────────────────────────────────────────────────
def load_configs():
    """
    Returns dict: tank_id → {lat_A, lon_A, r_A, lat_C, lon_C, r_B, r_C}
    A = original registry (tank_registry.py)
    B = same centres as A, r_fit from geometry_v2
    C = v2 centres from geometry_v2, r_fit
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import tank_registry as tr

    al_faw = next(l for l in tr.LOCATIONS if l["id"] == TERMINAL_ID)
    orig = {t["id"]: t for t in al_faw["tanks"]}

    geom = list(csv.DictReader(open(GEOM_CSV)))
    configs = {}
    for row in geom:
        tid  = row["tank_id"]
        if tid not in orig:
            continue
        r_fit = int(round(float(row["r_fit_m"])))
        configs[tid] = {
            "lat_A": orig[tid]["lat"],
            "lon_A": orig[tid]["lon"],
            "r_A":   orig[tid]["radius_m"],      # 40
            "r_B":   r_fit,                       # r_fit, old centre
            "lat_C": float(row["lat_v2"]),
            "lon_C": float(row["lon_v2"]),
            "r_C":   r_fit,                       # r_fit, v2 centre
        }
    return configs


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log("=== Gate A/B/C — Al-Faw SAR geometry arbitration ===")
    log(f"  {datetime.now(timezone.utc).isoformat()}")

    # 1. Scene dates
    log("\n[1] Fetching recent DESCENDING scene dates…")
    dates = fetch_recent_dates()
    log(f"  {len(dates)} dates: {dates[0]} → {dates[-1]}")
    if len(dates) < 10:
        log("FATAL: <10 scenes, aborting")
        save_log(); sys.exit(1)

    # 2. Config table
    log("\n[2] Loading geometry configs…")
    configs = load_configs()
    tank_ids = sorted(configs.keys())
    log(f"  {len(tank_ids)} tanks: {tank_ids[0]}–{tank_ids[-1]}")

    # 3. Config A: load from Supabase (production db_mean, no SH needed)
    log("\n[3] Loading config A db_mean from Supabase…")
    sb_db = fetch_supabase_db()
    log(f"  {len(sb_db)} (date,tank) pairs")

    # 4. SH token (needed for B/C only)
    log("\n[4] Getting SH token…")
    try:
        token = _get_sh_token()
        log("  token OK")
    except Exception as e:
        log(f"  FATAL: SH token failed: {e}")
        save_log(); sys.exit(1)

    # 5. Fetch db_mean per (date, tank, config B/C) via SH Process API
    # Config A: Supabase. Config B: old centre+r_fit. Config C: v2 centre+r_fit.
    db_rec = {t: {} for t in tank_ids}
    total_bc = len(dates) * len(tank_ids) * 2
    log(f"\n[5] Fetching SAR db_mean for B/C — {total_bc} SH calls…")

    call_n = 0
    for di, date_str in enumerate(dates):
        for tid in tank_ids:
            cfg = configs[tid]
            vals = {"A": sb_db.get((date_str, tid))}
            for conf, lat, lon, r in [
                ("B", cfg["lat_A"], cfg["lon_A"], cfg["r_B"]),
                ("C", cfg["lat_C"], cfg["lon_C"], cfg["r_C"]),
            ]:
                try:
                    token = _get_sh_token()
                except Exception:
                    pass
                v = _fetch_db(lat, lon, r, date_str, token)
                vals[conf] = v
                call_n += 1
            db_rec[tid][date_str] = vals

        if (di + 1) % 5 == 0 or (di + 1) == len(dates):
            log(f"  {di+1}/{len(dates)} dates | {call_n}/{total_bc} BC calls")

    # 5. Compute step_db = std(db_mean across scenes) per tank per config
    step = {}
    for tid in tank_ids:
        step[tid] = {}
        for conf in ("A", "B", "C"):
            vals = [db_rec[tid][d][conf] for d in dates if db_rec[tid][d][conf] is not None]
            step[tid][conf] = round(float(np.std(vals)), 3) if len(vals) >= 5 else float("nan")

    # 6. Build result table
    rows_out = []
    for tid in tank_ids:
        rows_out.append({
            "tank_id": tid,
            "step_A":  step[tid]["A"],
            "step_B":  step[tid]["B"],
            "step_C":  step[tid]["C"],
        })
    # summary row
    med_A = round(float(np.nanmedian([r["step_A"] for r in rows_out])), 3)
    med_B = round(float(np.nanmedian([r["step_B"] for r in rows_out])), 3)
    med_C = round(float(np.nanmedian([r["step_C"] for r in rows_out])), 3)
    rows_out.append({"tank_id": "MEDIAN", "step_A": med_A, "step_B": med_B, "step_C": med_C})

    csv_path = OUT_DIR / "gate_abc_results.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tank_id","step_A","step_B","step_C"])
        w.writeheader()
        w.writerows(rows_out)
    log(f"\n  → {csv_path}")

    # 7. Verdict
    log("\n[5] Verdict…")
    log(f"  median step_A = {med_A}")
    log(f"  median step_B = {med_B}")
    log(f"  median step_C = {med_C}")

    if med_C < med_B * 0.85:
        verdict = "FULL_V2"
        winner  = "C"
        log("  FULL_V2: median(C) < 85%×median(B) → v2 centres + r_fit accepted")
    elif med_B < med_A * 0.85:
        verdict = "RADII_ONLY"
        winner  = "B"
        log("  RADII_ONLY: median(B) < 85%×median(A) and B ≤ C → r_fit accepted, old centres")
    else:
        verdict = "STOP"
        winner  = "A"
        log("  STOP: neither B nor C beats A by ≥15% — table to analyst")

    # 8. db_contrast sanity (inter-tank σ of db_mean per scene)
    log("\n[6] db_contrast sanity…")
    contrast = {}
    for conf, conf_key in [("A","A"),("B","B"),("C","C")]:
        sigmas = []
        for date_str in dates:
            vals_scene = [db_rec[tid][date_str][conf] for tid in tank_ids
                          if db_rec[tid][date_str][conf] is not None]
            if len(vals_scene) >= 10:
                sigmas.append(np.std(vals_scene))
        contrast[conf] = round(float(np.median(sigmas)), 3) if sigmas else float("nan")
        log(f"  db_contrast_{conf} = {contrast[conf]} dB")

    contrast_pass = contrast[winner] >= contrast["A"] if winner != "A" else True
    log(f"  db_contrast sanity (winner={winner} ≥ A): {'PASS' if contrast_pass else 'FAIL'}")

    # 9. Save verdict
    verdict_lines = [
        f"verdict: {verdict}",
        f"winner: {winner}",
        f"median_step_A: {med_A}",
        f"median_step_B: {med_B}",
        f"median_step_C: {med_C}",
        f"db_contrast_A: {contrast['A']}",
        f"db_contrast_B: {contrast['B']}",
        f"db_contrast_C: {contrast['C']}",
        f"db_contrast_pass: {contrast_pass}",
        f"scenes_used: {len(dates)}",
        f"date_range: {dates[0]}–{dates[-1]}",
        f"generated_utc: {datetime.now(timezone.utc).isoformat()}",
    ]
    verdict_path = OUT_DIR / "gate_abc_verdict.txt"
    with open(verdict_path, "w") as f:
        f.write("\n".join(verdict_lines))
    log(f"\n  → {verdict_path}")

    # Print table
    log("\n  tank_id     step_A   step_B   step_C")
    for r in rows_out:
        log(f"  {r['tank_id']:<12}{r['step_A']:<9}{r['step_B']:<9}{r['step_C']}")

    log("\nDone.")
    save_log()


if __name__ == "__main__":
    main()
