"""
p9_peak_gap.py — Task Priority 2: two-peak range profile metric

For each (tank, date) in the 20-scene pilot, extracts a range profile
through the tank centre (radial slice in LOS direction) and finds
TWO peaks:
  peak1 — roof-wall dihedral (bright line near the far edge of the tank)
  peak2 — base-wall dihedral (bright line near the base, slightly closer)

Returns peak_gap_m = distance between them, which cancels geolocation bias.

Outputs (diagnostics/mega/):
  peak_gap_pilot.csv  — (terminal, tank_id, date,
                          peak1_pos_m, peak2_pos_m, peak_gap_m,
                          peak1_dB, peak2_dB)
  run_p9_<timestamp>.txt

Scenes: same 20 as P7 pilot (12 Kharg + 8 RT DESC-35 baseline).
"""

import csv, math, os, sys
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta, timezone
from scipy.signal import find_peaks

SH_CLIENT_ID     = os.environ["CLIENT_ID"]
SH_CLIENT_SECRET = os.environ["CLIENT_SECRET"]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR   = REPO_ROOT / "diagnostics" / "mega"
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG = OUT_DIR / f"run_p9_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M')}.txt"
_lf = open(LOG, "w", buffering=1)

def log(msg):
    print(msg, flush=True)
    _lf.write(msg + "\n")


PILOT_SCENES = {
    "kharg": {
        "rel_orbit":   35,
        "direction":   "DESCENDING",
        "los_azimuth": 282,
        "dates": [
            "2019-04-21","2019-05-03","2019-06-08","2024-09-21","2024-10-03",
            "2024-10-15","2026-04-14","2026-05-02","2026-05-14","2026-06-19",
            "2026-06-26","2026-07-08",
        ],
    },
    "ras_tanura": {
        "rel_orbit":   35,
        "direction":   "DESCENDING",
        "los_azimuth": 282,
        "dates": [
            "2018-06-12","2018-07-06","2019-08-31","2019-09-24","2019-10-06",
            "2022-11-14","2023-03-19","2023-04-12",
        ],
    },
}

RESOLUTION  = 5    # m/px
BUFFER_M    = 300  # bbox padding around tank cluster

