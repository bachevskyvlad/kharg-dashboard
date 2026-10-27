"""
p8b_geoloc_png.py — P8 geolocation verification + amplitude PNG

Steps:
  1. Read committed annotation XML, parse GCPs + pixel spacings
  2. Build bilinear (lat,lon) → (line_global, sample) interpolator
  3. Geolocate 5 reference tanks + coastline, check in-burst bounds
  4. Download burst bytes (Range), compute amplitude
  5. PNG-A: SLC amplitude ±500m around Kharg centroid, tank circles overlay
  6. PNG-B: GRD (SH) same date, same extent, tank circles overlay

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
KHARG_LAT   = 29.23
KHARG_LON   = 50.33
REL_ORBIT   = 35
DIRECTION   = "DESCENDING"

# annotation XML already committed
ANNOT_XML = OUT_DIR / "s1a-iw2-slc-vv-20241003t023931-20241003t023956-055932-06d6a2-005.xml"

# 5 reference tanks + coastline point
CHECK_POINTS = [
    {"id": "t60_1",  "lat": 29.22422756973714, "lon": 50.31692094143066},
    {"id": "t55_5",  "lat": 29.23062747,        "lon": 50.32382152},
    {"id": "t55_17", "lat": 29.23720187,        "lon": 50.33057032},
    {"id": "t60_2",  "lat": 29.22671505899543,  "lon": 50.31729348356078},
    {"id": "t25_0",  "lat": 29.22488648,        "lon": 50.30613213},
    {"id": "coastline", "lat": 29.21,           "lon": 50.26},
]

# All 47 tanks for circle overlay (subset of kharg)
ALL_KHARG = [
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

    # pixel spacings
    rps = float(root.findtext(".//rangePixelSpacing"))
    aps_az_raw = root.findtext(".//azimuthPixelSpacing")
    # azimuth time interval → effective azimuth spacing
    ati = root.findtext(".//azimuthTimeInterval")
    prf = root.findtext(".//prf")  # may be absent for TOPS

    # lines/samples per burst
    lpb = int(root.findtext(".//linesPerBurst"))
    spb = int(root.findtext(".//samplesPerBurst"))

    # burst list → find burst covering Kharg
    bursts = root.findall(".//burst")
    n_bursts = len(bursts)

    # geolocationGridPointList
    gcps = []
    for pt in root.findall(".//geolocationGridPoint"):
        gcps.append({
            "line":   int(pt.findtext("line")),
            "pixel":  int(pt.findtext("pixel")),
            "lat":    float(pt.findtext("latitude")),
            "lon":    float(pt.findtext("longitude")),
        })

    # azimuthPixelSpacing may appear directly
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
    """
    Returns callable interp(lat, lon) → (line_global_float, sample_float)
    using scipy griddata bilinear over the GCP grid.
    """
    from scipy.interpolate import griddata
    pts      = np.array([[g["lat"], g["lon"]] for g in gcps])
    lines    = np.array([g["line"]  for g in gcps], dtype=np.float64)
    samples  = np.array([g["pixel"] for g in gcps], dtype=np.float64)

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
    items = r.json().get("value", [])
    return items


# ── burst byte range (from annotation XML byteOffset) ────────────────────────

def burst_byte_range(xml_root, burst_idx):
    bursts = xml_root.findall(".//burst")
    b = bursts[burst_idx]
    bo_el = b.find("byteOffset")
    if bo_el is None:
        return None, None
    lpb  = int(xml_root.findtext(".//linesPerBurst"))
    spb  = int(xml_root.findtext(".//samplesPerBurst"))
    bo   = int(bo_el.text)
    size = lpb * spb * 4   # complex int16 = 4 bytes/pixel
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
    chunks = []
    n = 0
    for chunk in r.iter_content(chunk_size=10*1024*1024):
        chunks.append(chunk)
        n += len(chunk)
        log(f"  ... {n//1024//1024} MB")
    data = b"".join(chunks)
    log(f"  done: {len(data)/1e6:.1f} MB")
    return data


# ── SH GRD fetch ──────────────────────────────────────────────────────────────

_EVALSCRIPT = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
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

def _add_tank_circles_slc(ax, tanks, interp, burst_idx, lpb, rps, aps):
    """Overlay tank circles on SLC amplitude image (meters axes)."""
    import matplotlib.patches as mpatches
    for t in tanks:
        li_g, sa = interp(t["lat"], t["lon"])
        if np.isnan(li_g) or np.isnan(sa):
            continue
        li_b = li_g - burst_idx * lpb   # burst-local line
        # centre in meters (from crop origin, added in caller)
        ax._tank_slc_pts = getattr(ax, "_tank_slc_pts", [])
        ax._tank_slc_pts.append((li_b * aps, sa * rps, t["radius_m"]))


def plot_slc(amp, burst_idx, lpb, rps, aps, interp, tanks,
             center_line_b, center_sample, half_az, half_rg, out_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    # crop window
    l0 = max(0, int(center_line_b  - half_az))
    l1 = min(amp.shape[0], int(center_line_b  + half_az))
    s0 = max(0, int(center_sample  - half_rg))
    s1 = min(amp.shape[1], int(center_sample  + half_rg))
    crop = amp[l0:l1, s0:s1]

    db = 10 * np.log10(np.clip(crop, 1e-6, None))
    vmin, vmax = np.percentile(db, [2, 98])

    # extent in meters from crop origin
    extent = [s0*rps, s1*rps, l1*aps, l0*aps]

    fig, ax = plt.subplots(figsize=(12, 5))
    im = ax.imshow(db, extent=extent, cmap="gray", vmin=vmin, vmax=vmax,
                   aspect="equal", origin="upper", interpolation="nearest")
    plt.colorbar(im, ax=ax, label="Amplitude (dB)", fraction=0.02)

    # tank circles
    for t in tanks:
        li_g, sa = interp(t["lat"], t["lon"])
        if np.isnan(li_g) or np.isnan(sa):
            continue
        li_b = li_g - burst_idx * lpb
        cx = sa  * rps
        cy = li_b * aps
        if s0*rps <= cx <= s1*rps and l0*aps <= cy <= l1*aps:
            circ = mpatches.Circle((cx, cy), t["radius_m"],
                                   edgecolor="cyan", facecolor="none", lw=0.8)
            ax.add_patch(circ)

    ax.set_xlabel("Range (m, slant)")
    ax.set_ylabel("Azimuth (m)")
    ax.set_title(f"SLC IW2/VV amplitude — {PILOT_DATE} — burst {burst_idx}\n"
                 f"range px {rps:.3f} m  azimuth px {aps:.3f} m  "
                 f"[cyan circles = tank registry]")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    log(f"  saved: {out_path.name}")


def plot_grd(arr_grd, bbox_deg, tanks, out_path, date_str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    lon0, lat0, lon1, lat1 = bbox_deg
    h, w = arr_grd.shape
    res_lon = (lon1 - lon0) / w
    res_lat = (lat1 - lat0) / h
    res_m   = res_lon * 111320 * math.cos(math.radians((lat0+lat1)/2))

    db = 10 * np.log10(np.clip(arr_grd, 1e-10, None))
    db[~np.isfinite(db)] = np.nan
    vmin, vmax = np.nanpercentile(db, [2, 98])

    fig, ax = plt.subplots(figsize=(8, 8))
    im = ax.imshow(np.flipud(db), extent=[lon0, lon1, lat0, lat1],
                   cmap="gray", vmin=vmin, vmax=vmax, aspect="equal",
                   origin="lower", interpolation="nearest")
    plt.colorbar(im, ax=ax, label="Amplitude (dB)", fraction=0.02)

    for t in tanks:
        if not (lon0 <= t["lon"] <= lon1 and lat0 <= t["lat"] <= lat1):
            continue
        r_deg_lon = t["radius_m"] / (111320 * math.cos(math.radians(t["lat"])))
        r_deg_lat = t["radius_m"] / 111320
        circ = mpatches.Ellipse((t["lon"], t["lat"]),
                                 width=2*r_deg_lon, height=2*r_deg_lat,
                                 edgecolor="cyan", facecolor="none", lw=0.8)
        ax.add_patch(circ)

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(f"GRD Γ₀ VV — {date_str} — {res_m:.0f} m/px\n"
                 f"[cyan circles = tank registry, same extent as SLC]")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    log(f"  saved: {out_path.name}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    log(f"p8b_geoloc_png.py  {datetime.now(timezone.utc).isoformat()}")

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
    log(f"  azimuthPixelSpacing: {aps if aps else 'not in XML (derived below)'}")
    log(f"  linesPerBurst:       {lpb}")
    log(f"  samplesPerBurst:     {spb}")
    log(f"  n_bursts:            {info['n_bursts']}")
    log(f"  GCP count:           {len(gcps)}")

    # If azimuth spacing missing, derive from azimuthTimeInterval × velocity ≈ 7300 m/s
    if aps is None or aps == 0:
        ati_str = ET.parse(ANNOT_XML).getroot().findtext(".//azimuthTimeInterval")
        aps = float(ati_str) * 7300.0
        log(f"  azimuthPixelSpacing (derived from ati×v): {aps:.4f} m")
    else:
        log(f"  azimuthPixelSpacing: {aps:.6f} m")

    # ── Step 2: build GCP interpolator ───────────────────────────────────────
    log(f"\n=== Step 2: build GCP interpolator ===")
    interp = build_gcp_interpolator(gcps)

    # find burst index for Kharg using GCPs (same logic as p8 pilot)
    li_c, sa_c = interp(KHARG_LAT, KHARG_LON)
    burst_idx = int(li_c // lpb)
    log(f"  Kharg centre → line_global={li_c:.1f}  sample={sa_c:.1f}  burst_idx={burst_idx}")

    # burst-local line for centroid
    center_line_b  = li_c - burst_idx * lpb
    center_sample  = sa_c
    half_az = 500.0 / aps   # lines
    half_rg = 500.0 / rps   # samples
    log(f"  ±500m crop: ±{half_az:.1f} lines (az)  ±{half_rg:.1f} samples (rg)")

    # ── Step 3: geolocate reference points ───────────────────────────────────
    log(f"\n=== Step 3: geolocate reference points ===")
    burst_line_min = burst_idx * lpb
    burst_line_max = (burst_idx + 1) * lpb - 1
    log(f"  burst {burst_idx}: global lines {burst_line_min}–{burst_line_max}, "
        f"samples 0–{spb-1}")
    log(f"  {'Point':<12}  {'lat':>8}  {'lon':>8}  "
        f"{'line_g':>8}  {'sample':>8}  {'in_burst':>10}  {'line_b':>7}")
    for pt in CHECK_POINTS:
        li, sa = interp(pt["lat"], pt["lon"])
        in_b = (burst_line_min <= li <= burst_line_max and 0 <= sa <= spb-1)
        lib  = li - burst_idx * lpb
        flag = "YES" if in_b else "NO "
        log(f"  {pt['id']:<12}  {pt['lat']:>8.5f}  {pt['lon']:>8.5f}  "
            f"{li:>8.1f}  {sa:>8.1f}  {flag:>10}  {lib:>7.1f}")

    # ── Step 4: get token + find SLC product ──────────────────────────────────
    log(f"\n=== Step 4: CDSE token + SLC product ===")
    token = get_token()
    log("  token OK")

    items = search_slc(token, PILOT_DATE)
    if not items:
        log("  FATAL: no SLC product found")
        sys.exit(1)

    prod  = items[0]
    pid   = prod["Id"]
    safe  = prod["Name"]
    log(f"  product: {pid}  {safe}")

    # tiff name for IW2/VV
    tiff_name = ANNOT_XML.name.replace(".xml", ".tiff").replace(
        "s1a-iw2-slc-vv-", "s1a-iw2-slc-vv-")

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
    # ±600m bbox in degrees
    buf_deg_lat = 600 / 111320
    buf_deg_lon = 600 / (111320 * math.cos(math.radians(KHARG_LAT)))
    bbox_grd = (KHARG_LON - buf_deg_lon, KHARG_LAT - buf_deg_lat,
                KHARG_LON + buf_deg_lon, KHARG_LAT + buf_deg_lat)
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
