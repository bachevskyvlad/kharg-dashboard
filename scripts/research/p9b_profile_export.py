"""
p9b_profile_export.py — P9 raw profile export for 10+10 tanks

Reads peak_gap_pilot.csv (kharg terminal).
  - "with_peaks" : tanks where two_peaks found on ALL dates  (peak1_pos_m not empty)
  - "no_peaks"   : tanks where NO peaks found on ANY date

Takes up to 10 from each group.
For those 20 tanks × 12 kharg dates, re-fetches GRD from SH and exports
the raw radial profile (GRD amplitude along LOS az=282°).

Output (diagnostics/mega/):
  p9b_profiles_raw.csv   — tank_id, date, dist_m, value_db
  p9b_tank_groups.txt    — which tanks in which group
  run_p9b_<ts>.txt
"""

import csv, io, math, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR   = REPO_ROOT / "diagnostics" / "mega"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TS  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
LOG = OUT_DIR / f"run_p9b_{TS}.txt"
_lf = open(LOG, "w", buffering=1)

def log(msg):
    print(msg, flush=True)
    _lf.write(msg + "\n")

CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]

CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS     = "https://sh.dataspace.copernicus.eu/api/v1/process"

PEAK_GAP_CSV = OUT_DIR / "peak_gap_pilot.csv"

KHARG_DATES = [
    "2019-04-21","2019-05-03","2019-06-08","2024-09-21","2024-10-03",
    "2024-10-15","2026-04-14","2026-05-02","2026-05-14","2026-06-19",
    "2026-06-26","2026-07-08",
]
REL_ORBIT  = 35
DIRECTION  = "DESCENDING"
LOS_AZ     = 282
RESOLUTION = 10  # m/px
BUFFER_M   = 300

