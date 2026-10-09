"""
jask_smoke.py — smoke test for jk_11: log all fetch steps, save raw images.
Run: python jask_smoke.py
"""
import io, math, os, sys, time, json
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import requests

from sar_evalscript import SAR_EVALSCRIPT, linear_to_db

try:
    import tifffile
    from PIL import Image
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError as e:
    print(f"Missing dep: {e}"); sys.exit(1)

SB_URL = (os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"

TANK = {"id": "jk_11", "lat": 25.855000865, "lon": 57.306648174, "radius_m": 41.8}
ORBIT_REL = 93
OUT = Path(__file__).parent.parent / "diagnostics" / "jask" / "smoke"
OUT.mkdir(parents=True, exist_ok=True)

def log(msg): print(msg, flush=True)

# ── Supabase ──────────────────────────────────────────────────────────────────
def sb_get(endpoint, params):
    headers = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}
    url = f"{SB_URL}/rest/v1/{endpoint}"
    log(f"  GET {url}")
    log(f"  params: {json.dumps(params)}")
    r = requests.get(url, headers=headers, params=params, timeout=30)
    log(f"  status: {r.status_code}  len: {len(r.content)}")
    r.raise_for_status()
    return r.json()

log("=== STEP 1: Supabase — terminal_observations for jk_11 ===")
rows = sb_get("terminal_observations", {
    "select": "date,db_mean,relative_orbit,orbit_direction",
    "terminal_id": f"eq.jask",
    "tank_id":     f"eq.jk_11",
    "order":       "date.desc",
    "limit":       "10",
})
log(f"  rows returned (no orbit filter): {len(rows)}")
for r in rows[:5]:
    log(f"    {r}")

log("\n--- with orbit filter ---")
rows_orb = sb_get("terminal_observations", {
    "select": "date,db_mean,relative_orbit,orbit_direction",
    "terminal_id":    f"eq.jask",
    "tank_id":        f"eq.jk_11",
    "relative_orbit": f"eq.{ORBIT_REL}",
    "order":          "date.desc",
    "limit":          "10",
})
log(f"  rows returned (orbit={ORBIT_REL}): {len(rows_orb)}")
for r in rows_orb[:5]:
    log(f"    {r}")

if not rows_orb:
    log("  !!! NO ROWS WITH orbit=93 — check orbit values in DB")
    log("  Distinct orbits in all rows:")
    all_rows = sb_get("terminal_observations", {
        "select": "relative_orbit,orbit_direction",
        "terminal_id": "eq.jask",
        "tank_id":     "eq.jk_11",
        "limit": "200",
    })
    orbits = set((r.get("relative_orbit"), r.get("orbit_direction")) for r in all_rows)
    for orb in sorted(orbits, key=lambda x: str(x)):
        log(f"    orbit={orb[0]} dir={orb[1]}")
    if not rows:
        log("  !!! NO ROWS AT ALL for jk_11 in terminal_observations")
        sys.exit(1)
    # Use first available row for SAR test
    rows_orb = rows[:5]

# Pick SAR-B and SAR-A
rows_sorted = sorted(rows_orb, key=lambda r: r["date"], reverse=True)
sar_b = rows_sorted[0]
sar_a = None
sar_b_dt = datetime.strptime(sar_b["date"][:10], "%Y-%m-%d")
for r in rows_sorted[1:]:
    dt = datetime.strptime(r["date"][:10], "%Y-%m-%d")
    if (sar_b_dt - dt).days >= 18:
        sar_a = r
        break

log(f"\n  SAR-B chosen: {sar_b}")
log(f"  SAR-A chosen: {sar_a}")

# ── SH token ─────────────────────────────────────────────────────────────────
log("\n=== STEP 2: SentinelHub token ===")
if not SH_CLIENT_ID or not SH_CLIENT_SECRET:
    log("  ERROR: CLIENT_ID / CLIENT_SECRET not set — cannot fetch SAR")
    sys.exit(1)

