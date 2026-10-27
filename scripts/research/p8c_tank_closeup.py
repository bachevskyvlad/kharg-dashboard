"""
p8c_tank_closeup.py — P8 Step 3: per-tank close-up + range profiles

For 4 tanks on two dates (2024-09-21 before, 2024-10-03 after):
  1. Download SLC IW2/VV burst for each date
  2. Close-up PNG ±120m, aspect-corrected (azimuth stretched to match range scale)
  3. Range profile at 1m step, ±1.5×radius → CSV + overlaid plot

Also retries the GRD comparison PNG (SH error 500 on first attempt).
"""

import csv, io, math, os, re, struct, sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR   = REPO_ROOT / "diagnostics" / "mega"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TS  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
LOG = OUT_DIR / f"p8c_tank_closeup_{TS}.txt"
_lf = open(LOG, "w", buffering=1)

def log(msg):
    print(msg, flush=True)
    _lf.write(msg + "\n")

# ── credentials ───────────────────────────────────────────────────────────────
CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]

CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CDSE_CATALOG   = "https://catalogue.dataspace.copernicus.eu/odata/v1"
CDSE_DL_BASE   = "https://download.dataspace.copernicus.eu/odata/v1"
SH_PROCESS     = "https://sh.dataspace.copernicus.eu/api/v1/process"

KHARG_LAT  = 29.2296
KHARG_LON  = 50.3126
REL_ORBIT  = 35
DIRECTION  = "DESCENDING"

DATES = ["2024-09-21", "2024-10-03"]

TARGET_TANK_IDS = ["t60_1", "t60_2", "t55_5", "t55_17"]

