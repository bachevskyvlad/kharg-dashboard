"""
panels_v2.py — Al-Faw v2 per-tank SAR panels (24 tanks, chat delivery)
========================================================================
For each of the 24 af_* tanks, renders a 3-panel image:
  - SAR-A: most recent DESCENDING pass with valid data (≥18d before SAR-B)
  - SAR-B: most recent DESCENDING pass with valid data (working orbit)
  - S2:    most recent Sentinel-2 optical within 14d of SAR-B

Layout per tank:
  - 4-diameter window (8× r_fit across) centred on tank
  - Thin ring overlay at r_fit radius
  - Corner label on dark chip: "{tank_id}  {date}  db={db:.1f}dB"
  - 3 panels side-by-side: SAR-A | SAR-B | S2

Output: diagnostics/al_faw/panels_v2/{tank_id}.png  (24 files)
Requires: matplotlib, tifffile, requests, numpy, Pillow

Usage:
  python panels_v2.py [--tank af_0]  (single tank for test)
  python panels_v2.py                (all 24)
"""

import os, sys, math, io, time
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import requests

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.patches import Circle
    import tifffile
    from PIL import Image
except ImportError as e:
    print(f"Missing dep: {e}")
    print("pip install matplotlib tifffile Pillow")
    sys.exit(1)

SB_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or ""
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""

SH_TOKEN_URL   = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

TERMINAL_ID = "al_flaw"
ORBIT_DIR   = "DESCENDING"
PAGE_SIZE   = 1000

SAR_B_MIN_AGE_DAYS = 18  # SAR-A must be at least 18 days before SAR-B
S2_WINDOW_DAYS     = 14  # S2 must be within 14 days of SAR-B date
IMG_PX             = 128  # pixels per panel side

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw" / "panels_v2"
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


def sb_get(path, params=None):
    r = requests.get(f"{SB_URL}/rest/v1/{path}", params=params,
                     headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}, timeout=30)
    r.raise_for_status()
    return r.json()


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


def tank_bbox(tid, factor=4.0):
    """Bounding box for 4-diameter (8× r) window."""
    c   = TANK_COORDS[tid]
    r_m = R_FIT[tid] * factor  # 4 diameters = 4 * 2r
    dr_lat = r_m / 111320.0
    dr_lon = r_m / (111320.0 * math.cos(math.radians(c["lat"])))
    return [c["lon"] - dr_lon, c["lat"] - dr_lat, c["lon"] + dr_lon, c["lat"] + dr_lat]


def fetch_sar(tid, date_str, token, retries=2):
    """Fetch VV dB image array (IMG_PX × IMG_PX) for tank, return float32 arr or None."""
    bbox = tank_bbox(tid)
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
        "output": {"width": IMG_PX, "height": IMG_PX,
                   "responses": [{"identifier": "default",
                                  "format": {"type": "image/tiff"}}]},
        "evalscript": evalscript
    }
    for attempt in range(retries + 1):
        try:
            resp = requests.post(SH_PROCESS_URL, json=body,
                                 headers={"Authorization": f"Bearer {token}"}, timeout=90)
            if resp.status_code == 429:
                time.sleep(3 * (attempt + 1)); continue
            if resp.status_code != 200 or len(resp.content) < 16:
                if attempt < retries:
                    time.sleep(2); continue
                return None
            arr = tifffile.imread(io.BytesIO(resp.content)).astype(np.float32)
            if arr.ndim == 3:
                arr = arr[:, :, 0]
            return arr
        except Exception:
            if attempt < retries:
                time.sleep(2)
    return None