_TANKS_INLINE = {
    "kharg": [
        {"id":"t60_0","lat":29.22173338156137,"lon":50.31654819949878,"radius_m":60,"h_max_m":18},
        {"id":"t60_1","lat":29.22422756973714,"lon":50.31692094143066,"radius_m":60,"h_max_m":18},
        {"id":"t60_2","lat":29.22671505899543,"lon":50.31729348356078,"radius_m":60,"h_max_m":18},
        {"id":"t60_3","lat":29.22920183961888,"lon":50.31766542582773,"radius_m":60,"h_max_m":18},
        {"id":"t60_4","lat":29.23168790085929,"lon":50.31803676808742,"radius_m":60,"h_max_m":18},
        {"id":"t55_0","lat":29.21819823,"lon":50.32196045,"radius_m":55,"h_max_m":17},
        {"id":"t55_1","lat":29.22068549,"lon":50.32233386,"radius_m":55,"h_max_m":17},
        {"id":"t55_2","lat":29.22317204,"lon":50.32270667,"radius_m":55,"h_max_m":17},
        {"id":"t55_3","lat":29.22565789,"lon":50.32307889,"radius_m":55,"h_max_m":17},
        {"id":"t55_4","lat":29.22814303,"lon":50.32345051,"radius_m":55,"h_max_m":17},
        {"id":"t55_5","lat":29.23062747,"lon":50.32382152,"radius_m":55,"h_max_m":17},
        {"id":"t55_6","lat":29.23311121,"lon":50.32419193,"radius_m":55,"h_max_m":17},
        {"id":"t55_7","lat":29.23559424,"lon":50.32456173,"radius_m":55,"h_max_m":17},
        {"id":"t55_8","lat":29.21484855,"lon":50.32724142,"radius_m":55,"h_max_m":17},
        {"id":"t55_9","lat":29.21733506,"lon":50.32761374,"radius_m":55,"h_max_m":17},
        {"id":"t55_10","lat":29.21982087,"lon":50.32798545,"radius_m":55,"h_max_m":17},
        {"id":"t55_11","lat":29.22230598,"lon":50.32835655,"radius_m":55,"h_max_m":17},
        {"id":"t55_12","lat":29.22479038,"lon":50.32872704,"radius_m":55,"h_max_m":17},
        {"id":"t55_13","lat":29.22727408,"lon":50.32909692,"radius_m":55,"h_max_m":17},
        {"id":"t55_14","lat":29.22975708,"lon":50.32946619,"radius_m":55,"h_max_m":17},
        {"id":"t55_15","lat":29.23223938,"lon":50.32983484,"radius_m":55,"h_max_m":17},
        {"id":"t55_16","lat":29.23472098,"lon":50.33020289,"radius_m":55,"h_max_m":17},
        {"id":"t55_17","lat":29.23720187,"lon":50.33057032,"radius_m":55,"h_max_m":17},
        {"id":"t55_18","lat":29.21149828,"lon":50.33252149,"radius_m":55,"h_max_m":17},
        {"id":"t55_19","lat":29.21398404,"lon":50.33289271,"radius_m":55,"h_max_m":17},
        {"id":"t55_20","lat":29.21646910,"lon":50.33326332,"radius_m":55,"h_max_m":17},
        {"id":"t55_21","lat":29.21895345,"lon":50.33363332,"radius_m":55,"h_max_m":17},
        {"id":"t55_22","lat":29.22143710,"lon":50.33400271,"radius_m":55,"h_max_m":17},
        {"id":"t55_23","lat":29.22392005,"lon":50.33437148,"radius_m":55,"h_max_m":17},
        {"id":"t55_24","lat":29.22640229,"lon":50.33473964,"radius_m":55,"h_max_m":17},
        {"id":"t55_25","lat":29.22888383,"lon":50.33510719,"radius_m":55,"h_max_m":17},
        {"id":"t55_26","lat":29.23136466,"lon":50.33547412,"radius_m":55,"h_max_m":17},
        {"id":"t55_27","lat":29.23384479,"lon":50.33584044,"radius_m":55,"h_max_m":17},
        {"id":"t55_28","lat":29.23632422,"lon":50.33620614,"radius_m":55,"h_max_m":17},
        {"id":"t55_29","lat":29.23880294,"lon":50.33657123,"radius_m":55,"h_max_m":17},
        {"id":"t55_30","lat":29.24128095,"lon":50.33693570,"radius_m":55,"h_max_m":17},
        {"id":"t55_31","lat":29.24375825,"lon":50.33729956,"radius_m":55,"h_max_m":17},
        {"id":"t40_0","lat":29.21872199,"lon":50.31175232,"radius_m":40,"h_max_m":14},
        {"id":"t40_1","lat":29.22120781,"lon":50.31212219,"radius_m":40,"h_max_m":14},
        {"id":"t40_2","lat":29.22369293,"lon":50.31249145,"radius_m":40,"h_max_m":14},
        {"id":"t40_3","lat":29.22617734,"lon":50.31286010,"radius_m":40,"h_max_m":14},
        {"id":"t40_4","lat":29.22866104,"lon":50.31322814,"radius_m":40,"h_max_m":14},
        {"id":"t40_5","lat":29.23114404,"lon":50.31359557,"radius_m":40,"h_max_m":14},
        {"id":"t40_6","lat":29.23362633,"lon":50.31396239,"radius_m":40,"h_max_m":14},
        {"id":"t40_7","lat":29.23610791,"lon":50.31432860,"radius_m":40,"h_max_m":14},
        {"id":"t40_8","lat":29.23858878,"lon":50.31469419,"radius_m":40,"h_max_m":14},
        {"id":"t40_9","lat":29.24106895,"lon":50.31505917,"radius_m":40,"h_max_m":14},
        {"id":"t40_10","lat":29.24354840,"lon":50.31542354,"radius_m":40,"h_max_m":14},
        {"id":"t40_11","lat":29.24602715,"lon":50.31578729,"radius_m":40,"h_max_m":14},
        {"id":"t25_0","lat":29.22488648,"lon":50.30613213,"radius_m":25,"h_max_m":12},
        {"id":"t25_1","lat":29.22737066,"lon":50.30649949,"radius_m":25,"h_max_m":12},
        {"id":"t25_2","lat":29.22985413,"lon":50.30686624,"radius_m":25,"h_max_m":12},
        {"id":"t25_3","lat":29.23233689,"lon":50.30723237,"radius_m":25,"h_max_m":12},
        {"id":"t25_4","lat":29.23481894,"lon":50.30759789,"radius_m":25,"h_max_m":12},
        {"id":"t25_5","lat":29.23730027,"lon":50.30796280,"radius_m":25,"h_max_m":12},
        {"id":"t25_6","lat":29.23978089,"lon":50.30832709,"radius_m":25,"h_max_m":12},
        {"id":"t25_7","lat":29.24226080,"lon":50.30869077,"radius_m":25,"h_max_m":12},
        {"id":"t25_8","lat":29.24474000,"lon":50.30905384,"radius_m":25,"h_max_m":12},
        {"id":"t25_9","lat":29.24721848,"lon":50.30941629,"radius_m":25,"h_max_m":12},
        {"id":"t25_10","lat":29.24969625,"lon":50.30977812,"radius_m":25,"h_max_m":12},
        {"id":"t25_11","lat":29.25217330,"lon":50.31013933,"radius_m":25,"h_max_m":12},
    ],
    "ras_tanura": [
        {"id":f"rt_{i}","lat":lat,"lon":lon,"radius_m":35,"h_max_m":14}
        for i,(lat,lon) in enumerate([
            (26.638626,50.154236),(26.640254,50.155012),(26.641882,50.155788),
            (26.643510,50.156564),(26.645138,50.157340),(26.646766,50.158116),
            (26.648394,50.158892),(26.650022,50.159668),(26.651650,50.160444),
            (26.653278,50.161220),(26.654906,50.161996),(26.656534,50.162772),
            (26.658162,50.163548),(26.659790,50.164324),(26.661418,50.165100),
            (26.663046,50.165876),(26.664674,50.166652),(26.666302,50.167428),
            (26.638626,50.157000),(26.640254,50.157776),(26.641882,50.158552),
            (26.643510,50.159328),(26.645138,50.160104),(26.646766,50.160880),
            (26.648394,50.161656),(26.650022,50.162432),(26.651650,50.163208),
            (26.653278,50.163984),(26.654906,50.164760),(26.656534,50.165536),
            (26.658162,50.166312),(26.659790,50.167088),(26.661418,50.167864),
            (26.663046,50.168640),(26.664674,50.169416),(26.666302,50.170192),
            (26.638626,50.159764),(26.640254,50.160540),(26.641882,50.161316),
            (26.643510,50.162092),(26.645138,50.162868),(26.646766,50.163644),
            (26.648394,50.164420),(26.650022,50.165196),(26.651650,50.165972),
            (26.653278,50.166748),(26.654906,50.167524),(26.656534,50.168300),
            (26.658162,50.169076),(26.659790,50.169852),(26.661418,50.170628),
            (26.663046,50.171404),(26.664674,50.172180),(26.666302,50.172956),
            (26.638626,50.162528),(26.640254,50.163304),(26.641882,50.164080),
            (26.643510,50.164856),(26.645138,50.165632),(26.646766,50.166408),
            (26.648394,50.167184),(26.650022,50.167960),(26.651650,50.168736),
            (26.653278,50.169512),(26.654906,50.170288),(26.656534,50.171064),
        ])
    ],
}


