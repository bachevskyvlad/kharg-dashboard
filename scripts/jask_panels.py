"""
jask_panels.py — Per-tank SAR panels for Jask (20 tanks, human verdicts)
=========================================================================
For each of the 20 jk_* tanks, renders a 3-panel image:
  - SAR-A: DESC rel=93 pass, ≥18d before SAR-B
  - SAR-B: DESC rel=93 pass, most recent with valid data
  - S2:    most recent Sentinel-2 optical within 14d of SAR-B

Layout (canonical):
  - 4-diameter window (8× r_fit across) centred on tank
  - Thin ring overlay at r_fit radius (1px line)
  - Corner label on dark chip: "{tank_id}  {date}  db={db:.1f}dB"
  - 3 panels side-by-side: SAR-A | SAR-B | S2

Output: diagnostics/jask/panels/{tank_id}.png  (20 files)

Usage:
  python jask_panels.py [--tank jk_0]   (single tank for test)
  python jask_panels.py                 (all 20)

Requires: matplotlib, tifffile, Pillow, requests
"""

import argparse
import io
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import requests

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import tifffile
    from PIL import Image
except ImportError as e:
    print(f"Missing dep: {e}"); print("pip install matplotlib tifffile Pillow"); sys.exit(1)

SB_URL = (os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"

TERMINAL_ID        = "jask"
ORBIT_REL          = 93
ORBIT_DIR          = "DESC"
SAR_B_MIN_AGE_DAYS = 18
S2_WINDOW_DAYS     = 14
IMG_PX             = 128  # pixels per panel side

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "jask" / "panels"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Tank registry (from tank_registry.py)
TANKS = [
    {"id": "jk_0",  "lat": 25.854575807, "lon": 57.304184128, "radius_m": 38.3},
    {"id": "jk_1",  "lat": 25.854290389, "lon": 57.302805193, "radius_m": 37.9},
    {"id": "jk_2",  "lat": 25.853049156, "lon": 57.303027087, "radius_m": 36.7},
    {"id": "jk_3",  "lat": 25.853262315, "lon": 57.304478451, "radius_m": 39.6},
    {"id": "jk_4",  "lat": 25.861242541, "lon": 57.301400134, "radius_m": 36.9},
    {"id": "jk_5",  "lat": 25.859992238, "lon": 57.301668786, "radius_m": 36.3},
    {"id": "jk_6",  "lat": 25.860252259, "lon": 57.302961780, "radius_m": 36.7},
    {"id": "jk_7",  "lat": 25.861527234, "lon": 57.302683980, "radius_m": 36.8},
    {"id": "jk_8",  "lat": 25.853687717, "lon": 57.306985889, "radius_m": 41.2},
    {"id": "jk_9",  "lat": 25.853956990, "lon": 57.308340776, "radius_m": 39.6},
    {"id": "jk_10", "lat": 25.855252023, "lon": 57.308039099, "radius_m": 42.3},
    {"id": "jk_11", "lat": 25.855000865, "lon": 57.306648174, "radius_m": 41.8},
    {"id": "jk_12", "lat": 25.861946310, "lon": 57.305238458, "radius_m": 35.0},
    {"id": "jk_13", "lat": 25.862228421, "lon": 57.306587685, "radius_m": 33.2},
    {"id": "jk_14", "lat": 25.860704129, "lon": 57.305475512, "radius_m": 32.5},
    {"id": "jk_15", "lat": 25.860904482, "lon": 57.306916748, "radius_m": 33.2},
    {"id": "jk_16", "lat": 25.858447932, "lon": 57.305924122, "radius_m": 39.0},
    {"id": "jk_17", "lat": 25.858696487, "lon": 57.307318681, "radius_m": 36.5},
    {"id": "jk_18", "lat": 25.857197864, "lon": 57.306191087, "radius_m": 37.6},
    {"id": "jk_19", "lat": 25.857398073, "lon": 57.307667801, "radius_m": 36.5},
]

# ─── SH auth ──────────────────────────────────────────────────────────────────

_sh_token = None
_sh_token_exp = 0

def get_sh_token():
    global _sh_token, _sh_token_exp
    if time.time() < _sh_token_exp - 60:
        return _sh_token
    r = requests.post(SH_TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": SH_CLIENT_ID,
        "client_secret": SH_CLIENT_SECRET,
    }, timeout=30)
    r.raise_for_status()
    data = r.json()
    _sh_token = data["access_token"]
    _sh_token_exp = time.time() + data.get("expires_in", 3600)
    return _sh_token


# ─── Supabase fetch ───────────────────────────────────────────────────────────

