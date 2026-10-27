"""
p8b_geoloc_png.py — P8 geolocation verification + amplitude PNG

Steps:
  1. Read committed annotation XML, parse GCPs + pixel spacings
  2. Build bilinear (lat,lon) → (line_global, sample) interpolator
  3. Geolocate 5 reference tanks + coastline, check in-burst bounds
  4. Download burst bytes (Range), compute amplitude
  5. PNG-A: SLC amplitude ±800m around Kharg centroid, all-47 tank circles
  6. PNG-B: GRD (SH) same date, same extent, all-47 tank circles

Outputs (diagnostics/mega/):
  p8b_geoloc_<ts>.txt
  p8b_slc_amplitude_<ts>.png
  p8b_grd_comparison_<ts>.png
"""

import csv, io, math, os, struct, sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR   = REPO_ROOT / "diagnostics" / "mega"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TS  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
LOG = OUT_DIR / f"p8b_geoloc_{TS}.txt"
_lf = open(LOG, "w", buffering=1)

def log(msg):
    print(msg, flush=True)
    _lf.write(msg + "\n")

# ── credentials ──────────────────────────────────────────────────────────────

CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]

CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CDSE_CATALOG   = "https://catalogue.dataspace.copernicus.eu/odata/v1"
CDSE_DL_BASE   = "https://download.dataspace.copernicus.eu/odata/v1"
SH_PROCESS     = "https://sh.dataspace.copernicus.eu/api/v1/process"

PILOT_DATE  = "2024-10-03"
# Correct farm centre (tank_registry.py bounding box: lat 29.2217-29.2391, lon 50.3068-50.3166)
KHARG_LAT   = 29.2296
KHARG_LON   = 50.3126
REL_ORBIT   = 35
DIRECTION   = "DESCENDING"

# annotation XML already committed
ANNOT_XML = OUT_DIR / "s1a-iw2-slc-vv-20241003t023931-20241003t023956-055932-06d6a2-005.xml"

# 5 reference tanks + coastline — coordinates from tank_registry.py
CHECK_POINTS = [
    {"id": "t60_1",  "lat": 29.221638820210273, "lon": 50.315043243946754},
    {"id": "t55_5",  "lat": 29.23596908236423,  "lon": 50.31262041733899},
    {"id": "t55_17", "lat": 29.23144045674206,  "lon": 50.308740382712706},
    {"id": "t60_2",  "lat": 29.221840470329855, "lon": 50.31352911803618},
    {"id": "t25_35", "lat": 29.226211162396392, "lon": 50.31288089406942},
    {"id": "coastline", "lat": 29.21, "lon": 50.26},
]

