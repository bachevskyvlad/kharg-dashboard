"""
stage_h5_alfaw_panels.py — Этап 0 (H5 blocker)
================================================
Generates SAR-A + SAR-B + S2 optical panels for all 24 Al-Faw (al_flaw) tanks.
Also outputs optical r=40 measurement (shadow_frac) per tank per scene.

Outputs (all in diagnostics/al_faw/panels/):
  panels/t_{tank_id}.png  — 3-column panel: SAR-ASC | SAR-DESC | S2-best
  panels/alfaw_optical_r40.csv  — date, tank_id, shadow_frac, sun_az, sun_elv
  panels/alfaw_sar_asc_overview.png  — overview grid SAR-ASC
  panels/alfaw_sar_desc_overview.png — overview grid SAR-DESC
  panels/alfaw_s2_overview.png       — overview grid S2
  panels/run.txt   — log

Panels go to chat; verdicts are the analyst's.
"""

import os, sys, math, io
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests

# ── Credentials ──────────────────────────────────────────────────────────────
SB_URL = os.environ.get("SUPABASE_URL", "https://yoekhkczwdeyauwarsyy.supabase.co").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM")
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or "sh-5215639c-a2e5-47db-8d46-7e28a3150cde"
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or "UyosmDOgQ4ejiHbrWpDCT0WzLYgvhfloa3VgOLTtLd7c050tvejLgIVCcXfr3eLXT2JWqfrqjQhBgewF3g6nGF"

# ── Tank registry — al_flaw, 24 tanks, uniform r=40m ─────────────────────────
TANKS = [
    {"id": "af_0",  "lat": 29.976037712391882, "lon": 48.44195552300743},
    {"id": "af_1",  "lat": 29.97446023561727,  "lon": 48.44041073298363},
    {"id": "af_2",  "lat": 29.973482237683964, "lon": 48.44170682926327},
    {"id": "af_3",  "lat": 29.97506237979022,  "lon": 48.443256604287775},
    {"id": "af_4",  "lat": 29.972052466075166, "lon": 48.44360540262179},
    {"id": "af_5",  "lat": 29.973613605327138, "lon": 48.44517136039961},
    {"id": "af_6",  "lat": 29.971043737749962, "lon": 48.44490239092673},
    {"id": "af_7",  "lat": 29.972645241700267, "lon": 48.44646203210755},
    {"id": "af_8",  "lat": 29.970602225788706, "lon": 48.44914349229505},
    {"id": "af_9",  "lat": 29.96964506640903,  "lon": 48.45042448972437},
    {"id": "af_10", "lat": 29.969053578374012, "lon": 48.44757323726147},
    {"id": "af_11", "lat": 29.968074369587153, "lon": 48.44888119527686},
    {"id": "af_12", "lat": 29.967562585491844, "lon": 48.44613477210609},
    {"id": "af_13", "lat": 29.966616705980595, "lon": 48.44740303358956},
    {"id": "af_14", "lat": 29.965160381102958, "lon": 48.449324515383225},
    {"id": "af_15", "lat": 29.966613187227228, "lon": 48.45079062045803},
    {"id": "af_16", "lat": 29.964164829146835, "lon": 48.450625111425616},
    {"id": "af_17", "lat": 29.965658494437534, "lon": 48.45208349893114},
    {"id": "af_18", "lat": 29.961779354173064, "lon": 48.45380166725049},
    {"id": "af_19", "lat": 29.96274476143991,  "lon": 48.45253449582525},
    {"id": "af_20", "lat": 29.964218037290507, "lon": 48.45399437056702},
    {"id": "af_21", "lat": 29.96326674821542,  "lon": 48.45527466098349},
    {"id": "af_22", "lat": 29.964822804270934, "lon": 48.45681522152186},
    {"id": "af_23", "lat": 29.96580178961507,  "lon": 48.45553712042206},
]
R_M   = 40.0    # uniform radius (ТЗ)
H_MAX = 14.0

# Bounding box for Al-Faw terminal (covers all 24 tanks + buffer)
LAT_MIN, LAT_MAX = 29.958, 29.980
LON_MIN, LON_MAX = 48.436, 48.462
BBOX = [LON_MIN, LAT_MIN, LON_MAX, LAT_MAX]