tok_r = requests.post(SH_TOKEN_URL, data={
    "grant_type": "client_credentials",
    "client_id": SH_CLIENT_ID,
    "client_secret": SH_CLIENT_SECRET,
}, timeout=30)
log(f"  token status: {tok_r.status_code}")
tok_r.raise_for_status()
token = tok_r.json()["access_token"]
log("  token OK")

# ── bbox ─────────────────────────────────────────────────────────────────────
def bbox_for_tank(t, n_diam=4):
    r = t["radius_m"]
    half_m = r * n_diam
    dlat = half_m / 111320.0
    dlon = half_m / (111320.0 * math.cos(math.radians(t["lat"])))
    return [t["lon"] - dlon, t["lat"] - dlat, t["lon"] + dlon, t["lat"] + dlat]

bbox = bbox_for_tank(TANK)
log(f"\n  bbox: {[round(x,6) for x in bbox]}")
log(f"  window: {TANK['radius_m']*8:.0f}m × {TANK['radius_m']*8:.0f}m")

# ── SAR fetch (two variants: relativeOrbit vs relativeOrbitNum) ───────────────

def fetch_sar_verbose(date_str, field_name, label):
    log(f"\n=== STEP 3: SAR fetch — {label} (field={field_name}) date={date_str} ===")
    payload = {
        "input": {
            "bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{"type": "sentinel-1-grd",
                      "dataFilter": {
                          "timeRange": {"from": date_str + "T00:00:00Z", "to": date_str + "T23:59:59Z"},
                          "orbitDirection": "DESCENDING",
                          field_name: ORBIT_REL,
                      },
                      "processing": {"backCoeff": "SIGMA0_ELLIPSOID", "orthorectify": True}}],
        },
        "output": {"resx": 5, "resy": 5, "responses": [
            {"identifier": "default", "format": {"type": "image/tiff"}}
        ]},
        "evalscript": SAR_EVALSCRIPT,
    }
    log(f"  payload dataFilter: {json.dumps(payload['input']['data'][0]['dataFilter'])}")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "image/tiff"}
    r = requests.post(SH_PROCESS_URL, json=payload, headers=headers, timeout=60)
    log(f"  status: {r.status_code}  content-type: {r.headers.get('content-type')}  len: {len(r.content)}")
    if r.status_code != 200:
        log(f"  response body: {r.text[:500]}")
        return None
    try:
        arr_linear = tifffile.imread(io.BytesIO(r.content)).astype(np.float32)
        log(f"  array shape: {arr_linear.shape}  dtype: {arr_linear.dtype}")
        valid_linear = arr_linear[np.isfinite(arr_linear) & (arr_linear > 0)]
        log(f"  valid pixels (linear>0): {len(valid_linear)}")
        if len(valid_linear) == 0:
            log("  !!! ALL PIXELS INVALID (NaN/zero fill) — NO_DATA")
        else:
            db_arr = linear_to_db(arr_linear)
            valid_db = db_arr[np.isfinite(db_arr)]
            log(f"  dB range: min={float(valid_db.min()):.2f}  max={float(valid_db.max()):.2f}  std={float(valid_db.std()):.3f}")
        return arr_linear
    except Exception as e:
        log(f"  tifffile error: {e}")
        return None

date_b = sar_b["date"][:10]
date_a = sar_a["date"][:10] if sar_a else None

arr_b_old = fetch_sar_verbose(date_b, "relativeOrbit",    "SAR-B old field")
arr_b_new = fetch_sar_verbose(date_b, "relativeOrbitNum", "SAR-B new field")

if date_a:
    arr_a_old = fetch_sar_verbose(date_a, "relativeOrbit",    "SAR-A old field")
    arr_a_new = fetch_sar_verbose(date_a, "relativeOrbitNum", "SAR-A new field")
else:
    arr_a_old = arr_a_new = None