# All 47 Kharg tanks — ground-truth coordinates from tank_registry.py
ALL_KHARG = [
    # t60 cluster (5)
    {"id": "t60_0", "lat": 29.22173338156137,  "lon": 50.31654819949878,  "radius_m": 60},
    {"id": "t60_1", "lat": 29.221638820210273, "lon": 50.315043243946754, "radius_m": 60},
    {"id": "t60_2", "lat": 29.221840470329855, "lon": 50.31352911803618,  "radius_m": 60},
    {"id": "t60_3", "lat": 29.223156804822317, "lon": 50.315849029771684, "radius_m": 60},
    {"id": "t60_4", "lat": 29.222897954869325, "lon": 50.3144195158663,   "radius_m": 60},
    # t55 cluster (17)
    {"id": "t55_5",  "lat": 29.23596908236423,  "lon": 50.31262041733899,  "radius_m": 55},
    {"id": "t55_6",  "lat": 29.236450714039865, "lon": 50.31093940154909,  "radius_m": 55},
    {"id": "t55_7",  "lat": 29.237953299445927, "lon": 50.31084946173491,  "radius_m": 55},
    {"id": "t55_8",  "lat": 29.239068986858886, "lon": 50.30968216852048,  "radius_m": 55},
    {"id": "t55_9",  "lat": 29.238926386438376, "lon": 50.306814019231595, "radius_m": 55},
    {"id": "t55_10", "lat": 29.237608159424354, "lon": 50.30726790052075,  "radius_m": 55},
    {"id": "t55_11", "lat": 29.236277626607237, "lon": 50.307724616432196, "radius_m": 55},
    {"id": "t55_12", "lat": 29.23632825886285,  "lon": 50.30930379724654,  "radius_m": 55},
    {"id": "t55_13", "lat": 29.234972729277782, "lon": 50.3081865326845,   "radius_m": 55},
    {"id": "t55_14", "lat": 29.23511372342508,  "lon": 50.30981988700097,  "radius_m": 55},
    {"id": "t55_15", "lat": 29.233643820114974, "lon": 50.30863801777735,  "radius_m": 55},
    {"id": "t55_16", "lat": 29.233830846966335, "lon": 50.31028094525233,  "radius_m": 55},
    {"id": "t55_17", "lat": 29.23144045674206,  "lon": 50.308740382712706, "radius_m": 55},
    {"id": "t55_18", "lat": 29.231783644202153, "lon": 50.310385461009616, "radius_m": 55},
    {"id": "t55_19", "lat": 29.232076421746672, "lon": 50.312234207587345, "radius_m": 55},
    {"id": "t55_20", "lat": 29.23021527314446,  "lon": 50.3101614506557,   "radius_m": 55},
    {"id": "t55_21", "lat": 29.228871179764596, "lon": 50.309966015820464, "radius_m": 55},
    # t40 cluster (9)
    {"id": "t40_22", "lat": 29.22952889859548,  "lon": 50.312187062458655, "radius_m": 40},
    {"id": "t40_23", "lat": 29.22976318244905,  "lon": 50.313501526357314, "radius_m": 40},
    {"id": "t40_24", "lat": 29.229963868790236, "lon": 50.31481471193477,  "radius_m": 40},
    {"id": "t40_25", "lat": 29.228423649589555, "lon": 50.31244141506387,  "radius_m": 40},
    {"id": "t40_26", "lat": 29.228616543309535, "lon": 50.313733669284346, "radius_m": 40},
    {"id": "t40_27", "lat": 29.22881135666992,  "lon": 50.31501996070619,  "radius_m": 40},
    {"id": "t40_28", "lat": 29.22720915866873,  "lon": 50.312698386632235, "radius_m": 40},
    {"id": "t40_29", "lat": 29.227435720069668, "lon": 50.31399368455909,  "radius_m": 40},
    {"id": "t40_30", "lat": 29.227640813983122, "lon": 50.31526721678495,  "radius_m": 40},
    # t55_31 strategic reserve
    {"id": "t55_31", "lat": 29.232346117474552, "lon": 50.313766336270064, "radius_m": 55},
    # t40 additions (3)
    {"id": "t40_32", "lat": 29.230717804109947, "lon": 50.31192906218232,  "radius_m": 40},
    {"id": "t40_33", "lat": 29.230947410364493, "lon": 50.31324186520479,  "radius_m": 40},
    {"id": "t40_34", "lat": 29.231135117157258, "lon": 50.31454377331308,  "radius_m": 40},
    # t25 cluster (12)
    {"id": "t25_35", "lat": 29.226211162396392, "lon": 50.31288089406942,  "radius_m": 25},
    {"id": "t25_36", "lat": 29.226383532289553, "lon": 50.313809940357444, "radius_m": 25},
    {"id": "t25_37", "lat": 29.226542892768347, "lon": 50.31474811903658,  "radius_m": 25},
    {"id": "t25_38", "lat": 29.226689095834363, "lon": 50.31565041182445,  "radius_m": 25},
    {"id": "t25_39", "lat": 29.22515641565613,  "lon": 50.31310639099006,  "radius_m": 25},
    {"id": "t25_40", "lat": 29.22532307473051,  "lon": 50.31405678957588,  "radius_m": 25},
    {"id": "t25_41", "lat": 29.225474230400657, "lon": 50.31496277701283,  "radius_m": 25},
    {"id": "t25_42", "lat": 29.22561536435356,  "lon": 50.315886541434466, "radius_m": 25},
    {"id": "t25_43", "lat": 29.22435349448338,  "lon": 50.313281303050275, "radius_m": 25},
    {"id": "t25_44", "lat": 29.224512933127766, "lon": 50.31422412891253,  "radius_m": 25},
    {"id": "t25_45", "lat": 29.224656215116465, "lon": 50.31513496008376,  "radius_m": 25},
    {"id": "t25_46", "lat": 29.22481810164328,  "lon": 50.316063441149545, "radius_m": 25},
]