LOS_AZIMUTH = 282   # Sentinel-1 DESC orbit at al_flaw
SHADOW_ANGLE = 120  # ±60° sector

RESOLUTION_M = 10   # S2 native resolution for panels (10m = B4/B3/B2)
SAR_RES_M    = 10   # SAR display resolution for panels (10m GRD)

BASE_DIR  = Path(__file__).parent.parent
OUT_DIR   = BASE_DIR / "diagnostics" / "al_faw" / "panels"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TILES_DIR = OUT_DIR / "tiles"
TILES_DIR.mkdir(exist_ok=True)


def log(msg):
    print(msg, flush=True)


# ── SH token ────────────────────────────────────────────────────────────────
_token_cache = {}

def _get_sh_token():
    import time
    now = time.time()
    if _token_cache.get("expires_at", 0) > now + 60:
        return _token_cache["access_token"]
    r = requests.post(
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
        data={"grant_type": "client_credentials",
              "client_id": SH_CLIENT_ID,
              "client_secret": SH_CLIENT_SECRET},
        timeout=30,
    )
    r.raise_for_status()
    d = r.json()
    _token_cache["access_token"] = d["access_token"]
    _token_cache["expires_at"]   = now + d["expires_in"]
    return d["access_token"]


# ── Solar ephemeris fallback ─────────────────────────────────────────────────
def _solar_az_elv(dt_iso: str, lat: float, lon: float) -> tuple:
    from datetime import datetime, timezone, timedelta
    try:
        dt = datetime.fromisoformat(dt_iso.replace("Z", "+00:00"))
    except Exception:
        dt = datetime(int(dt_iso[:4]), int(dt_iso[5:7]), int(dt_iso[8:10]), 10, 30,
                      tzinfo=timezone.utc)
    day_of_year = dt.timetuple().tm_yday
    B  = math.radians((360 / 365) * (day_of_year - 81))
    eq = 9.87 * math.sin(2*B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)
    lstm = 15 * round(lon / 15)
    tc   = 4 * (lon - lstm) + eq
    lst  = dt.hour + dt.minute / 60 + dt.second / 3600 + tc / 60
    hra  = math.radians(15 * (lst - 12))
    lat_r = math.radians(lat)
    decl  = math.radians(23.45 * math.sin(math.radians((360/365) * (day_of_year - 81))))
    sin_alt = (math.sin(lat_r)*math.sin(decl) +
               math.cos(lat_r)*math.cos(decl)*math.cos(hra))
    sin_alt = max(-1.0, min(1.0, sin_alt))
    elv = math.degrees(math.asin(sin_alt))
    cos_az = ((math.sin(decl) - math.sin(lat_r)*sin_alt) /
              (math.cos(lat_r)*math.cos(math.asin(sin_alt)) + 1e-12))
    cos_az = max(-1.0, min(1.0, cos_az))
    az = math.degrees(math.acos(cos_az))
    if math.sin(hra) > 0:
        az = 360 - az
    return round(az, 2), round(elv, 2)