def sb_get(endpoint, params=None):
    headers = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}
    r = requests.get(f"{SB_URL}/rest/v1/{endpoint}", headers=headers,
                     params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_tank_series(tank_id):
    """Fetch full db_mean series for one jk_* tank from terminal_observations."""
    rows = sb_get("terminal_observations",
                  params={
                      "select": "date,db_mean",
                      "terminal_id": f"eq.{TERMINAL_ID}",
                      "tank_id":     f"eq.{tank_id}",
                      "relative_orbit": f"eq.{ORBIT_REL}",
                      "order": "date.desc",
                      "limit": "500",
                  })
    return rows  # list of {date, db_mean}


def pick_sar_pair(rows):
    """Return (sar_a, sar_b) dicts, or None.

    sar_b = most recent row; sar_a = most recent row ≥18 days before sar_b.
    """
    if len(rows) < 2:
        return None, None
    rows_sorted = sorted(rows, key=lambda r: r["date"], reverse=True)
    sar_b = rows_sorted[0]
    sar_b_dt = datetime.strptime(sar_b["date"][:10], "%Y-%m-%d")
    for r in rows_sorted[1:]:
        dt = datetime.strptime(r["date"][:10], "%Y-%m-%d")
        if (sar_b_dt - dt).days >= SAR_B_MIN_AGE_DAYS:
            return r, sar_b
    return None, sar_b


# ─── SAR fetch ────────────────────────────────────────────────────────────────

def lat_m_per_deg():
    return 111320.0


def lon_m_per_deg(lat):
    return 111320.0 * math.cos(math.radians(lat))


def bbox_for_tank(tank, n_diam=4):
    r = tank["radius_m"]
    half_m = r * n_diam
    dlat = half_m / lat_m_per_deg()
    dlon = half_m / lon_m_per_deg(tank["lat"])
    return [tank["lon"] - dlon, tank["lat"] - dlat,
            tank["lon"] + dlon, tank["lat"] + dlat]


SAR_EVALSCRIPT = """
//VERSION=3
function setup(){return {input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){var v=s.VV;return [v>0?10*Math.log10(v):-50];}
"""

S2_EVALSCRIPT = """
//VERSION=3
function setup(){return {input:[{bands:["B04","B03","B02"]}],output:{bands:3,sampleType:"UINT8"}};}
function evaluatePixel(s){return [Math.min(255,s.B04*4*255),Math.min(255,s.B03*4*255),Math.min(255,s.B02*4*255)];}
"""


def fetch_sar(tank, date_str, res_m=5):
    bbox = bbox_for_tank(tank)
    token = get_sh_token()
    payload = {
        "input": {
            "bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{"type": "sentinel-1-grd",
                      "dataFilter": {
                          "timeRange": {
                              "from": date_str + "T00:00:00Z",
                              "to":   date_str + "T23:59:59Z",
                          },
                          "orbitDirection": "DESCENDING",
                          "relativeOrbit": ORBIT_REL,
                      },
                      "processing": {"backCoeff": "SIGMA0_ELLIPSOID", "orthorectify": True}}],
        },
        "output": {
            "resx": res_m, "resy": res_m, "responses": [
                {"identifier": "default", "format": {"type": "image/tiff"}}
            ],
        },
        "evalscript": SAR_EVALSCRIPT,
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "Accept": "image/tiff"}
    for attempt in range(3):
        try:
            r = requests.post(SH_PROCESS_URL, json=payload, headers=headers, timeout=60)
            if r.status_code == 200:
                arr = tifffile.imread(io.BytesIO(r.content))
                return arr.astype(np.float32)
        except Exception as e:
            print(f"    SAR fetch attempt {attempt+1} failed: {e}")
            time.sleep(2)
    return None


def fetch_s2(tank, sar_b_date_str, window_days=S2_WINDOW_DAYS):
    sar_dt = datetime.strptime(sar_b_date_str[:10], "%Y-%m-%d")
    t_from = (sar_dt - timedelta(days=window_days)).strftime("%Y-%m-%dT00:00:00Z")
    t_to   = sar_dt.strftime("%Y-%m-%dT23:59:59Z")
    bbox = bbox_for_tank(tank)
    token = get_sh_token()
    payload = {
        "input": {
            "bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{"type": "sentinel-2-l2a",
                      "dataFilter": {
                          "timeRange": {"from": t_from, "to": t_to},
                          "maxCloudCoverage": 30,
                          "mosaickingOrder": "leastCC",
                      }}],
        },
        "output": {
            "resx": 10, "resy": 10, "responses": [
                {"identifier": "default", "format": {"type": "image/tiff"}}
            ],
        },
        "evalscript": S2_EVALSCRIPT,
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "Accept": "image/tiff"}
    for attempt in range(3):
        try:
            r = requests.post(SH_PROCESS_URL, json=payload, headers=headers, timeout=60)
            if r.status_code == 200:
                arr = tifffile.imread(io.BytesIO(r.content))
                return arr
        except Exception as e:
            print(f"    S2 fetch attempt {attempt+1} failed: {e}")
            time.sleep(2)
    return None


# ─── render panel ─────────────────────────────────────────────────────────────

def sar_to_rgb(arr, vmin=-15, vmax=5):
    """Convert SAR float32 (dB) to uint8 grayscale RGB."""
    if arr is None:
        return np.zeros((IMG_PX, IMG_PX, 3), dtype=np.uint8)
    clipped = np.clip(arr, vmin, vmax)
    normed  = ((clipped - vmin) / (vmax - vmin) * 255).astype(np.uint8)
    if normed.ndim == 2:
        return np.stack([normed, normed, normed], axis=-1)
    return normed[:, :, :3]