# full kharg tank registry (same as p9)
KHARG_TANKS = [
    {"id":"t60_0","lat":29.22173338156137,"lon":50.31654819949878,"radius_m":60},
    {"id":"t60_1","lat":29.22422756973714,"lon":50.31692094143066,"radius_m":60},
    {"id":"t60_2","lat":29.22671505899543,"lon":50.31729348356078,"radius_m":60},
    {"id":"t60_3","lat":29.22920183961888,"lon":50.31766542582773,"radius_m":60},
    {"id":"t60_4","lat":29.23168790085929,"lon":50.31803676808742,"radius_m":60},
    {"id":"t55_0","lat":29.21819823,"lon":50.32196045,"radius_m":55},
    {"id":"t55_1","lat":29.22068549,"lon":50.32233386,"radius_m":55},
    {"id":"t55_2","lat":29.22317204,"lon":50.32270667,"radius_m":55},
    {"id":"t55_3","lat":29.22565789,"lon":50.32307889,"radius_m":55},
    {"id":"t55_4","lat":29.22814303,"lon":50.32345051,"radius_m":55},
    {"id":"t55_5","lat":29.23062747,"lon":50.32382152,"radius_m":55},
    {"id":"t55_6","lat":29.23311121,"lon":50.32419193,"radius_m":55},
    {"id":"t55_7","lat":29.23559424,"lon":50.32456173,"radius_m":55},
    {"id":"t55_8","lat":29.21484855,"lon":50.32724142,"radius_m":55},
    {"id":"t55_9","lat":29.21733506,"lon":50.32761374,"radius_m":55},
    {"id":"t55_10","lat":29.21982087,"lon":50.32798545,"radius_m":55},
    {"id":"t55_11","lat":29.22230598,"lon":50.32835655,"radius_m":55},
    {"id":"t55_12","lat":29.22479038,"lon":50.32872704,"radius_m":55},
    {"id":"t55_13","lat":29.22727408,"lon":50.32909692,"radius_m":55},
    {"id":"t55_14","lat":29.22975708,"lon":50.32946619,"radius_m":55},
    {"id":"t55_15","lat":29.23223938,"lon":50.32983484,"radius_m":55},
    {"id":"t55_16","lat":29.23472098,"lon":50.33020289,"radius_m":55},
    {"id":"t55_17","lat":29.23720187,"lon":50.33057032,"radius_m":55},
    {"id":"t55_18","lat":29.21149828,"lon":50.33252149,"radius_m":55},
    {"id":"t55_19","lat":29.21398404,"lon":50.33289271,"radius_m":55},
    {"id":"t55_20","lat":29.21646910,"lon":50.33326332,"radius_m":55},
    {"id":"t55_21","lat":29.21895345,"lon":50.33363332,"radius_m":55},
    {"id":"t55_22","lat":29.22143710,"lon":50.33400271,"radius_m":55},
    {"id":"t55_23","lat":29.22392005,"lon":50.33437148,"radius_m":55},
    {"id":"t55_24","lat":29.22640229,"lon":50.33473964,"radius_m":55},
    {"id":"t55_25","lat":29.22888383,"lon":50.33510719,"radius_m":55},
    {"id":"t55_26","lat":29.23136466,"lon":50.33547412,"radius_m":55},
    {"id":"t55_27","lat":29.23384479,"lon":50.33584044,"radius_m":55},
    {"id":"t55_28","lat":29.23632422,"lon":50.33620614,"radius_m":55},
    {"id":"t55_29","lat":29.23880294,"lon":50.33657123,"radius_m":55},
    {"id":"t55_30","lat":29.24128095,"lon":50.33693570,"radius_m":55},
    {"id":"t55_31","lat":29.24375825,"lon":50.33729956,"radius_m":55},
    {"id":"t40_0","lat":29.21872199,"lon":50.31175232,"radius_m":40},
    {"id":"t40_1","lat":29.22120781,"lon":50.31212219,"radius_m":40},
    {"id":"t40_2","lat":29.22369293,"lon":50.31249145,"radius_m":40},
    {"id":"t40_3","lat":29.22617734,"lon":50.31286010,"radius_m":40},
    {"id":"t40_4","lat":29.22866104,"lon":50.31322814,"radius_m":40},
    {"id":"t40_5","lat":29.23114404,"lon":50.31359557,"radius_m":40},
    {"id":"t40_6","lat":29.23362633,"lon":50.31396239,"radius_m":40},
    {"id":"t40_7","lat":29.23610791,"lon":50.31432860,"radius_m":40},
    {"id":"t40_8","lat":29.23858878,"lon":50.31469419,"radius_m":40},
    {"id":"t40_9","lat":29.24106895,"lon":50.31505917,"radius_m":40},
    {"id":"t40_10","lat":29.24354840,"lon":50.31542354,"radius_m":40},
    {"id":"t40_11","lat":29.24602715,"lon":50.31578729,"radius_m":40},
    {"id":"t25_0","lat":29.22488648,"lon":50.30613213,"radius_m":25},
    {"id":"t25_1","lat":29.22737066,"lon":50.30649949,"radius_m":25},
    {"id":"t25_2","lat":29.22985413,"lon":50.30686624,"radius_m":25},
    {"id":"t25_3","lat":29.23233689,"lon":50.30723237,"radius_m":25},
    {"id":"t25_4","lat":29.23481894,"lon":50.30759789,"radius_m":25},
    {"id":"t25_5","lat":29.23730027,"lon":50.30796280,"radius_m":25},
    {"id":"t25_6","lat":29.23978089,"lon":50.30832709,"radius_m":25},
    {"id":"t25_7","lat":29.24226080,"lon":50.30869077,"radius_m":25},
    {"id":"t25_8","lat":29.24474000,"lon":50.30905384,"radius_m":25},
    {"id":"t25_9","lat":29.24721848,"lon":50.30941629,"radius_m":25},
    {"id":"t25_10","lat":29.24969625,"lon":50.30977812,"radius_m":25},
    {"id":"t25_11","lat":29.25217330,"lon":50.31013933,"radius_m":25},
]
TANK_BY_ID = {t["id"]: t for t in KHARG_TANKS}