# ── SH token ─────────────────────────────────────────────────────────────────

def sh_token():
    import requests
    r = requests.post(
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
        data={"grant_type":"client_credentials","client_id":SH_CLIENT_ID,"client_secret":SH_CLIENT_SECRET},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


# ── SH fetch (same evalscript as ingest) ─────────────────────────────────────

_EVALSCRIPT = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""

def sh_request(token, bbox_wgs84, date_str, rel_orbit, direction, resolution=10):
    import requests as req
    d1 = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    lon0,lat0,lon1,lat1 = bbox_wgs84
    width  = int(abs(lon1-lon0)*111320*math.cos(math.radians((lat0+lat1)/2))/resolution)
    height = int(abs(lat1-lat0)*111320/resolution)
    payload = {
        "input": {"bounds": {"bbox": [lon0,lat0,lon1,lat1], "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
                  "data": [{"type": "sentinel-1-grd",
                             "dataFilter": {"orbitDirection": direction.upper(),
                                            "relativeOrbitNumber": rel_orbit,
                                            "resolution": "HIGH"},
                             "processing": {"orthorectify": True,
                                            "backCoeff": "GAMMA0_TERRAIN",
                                            "demInstance": "COPERNICUS_30",
                                            "speckleFilter": {"type": "NONE"}},
                             "timeRange": {"from": date_str+"T00:00:00Z", "to": d1+"T00:00:00Z"}}]},
        "output": {"width": width, "height": height,
                   "responses": [{"identifier": "default",
                                  "format": {"type": "image/tiff"}}]},
        "evalscript": _EVALSCRIPT,
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = req.post("https://sh.dataspace.copernicus.eu/api/v1/process",
                 json=payload, headers=headers, timeout=120)
    if r.status_code != 200:
        log(f"    SH {r.status_code}: {r.text[:200]}")
        return None, None
    import tifffile, io
    arr = tifffile.imread(io.BytesIO(r.content)).squeeze().astype(np.float32)
    return arr, (lon0,lat0,lon1,lat1,width,height)


# ── bbox from tanks ───────────────────────────────────────────────────────────

def bbox_for_tanks(tanks, buf_m):
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    buf_deg_lat = buf_m / 111320
    buf_deg_lon = buf_m / (111320 * math.cos(math.radians(np.mean(lats))))
    return (min(lons)-buf_deg_lon, min(lats)-buf_deg_lat,
            max(lons)+buf_deg_lon, max(lats)+buf_deg_lat)


# ── lonlat → pixel ────────────────────────────────────────────────────────────

def lonlat_to_pix(lon, lat, bbox, w, h):
    lon0,lat0,lon1,lat1 = bbox[:4]
    x = (lon - lon0) / (lon1 - lon0) * w
    y = (lat1 - lat) / (lat1 - lat0) * h
    return x, y


# ── range profile through tank centre ────────────────────────────────────────

def range_profile(img, tank, bbox, res_m, los_az):
    """
    Extract radial profile along LOS direction through tank centre.
    Returns (positions_m, dB_values) arrays of length 2*radius+buffer.
    """
    lon0,lat0,lon1,lat1,w,h = bbox
    cx, cy = lonlat_to_pix(tank["lon"], tank["lat"], (lon0,lat0,lon1,lat1), w, h)
    r_px   = tank["radius_m"] / res_m + 4   # sample ±(radius + 4px) along LOS

    # unit vector along LOS (SAR range direction = away from sensor)
    dx = math.sin(math.radians(los_az))
    dy = -math.cos(math.radians(los_az))

    n_steps = int(r_px * 2) + 1
    positions_m = []
    values_db   = []

    for i in range(n_steps):
        t = (i - n_steps // 2)   # steps from -r_px to +r_px
        px = int(round(cx + t * dx))
        py = int(round(cy + t * dy))
        if 0 <= px < w and 0 <= py < h:
            v = img[py, px]
            if np.isfinite(v) and v > 0:
                positions_m.append(t * res_m)
                values_db.append(10 * math.log10(float(v)))

    return np.array(positions_m), np.array(values_db)


# ── find two peaks ────────────────────────────────────────────────────────────

def find_two_peaks(pos_m, db_vals, tank_radius_m):
    """
    Find roof-wall (peak1) and base-wall (peak2) peaks on the range profile.
    Both peaks should be within 0..radius_m from centre on the far-wall side.
    Returns (p1_pos, p2_pos, gap_m, p1_db, p2_db) or None if not enough peaks.
    """
    if len(db_vals) < 4:
        return None

    # far-wall side: positions > 0 (in LOS direction)
    mask = pos_m >= 0
    if mask.sum() < 4:
        return None

    sub_pos = pos_m[mask]
    sub_db  = db_vals[mask]

    # find peaks with minimum prominence
    median_db = float(np.median(sub_db))
    peaks_idx, props = find_peaks(sub_db, prominence=1.5, distance=2)

    if len(peaks_idx) < 2:
        return None

    # sort by position (near first)
    sorted_peaks = sorted(peaks_idx, key=lambda i: sub_pos[i])

    p2_idx = sorted_peaks[0]   # base-wall: closer to centre
    p1_idx = sorted_peaks[1]   # roof-wall: further from centre

    p1_pos = float(sub_pos[p1_idx])
    p2_pos = float(sub_pos[p2_idx])
    gap    = abs(p1_pos - p2_pos)
    p1_db  = float(sub_db[p1_idx])
    p2_db  = float(sub_db[p2_idx])

    # sanity: gap should be between 1m and tank_radius_m
    if not (1.0 <= gap <= tank_radius_m):
        return None

    return p1_pos, p2_pos, gap, p1_db, p2_db


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    log(f"p9_peak_gap.py  start {datetime.now(timezone.utc).isoformat()}")
    token = sh_token()
    log("  token OK")

    out_rows = []

    for terminal_id, cfg in PILOT_SCENES.items():
        tanks     = _TANKS_INLINE[terminal_id]
        dates     = cfg["dates"]
        rel_orbit = cfg["rel_orbit"]
        direction = cfg["direction"]
        los_az    = cfg["los_azimuth"]

        bbox = bbox_for_tanks(tanks, BUFFER_M)
        log(f"\n  Terminal: {terminal_id}  tanks={len(tanks)}  dates={len(dates)}")

        for di, date in enumerate(dates):
            log(f"  [{di+1}/{len(dates)}] {date}")

            arr, bbox_info = sh_request(token, bbox, date, rel_orbit, direction, resolution=10)
            if arr is None:
                log(f"    no data")
                continue

            lon0,lat0,lon1,lat1,w,h = bbox_info
            hit = 0
            for tank in tanks:
                pos_m, db_vals = range_profile(arr, tank, bbox_info, 10, los_az)
                result = find_two_peaks(pos_m, db_vals, tank["radius_m"])
                if result is None:
                    out_rows.append(dict(
                        terminal=terminal_id, tank_id=tank["id"], date=date,
                        peak1_pos_m="", peak2_pos_m="", peak_gap_m="",
                        peak1_dB="", peak2_dB="",
                    ))
                else:
                    p1_pos, p2_pos, gap, p1_db, p2_db = result
                    hit += 1
                    out_rows.append(dict(
                        terminal=terminal_id, tank_id=tank["id"], date=date,
                        peak1_pos_m=f"{p1_pos:.1f}", peak2_pos_m=f"{p2_pos:.1f}",
                        peak_gap_m=f"{gap:.1f}", peak1_dB=f"{p1_db:.2f}", peak2_dB=f"{p2_db:.2f}",
                    ))
            total = len(tanks)
            log(f"    peaks found: {hit}/{total}  ({100*hit//total}%)")

    out_path = OUT_DIR / "peak_gap_pilot.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["terminal","tank_id","date",
                                          "peak1_pos_m","peak2_pos_m","peak_gap_m",
                                          "peak1_dB","peak2_dB"])
        w.writeheader()
        w.writerows(out_rows)

    # quality report
    log(f"\n=== Quality report: non-null peak_gap_m per terminal ===")
    from collections import Counter
    for term in ["kharg","ras_tanura"]:
        rows_t = [r for r in out_rows if r["terminal"] == term]
        non_null = sum(1 for r in rows_t if r["peak_gap_m"] != "")
        total    = len(rows_t)
        log(f"  {term:<12}  {non_null:4}/{total}  {non_null/total:.3f}")

    log(f"\nDONE  {datetime.now(timezone.utc).isoformat()}")
    _lf.close()


if __name__ == "__main__":
    main()