def resize_to_square(rgb, size=IMG_PX):
    img = Image.fromarray(rgb.astype(np.uint8))
    img = img.resize((size, size), Image.LANCZOS)
    return np.array(img)


def draw_ring(ax, cx, cy, r_px, color="cyan", lw=1):
    circle = plt.Circle((cx, cy), r_px, color=color, fill=False, linewidth=lw)
    ax.add_patch(circle)


def render_tank(tank, sar_a_arr, sar_a_meta, sar_b_arr, sar_b_meta, s2_arr, out_path):
    fig, axes = plt.subplots(1, 3, figsize=(9, 3.2), facecolor="black")
    fig.subplots_adjust(wspace=0.02, left=0.01, right=0.99, top=0.92, bottom=0.01)

    panels = [
        ("SAR-A", sar_a_arr, sar_a_meta),
        ("SAR-B", sar_b_arr, sar_b_meta),
        ("S2",    s2_arr,    None),
    ]

    r = tank["radius_m"]
    # approximate pixel radius: 4-diam window = 8r across; SAR at 5m/px
    r_px_sar = r / 5 * (IMG_PX / (8 * r / 5))   # = IMG_PX / 8
    r_px_s2  = r / 10 * (IMG_PX / (8 * r / 10))  # same fraction

    for ax, (title, arr, meta) in zip(axes, panels):
        if arr is None:
            rgb = np.zeros((IMG_PX, IMG_PX, 3), dtype=np.uint8)
            label_text = f"{title}  NO DATA"
        elif title == "S2":
            rgb = resize_to_square(arr if arr.ndim == 3 else
                                   np.stack([arr]*3, axis=-1))
            label_text = f"S2"
        else:
            rgb = resize_to_square(sar_to_rgb(arr))
            db_val  = meta.get("db_mean") if meta else None
            date_s  = meta.get("date", "")[:10] if meta else ""
            label_text = f"{tank['id']}  {date_s}  db={db_val:.1f}dB" if db_val is not None else f"{tank['id']}  {date_s}"

        ax.imshow(rgb, origin="upper")
        r_px = r_px_s2 if title == "S2" else r_px_sar
        draw_ring(ax, IMG_PX / 2, IMG_PX / 2, r_px)
        ax.set_xlim(0, IMG_PX); ax.set_ylim(IMG_PX, 0)
        ax.axis("off")

        # Corner label with dark chip
        ax.text(4, 4, label_text, color="white", fontsize=6.5,
                va="top", ha="left",
                bbox=dict(boxstyle="round,pad=0.2", facecolor="black",
                          alpha=0.75, edgecolor="none"))

    fig.suptitle(tank["id"], color="white", fontsize=10, y=0.98)
    fig.savefig(str(out_path), dpi=120, bbox_inches="tight",
                facecolor="black", edgecolor="none")
    plt.close(fig)


# ─── main ─────────────────────────────────────────────────────────────────────

def process_tank(tank):
    tid = tank["id"]
    out_path = OUT_DIR / f"{tid}.png"
    print(f"\n[{tid}]")

    rows = fetch_tank_series(tid)
    if not rows:
        print(f"  No data in terminal_observations — skipping")
        return

    sar_a_meta, sar_b_meta = pick_sar_pair(rows)
    if sar_b_meta is None:
        print(f"  Cannot pick SAR pair — skipping")
        return

    print(f"  SAR-B: {sar_b_meta['date'][:10]}  db={sar_b_meta['db_mean']:.1f}dB")
    if sar_a_meta:
        print(f"  SAR-A: {sar_a_meta['date'][:10]}  db={sar_a_meta['db_mean']:.1f}dB")
    else:
        print(f"  SAR-A: not found (need ≥{SAR_B_MIN_AGE_DAYS}d before SAR-B)")

    sar_b_arr = fetch_sar(tank, sar_b_meta["date"][:10])
    sar_a_arr = fetch_sar(tank, sar_a_meta["date"][:10]) if sar_a_meta else None

    s2_arr = fetch_s2(tank, sar_b_meta["date"][:10])
    print(f"  S2: {'fetched' if s2_arr is not None else 'not available'}")

    render_tank(tank, sar_a_arr, sar_a_meta, sar_b_arr, sar_b_meta, s2_arr, out_path)
    print(f"  Saved: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate Jask per-tank panels")
    parser.add_argument("--tank", default=None, help="Single tank id (e.g. jk_0)")
    args = parser.parse_args()

    tanks = [t for t in TANKS if t["id"] == args.tank] if args.tank else TANKS
    print(f"Rendering {len(tanks)} tank(s)...")

    for tank in tanks:
        process_tank(tank)

    print(f"\nDone. Panels in {OUT_DIR}")


if __name__ == "__main__":
    main()