# ── token ─────────────────────────────────────────────────────────────────────

def get_token():
    r = requests.post(CDSE_TOKEN_URL,
        data={"grant_type":"client_credentials",
              "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


# ── read peak_gap CSV → identify groups ───────────────────────────────────────

def identify_groups():
    if not PEAK_GAP_CSV.exists():
        log(f"FATAL: {PEAK_GAP_CSV} not found")
        sys.exit(1)

    n_dates = len(KHARG_DATES)
    tank_peak_count = {}   # tank_id → count of dates with two peaks

    with open(PEAK_GAP_CSV) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("terminal") != "kharg":
                continue
            tid  = row["tank_id"]
            has  = bool(row.get("peak1_pos_m", "").strip())
            tank_peak_count.setdefault(tid, 0)
            if has:
                tank_peak_count[tid] += 1

    # all kharg tanks in CSV
    all_tanks_seen = set(tank_peak_count.keys())
    n_all = len(all_tanks_seen)

    # with_peaks: present on EVERY date in CSV
    with_peaks = sorted([t for t,c in tank_peak_count.items() if c == n_dates])
    # no_peaks: present but always 0 peaks
    no_peaks   = sorted([t for t,c in tank_peak_count.items() if c == 0])

    log(f"  kharg tanks in CSV: {n_all}  n_dates={n_dates}")
    log(f"  with_peaks (all {n_dates} dates): {len(with_peaks)} → {with_peaks}")
    log(f"  no_peaks (0 dates):               {len(no_peaks)}  → {no_peaks[:10]}")

    # cap at 10 each
    grp_with = with_peaks[:10]
    grp_none = no_peaks[:10]

    # write group file
    grp_path = OUT_DIR / "p9b_tank_groups.txt"
    with open(grp_path, "w") as f:
        f.write("group,tank_id\n")
        for t in grp_with:
            f.write(f"with_peaks,{t}\n")
        for t in grp_none:
            f.write(f"no_peaks,{t}\n")
    log(f"  wrote {grp_path.name}")

    return grp_with, grp_none


# ── SH GRD fetch for a bbox + date ───────────────────────────────────────────

_EVALSCRIPT = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""

def sh_grd(token, bbox, date_str):
    import tifffile
    lon0, lat0, lon1, lat1 = bbox
    d1 = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    w  = int(abs(lon1-lon0)*111320*math.cos(math.radians((lat0+lat1)/2))/RESOLUTION)
    h  = int(abs(lat1-lat0)*111320/RESOLUTION)
    payload = {
        "input": {
            "bounds": {"bbox": [lon0,lat0,lon1,lat1],
                       "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{"type": "sentinel-1-grd",
                      "dataFilter": {"orbitDirection": DIRECTION,
                                     "relativeOrbitNumber": REL_ORBIT,
                                     "resolution": "HIGH"},
                      "processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN",
                                     "demInstance": "COPERNICUS_30",
                                     "speckleFilter": {"type": "NONE"}},
                      "timeRange": {"from": date_str+"T00:00:00Z", "to": d1+"T00:00:00Z"}}]},
        "output": {"width": w, "height": h,
                   "responses": [{"identifier":"default","format":{"type":"image/tiff"}}]},
        "evalscript": _EVALSCRIPT,
    }
    r = requests.post(SH_PROCESS, json=payload,
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"},
                      timeout=120)
    if r.status_code != 200:
        log(f"    SH {r.status_code}: {r.text[:120]}")
        return None, w, h
    arr = tifffile.imread(io.BytesIO(r.content)).squeeze().astype(np.float32)
    return arr, w, h


# ── bbox for a single tank ────────────────────────────────────────────────────