def fetch_s2_rgb(tid, date_str, token, window_days=S2_WINDOW_DAYS, retries=2):
    """Fetch Sentinel-2 L2A RGB (uint8 scaled) for date ±window. Returns H×W×3 uint8 or None."""
    bbox = tank_bbox(tid)
    d0   = datetime.strptime(date_str, "%Y-%m-%d")
    from_dt = (d0 - timedelta(days=window_days)).strftime("%Y-%m-%dT00:00:00Z")
    to_dt   = (d0 + timedelta(days=window_days)).strftime("%Y-%m-%dT23:59:59Z")

    evalscript = """
//VERSION=3
function setup(){
  return{input:[{bands:["B04","B03","B02"],units:"REFLECTANCE"}],
         output:{bands:3,sampleType:"UINT8"}};
}
function evaluatePixel(s){
  return [Math.round(Math.min(s.B04*3.5,1)*255),
          Math.round(Math.min(s.B03*3.5,1)*255),
          Math.round(Math.min(s.B02*3.5,1)*255)];
}"""
    body = {
        "input": {
            "bounds": {"bbox": bbox,
                       "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{"type": "sentinel-2-l2a", "dataFilter": {
                "timeRange": {"from": from_dt, "to": to_dt},
                "maxCloudCoverage": 20,
                "mosaickingOrder": "leastCC"
            }}]
        },
        "output": {"width": IMG_PX, "height": IMG_PX,
                   "responses": [{"identifier": "default",
                                  "format": {"type": "image/tiff"}}]},
        "evalscript": evalscript
    }
    for attempt in range(retries + 1):
        try:
            resp = requests.post(SH_PROCESS_URL, json=body,
                                 headers={"Authorization": f"Bearer {token}"}, timeout=90)
            if resp.status_code == 429:
                time.sleep(3 * (attempt + 1)); continue
            if resp.status_code != 200 or len(resp.content) < 16:
                if attempt < retries:
                    time.sleep(2); continue
                return None
            arr = tifffile.imread(io.BytesIO(resp.content)).astype(np.uint8)
            if arr.ndim == 2:
                arr = np.stack([arr, arr, arr], axis=-1)
            return arr
        except Exception:
            if attempt < retries:
                time.sleep(2)
    return None


def sar_to_display(arr):
    """Convert float32 dB array to uint8 [0,255] with robust stretch [-25, 0] dB."""
    a = arr.copy()
    lo, hi = -25.0, 0.0
    a = np.clip(a, lo, hi)
    a = ((a - lo) / (hi - lo) * 255).astype(np.uint8)
    return np.stack([a, a, a], axis=-1)


def ring_overlay(ax, img_px, r_fit_m, window_m):
    """Draw thin ring at r_fit on image axes."""
    # window_m = half-size of bounding box in metres
    # ring radius in pixels = r_fit_m / window_m * img_px/2
    r_px = (r_fit_m / window_m) * (img_px / 2)
    cx   = img_px / 2
    ring = Circle((cx, cx), r_px, fill=False, edgecolor="yellow",
                  linewidth=0.8, linestyle="-")
    ax.add_patch(ring)


def render_panel(ax, img_rgb, title, tid, date_str, db_val, r_fit, window_m, is_s2=False):
    ax.imshow(img_rgb, origin="upper")
    if not is_s2:
        ring_overlay(ax, IMG_PX, r_fit, window_m)
    ax.axis("off")
    # Corner label
    label = f"{tid}  {date_str}\n{f'db={db_val:.1f}dB' if db_val is not None else ''}"
    ax.text(3, IMG_PX - 4, label, fontsize=5.5, color="white",
            va="bottom", ha="left",
            bbox={"facecolor": "black", "alpha": 0.6, "pad": 1, "edgecolor": "none"})
    ax.set_title(title, fontsize=7, color="white", pad=2)


def render_blank(ax, msg):
    ax.imshow(np.zeros((IMG_PX, IMG_PX, 3), dtype=np.uint8))
    ax.text(IMG_PX//2, IMG_PX//2, msg, color="gray", fontsize=7,
            ha="center", va="center")
    ax.axis("off")