# ── S2 fetch ──────────────────────────────────────────────────────────────────
log(f"\n=== STEP 4: S2 fetch (14d before SAR-B={date_b}) ===")
sar_dt = datetime.strptime(date_b, "%Y-%m-%d")
t_from = (sar_dt - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z")
t_to   = sar_dt.strftime("%Y-%m-%dT23:59:59Z")
S2_ES = """//VERSION=3
function setup(){return {input:[{bands:["B04","B03","B02"]}],output:{bands:3,sampleType:"UINT8"}};}
function evaluatePixel(s){return [Math.min(255,s.B04*4*255),Math.min(255,s.B03*4*255),Math.min(255,s.B02*4*255)];}
"""
s2_payload = {
    "input": {
        "bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
        "data": [{"type": "sentinel-2-l2a", "dataFilter": {
            "timeRange": {"from": t_from, "to": t_to},
            "maxCloudCoverage": 30, "mosaickingOrder": "leastCC",
        }}],
    },
    "output": {"resx": 10, "resy": 10, "responses": [
        {"identifier": "default", "format": {"type": "image/tiff"}}
    ]},
    "evalscript": S2_ES,
}
log(f"  timeRange: {t_from} → {t_to}")
s2_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "image/tiff"}
s2_r = requests.post(SH_PROCESS_URL, json=s2_payload, headers=s2_headers, timeout=60)
log(f"  status: {s2_r.status_code}  len: {len(s2_r.content)}")
s2_arr = None
if s2_r.status_code == 200:
    try:
        s2_arr = tifffile.imread(io.BytesIO(s2_r.content))
        log(f"  S2 shape: {s2_arr.shape}  dtype: {s2_arr.dtype}")
        if s2_arr.ndim == 3:
            log(f"  S2 std (band 0): {float(s2_arr[:,:,0].std()):.2f}  mean: {float(s2_arr[:,:,0].mean()):.2f}")
        elif s2_arr.ndim == 2:
            log(f"  S2 std: {float(s2_arr.std()):.2f}  mean: {float(s2_arr.mean()):.2f}")
    except Exception as e:
        log(f"  S2 tifffile error: {e}")
else:
    log(f"  S2 error: {s2_r.text[:200]}")

# ── save comparison PNG ───────────────────────────────────────────────────────
log("\n=== STEP 5: Saving smoke PNGs ===")
IMG_PX = 128

def to_rgb(arr, vmin=-15, vmax=5):
    if arr is None: return np.zeros((IMG_PX, IMG_PX, 3), np.uint8)
    db = linear_to_db(arr)
    c = np.clip(db, vmin, vmax)
    n = np.where(np.isfinite(c), ((c - vmin) / (vmax - vmin) * 255), 0).astype(np.uint8)
    rgb = np.stack([n, n, n], axis=-1) if n.ndim == 2 else n[:,:,:3]
    img = Image.fromarray(rgb).resize((IMG_PX, IMG_PX), Image.LANCZOS)
    return np.array(img)

def to_s2_rgb(arr):
    if arr is None: return np.zeros((IMG_PX, IMG_PX, 3), np.uint8)
    img = Image.fromarray(arr.astype(np.uint8) if arr.ndim == 3 else
                          np.stack([arr]*3, axis=-1).astype(np.uint8))
    img = img.resize((IMG_PX, IMG_PX), Image.LANCZOS)
    return np.array(img)

fig, axes = plt.subplots(1, 5, figsize=(15, 3.2), facecolor="black")
fig.subplots_adjust(wspace=0.02)
data = [
    (f"SAR-B relOrbit\n{date_b}", to_rgb(arr_b_old)),
    (f"SAR-B relOrbitNum\n{date_b}", to_rgb(arr_b_new)),
    (f"SAR-A relOrbit\n{date_a or 'n/a'}", to_rgb(arr_a_old)),
    (f"SAR-A relOrbitNum\n{date_a or 'n/a'}", to_rgb(arr_a_new)),
    (f"S2", to_s2_rgb(s2_arr)),
]
for ax, (title, rgb) in zip(axes, data):
    ax.imshow(rgb, origin="upper")
    ax.set_title(title, color="white", fontsize=7, pad=2)
    ax.axis("off")
fig.suptitle(f"jk_11 smoke test  bbox={[round(x,4) for x in bbox]}", color="white", fontsize=8)
out_path = OUT / "jk_11_smoke.png"
fig.savefig(str(out_path), dpi=120, bbox_inches="tight", facecolor="black")
plt.close(fig)
log(f"  saved: {out_path}")
log("\n=== SMOKE DONE ===")