def tank_bbox(tank, buf_m=BUFFER_M):
    lat, lon = tank["lat"], tank["lon"]
    dl  = buf_m / 111320
    dlo = buf_m / (111320 * math.cos(math.radians(lat)))
    return (lon-dlo, lat-dl, lon+dlo, lat+dl)


# ── range profile along LOS ───────────────────────────────────────────────────

def range_profile(arr, tank, bbox, res_m, half_m):
    """
    Extract radial profile through tank centre along LOS (az=282°).
    Returns (positions_m, dB_values) — all valid pixels within ±half_m.
    """
    lon0, lat0, lon1, lat1 = bbox
    w, h = arr.shape[1], arr.shape[0]
    cx   = (tank["lon"] - lon0) / (lon1 - lon0) * w
    cy   = (lat1 - tank["lat"]) / (lat1 - lat0) * h

    dx = math.sin(math.radians(LOS_AZ))
    dy = -math.cos(math.radians(LOS_AZ))

    half_px   = int(math.ceil(half_m / res_m))
    positions = []
    values    = []

    for step in range(-half_px, half_px + 1):
        px = int(round(cx + step * dx))
        py = int(round(cy + step * dy))
        if 0 <= px < w and 0 <= py < h:
            v = arr[py, px]
            if np.isfinite(v) and v > 0:
                pos_m = step * res_m
                positions.append(pos_m)
                values.append(10 * math.log10(float(v)))

    return positions, values


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    log(f"p9b_profile_export.py  {datetime.now(timezone.utc).isoformat()}")

    log(f"\n=== identify tank groups ===")
    grp_with, grp_none = identify_groups()
    all_tids = grp_with + grp_none
    log(f"  selected with_peaks: {grp_with}")
    log(f"  selected no_peaks:   {grp_none}")
    log(f"  total tanks: {len(all_tids)}  × {len(KHARG_DATES)} dates")

    log(f"\n=== get token ===")
    token = get_token()
    log("  token OK")

    # combined bbox covering all selected tanks
    sel_tanks = [TANK_BY_ID[tid] for tid in all_tids if tid in TANK_BY_ID]
    lats = [t["lat"] for t in sel_tanks]
    lons = [t["lon"] for t in sel_tanks]
    bd = BUFFER_M / 111320
    bdo = BUFFER_M / (111320 * math.cos(math.radians(np.mean(lats))))
    full_bbox = (min(lons)-bdo, min(lats)-bd, max(lons)+bdo, max(lats)+bd)

    out_rows = []
    n_total  = len(KHARG_DATES)

    for di, date in enumerate(KHARG_DATES):
        log(f"\n  [{di+1}/{n_total}] {date}")
        arr, w, h = sh_grd(token, full_bbox, date)
        if arr is None:
            log(f"    SKIP — no GRD")
            continue
        res_m = abs(full_bbox[2]-full_bbox[0])*111320*math.cos(math.radians(
            (full_bbox[1]+full_bbox[3])/2)) / w
        log(f"    shape={arr.shape}  res≈{res_m:.1f} m")

        for tank in sel_tanks:
            group = "with_peaks" if tank["id"] in grp_with else "no_peaks"
            half_m = tank["radius_m"] * 1.5
            pos, vals = range_profile(arr, tank, full_bbox, res_m, half_m)
            log(f"    {tank['id']:12s} ({group:<10s})  profile pts={len(pos)}")
            for p, v in zip(pos, vals):
                out_rows.append({
                    "group":   group,
                    "tank_id": tank["id"],
                    "date":    date,
                    "dist_m":  f"{p:.1f}",
                    "value_db":f"{v:.4f}",
                })

    csv_path = OUT_DIR / "p9b_profiles_raw.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["group","tank_id","date","dist_m","value_db"])
        w.writeheader()
        w.writerows(out_rows)
    log(f"\n  wrote {csv_path.name}  rows={len(out_rows)}")
    log(f"DONE  {datetime.now(timezone.utc).isoformat()}")
    _lf.close()


if __name__ == "__main__":
    main()