ALL_KHARG = [
    {"id": "t60_0",  "lat": 29.22173338156137,  "lon": 50.31654819949878,  "radius_m": 60},
    {"id": "t60_1",  "lat": 29.221638820210273, "lon": 50.315043243946754, "radius_m": 60},
    {"id": "t60_2",  "lat": 29.221840470329855, "lon": 50.31352911803618,  "radius_m": 60},
    {"id": "t60_3",  "lat": 29.223156804822317, "lon": 50.315849029771684, "radius_m": 60},
    {"id": "t60_4",  "lat": 29.222897954869325, "lon": 50.3144195158663,   "radius_m": 60},
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
    {"id": "t40_22", "lat": 29.22952889859548,  "lon": 50.312187062458655, "radius_m": 40},
    {"id": "t40_23", "lat": 29.22976318244905,  "lon": 50.313501526357314, "radius_m": 40},
    {"id": "t40_24", "lat": 29.229963868790236, "lon": 50.31481471193477,  "radius_m": 40},
    {"id": "t40_25", "lat": 29.228423649589555, "lon": 50.31244141506387,  "radius_m": 40},
    {"id": "t40_26", "lat": 29.228616543309535, "lon": 50.313733669284346, "radius_m": 40},
    {"id": "t40_27", "lat": 29.22881135666992,  "lon": 50.31501996070619,  "radius_m": 40},
    {"id": "t40_28", "lat": 29.22720915866873,  "lon": 50.312698386632235, "radius_m": 40},
    {"id": "t40_29", "lat": 29.227435720069668, "lon": 50.31399368455909,  "radius_m": 40},
    {"id": "t40_30", "lat": 29.227640813983122, "lon": 50.31526721678495,  "radius_m": 40},
    {"id": "t55_31", "lat": 29.232346117474552, "lon": 50.313766336270064, "radius_m": 55},
    {"id": "t40_32", "lat": 29.230717804109947, "lon": 50.31192906218232,  "radius_m": 40},
    {"id": "t40_33", "lat": 29.230947410364493, "lon": 50.31324186520479,  "radius_m": 40},
    {"id": "t40_34", "lat": 29.231135117157258, "lon": 50.31454377331308,  "radius_m": 40},
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
TANK_BY_ID = {t["id"]: t for t in ALL_KHARG}


# ── auth ──────────────────────────────────────────────────────────────────────

def get_token():
    r = requests.post(CDSE_TOKEN_URL,
        data={"grant_type": "client_credentials",
              "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


# ── CDSE search + annotation download ────────────────────────────────────────

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


def _list_nodes(token, url):
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code != 200:
        log(f"    _list_nodes HTTP {r.status_code}: {r.text[:300]}")
        return []
    d = r.json()
    items = d.get("value", d.get("result", []))
    return items  # return full dicts


def find_iw2_vv_annotation_node(token, product_id, safe_name):
    """Return (node_id, safe_node) for the IW2/VV annotation XML."""
    # safe_name from catalog already ends with .SAFE
    safe_node = safe_name if safe_name.endswith(".SAFE") else safe_name + ".SAFE"
    url = (f"{CDSE_DL_BASE}/Products({product_id})"
           f"/Nodes({safe_node})/Nodes(annotation)/Nodes")
    nodes = _list_nodes(token, url)
    log(f"    annotation nodes ({len(nodes)}): {[n.get('Id', '?') for n in nodes[:5]]}")
    for n in nodes:
        nid = n.get("Id", "")
        if re.search(r"iw2.*vv", nid, re.I) and nid.lower().endswith(".xml"):
            return nid, safe_node
    # fallback: any iw2 xml
    for n in nodes:
        nid = n.get("Id", "")
        if re.search(r"iw2", nid, re.I) and nid.lower().endswith(".xml"):
            log(f"    fallback iw2: {nid}")
            return nid, safe_node
    return None, safe_node


def download_annotation_xml(token, product_id, safe_node, xml_node):
    url = (f"{CDSE_DL_BASE}/Products({product_id})"
           f"/Nodes({safe_node})/Nodes(annotation)/Nodes({xml_node})/$value")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    return r.content


def parse_annotation(xml_bytes):
    root = ET.fromstring(xml_bytes)
    rps  = float(root.findtext(".//rangePixelSpacing"))
    lpb  = int(root.findtext(".//linesPerBurst"))
    spb  = int(root.findtext(".//samplesPerBurst"))
    aps_raw = root.findtext(".//azimuthPixelSpacing")
    if aps_raw:
        aps = float(aps_raw)
    else:
        ati = float(root.findtext(".//azimuthTimeInterval"))
        aps = ati * 7300.0
    gcps = [{"line":  int(pt.findtext("line")),
             "pixel": int(pt.findtext("pixel")),
             "lat":   float(pt.findtext("latitude")),
             "lon":   float(pt.findtext("longitude"))}
            for pt in root.findall(".//geolocationGridPoint")]
    n_bursts = len(root.findall(".//burst"))
    return {"rps": rps, "aps": aps, "lpb": lpb, "spb": spb,
            "gcps": gcps, "n_bursts": n_bursts, "root": root}


def build_interp(gcps):
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


def burst_byte_range(root, burst_idx):
    bursts = root.findall(".//burst")
    b      = bursts[burst_idx]
    bo_el  = b.find("byteOffset")
    if bo_el is None:
        return None, None
    lpb  = int(root.findtext(".//linesPerBurst"))
    spb  = int(root.findtext(".//samplesPerBurst"))
    bo   = int(bo_el.text)
    size = lpb * spb * 4
    return bo, bo + size - 1


def download_burst(token, product_id, safe_name, tiff_node, b_start, b_end):
    url = (f"{CDSE_DL_BASE}/Products({product_id})"
           f"/Nodes({safe_name})/Nodes(measurement)/Nodes({tiff_node})/$value")
    headers = {"Authorization": f"Bearer {token}",
               "Range": f"bytes={b_start}-{b_end}"}
    log(f"    Range bytes={b_start}-{b_end}  ({(b_end-b_start+1)/1e6:.1f} MB)")
    r = requests.get(url, headers=headers, stream=True, timeout=600)
    if r.status_code not in (200, 206):
        log(f"    HTTP {r.status_code}")
        return None
    chunks = []; n = 0
    for chunk in r.iter_content(chunk_size=10*1024*1024):
        chunks.append(chunk); n += len(chunk)
        log(f"    ... {n//1024//1024} MB")
    data = b"".join(chunks)
    log(f"    done {len(data)/1e6:.1f} MB")
    return data


# ── GRD retry ─────────────────────────────────────────────────────────────────

_EVALSCRIPT_GRD = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""

def sh_grd(token, date_str, resolution=10):
    import tifffile
    buf_deg_lat = 800 / 111320
    buf_deg_lon = 800 / (111320 * math.cos(math.radians(KHARG_LAT)))
    lon0 = KHARG_LON - buf_deg_lon; lon1 = KHARG_LON + buf_deg_lon
    lat0 = KHARG_LAT - buf_deg_lat; lat1 = KHARG_LAT + buf_deg_lat
    d1 = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    w  = int(abs(lon1-lon0)*111320*math.cos(math.radians(KHARG_LAT))/resolution)
    h  = int(abs(lat1-lat0)*111320/resolution)
    payload = {
        "input": {
            "bounds": {"bbox": [lon0, lat0, lon1, lat1],
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
                   "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
        "evalscript": _EVALSCRIPT_GRD,
    }
    r = requests.post(SH_PROCESS, json=payload,
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"},
                      timeout=120)
    if r.status_code != 200:
        log(f"  SH GRD {r.status_code}: {r.text[:300]}")
        return None, (lon0, lat0, lon1, lat1)
    arr = __import__("tifffile").imread(io.BytesIO(r.content)).squeeze().astype(np.float32)
    return arr, (lon0, lat0, lon1, lat1)


# ── close-up PNG ──────────────────────────────────────────────────────────────

def tank_closeup_png(amp_by_date, info_by_date, burst_idx_by_date, tank, out_path):
    """Two-panel PNG: left=2024-09-21, right=2024-10-03.
    Axes in metres (range horizontal, azimuth vertical).
    aspect="equal" — azimuth stretched to real-world scale.
    Satellite approach direction annotated."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    WIN_M = 120.0  # ±120 m window

    fig, axes = plt.subplots(1, 2, figsize=(12, 7))

    for ax, date in zip(axes, DATES):
        amp       = amp_by_date[date]
        info      = info_by_date[date]
        rps       = info["rps"]
        aps       = info["aps"]
        lpb       = info["lpb"]
        burst_idx = burst_idx_by_date[date]
        interp    = info["interp"]

        li_g, sa_c = interp(tank["lat"], tank["lon"])
        li_b = li_g - burst_idx * lpb

        half_rg_px = WIN_M / rps
        half_az_px = WIN_M / aps

        l0 = max(0, int(li_b - half_az_px))
        l1 = min(amp.shape[0], int(li_b + half_az_px) + 1)
        s0 = max(0, int(sa_c - half_rg_px))
        s1 = min(amp.shape[1], int(sa_c + half_rg_px) + 1)

        crop = amp[l0:l1, s0:s1]
        db   = 10 * np.log10(np.clip(crop, 1e-6, None))
        vmin, vmax = np.percentile(db, [1, 99])

        # extent in metres (range × azimuth) → aspect="equal" gives correct geometry
        ext = [(s0 - sa_c) * rps, (s1 - sa_c) * rps,
               (l1 - li_b) * aps, (l0 - li_b) * aps]

        ax.imshow(db, extent=ext, cmap="gray", vmin=vmin, vmax=vmax,
                  aspect="equal", origin="upper", interpolation="nearest")

        # tank circle
        circ = mpatches.Circle((0, 0), tank["radius_m"],
                                edgecolor="cyan", facecolor="none", lw=1.2)
        ax.add_patch(circ)
        ax.plot(0, 0, "+", color="cyan", ms=6, lw=0.8)

        # ±radius markers
        for sgn in (-1, 1):
            ax.axvline(sgn * tank["radius_m"], color="cyan", lw=0.5, ls="--", alpha=0.6)

        # satellite direction: for descending S-1 over Kharg, near range is to the west
        # In slant-range image, range axis runs left (near, ~W) to right (far, ~E)
        ax.annotate("", xy=(-WIN_M * 0.85, -WIN_M * 0.85),
                    xytext=(-WIN_M * 0.55, -WIN_M * 0.85),
                    arrowprops=dict(arrowstyle="->", color="yellow", lw=1.2))
        ax.text(-WIN_M * 0.85, -WIN_M * 0.77, "satellite", color="yellow",
                fontsize=7, ha="left")

        ax.set_xlim(-WIN_M, WIN_M)
        ax.set_ylim(WIN_M, -WIN_M)
        ax.set_xlabel("Range offset (m, slant)")
        ax.set_ylabel("Azimuth offset (m)")
        ax.set_title(f"{date}\nrps={rps:.3f}m  aps={aps:.3f}m")

    fig.suptitle(f"{tank['id']}  r={tank['radius_m']}m  [cyan circle = registry radius]", fontsize=12)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    log(f"  saved {out_path.name}")


# ── range profile ─────────────────────────────────────────────────────────────

def range_profile(amp, info, burst_idx, tank, date_str):
    """Extract amplitude profile along range through tank centre.
    Returns (dist_m array, db array) at 1m steps, ±1.5×radius."""
    rps   = info["rps"]
    aps   = info["aps"]
    lpb   = info["lpb"]
    interp = info["interp"]

    li_g, sa_c = interp(tank["lat"], tank["lon"])
    li_b = li_g - burst_idx * lpb
    row  = int(round(li_b))
    row  = max(1, min(amp.shape[0] - 2, row))

    # Average 3 azimuth lines to reduce speckle (14m res → centre ±1 line)
    amp_row = amp[row-1:row+2, :].mean(axis=0)

    win_m  = 1.5 * tank["radius_m"]
    dists  = np.arange(-win_m, win_m + 1.0, 1.0)
    samps  = sa_c + dists / rps

    in_bounds = (samps >= 0) & (samps < amp_row.shape[0] - 1)
    amp_interp = np.full(len(dists), np.nan)
    if in_bounds.any():
        xi = samps[in_bounds]
        lo = np.floor(xi).astype(int)
        frac = xi - lo
        amp_interp[in_bounds] = (amp_row[lo] * (1 - frac) + amp_row[lo + 1] * frac)

    db_interp = 10 * np.log10(np.clip(amp_interp, 1e-10, None))
    db_interp[~np.isfinite(db_interp)] = np.nan
    return dists, db_interp


def save_profile_csv(dists, db_arr, tank_id, date_str, out_path):
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tank_id", "date", "distance_m", "amplitude_db"])
        for d, v in zip(dists, db_arr):
            w.writerow([tank_id, date_str, f"{d:.1f}",
                        f"{v:.4f}" if np.isfinite(v) else ""])
    log(f"  CSV: {out_path.name}  ({len(dists)} rows)")


def plot_profiles(profiles_by_date, tank, out_path):
    """Overlay profiles from both dates on one figure."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"2024-09-21": "#5599ff", "2024-10-03": "#ff7755"}
    labels = {"2024-09-21": "2024-09-21 (before)", "2024-10-03": "2024-10-03 (after)"}

    fig, ax = plt.subplots(figsize=(10, 4))
    for date, (dists, dbs) in profiles_by_date.items():
        mask = np.isfinite(dbs)
        ax.plot(dists[mask], dbs[mask], color=colors[date], lw=1.2, label=labels[date])

    r = tank["radius_m"]
    for sgn in (-1, 1):
        ax.axvline(sgn * r, color="gray", lw=0.8, ls="--", label=f"±r={r}m" if sgn == 1 else "")
    ax.axvline(0, color="white", lw=0.5, ls=":")

    ax.set_xlabel("Distance from tank centre (m, range)")
    ax.set_ylabel("Amplitude (dB)")
    ax.set_title(f"{tank['id']}  r={r}m — range profile  (3-line az average)")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    log(f"  profile PNG: {out_path.name}")


# ── GRD comparison PNG ────────────────────────────────────────────────────────

def plot_grd(arr_grd, bbox_deg, tanks, out_path, date_str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    lon0, lat0, lon1, lat1 = bbox_deg
    db = 10 * np.log10(np.clip(arr_grd, 1e-10, None))
    db[~np.isfinite(db)] = np.nan
    vmin, vmax = np.nanpercentile(db, [2, 98])

    fig, ax = plt.subplots(figsize=(10, 10))
    ax.imshow(np.flipud(db), extent=[lon0, lon1, lat0, lat1],
              cmap="gray", vmin=vmin, vmax=vmax, aspect="equal",
              origin="lower", interpolation="nearest")
    n = 0
    for t in tanks:
        if not (lon0 <= t["lon"] <= lon1 and lat0 <= t["lat"] <= lat1):
            continue
        r_lon = t["radius_m"] / (111320 * math.cos(math.radians(t["lat"])))
        r_lat = t["radius_m"] / 111320
        circ  = mpatches.Ellipse((t["lon"], t["lat"]),
                                  width=2*r_lon, height=2*r_lat,
                                  edgecolor="cyan", facecolor="none", lw=0.8)
        ax.add_patch(circ); n += 1
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_title(f"GRD Γ₀ VV — {date_str} — ≈10m/px  [{n}/{len(tanks)} tanks]")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150); plt.close(fig)
    log(f"  saved {out_path.name}  ({n} circles)")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    log(f"p8c_tank_closeup.py  {datetime.now(timezone.utc).isoformat()}")

    token = get_token()
    log("token OK")

    amp_by_date       = {}
    info_by_date      = {}
    burst_idx_by_date = {}

    for date in DATES:
        log(f"\n=== {date} ===")
        items = search_slc(token, date)
        if not items:
            log(f"  FATAL: no SLC product for {date}")
            sys.exit(1)
        prod  = items[0]
        pid   = prod["Id"]
        safe  = prod["Name"]
        log(f"  {safe}")

        xml_node, safe_node = find_iw2_vv_annotation_node(token, pid, safe)
        if not xml_node:
            log("  FATAL: cannot find IW2/VV annotation node")
            sys.exit(1)
        log(f"  annotation node: {xml_node}")

        xml_bytes = download_annotation_xml(token, pid, safe_node, xml_node)
        info      = parse_annotation(xml_bytes)
        log(f"  rps={info['rps']:.3f}m  aps={info['aps']:.3f}m  "
            f"lpb={info['lpb']}  spb={info['spb']}  bursts={info['n_bursts']}")

        interp     = build_interp(info["gcps"])
        info["interp"] = interp
        li_c, sa_c = interp(KHARG_LAT, KHARG_LON)
        burst_idx  = int(li_c // info["lpb"])
        log(f"  farm centre → line_global={li_c:.1f}  sample={sa_c:.1f}  burst={burst_idx}")

        b_start, b_end = burst_byte_range(info["root"], burst_idx)
        if b_start is None:
            log("  FATAL: no byteOffset in annotation")
            sys.exit(1)

        tiff_node = xml_node.replace(".xml", ".tiff")
        raw = download_burst(token, pid, safe_node, tiff_node, b_start, b_end)
        if raw is None:
            log("  FATAL: burst download failed")
            sys.exit(1)

        arr16 = np.frombuffer(raw, dtype=np.int16).reshape(info["lpb"], info["spb"], 2)
        z     = arr16[:, :, 0].astype(np.float32) + 1j * arr16[:, :, 1].astype(np.float32)
        amp   = np.abs(z)
        log(f"  amp shape={amp.shape}  mean={amp.mean():.2f}  max={amp.max():.2f}")

        amp_by_date[date]       = amp
        info_by_date[date]      = info
        burst_idx_by_date[date] = burst_idx

    # ── close-ups + profiles ──────────────────────────────────────────────────
    for tid in TARGET_TANK_IDS:
        tank = TANK_BY_ID[tid]
        log(f"\n--- {tid}  r={tank['radius_m']}m ---")

        closeup_path = OUT_DIR / f"p8c_closeup_{tid}_{TS}.png"
        tank_closeup_png(amp_by_date, info_by_date, burst_idx_by_date, tank, closeup_path)

        profiles_by_date = {}
        for date in DATES:
            dists, dbs = range_profile(
                amp_by_date[date], info_by_date[date],
                burst_idx_by_date[date], tank, date)
            profiles_by_date[date] = (dists, dbs)
            csv_path = OUT_DIR / f"p8c_profile_{tid}_{date}_{TS}.csv"
            save_profile_csv(dists, dbs, tid, date, csv_path)

        prof_png = OUT_DIR / f"p8c_profile_{tid}_{TS}.png"
        plot_profiles(profiles_by_date, tank, prof_png)

    # ── GRD retry ─────────────────────────────────────────────────────────────
    log(f"\n=== GRD retry (2024-10-03) ===")
    arr_grd, bbox_grd = sh_grd(token, "2024-10-03", resolution=10)
    if arr_grd is not None:
        grd_png = OUT_DIR / f"p8c_grd_{TS}.png"
        plot_grd(arr_grd, bbox_grd, ALL_KHARG, grd_png, "2024-10-03")
    else:
        log("  GRD failed again — skip")

    log(f"\nDONE  {datetime.now(timezone.utc).isoformat()}")
    _lf.close()


if __name__ == "__main__":
    main()