# ── S2 catalog query ─────────────────────────────────────────────────────────
def _query_s2_catalog(bbox, date_from="2023-01-01", max_cloud=30, n_scenes=5):
    """Return up to n_scenes S2 scenes sorted by cloud_cover asc, date desc."""
    token = _get_sh_token()
    date_to = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = {
        "bbox": bbox,
        "datetime": f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
        "collections": ["sentinel-2-l2a"],
        "limit": 100,
        "filter": {"op": "lte", "args": [{"property": "eo:cloud_cover"}, max_cloud]},
        "filter-lang": "cql2-json",
    }
    r = requests.post(
        "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    r.raise_for_status()
    features = r.json().get("features", [])

    scenes = []
    lat_c = (bbox[1] + bbox[3]) / 2
    lon_c = (bbox[0] + bbox[2]) / 2
    for f in features:
        p = f.get("properties", {})
        dt_str = (f.get("datetime") or p.get("datetime", ""))[:10]
        dt_full = f.get("datetime") or p.get("datetime", "")
        cc = float(p.get("eo:cloud_cover", 50))
        sun_az_raw = p.get("s2:mean_solar_azimuth")
        sun_zen_raw = p.get("s2:mean_solar_zenith")
        if sun_az_raw is not None and sun_zen_raw is not None:
            sun_az  = float(sun_az_raw)
            sun_elv = 90.0 - float(sun_zen_raw)
        else:
            sun_az, sun_elv = _solar_az_elv(dt_full or dt_str, lat_c, lon_c)
        if sun_elv < 20:
            continue
        scenes.append({"date": dt_str, "cloud_cover": cc,
                        "sun_azimuth": sun_az, "sun_elevation": sun_elv})

    scenes.sort(key=lambda s: (s["cloud_cover"], -int(s["date"].replace("-", ""))))
    # deduplicate by date
    seen, out = set(), []
    for s in scenes:
        if s["date"] not in seen:
            seen.add(s["date"])
            out.append(s)
    return out[:n_scenes]


# ── S1 catalog query ─────────────────────────────────────────────────────────
def _query_s1_catalog(bbox, orbit_dir="DESCENDING", n_scenes=5):
    """Return up to n_scenes S1 scenes."""
    token = _get_sh_token()
    date_to   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_from = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")
    body = {
        "bbox": bbox,
        "datetime": f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
        "collections": ["sentinel-1-grd"],
        "limit": 50,
        "filter": {
            "op": "and",
            "args": [
                {"op": "=", "args": [{"property": "sat:orbit_state"}, orbit_dir.lower()]},
            ],
        },
        "filter-lang": "cql2-json",
        "fields": {"include": ["id", "datetime", "properties.sat:orbit_state"]},
    }
    r = requests.post(
        "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search",
        json=body,
        headers={"Authorization": f"Bearer {_get_sh_token()}"},
        timeout=60,
    )
    r.raise_for_status()
    features = r.json().get("features", [])
    scenes = []
    seen = set()
    for f in sorted(features, key=lambda x: x.get("datetime", ""), reverse=True):
        dt = (f.get("datetime", ""))[:10]
        if dt not in seen:
            seen.add(dt)
            scenes.append({"date": dt})
        if len(scenes) >= n_scenes:
            break
    return scenes


# ── Tile fetch ───────────────────────────────────────────────────────────────
def _fetch_tile(bbox, date_str, sensor="S2", res_m=10):
    """
    Fetch a single tile as RGB numpy array (H,W,3) uint8.
    sensor: "S2" (Sentinel-2 TrueColor) or "S1" (Sentinel-1 VV grayscale→RGB).
    """
    token = _get_sh_token()
    w_m = (bbox[2] - bbox[0]) * 111320 * math.cos(math.radians((bbox[1]+bbox[3])/2))
    h_m = (bbox[3] - bbox[1]) * 111320
    width  = max(64, min(2048, int(w_m / res_m)))
    height = max(64, min(2048, int(h_m / res_m)))

    if sensor == "S2":
        evalscript = """
//VERSION=3
function setup(){return{input:[{bands:["B04","B03","B02"],units:"REFLECTANCE"}],output:{bands:3}};}
function evaluatePixel(s){
  var gain=3.5;
  return [Math.min(1,s.B04*gain),Math.min(1,s.B03*gain),Math.min(1,s.B02*gain)];
}"""
        collection = "sentinel-2-l2a"
        data_field = {"type": "sentinel-2-l2a",
                      "dataFilter": {"timeRange": {
                          "from": f"{date_str}T00:00:00Z",
                          "to":   f"{date_str}T23:59:59Z"},
                          "maxCloudCoverage": 50}}
    else:  # S1
        evalscript = """
//VERSION=3
function setup(){return{input:[{bands:["VV"],units:"LINEAR_POWER"}],output:{bands:3}};}
function evaluatePixel(s){
  var db=10*Math.log10(s.VV+1e-10);
  var v=Math.min(1,Math.max(0,(db+20)/30));
  return [v,v,v];
}"""
        collection = "sentinel-1-grd"
        orbit = "DESCENDING" if sensor == "S1_DESC" else "ASCENDING"
        data_field = {"type": "sentinel-1-grd",
                      "dataFilter": {"timeRange": {
                          "from": f"{date_str}T00:00:00Z",
                          "to":   f"{date_str}T23:59:59Z"},
                          "acquisitionMode": "IW",
                          "orbitDirection": orbit}}

    body = {
        "input": {"bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
                  "data": [data_field]},
        "output": {"width": width, "height": height,
                   "responses": [{"identifier": "default",
                                  "format": {"type": "image/png"}}]},
        "evalscript": evalscript,
    }
    r = requests.post(
        "https://sh.dataspace.copernicus.eu/api/v1/process",
        json=body,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        timeout=120,
    )
    if r.status_code != 200:
        return None
    from PIL import Image
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    return np.array(img)


# ── Pixel helpers ────────────────────────────────────────────────────────────
def _pix(lat, lon, bbox, W, H):
    px = (lon - bbox[0]) / (bbox[2] - bbox[0]) * W
    py = (1 - (lat - bbox[1]) / (bbox[3] - bbox[1])) * H
    return px, py


def _res_m_per_px(bbox, W, lat):
    return (bbox[2] - bbox[0]) * 111320 * math.cos(math.radians(lat)) / W


# ── Shadow metric (optical, r=40) ────────────────────────────────────────────
def _shadow_frac(rgb, cx, cy, r_px, sun_az, shadow_angle=120):
    from PIL import Image
    H, W = rgb.shape[:2]
    brightness = (rgb[:,:,0].astype(float)*0.299 +
                  rgb[:,:,1].astype(float)*0.587 +
                  rgb[:,:,2].astype(float)*0.114)
    r_inner = max(1.0, r_px * 0.85)
    ys, xs = np.mgrid[0:H, 0:W]
    dx = xs - cx
    dy = ys - cy
    dist = np.sqrt(dx**2 + dy**2)
    in_disk = dist <= r_inner

    shadow_centre = (sun_az + 180.0) % 360.0
    half_ang = shadow_angle / 2.0
    az_px = (np.degrees(np.arctan2(dx, -dy)) + 360) % 360
    angle_diff = ((az_px - shadow_centre) + 180.0) % 360.0 - 180.0
    in_sector = in_disk & (np.abs(angle_diff) <= half_ang)
    n_sector = int(in_sector.sum())
    if n_sector < 3:
        return float("nan"), n_sector
    thresh = np.percentile(brightness[in_disk], 30)
    dark = int((brightness[in_sector] < thresh).sum())
    return round(dark / n_sector, 4), n_sector


# ── Per-tank panel ────────────────────────────────────────────────────────────
def _draw_panel(tank, rgb_asc, rgb_desc, rgb_s2, bbox, sun_az_s2):
    """Return a 3-column panel PIL Image for one tank."""
    from PIL import Image, ImageDraw, ImageFont
    CROP_R = 3.5   # multiples of r_px

    results = {}
    panels  = []
    labels  = ["SAR-ASC", "SAR-DESC", "S2"]

    for lbl, rgb in zip(labels, [rgb_asc, rgb_desc, rgb_s2]):
        if rgb is None:
            # grey placeholder
            panels.append(Image.fromarray(np.full((200, 200, 3), 80, dtype=np.uint8)))
            results[lbl] = float("nan")
            continue
        H, W = rgb.shape[:2]
        res_m = _res_m_per_px(bbox, W, tank["lat"])
        r_px  = max(2.0, R_M / res_m)
        cx, cy = _pix(tank["lat"], tank["lon"], bbox, W, H)
        crop_r = int(r_px * CROP_R)
        x0, y0 = max(0, int(cx)-crop_r), max(0, int(cy)-crop_r)
        x1, y1 = min(W, int(cx)+crop_r), min(H, int(cy)+crop_r)

        img  = Image.fromarray(rgb[y0:y1, x0:x1])
        draw = ImageDraw.Draw(img)
        cx_c, cy_c = cx - x0, cy - y0

        # tank circle (red)
        draw.ellipse([cx_c-r_px, cy_c-r_px, cx_c+r_px, cy_c+r_px],
                     outline=(255, 60, 60), width=2)

        # S2: draw shadow sector
        if lbl == "S2" and not math.isnan(sun_az_s2):
            sc = (sun_az_s2 + 180.0) % 360.0
            ha = SHADOW_ANGLE / 2.0
            r_i = max(1.0, r_px * 0.85)
            def az_pil(az): return (-az + 90.0) % 360.0
            draw.arc([cx_c-r_i, cy_c-r_i, cx_c+r_i, cy_c+r_i],
                     start=az_pil(sc+ha), end=az_pil(sc-ha),
                     fill=(0, 220, 220), width=2)
            sf, _ = _shadow_frac(rgb[y0:y1, x0:x1],
                                  cx_c, cy_c, r_i, sun_az_s2)
            results[lbl] = sf
            # label
            draw.text((4, 4),
                      f"sf={sf:.3f}  sun_az={sun_az_s2:.0f}°",
                      fill=(255,255,0))

        # SAR: draw SAR sector (DESC orbit LOS)
        if lbl in ("SAR-ASC", "SAR-DESC"):
            sar_az = LOS_AZIMUTH if lbl == "SAR-DESC" else (LOS_AZIMUTH + 180) % 360
            sc2 = (sar_az + 180.0) % 360.0
            ha2 = SHADOW_ANGLE / 2.0
            r_i2 = max(1.0, r_px * 0.85)
            def az_pil2(az): return (-az + 90.0) % 360.0
            draw.arc([cx_c-r_i2, cy_c-r_i2, cx_c+r_i2, cy_c+r_i2],
                     start=az_pil2(sc2+ha2), end=az_pil2(sc2-ha2),
                     fill=(255, 200, 0), width=2)

        panels.append(img.resize((200, 200), Image.LANCZOS))

    # Compose 3-column strip
    strip_w  = 3 * 200 + 2 * 4
    strip_h  = 200 + 24
    strip    = Image.new("RGB", (strip_w, strip_h), (30, 30, 30))
    for i, (pnl, lbl) in enumerate(zip(panels, labels)):
        x_off = i * (200 + 4)
        strip.paste(pnl, (x_off, 24))
        d = ImageDraw.Draw(strip)
        d.text((x_off + 4, 4), lbl, fill=(220, 220, 220))

    return strip, results


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    log(f"=== H5 Al-Faw Panels — {len(TANKS)} tanks, r={R_M}m ===")
    log(f"  bbox: {BBOX}")

    # 1. Query catalogs
    log("\n[S2 catalog]")
    s2_scenes  = _query_s2_catalog(BBOX, n_scenes=3)
    log(f"  {len(s2_scenes)} S2 scenes: {[s['date'] for s in s2_scenes]}")

    log("\n[S1-ASC catalog]")
    s1a_scenes = _query_s1_catalog(BBOX, orbit_dir="ASCENDING",  n_scenes=3)
    log(f"  {len(s1a_scenes)} S1-ASC scenes: {[s['date'] for s in s1a_scenes]}")

    log("\n[S1-DESC catalog]")
    s1d_scenes = _query_s1_catalog(BBOX, orbit_dir="DESCENDING", n_scenes=3)
    log(f"  {len(s1d_scenes)} S1-DESC scenes: {[s['date'] for s in s1d_scenes]}")

    if not s2_scenes and not s1a_scenes and not s1d_scenes:
        log("FATAL: no scenes found — check credentials and bbox")
        sys.exit(1)

    # 2. Fetch best tiles
    best_s2   = s2_scenes[0]  if s2_scenes  else None
    best_s1a  = s1a_scenes[0] if s1a_scenes else None
    best_s1d  = s1d_scenes[0] if s1d_scenes else None

    log("\n[Fetching tiles]")
    rgb_s2 = rgb_asc = rgb_desc = None

    if best_s2:
        log(f"  S2  {best_s2['date']} cloud={best_s2['cloud_cover']:.0f}%")
        rgb_s2 = _fetch_tile(BBOX, best_s2["date"], sensor="S2", res_m=RESOLUTION_M)
        log(f"  S2 tile: {'OK' if rgb_s2 is not None else 'FAIL'} "
            f"{rgb_s2.shape if rgb_s2 is not None else ''}")

    if best_s1a:
        log(f"  S1-ASC  {best_s1a['date']}")
        rgb_asc = _fetch_tile(BBOX, best_s1a["date"], sensor="S1_ASC", res_m=SAR_RES_M)
        log(f"  S1-ASC tile: {'OK' if rgb_asc is not None else 'FAIL'}")

    if best_s1d:
        log(f"  S1-DESC {best_s1d['date']}")
        rgb_desc = _fetch_tile(BBOX, best_s1d["date"], sensor="S1_DESC", res_m=SAR_RES_M)
        log(f"  S1-DESC tile: {'OK' if rgb_desc is not None else 'FAIL'}")

    if rgb_s2 is None and rgb_asc is None and rgb_desc is None:
        log("FATAL: all tile fetches failed")
        sys.exit(1)

    sun_az_s2 = best_s2["sun_azimuth"] if best_s2 else float("nan")

    # 3. Per-tank panels
    log(f"\n[Per-tank panels] sun_az_s2={sun_az_s2:.1f}° (S2 {best_s2['date'] if best_s2 else 'N/A'})")
    from PIL import Image
    optical_rows = []
    panel_paths  = []

    for tank in TANKS:
        tid   = tank["id"]
        strip, metrics = _draw_panel(tank, rgb_asc, rgb_desc, rgb_s2, BBOX, sun_az_s2)
        out_path = OUT_DIR / f"panel_{tid}.png"
        strip.save(str(out_path))
        panel_paths.append(out_path)

        sf = metrics.get("S2", float("nan"))
        optical_rows.append({
            "tank_id":       tid,
            "date_s2":       best_s2["date"] if best_s2 else "",
            "shadow_frac":   sf,
            "sun_az_s2":     sun_az_s2,
            "sun_elv_s2":    best_s2["sun_elevation"] if best_s2 else float("nan"),
            "date_sar_asc":  best_s1a["date"] if best_s1a else "",
            "date_sar_desc": best_s1d["date"] if best_s1d else "",
        })
        log(f"  {tid}  sf={sf:.4f}" if not math.isnan(sf) else f"  {tid}  sf=NaN")

    # 4. Optical r=40 table
    df_opt = pd.DataFrame(optical_rows)
    csv_path = OUT_DIR / "alfaw_optical_r40.csv"
    df_opt.to_csv(csv_path, index=False)
    log(f"\nOptical r=40 table → {csv_path}")
    log(df_opt[["tank_id","shadow_frac"]].to_string(index=False))

    # 5. Overview grids (4×6 layout)
    log("\n[Overview grids]")
    from PIL import Image
    N_TANKS = len(TANKS)
    COLS, ROWS = 6, 4
    CELL = 200

    for label, rgb, date_str in [
        ("s2",       rgb_s2,   best_s2["date"]  if best_s2  else ""),
        ("sar_asc",  rgb_asc,  best_s1a["date"] if best_s1a else ""),
        ("sar_desc", rgb_desc, best_s1d["date"] if best_s1d else ""),
    ]:
        if rgb is None:
            log(f"  overview {label}: skipped (no tile)")
            continue
        H, W = rgb.shape[:2]
        grid = Image.new("RGB", (COLS*CELL + (COLS-1)*4, ROWS*CELL + 20*(ROWS+1)), (20,20,20))
        from PIL import ImageDraw
        gd = ImageDraw.Draw(grid)
        gd.text((10, 4), f"Al-Faw {label.upper()} {date_str}  r=40m", fill=(200,200,200))

        for idx, tank in enumerate(TANKS):
            col = idx % COLS
            row = idx // COLS
            if row >= ROWS:
                break
            res_m = _res_m_per_px(BBOX, W, tank["lat"])
            r_px  = max(2.0, R_M / res_m)
            cx, cy = _pix(tank["lat"], tank["lon"], BBOX, W, H)
            cr = int(r_px * 3.0)
            x0, y0 = max(0, int(cx)-cr), max(0, int(cy)-cr)
            x1, y1 = min(W, int(cx)+cr), min(H, int(cy)+cr)
            cell_img = Image.fromarray(rgb[y0:y1, x0:x1]).resize((CELL, CELL), Image.LANCZOS)
            draw_c = ImageDraw.Draw(cell_img)
            cx_c = cy_c = CELL // 2
            r_pxc = CELL // (2 * 3)
            draw_c.ellipse([cx_c-r_pxc, cy_c-r_pxc, cx_c+r_pxc, cy_c+r_pxc],
                           outline=(255,60,60), width=2)
            draw_c.text((2, 2), tank["id"], fill=(255,255,0))
            gx = col * (CELL + 4)
            gy = 20 + row * (CELL + 20)
            grid.paste(cell_img, (gx, gy))

        ov_path = OUT_DIR / f"alfaw_{label}_overview.png"
        grid.save(str(ov_path))
        log(f"  overview {label}: {ov_path.name}")

    log(f"\nDone. {len(panel_paths)} per-tank panels + 3 overviews → {OUT_DIR}")
    log("Panels to chat — analyst verdicts per tank.")


if __name__ == "__main__":
    main()
