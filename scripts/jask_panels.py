"""
jask_panels.py — Per-tank SAR panels for Jask (20 tanks, human verdicts)
=========================================================================
For each of the 20 jk_* tanks, renders a 3-panel image:
  - SAR-1: DESC rel=93 pass, ≥18d before SAR-2  (earlier scene)
  - SAR-2: DESC rel=93 pass, most recent with valid data  (current scene)
  - S2:    most recent Sentinel-2 optical within 14d of SAR-2

Labels show platform from SH Catalog (S1A/S1C).
S1B decommissioned 2021-12-23 — "SAR-1/SAR-2" are TEMPORAL labels, not platform.
Both scenes always from the same relOrbit (filtered by ORBIT_REL=93 in Supabase).

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

from sar_evalscript import SAR_EVALSCRIPT as _SAR_EVALSCRIPT, linear_to_db, mean_db

SB_URL = (os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"
SH_CATALOG_URL   = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"

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


def fetch_sar_scene_meta(date_str, bbox):
    """Return (platform, rel_orbit) from SH Catalog for the scene covering date_str.

    Uses ±1 day window to handle UTC-midnight straddle.  Picks the feature whose
    datetime is closest to date_str noon.  Returns ('S1?', None) on any failure.
    Platform field: 'properties.platform' e.g. 'sentinel-1a'.
    Orbit field:    'properties.sat:relative_orbit' or 'properties.relativeOrbit'.
    """
    token = get_sh_token()
    dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
    t_from = (dt - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    t_to   = (dt + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59Z")
    payload = {
        "collections": ["sentinel-1-grd"],
        "datetime": f"{t_from}/{t_to}",
        "bbox": bbox,
        "limit": 10,
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        r = requests.post(SH_CATALOG_URL, json=payload, headers=headers, timeout=20)
        if r.status_code != 200:
            print(f"    catalog HTTP {r.status_code}")
            return "S1?", None
        feats = r.json().get("features", [])
        if not feats:
            print(f"    catalog: no features returned for {date_str} ±1d")
            return "S1?", None
        # Pick feature whose datetime is closest to target date noon
        target_ts = dt.replace(hour=12).timestamp()
        def feat_ts(f):
            d = f.get("properties", {}).get("datetime", "")
            try:
                return abs(datetime.strptime(d[:19], "%Y-%m-%dT%H:%M:%S").timestamp() - target_ts)
            except Exception:
                return 1e9
        best = min(feats, key=feat_ts)
        props = best.get("properties", {})
        # Platform
        p = props.get("platform", "").upper()
        if "1A" in p:
            plat = "S1A"
        elif "1C" in p:
            plat = "S1C"
        elif "1B" in p:
            plat = "S1B"
        else:
            plat = "S1?"
        # Relative orbit — try both field names
        ro_raw = props.get("sat:relative_orbit") or props.get("relativeOrbit")
        ro = int(ro_raw) if ro_raw is not None else None
        print(f"    catalog: date={date_str}  platform={plat}  rel_orbit={ro}  "
              f"(scene_dt={props.get('datetime','?')[:16]})")
        return plat, ro
    except Exception as e:
        print(f"    catalog error: {e}")
    return "S1?", None


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


SAR_EVALSCRIPT = _SAR_EVALSCRIPT

# S2: return raw FLOAT32 reflectance; Python applies 2-98% per-tile percentile stretch.
# Fixed gain (3.5×255) over-saturates bright desert sand — percentile stretch adapts.
S2_EVALSCRIPT = """
//VERSION=3
function setup(){return {input:[{bands:["B04","B03","B02"]}],output:{bands:3,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return [s.B04, s.B03, s.B02];}
"""


def _tile_px(bbox, res_m, size=IMG_PX):
    """Return (width_px, height_px) for a geographic bbox at res_m metres/pixel.

    Uses width/height counts (NOT resx/resy), because the CRS is WGS84 and
    resx/resy would be interpreted as DEGREES — causing 1×1 pixel tiles.
    """
    lon0, lat0, lon1, lat1 = bbox
    lat_c = (lat0 + lat1) / 2
    span_m_lon = (lon1 - lon0) * 111320 * math.cos(math.radians(lat_c))
    span_m_lat = (lat1 - lat0) * 111320
    w = max(size, int(round(span_m_lon / res_m)))
    h = max(size, int(round(span_m_lat / res_m)))
    return min(w, 512), min(h, 512)


def fetch_sar(tank, date_str, res_m=5):
    bbox = bbox_for_tank(tank)
    w_px, h_px = _tile_px(bbox, res_m)
    print(f"    SAR  bbox={[round(x,5) for x in bbox]}  CRS=WGS84  "
          f"res={res_m}m/px  width={w_px}  height={h_px}")
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
            "width": w_px, "height": h_px, "responses": [
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
                arr = arr.astype(np.float32)
                db = 10.0 * np.log10(np.clip(arr, 1e-10, None))
                n_unique = len(np.unique(arr))
                print(f"    SAR  dtype={arr.dtype}  shape={arr.shape}  "
                      f"n_unique={n_unique}  db min={np.nanmin(db):.1f}  "
                      f"max={np.nanmax(db):.1f}  std={np.nanstd(db):.2f}")
                assert n_unique > 100 and np.nanstd(db) > 0, \
                    f"FAIL: SAR tile degenerate (n_unique={n_unique}, std={np.nanstd(db):.3f})"
                return arr
            else:
                print(f"    SAR HTTP {r.status_code}: {r.text[:200]}")
        except AssertionError as e:
            print(f"    {e}"); return None
        except Exception as e:
            print(f"    SAR fetch attempt {attempt+1} failed: {e}")
            time.sleep(2)
    return None


def fetch_s2(tank, sar_b_date_str, window_days=S2_WINDOW_DAYS, res_m=10):
    sar_dt = datetime.strptime(sar_b_date_str[:10], "%Y-%m-%d")
    t_from = (sar_dt - timedelta(days=window_days)).strftime("%Y-%m-%dT00:00:00Z")
    t_to   = sar_dt.strftime("%Y-%m-%dT23:59:59Z")
    bbox = bbox_for_tank(tank)
    w_px, h_px = _tile_px(bbox, res_m)
    print(f"    S2   bbox={[round(x,5) for x in bbox]}  CRS=WGS84  "
          f"res={res_m}m/px  width={w_px}  height={h_px}  window={t_from[:10]}–{t_to[:10]}")
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
            "width": w_px, "height": h_px, "responses": [
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
                arr = tifffile.imread(io.BytesIO(r.content))  # float32 reflectance HxWx3
                # sat_frac: fraction of pixels where ANY band ≥ 0.5 (cloud/specular)
                if arr.ndim == 3:
                    sat_mask = arr.max(axis=-1) >= 0.5
                else:
                    sat_mask = arr >= 0.5
                saturated_frac = float(sat_mask.mean())
                n_unique = len(np.unique(arr))
                print(f"    S2   dtype={arr.dtype}  shape={arr.shape}  "
                      f"n_unique={n_unique}  refl min={arr.min():.3f}  max={arr.max():.3f}  "
                      f"std={arr.std():.3f}  sat_frac={saturated_frac:.3f}")
                assert n_unique > 100 and arr.std() > 0, \
                    f"FAIL: S2 tile degenerate (n_unique={n_unique}, std={arr.std():.4f})"
                assert saturated_frac < 0.50, \
                    f"FAIL: S2 tile over-saturated (sat_frac={saturated_frac:.3f})"
                # per-channel 2-98% percentile stretch → uint8 RGB
                # Must be per-channel: desert reflectance differs by band
                # (B04~0.45, B02~0.35); a single combined range causes colour imbalance.
                n_ch = arr.shape[2] if arr.ndim == 3 else 1
                stretched = np.zeros(arr.shape, dtype=np.uint8)
                for c in range(n_ch):
                    band = arr[:, :, c] if arr.ndim == 3 else arr
                    valid = band[band > 0]
                    p2c, p98c = (np.percentile(valid, [2, 98])
                                 if len(valid) > 10 else (0.0, 0.6))
                    denom = max(float(p98c - p2c), 1e-6)
                    s = np.clip((band - p2c) / denom * 255, 0, 255).astype(np.uint8)
                    if arr.ndim == 3:
                        stretched[:, :, c] = s
                    else:
                        stretched = s
                return stretched
            else:
                print(f"    S2 HTTP {r.status_code}: {r.text[:200]}")
        except AssertionError as e:
            print(f"    {e}"); return None
        except Exception as e:
            print(f"    S2 fetch attempt {attempt+1} failed: {e}")
            time.sleep(2)
    return None


# ─── render panel ─────────────────────────────────────────────────────────────

def sar_to_rgb(arr, vmin=-15, vmax=5):
    """Convert SAR float32 (linear power) to uint8 grayscale RGB via dB."""
    if arr is None:
        return np.zeros((IMG_PX, IMG_PX, 3), dtype=np.uint8)
    db = linear_to_db(arr)
    clipped = np.clip(db, vmin, vmax)
    normed  = np.where(np.isfinite(clipped),
                       ((clipped - vmin) / (vmax - vmin) * 255), 0).astype(np.uint8)
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


def render_tank(tank, sar_1_arr, sar_1_meta, sar_1_platform, sar_1_orbit,
               sar_2_arr, sar_2_meta, sar_2_platform, sar_2_orbit,
               s2_arr, out_path):
    fig, axes = plt.subplots(1, 3, figsize=(9, 3.2), facecolor="black")
    fig.subplots_adjust(wspace=0.02, left=0.01, right=0.99, top=0.92, bottom=0.01)

    panels = [
        ("SAR-1", sar_1_arr, sar_1_meta, sar_1_platform, sar_1_orbit),
        ("SAR-2", sar_2_arr, sar_2_meta, sar_2_platform, sar_2_orbit),
        ("S2",    s2_arr,    None,        None,           None),
    ]

    r_px_sar = IMG_PX / 8
    r_px_s2  = IMG_PX / 8

    for ax, (title, arr, meta, platform, orb) in zip(axes, panels):
        if arr is None:
            rgb = np.zeros((IMG_PX, IMG_PX, 3), dtype=np.uint8)
            label_text = f"{title}  NO DATA"
        elif title == "S2":
            rgb = resize_to_square(arr if arr.ndim == 3 else
                                   np.stack([arr]*3, axis=-1))
            label_text = "S2"
        else:
            rgb = resize_to_square(sar_to_rgb(arr))
            db_val = meta.get("db_mean") if meta else None
            date_s = meta.get("date", "")[:10] if meta else ""
            plat   = platform or "S1?"
            orb_s  = str(orb) if orb is not None else "?"
            db_str = f"  db={db_val:.1f}dB" if db_val is not None else ""
            label_text = f"{plat}  {date_s}  orb={orb_s}  {ORBIT_DIR}{db_str}"

        ax.imshow(rgb, origin="upper")
        r_px = r_px_s2 if title == "S2" else r_px_sar
        draw_ring(ax, IMG_PX / 2, IMG_PX / 2, r_px)
        ax.set_xlim(0, IMG_PX); ax.set_ylim(IMG_PX, 0)
        ax.axis("off")

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

    sar_1_meta, sar_2_meta = pick_sar_pair(rows)  # sar_1=earlier, sar_2=current
    if sar_2_meta is None:
        print(f"  Cannot pick SAR pair — skipping")
        return

    bbox = bbox_for_tank(tank)
    print(f"  SAR-2 (current): {sar_2_meta['date'][:10]}  db={sar_2_meta['db_mean']:.1f}dB")
    if sar_1_meta:
        print(f"  SAR-1 (earlier): {sar_1_meta['date'][:10]}  db={sar_1_meta['db_mean']:.1f}dB")
    else:
        print(f"  SAR-1: not found (need ≥{SAR_B_MIN_AGE_DAYS}d before SAR-2)")

    # Get platform AND actual relOrbit from scene metadata (±1d catalog search).
    # Do NOT assume relOrbit from query params — must confirm from metadata.
    sar_2_platform, sar_2_orbit = fetch_sar_scene_meta(sar_2_meta["date"][:10], bbox)
    sar_1_platform, sar_1_orbit = (fetch_sar_scene_meta(sar_1_meta["date"][:10], bbox)
                                   if sar_1_meta else (None, None))
    print(f"  platforms: SAR-2={sar_2_platform}(orb={sar_2_orbit})  "
          f"SAR-1={sar_1_platform}(orb={sar_1_orbit})")

    # Assert: both scenes must come from the same relOrbit.
    if (sar_1_orbit is not None and sar_2_orbit is not None
            and sar_1_orbit != sar_2_orbit):
        print(f"  PANEL FAIL: relOrbit mismatch "
              f"SAR-1={sar_1_orbit} vs SAR-2={sar_2_orbit} — skipping")
        return

    sar_2_arr = fetch_sar(tank, sar_2_meta["date"][:10])
    sar_1_arr = fetch_sar(tank, sar_1_meta["date"][:10]) if sar_1_meta else None

    s2_arr = fetch_s2(tank, sar_2_meta["date"][:10])
    print(f"  S2: {'fetched' if s2_arr is not None else 'not available'}")

    render_tank(tank,
                sar_1_arr, sar_1_meta, sar_1_platform, sar_1_orbit,
                sar_2_arr, sar_2_meta, sar_2_platform, sar_2_orbit,
                s2_arr, out_path)
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