def main():
    single_tank = None
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--tank" and i + 1 < len(sys.argv) - 1:
            single_tank = sys.argv[i + 2]

    tanks = [single_tank] if single_tank else sorted(R_FIT.keys())
    print(f"=== Al-Faw panels v2 === {len(tanks)} tank(s)")

    # Fetch date catalogue from Supabase
    print("[1] Fetching date catalogue …")
    rows, offset = [], 0
    while True:
        chunk = sb_get("terminal_observations",
                       {"terminal_id": f"eq.{TERMINAL_ID}",
                        "orbit_direction": f"eq.{ORBIT_DIR}",
                        "select": "date,tank_id,db_mean",
                        "order": "date.desc",
                        "offset": str(offset), "limit": str(PAGE_SIZE)})
        rows.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    import pandas as pd
    df = pd.DataFrame(rows).drop_duplicates(subset=["date","tank_id"])
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")
    print(f"  {len(df)} rows loaded")

    token = sh_token()

    for ti, tid in enumerate(tanks):
        print(f"[{ti+1}/{len(tanks)}] {tid} …", end=" ", flush=True)
        tank_df = df[df["tank_id"] == tid].sort_values("date", ascending=False)

        if len(tank_df) < 2:
            print("SKIP (< 2 dates)")
            continue

        dates_sorted = tank_df["date"].tolist()

        # SAR-B = most recent
        sar_b_date = dates_sorted[0]
        sar_b_db   = float(tank_df[tank_df["date"] == sar_b_date]["db_mean"].values[0])

        # SAR-A = most recent date that is ≥ SAR_B_MIN_AGE_DAYS before SAR-B
        sar_b_dt = datetime.strptime(sar_b_date, "%Y-%m-%d")
        sar_a_date = None
        sar_a_db   = None
        for d in dates_sorted[1:]:
            dt = datetime.strptime(d, "%Y-%m-%d")
            if (sar_b_dt - dt).days >= SAR_B_MIN_AGE_DAYS:
                sar_a_date = d
                sar_a_db   = float(tank_df[tank_df["date"] == d]["db_mean"].values[0])
                break

        r_fit    = R_FIT[tid]
        window_m = r_fit * 4.0  # half-window = 4 * r_fit (4-diameter = 8r across, half = 4r)

        # Fetch images
        arr_b = fetch_sar(tid, sar_b_date, token)
        arr_a = fetch_sar(tid, sar_a_date, token) if sar_a_date else None
        arr_s2 = fetch_s2_rgb(tid, sar_b_date, token)
        token = sh_token()  # refresh after 3 SH calls

        fig, axes = plt.subplots(1, 3, figsize=(6.4, 2.3),
                                 facecolor="#0a0a0a",
                                 gridspec_kw={"wspace": 0.03})

        # SAR-A
        if arr_a is not None:
            render_panel(axes[0], sar_to_display(arr_a), "SAR-A",
                         tid, sar_a_date, sar_a_db, r_fit, window_m)
        else:
            render_blank(axes[0], f"SAR-A\n{sar_a_date or 'no date'}\nunavailable")

        # SAR-B
        if arr_b is not None:
            render_panel(axes[1], sar_to_display(arr_b), "SAR-B",
                         tid, sar_b_date, sar_b_db, r_fit, window_m)
        else:
            render_blank(axes[1], f"SAR-B\n{sar_b_date}\nunavailable")

        # S2
        if arr_s2 is not None:
            render_panel(axes[2], arr_s2, f"S2 ±{S2_WINDOW_DAYS}d",
                         tid, sar_b_date, None, r_fit, window_m, is_s2=True)
        else:
            render_blank(axes[2], f"S2\n±{S2_WINDOW_DAYS}d\nno clear scene")

        plt.suptitle(f"{tid}  |  SAR-A:{sar_a_date or '—'}  SAR-B:{sar_b_date}",
                     fontsize=7, color="white", y=0.98)

        out_path = OUT_DIR / f"{tid}.png"
        plt.savefig(out_path, dpi=150, bbox_inches="tight",
                    facecolor="#0a0a0a", pad_inches=0.05)
        plt.close(fig)
        print(f"→ {out_path.name}")

    print(f"\nDone. Panels in {OUT_DIR}")
    print("Send files to chat for human verdicts.")


if __name__ == "__main__":
    main()