# ── token ─────────────────────────────────────────────────────────────────────

def get_token():
    r = requests.post(CDSE_TOKEN_URL,
        data={"grant_type":"client_credentials",
              "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


# ── annotation XML parsing ────────────────────────────────────────────────────

def parse_annotation(xml_path):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    rps = float(root.findtext(".//rangePixelSpacing"))
    aps_az_raw = root.findtext(".//azimuthPixelSpacing")
    lpb = int(root.findtext(".//linesPerBurst"))
    spb = int(root.findtext(".//samplesPerBurst"))

    bursts   = root.findall(".//burst")
    n_bursts = len(bursts)

    gcps = []
    for pt in root.findall(".//geolocationGridPoint"):
        gcps.append({
            "line":  int(pt.findtext("line")),
            "pixel": int(pt.findtext("pixel")),
            "lat":   float(pt.findtext("latitude")),
            "lon":   float(pt.findtext("longitude")),
        })

    try:
        aps = float(aps_az_raw) if aps_az_raw else None
    except Exception:
        aps = None

    return {
        "rangePixelSpacing":   rps,
        "azimuthPixelSpacing": aps,
        "linesPerBurst":       lpb,
        "samplesPerBurst":     spb,
        "n_bursts":            n_bursts,
        "gcps":                gcps,
        "bursts":              bursts,
    }


# ── bilinear GCP interpolator ─────────────────────────────────────────────────

def build_gcp_interpolator(gcps):
    from scipy.interpolate import griddata
    pts     = np.array([[g["lat"], g["lon"]] for g in gcps])
    lines   = np.array([g["line"]  for g in gcps], dtype=np.float64)
    samples = np.array([g["pixel"] for g in gcps], dtype=np.float64)

    def interp(lat, lon):
        q  = np.array([[lat, lon]])
        li = griddata(pts, lines,   q, method="linear")
        sa = griddata(pts, samples, q, method="linear")
        return float(li[0]), float(sa[0])

    return interp


# ── CDSE product search ───────────────────────────────────────────────────────

def search_slc(token, date_str):
    d1 = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    url = (f"{CDSE_CATALOG}/Products"
           f"?$filter=Collection/Name eq 'SENTINEL-1'"
           f" and Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'productType' and a/Value eq 'IW_SLC__1S')"
           f" and OData.CSC.Intersects(area=geography'SRID=4326;POINT({KHARG_LON} {KHARG_LAT})')"
           f" and Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'orbitDirection' and a/Value eq 'DESCENDING')"
           f" and ContentDate/Start ge {date_str}T00:00:00.000Z"
           f" and ContentDate/Start lt {d1}T00:00:00.000Z"
           f"&$top=5")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json().get("value", [])


# ── burst byte range ──────────────────────────────────────────────────────────

def burst_byte_range(xml_root, burst_idx):
    bursts = xml_root.findall(".//burst")
    b      = bursts[burst_idx]
    bo_el  = b.find("byteOffset")
    if bo_el is None:
        return None, None
    lpb  = int(xml_root.findtext(".//linesPerBurst"))
    spb  = int(xml_root.findtext(".//samplesPerBurst"))
    bo   = int(bo_el.text)
    size = lpb * spb * 4
    return bo, bo + size - 1


# ── download burst via Range ──────────────────────────────────────────────────

def download_burst_range(token, product_id, safe_name, tiff_name, byte_start, byte_end):
    url = (f"{CDSE_DL_BASE}/Products({product_id})"
           f"/Nodes({safe_name})/Nodes(measurement)/Nodes({tiff_name})/$value")
    headers = {"Authorization": f"Bearer {token}",
               "Range": f"bytes={byte_start}-{byte_end}"}
    log(f"  Range: bytes={byte_start}-{byte_end} ({(byte_end-byte_start+1)/1e6:.1f} MB)")
    r = requests.get(url, headers=headers, stream=True, timeout=600)
    if r.status_code not in (200, 206):
        log(f"  ERROR: HTTP {r.status_code}")
        return None
    chunks = []; n = 0
    for chunk in r.iter_content(chunk_size=10*1024*1024):
        chunks.append(chunk); n += len(chunk)
        log(f"  ... {n//1024//1024} MB")
    data = b"".join(chunks)
    log(f"  done: {len(data)/1e6:.1f} MB")
    return data


# ── SH GRD fetch ──────────────────────────────────────────────────────────────

_EVALSCRIPT = """
//VERSION=3
function setup(){return{input:[{bands:[\"VV\"]}],output:{bands:1,sampleType:\"FLOAT32\"}};}
function evaluatePixel(s){return[s.VV];}
"""

def sh_grd(token, bbox, date_str, resolution=10):
    import tifffile
    lon0, lat0, lon1, lat1 = bbox
    d1 = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    w  = int(abs(lon1-lon0)*111320*math.cos(math.radians((lat0+lat1)/2))/resolution)
    h  = int(abs(lat1-lat0)*111320/resolution)
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
        log(f"  SH GRD error {r.status_code}: {r.text[:200]}")
        return None, w, h
    arr = tifffile.imread(io.BytesIO(r.content)).squeeze().astype(np.float32)
    return arr, w, h


# ── plot helpers ──────────────────────────────────────────────────────────────

def plot_slc(amp, burst_idx, lpb, rps, aps, interp, tanks,
             center_line_b, center_sample, half_az, half_rg, out_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    l0 = max(0, int(center_line_b  - half_az))
    l1 = min(amp.shape[0], int(center_line_b  + half_az))
    s0 = max(0, int(center_sample  - half_rg))
    s1 = min(amp.shape[1], int(center_sample  + half_rg))
    crop = amp[l0:l1, s0:s1]

    db = 10 * np.log10(np.clip(crop, 1e-6, None))
    vmin, vmax = np.percentile(db, [2, 98])

    extent = [s0*rps, s1*rps, l1*aps, l0*aps]

    fig, ax = plt.subplots(figsize=(14, 7))
    im = ax.imshow(db, extent=extent, cmap="gray", vmin=vmin, vmax=vmax,
                   aspect="equal", origin="upper", interpolation="nearest")
    plt.colorbar(im, ax=ax, label="Amplitude (dB)", fraction=0.02)

    n_drawn = 0
    for t in tanks:
        li_g, sa = interp(t["lat"], t["lon"])
        if np.isnan(li_g) or np.isnan(sa):
            continue
        li_b = li_g - burst_idx * lpb
        cx = sa   * rps
        cy = li_b * aps
        if s0*rps <= cx <= s1*rps and l0*aps <= cy <= l1*aps:
            circ = mpatches.Circle((cx, cy), t["radius_m"],
                                   edgecolor="cyan", facecolor="none", lw=0.8)
            ax.add_patch(circ)
            n_drawn += 1

    ax.set_xlabel("Range (m, slant)")
    ax.set_ylabel("Azimuth (m)")
    ax.set_title(f"SLC IW2/VV amplitude — {PILOT_DATE} — burst {burst_idx}  [{n_drawn}/{len(tanks)} tanks in frame]\n"
                 f"range {rps:.3f} m/px  azimuth {aps:.3f} m/px  [cyan = tank registry]")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    log(f"  saved: {out_path.name}  ({n_drawn} circles drawn)")


def plot_grd(arr_grd, bbox_deg, tanks, out_path, date_str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    lon0, lat0, lon1, lat1 = bbox_deg
    h, w = arr_grd.shape
    res_m = (lon1 - lon0) / w * 111320 * math.cos(math.radians((lat0+lat1)/2))

    db = 10 * np.log10(np.clip(arr_grd, 1e-10, None))
    db[~np.isfinite(db)] = np.nan
    vmin, vmax = np.nanpercentile(db, [2, 98])

    fig, ax = plt.subplots(figsize=(10, 10))
    im = ax.imshow(np.flipud(db), extent=[lon0, lon1, lat0, lat1],
                   cmap="gray", vmin=vmin, vmax=vmax, aspect="equal",
                   origin="lower", interpolation="nearest")
    plt.colorbar(im, ax=ax, label="Amplitude (dB)", fraction=0.02)

    n_drawn = 0
    for t in tanks:
        if not (lon0 <= t["lon"] <= lon1 and lat0 <= t["lat"] <= lat1):
            continue
        r_deg_lon = t["radius_m"] / (111320 * math.cos(math.radians(t["lat"])))
        r_deg_lat = t["radius_m"] / 111320
        circ = mpatches.Ellipse((t["lon"], t["lat"]),
                                 width=2*r_deg_lon, height=2*r_deg_lat,
                                 edgecolor="cyan", facecolor="none", lw=0.8)
        ax.add_patch(circ)
        n_drawn += 1

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(f"GRD Γ₀ VV — {date_str} — {res_m:.0f} m/px  [{n_drawn}/{len(tanks)} tanks in frame]\n"
                 f"[cyan = tank registry, same extent as SLC]")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    log(f"  saved: {out_path.name}  ({n_drawn} circles drawn)")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    log(f"p8b_geoloc_png.py  {datetime.now(timezone.utc).isoformat()}")
    log(f"Window centre: lat={KHARG_LAT}  lon={KHARG_LON}  (farm centroid from registry)")
    log(f"Tank count:    {len(ALL_KHARG)}")

    # ── Step 1: parse annotation ──────────────────────────────────────────────
    log(f"\n=== Step 1: parse annotation XML ===")
    if not ANNOT_XML.exists():
        log("  FATAL: annotation XML not found — run p8_slc_pilot.py first")
        sys.exit(1)

    info = parse_annotation(ANNOT_XML)
    rps  = info["rangePixelSpacing"]
    aps  = info["azimuthPixelSpacing"]
    lpb  = info["linesPerBurst"]
    spb  = info["samplesPerBurst"]
    gcps = info["gcps"]

    log(f"  rangePixelSpacing:   {rps:.6f} m")
    log(f"  linesPerBurst:       {lpb}")
    log(f"  samplesPerBurst:     {spb}")
    log(f"  n_bursts:            {info['n_bursts']}")
    log(f"  GCP count:           {len(gcps)}")

    if aps is None or aps == 0:
        ati_str = ET.parse(ANNOT_XML).getroot().findtext(".//azimuthTimeInterval")
        aps = float(ati_str) * 7300.0
        log(f"  azimuthPixelSpacing (derived from ati×v): {aps:.4f} m")
    else:
        log(f"  azimuthPixelSpacing: {aps:.6f} m")

    # ── Step 2: build GCP interpolator ───────────────────────────────────────
    log(f"\n=== Step 2: build GCP interpolator ===")
    interp = build_gcp_interpolator(gcps)

    li_c, sa_c = interp(KHARG_LAT, KHARG_LON)
    burst_idx  = int(li_c // lpb)
    log(f"  Farm centre ({KHARG_LAT},{KHARG_LON}) → line_global={li_c:.1f}  sample={sa_c:.1f}  burst_idx={burst_idx}")

    center_line_b = li_c - burst_idx * lpb
    center_sample = sa_c
    half_az = 800.0 / aps
    half_rg = 800.0 / rps
    log(f"  ±800m crop: ±{half_az:.1f} lines (az)  ±{half_rg:.1f} samples (rg)")

    # ── Step 3: geolocate reference points ───────────────────────────────────
    log(f"\n=== Step 3: geolocate reference points ===")
    burst_line_min = burst_idx * lpb
    burst_line_max = (burst_idx + 1) * lpb - 1
    log(f"  burst {burst_idx}: global lines {burst_line_min}–{burst_line_max}, samples 0–{spb-1}")
    log(f"  {'Point':<12}  {'lat':>9}  {'lon':>9}  {'line_g':>8}  {'sample':>8}  {'in_burst':>10}  {'line_b':>7}")
    for pt in CHECK_POINTS:
        li, sa = interp(pt["lat"], pt["lon"])
        in_b   = (burst_line_min <= li <= burst_line_max and 0 <= sa <= spb-1)
        lib    = li - burst_idx * lpb
        flag   = "YES" if in_b else "NO "
        log(f"  {pt['id']:<12}  {pt['lat']:>9.5f}  {pt['lon']:>9.5f}  "
            f"{li:>8.1f}  {sa:>8.1f}  {flag:>10}  {lib:>7.1f}")

    # ── Step 4: get token + find SLC product ──────────────────────────────────
    log(f"\n=== Step 4: CDSE token + SLC product ===")
    token = get_token()
    log("  token OK")

    items = search_slc(token, PILOT_DATE)
    if not items:
        log("  FATAL: no SLC product found")
        sys.exit(1)

    prod = items[0]
    pid  = prod["Id"]
    safe = prod["Name"]
    log(f"  product: {pid}  {safe}")

    tiff_name = ANNOT_XML.name.replace(".xml", ".tiff")

    # ── Step 5: burst byte range + download ───────────────────────────────────
    log(f"\n=== Step 5: burst byte range ===")
    xml_root = ET.parse(ANNOT_XML).getroot()
    b_start, b_end = burst_byte_range(xml_root, burst_idx)
    if b_start is None:
        log("  byteOffset not in annotation — cannot download burst")
        sys.exit(1)
    log(f"  burst {burst_idx}: bytes {b_start}–{b_end}  ({(b_end-b_start+1)/1e6:.1f} MB)")

    log(f"\n=== Step 6: download burst ===")
    burst_data = download_burst_range(token, pid, safe, tiff_name, b_start, b_end)
    if burst_data is None:
        sys.exit(1)

    expected = lpb * spb * 4
    log(f"  expected {expected} bytes, got {len(burst_data)}")

    arr16 = np.frombuffer(burst_data, dtype=np.int16).reshape(lpb, spb, 2)
    z     = arr16[:, :, 0].astype(np.float32) + 1j * arr16[:, :, 1].astype(np.float32)
    amp   = np.abs(z)
    log(f"  amplitude shape={amp.shape}  mean={amp.mean():.2f}  max={amp.max():.2f}")

    # ── Step 7: SLC amplitude PNG ─────────────────────────────────────────────
    log(f"\n=== Step 7: SLC amplitude PNG ===")
    slc_png = OUT_DIR / f"p8b_slc_amplitude_{TS}.png"
    plot_slc(amp, burst_idx, lpb, rps, aps, interp, ALL_KHARG,
             center_line_b, center_sample, half_az, half_rg, slc_png)

    # ── Step 8: GRD comparison PNG ────────────────────────────────────────────
    log(f"\n=== Step 8: GRD (SH) comparison ===")
    buf_deg_lat = 800 / 111320
    buf_deg_lon = 800 / (111320 * math.cos(math.radians(KHARG_LAT)))
    bbox_grd = (KHARG_LON - buf_deg_lon, KHARG_LAT - buf_deg_lat,
                KHARG_LON + buf_deg_lon, KHARG_LAT + buf_deg_lat)
    log(f"  GRD bbox: lon {bbox_grd[0]:.5f}–{bbox_grd[2]:.5f}  lat {bbox_grd[1]:.5f}–{bbox_grd[3]:.5f}")
    arr_grd, gw, gh = sh_grd(token, bbox_grd, PILOT_DATE, resolution=10)
    if arr_grd is None:
        log("  SH GRD failed — skipping comparison PNG")
    else:
        log(f"  GRD shape={arr_grd.shape}  res≈10m")
        grd_png = OUT_DIR / f"p8b_grd_comparison_{TS}.png"
        plot_grd(arr_grd, bbox_grd, ALL_KHARG, grd_png, PILOT_DATE)

    log(f"\nDONE  {datetime.now(timezone.utc).isoformat()}")
    _lf.close()


if __name__ == "__main__":
    main()
