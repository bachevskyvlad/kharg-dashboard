"""
Optical Kharg-30 gate — шаг 4

Fetches ≤30 clean S2 scenes over Kharg Island, computes shadow_frac for
5 reference tanks, then correlates shadow_frac with SAR db_mean.

Gate criterion (both must pass):
  1. gap(median shadow_frac EMPTY − FULL) is visible (>0)
  2. median per-tank |corr(shadow_frac, db_mean)| ≥ 0.40  (top-20 tanks)

Groups (read-only repères):
  EMPTY: t55_5, t55_10   — structurally idle, settled roof → no shadow
  FULL:  t60_1, t40_33, t55_31 — chronically full (low SAR dB)

Usage:
  python optical_kharg_gate.py
"""

import os, sys, math, time, json, csv
import numpy as np
import pandas as pd
import requests
from pathlib import Path
from datetime import datetime, timedelta
from io import BytesIO

# ── credentials ──────────────────────────────────────────────────────────────
SH_CLIENT_ID     = os.environ.get("CLIENT_ID",     "sh-5215639c-a2e5-47db-8d46-7e28a3150cde")
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "UyosmDOgQ4ejiHbrWpDCT0WzLYgvhfloa3VgOLTtLd7c050tvejLgIVCcXfr3eLXT2JWqfrqjQhBgewF3g6nGF")
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_CATALOG_URL   = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
SH_PROCESS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"

SB_URL = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SB_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

# ── constants ─────────────────────────────────────────────────────────────────
MAX_CLOUD        = 20
RESOLUTION       = 5.0   # m/px
BUFFER_M         = 600
SHADOW_ANGLE     = 120   # degrees, ±60°
DARK_PCTILE      = 30
MIN_ELV_SHADOW   = 10
LOW_SHADOW_ELV   = 65
INNER_MARGIN_M   = 5
MIN_SECTOR_PX    = 3
RATE_LIMIT_S     = 0.5
MAX_RETRIES      = 4
GATE_N_SCENES    = 30
CORR_PAIR_MAX_D  = 3     # days: max S1/S2 date gap for nearest-pair
TOP_N_CORR       = 20    # report median corr over this many top tanks
MIN_PAIRS        = 4     # minimum SAR/S2 pairs to compute corr

# ── gate tanks ────────────────────────────────────────────────────────────────
EMPTY_TANKS = ["t55_5", "t55_10"]
FULL_TANKS  = ["t60_1", "t40_33", "t55_31"]
GATE_TANKS  = EMPTY_TANKS + FULL_TANKS

# ── output ────────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent.parent
OUT_DIR   = BASE_DIR / "diagnostics" / "optical"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── tank coordinates (hard-coded from registry to avoid import side-effects) ──
GATE_TANK_INFO = {
    "t55_5":  {"lat": 29.23596908236423,  "lon": 50.31262041733899,  "radius_m": 55},
    "t55_10": {"lat": 29.237608159424354, "lon": 50.30726790052075,  "radius_m": 55},
    "t60_1":  {"lat": 29.221638820210273, "lon": 50.315043243946754, "radius_m": 60},
    "t40_33": {"lat": 29.230947410364493, "lon": 50.31324186520479,  "radius_m": 40},
    "t55_31": {"lat": 29.232346117474552, "lon": 50.313766336270064, "radius_m": 55},
}

# ── SentinelHub token cache ───────────────────────────────────────────────────
_token_cache: dict = {}

def _get_token() -> str:
    now = time.time()
    if _token_cache.get("expires_at", 0) - now > 30:
        return _token_cache["token"]
    r = requests.post(SH_TOKEN_URL, data={
        "grant_type":    "client_credentials",
        "client_id":     SH_CLIENT_ID,
        "client_secret": SH_CLIENT_SECRET,
    }, timeout=20)
    r.raise_for_status()
    d = r.json()
    _token_cache["token"]      = d["access_token"]
    _token_cache["expires_at"] = now + d.get("expires_in", 3600)
    return _token_cache["token"]


# ── geometry helpers ──────────────────────────────────────────────────────────
def _cluster_bbox(tanks: list, buf_m: float = BUFFER_M):
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buf_m / 111320.0
    dlon = buf_m / (111320.0 * math.cos(math.radians(sum(lats) / len(lats))))
    return [min(lons) - dlon, min(lats) - dlat,
            max(lons) + dlon, max(lats) + dlat]


def _bbox_dims(bbox, res_m):
    lon0, lat0, lon1, lat1 = bbox
    lat_c = (lat0 + lat1) / 2
    km_lon = (lon1 - lon0) * 111320 * math.cos(math.radians(lat_c))
    km_lat = (lat1 - lat0) * 111320
    return max(64, int(km_lon / res_m + 0.5)), max(64, int(km_lat / res_m + 0.5))


def _pixel_coords(lat, lon, bbox, img_w, img_h):
    lon0, lat0, lon1, lat1 = bbox
    col = (lon - lon0) / (lon1 - lon0) * img_w
    row = (lat1 - lat) / (lat1 - lat0) * img_h
    return col, row


# ── ephemeris ─────────────────────────────────────────────────────────────────
def _solar_az_elv(dt_str: str, lat: float, lon: float):
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.strptime(dt_str[:10], "%Y-%m-%d").replace(hour=9)
    ut = dt.hour + dt.minute / 60.0
    doy = dt.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (doy - 1)
    dec = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
           - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma))
    B = math.radians(360 * (doy - 81) / 365)
    eot_min = 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)
    tst = ut + lon / 15.0 + eot_min / 60.0
    ha = math.radians(15.0 * (tst - 12.0))
    lat_r = math.radians(lat)
    sin_elv = max(-1.0, min(1.0,
        math.sin(lat_r) * math.sin(dec) +
        math.cos(lat_r) * math.cos(dec) * math.cos(ha)))
    elv_deg = math.degrees(math.asin(sin_elv))
    cos_elv = math.cos(math.radians(elv_deg))
    if cos_elv < 1e-9:
        return 180.0, round(elv_deg, 1)
    cos_az = max(-1.0, min(1.0,
        (math.sin(dec) - math.sin(lat_r) * sin_elv) /
        (math.cos(lat_r) * cos_elv)))
    az_deg = math.degrees(math.acos(cos_az))
    if ha > 0:
        az_deg = 360.0 - az_deg
    return round(az_deg, 1), round(elv_deg, 1)


# ── catalog search ────────────────────────────────────────────────────────────
def _catalog_search(bbox: list, date_from: str, date_to: str) -> list:
    token = _get_token()
    lat_c = (bbox[1] + bbox[3]) / 2
    lon_c = (bbox[0] + bbox[2]) / 2
    items, next_token = [], None
    while True:
        payload = {
            "bbox":        bbox,
            "datetime":    f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
            "collections": ["sentinel-2-l2a"],
            "limit":       100,
            "filter":      f"eo:cloud_cover<{MAX_CLOUD}",
            "filter-lang": "cql2-text",
            "fields": {"include": ["id", "properties.datetime",
                                   "properties.eo:cloud_cover",
                                   "properties.s2:mean_solar_azimuth",
                                   "properties.s2:mean_solar_zenith"]},
        }
        if next_token:
            payload["next"] = next_token
        for attempt in range(MAX_RETRIES):
            try:
                r = requests.post(SH_CATALOG_URL, json=payload,
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type":  "application/json"}, timeout=30)
                if r.status_code == 401:
                    token = _get_token()
                    continue
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(2 ** attempt)
        resp = r.json()
        for feat in resp.get("features", []):
            p = feat.get("properties", {})
            dt_full = p.get("datetime", "")
            dt_str  = dt_full[:10]
            az_raw  = p.get("s2:mean_solar_azimuth")
            zen_raw = p.get("s2:mean_solar_zenith")
            if az_raw is not None and zen_raw is not None:
                sun_az  = float(az_raw)
                sun_elv = 90.0 - float(zen_raw)
            else:
                sun_az, sun_elv = _solar_az_elv(dt_full or dt_str, lat_c, lon_c)
            items.append({
                "date":          dt_str,
                "scene_id":      feat.get("id", ""),
                "sun_az":        round(sun_az, 2),
                "sun_elv":       round(sun_elv, 2),
                "cloud_cover":   round(float(p.get("eo:cloud_cover", 0)), 2),
            })
        next_token = resp.get("context", {}).get("next")
        if isinstance(resp.get("links"), list):
            next_token = next(
                (lk.get("body", {}).get("next") for lk in resp["links"] if lk.get("rel") == "next"),
                next_token)
        if not next_token or not resp.get("features"):
            break
    return items


# ── tile fetch ────────────────────────────────────────────────────────────────
EVALSCRIPT = """
//VERSION=3
function setup() {
  return { input: [{ bands: ["B04","B03","B02","dataMask"] }],
           output: { bands: 4, sampleType: "UINT8" } };
}
function evaluatePixel(s) {
  return [
    Math.min(255, Math.round(3.5 * s.B04 * 255)),
    Math.min(255, Math.round(3.5 * s.B03 * 255)),
    Math.min(255, Math.round(3.5 * s.B02 * 255)),
    s.dataMask * 255
  ];
}
"""

def _fetch_tile(bbox: list, scene_date: str) -> "np.ndarray | None":
    from PIL import Image
    token = _get_token()
    w_px, h_px = _bbox_dims(bbox, RESOLUTION)
    w_px = min(w_px, 2048)
    h_px = min(h_px, 2048)
    payload = {
        "input": {
            "bounds": {"bbox": bbox,
                       "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{"type": "sentinel-2-l2a",
                      "dataFilter": {
                          "timeRange": {"from": f"{scene_date}T00:00:00Z",
                                        "to":   f"{scene_date}T23:59:59Z"},
                          "maxCloudCoverage": MAX_CLOUD,
                          "mosaickingOrder": "mostRecent",
                      },
                      "processing": {"upsampling": "BICUBIC"}}],
        },
        "output": {
            "width": w_px, "height": h_px,
            "responses": [{"identifier": "default",
                           "format": {"type": "image/png"}}],
        },
        "evalscript": EVALSCRIPT,
    }
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(SH_PROCESS_URL, json=payload,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type":  "application/json",
                         "Accept":        "image/png"},
                timeout=60)
            if r.status_code == 401:
                token = _get_token()
                continue
            if r.status_code in (429, 503):
                time.sleep(2 ** attempt * 2)
                continue
            r.raise_for_status()
            img = Image.open(BytesIO(r.content)).convert("RGBA")
            return np.array(img)
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                print(f"    tile fetch failed for {scene_date}: {e}")
                return None
            time.sleep(2 ** attempt)
    return None


# ── shadow_frac computation ───────────────────────────────────────────────────
def _compute_shadow_frac(tile_rgb: np.ndarray, bbox: list,
                         tank: dict, sun_az: float, sun_elv: float) -> dict:
    H, W = tile_rgb.shape[:2]
    lat, lon = tank["lat"], tank["lon"]
    r_m = tank["radius_m"]
    res_m = (bbox[2] - bbox[0]) * 111320 * math.cos(math.radians(lat)) / W
    cx, cy = _pixel_coords(lat, lon, bbox, W, H)
    r_inner_px = max(1.0, (r_m - INNER_MARGIN_M) / res_m)
    if r_inner_px < 1:
        return {"shadow_frac": float("nan"), "valid": False, "sector_px": 0}

    shadow_centre = sun_az % 360.0  # shadow on sun-facing side
    half_ang = SHADOW_ANGLE / 2.0

    cols = np.arange(W, dtype=np.float32)
    rows = np.arange(H, dtype=np.float32)
    C, R = np.meshgrid(cols, rows)
    dc, dr = C - cx, cy - R
    in_disk = (dc * dc + dr * dr) <= r_inner_px ** 2

    if in_disk.sum() < MIN_SECTOR_PX:
        return {"shadow_frac": float("nan"), "valid": False, "sector_px": 0}

    az_px = (np.degrees(np.arctan2(dc, dr)) + 360.0) % 360.0
    angle_diff = ((az_px - shadow_centre) + 180.0) % 360.0 - 180.0
    in_sector = in_disk & (np.abs(angle_diff) <= half_ang)
    n_sector = int(in_sector.sum())

    if n_sector < MIN_SECTOR_PX:
        return {"shadow_frac": float("nan"), "valid": False, "sector_px": n_sector}

    brightness = tile_rgb[:, :, :3].mean(axis=2).astype(np.float32)
    threshold_p30 = float(np.percentile(brightness[in_disk], DARK_PCTILE))
    sector_pixels = brightness[in_sector]
    dark_count = int((sector_pixels < threshold_p30).sum())
    shadow_frac = dark_count / n_sector

    return {
        "shadow_frac":          round(shadow_frac, 4),
        "valid":                True,
        "sector_px":            n_sector,
        "low_shadow_geometry":  bool(sun_elv > LOW_SHADOW_ELV),
        "threshold_used":       round(threshold_p30, 1),
    }


# ── Supabase: load SAR db_mean for Kharg tanks ────────────────────────────────
def _load_sar(tank_ids: list) -> pd.DataFrame:
    """Return DataFrame: date (str), tank_id, db_mean"""
    headers = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}
    rows, offset, limit = [], 0, 1000
    tank_filter = ",".join(f'"{t}"' for t in tank_ids)
    while True:
        url = (f"{SB_URL}/rest/v1/raw_observations"
               f"?select=date,tank_id,db_mean"
               f"&tank_id=in.({tank_filter})"
               f"&order=date.asc"
               f"&offset={offset}&limit={limit}")
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        if len(batch) < limit:
            break
    if not rows:
        return pd.DataFrame(columns=["date", "tank_id", "db_mean"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")
    return df


# ── nearest-pair correlation ──────────────────────────────────────────────────
def _nearest_pair_corr(sf_df: pd.DataFrame, sar_df: pd.DataFrame) -> pd.DataFrame:
    """
    For each Kharg tank, find nearest SAR date within CORR_PAIR_MAX_D days for
    each S2 scene. Return DataFrame: tank_id, n_pairs, pearson_r.
    """
    results = []
    for tank_id in sf_df["tank_id"].unique():
        sf_t  = sf_df[sf_df["tank_id"] == tank_id][["date", "shadow_frac"]].dropna()
        sar_t = sar_df[sar_df["tank_id"] == tank_id][["date", "db_mean"]].dropna()
        if sf_t.empty or sar_t.empty:
            continue
        sf_dates  = list(sf_t["date"])
        sar_dates = list(sar_t["date"])
        pairs_sf, pairs_sar = [], []
        for s2_date, sf_val in zip(sf_t["date"], sf_t["shadow_frac"]):
            diffs = [abs((s2_date - s1_date).days) for s1_date in sar_dates]
            if not diffs:
                continue
            best_idx = int(np.argmin(diffs))
            if diffs[best_idx] <= CORR_PAIR_MAX_D:
                pairs_sf.append(sf_val)
                pairs_sar.append(float(sar_t.iloc[best_idx]["db_mean"]))
        if len(pairs_sf) < MIN_PAIRS:
            results.append({"tank_id": tank_id, "n_pairs": len(pairs_sf),
                             "pearson_r": float("nan")})
            continue
        corr = float(np.corrcoef(pairs_sf, pairs_sar)[0, 1])
        results.append({"tank_id": tank_id, "n_pairs": len(pairs_sf),
                        "pearson_r": round(corr, 4)})
    return pd.DataFrame(results)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    print("OPTICAL: шаг 4 — запуск Kharg-30 gate\n")

    tanks = list(GATE_TANK_INFO.values())
    for t_id, t_info in GATE_TANK_INFO.items():
        t_info["id"] = t_id

    bbox = _cluster_bbox(tanks)
    print(f"Bbox: {[round(x,4) for x in bbox]}")

    # ── catalog: last 2 years, take most recent GATE_N_SCENES scenes ──────────
    date_to   = datetime.utcnow().strftime("%Y-%m-%d")
    date_from = (datetime.utcnow() - timedelta(days=730)).strftime("%Y-%m-%d")
    print(f"Cataloging S2 scenes {date_from} → {date_to}, cloud<{MAX_CLOUD}% …")
    scenes = _catalog_search(bbox, date_from, date_to)
    scenes.sort(key=lambda x: x["date"], reverse=True)
    scenes = scenes[:GATE_N_SCENES]
    print(f"  {len(scenes)} scenes selected")
    if not scenes:
        print("ERROR: no scenes found — abort")
        sys.exit(1)

    # ── fetch tiles + compute shadow_frac ─────────────────────────────────────
    shadow_rows = []
    for i, sc in enumerate(scenes):
        print(f"  [{i+1:02d}/{len(scenes)}] {sc['date']}  sun_az={sc['sun_az']}° "
              f"elv={sc['sun_elv']}°  cloud={sc['cloud_cover']}%", end="", flush=True)
        if sc["sun_elv"] < MIN_ELV_SHADOW:
            print("  SKIP (low sun)")
            continue
        tile = _fetch_tile(bbox, sc["date"])
        if tile is None:
            print("  SKIP (tile fetch failed)")
            continue
        print(f"  tile {tile.shape[1]}×{tile.shape[0]}px", end="")
        for t_id, t_info in GATE_TANK_INFO.items():
            res = _compute_shadow_frac(tile, bbox, t_info, sc["sun_az"], sc["sun_elv"])
            if res["valid"]:
                shadow_rows.append({
                    "date":       sc["date"],
                    "tank_id":    t_id,
                    "shadow_frac": res["shadow_frac"],
                    "sun_az":     sc["sun_az"],
                    "sun_elv":    sc["sun_elv"],
                    "sector_px":  res["sector_px"],
                    "low_shadow": res.get("low_shadow_geometry", False),
                })
        print()
        time.sleep(RATE_LIMIT_S)

    sf_df = pd.DataFrame(shadow_rows)
    print(f"\n  shadow_frac rows: {len(sf_df)}, tanks: {sf_df['tank_id'].nunique() if not sf_df.empty else 0}")

    if sf_df.empty:
        print("ERROR: no valid shadow_frac measurements — abort")
        sys.exit(1)

    # Convert dates
    sf_df["date"] = pd.to_datetime(sf_df["date"]).dt.date

    # ── gate 1: median shadow_frac by group ───────────────────────────────────
    print("\n── Gate 1: median shadow_frac by group ──")
    med_empty = sf_df[sf_df["tank_id"].isin(EMPTY_TANKS)]["shadow_frac"].median()
    med_full  = sf_df[sf_df["tank_id"].isin(FULL_TANKS)]["shadow_frac"].median()
    gap = med_empty - med_full
    print(f"  EMPTY ({','.join(EMPTY_TANKS)}): median shadow_frac = {med_empty:.4f}")
    print(f"  FULL  ({','.join(FULL_TANKS)}):  median shadow_frac = {med_full:.4f}")
    print(f"  gap (EMPTY − FULL) = {gap:+.4f}")
    gate1_pass = gap > 0
    print(f"  Gate 1: {'PASS ✓' if gate1_pass else 'FAIL ✗'} (gap {'>' if gate1_pass else '<='} 0)")

    # Per-tank detail
    print("\n  Per-tank median shadow_frac:")
    for t_id in GATE_TANKS:
        sub = sf_df[sf_df["tank_id"] == t_id]["shadow_frac"]
        group = "EMPTY" if t_id in EMPTY_TANKS else "FULL"
        print(f"    {t_id:10s} [{group}]  n={len(sub):3d}  median={sub.median():.4f}  "
              f"p25={sub.quantile(0.25):.4f}  p75={sub.quantile(0.75):.4f}")

    # ── gate 2: corr(shadow_frac, db_mean) ────────────────────────────────────
    print("\n── Gate 2: corr(shadow_frac, db_mean) — loading SAR from Supabase ──")
    # Load SAR for all Kharg tanks (not just gate tanks) to get top-20 ranking
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from tank_registry import LOCATIONS
        kharg_loc = next(l for l in LOCATIONS if l["id"] == "kharg")
        all_kharg_tank_ids = [t["id"] for t in kharg_loc["tanks"]]
    except Exception:
        all_kharg_tank_ids = list(GATE_TANK_INFO.keys())

    print(f"  Fetching SAR for {len(all_kharg_tank_ids)} Kharg tanks …")
    sar_df = _load_sar(all_kharg_tank_ids)
    s1_min = sar_df["date"].min() if not sar_df.empty else "n/a"
    s1_max = sar_df["date"].max() if not sar_df.empty else "n/a"
    print(f"  SAR rows: {len(sar_df)}, dates: {s1_min} → {s1_max}")

    # Expand sf_df to all Kharg tanks that were in gate (plus any in sf_df)
    corr_df = _nearest_pair_corr(sf_df, sar_df)
    print(f"\n  Per-tank corr (≤{CORR_PAIR_MAX_D}d pairs, min {MIN_PAIRS} pairs):")
    if corr_df.empty:
        print("  no correlations computed")
        gate2_pass = False
        med_corr   = float("nan")
    else:
        corr_df_valid = corr_df.dropna(subset=["pearson_r"])
        corr_df_valid = corr_df_valid.reindex(
            corr_df_valid["pearson_r"].abs().sort_values(ascending=False).index)
        top20 = corr_df_valid.head(TOP_N_CORR)
        for _, row in corr_df_valid.iterrows():
            group = "EMPTY" if row["tank_id"] in EMPTY_TANKS else \
                    "FULL"  if row["tank_id"] in FULL_TANKS  else "—"
            print(f"    {row['tank_id']:10s} [{group}]  "
                  f"n_pairs={int(row['n_pairs']):3d}  r={row['pearson_r']:+.4f}")
        med_corr = float(top20["pearson_r"].abs().median()) if not top20.empty else float("nan")
        gate2_pass = med_corr >= 0.40
        print(f"\n  top-{min(TOP_N_CORR, len(top20))} median |r| = {med_corr:.4f}")
        print(f"  Gate 2: {'PASS ✓' if gate2_pass else 'FAIL ✗'} (median |r| "
              f"{'≥' if gate2_pass else '<'} 0.40)")

    # ── summary ───────────────────────────────────────────────────────────────
    gate_pass = gate1_pass and gate2_pass
    print("\n══════════════════════════════════════════")
    print(f"OPTICAL шаг 4 GATE: {'PASS ✓' if gate_pass else 'FAIL ✗'}")
    print(f"  Gate 1 gap     = {gap:+.4f}  ({'PASS' if gate1_pass else 'FAIL'})")
    print(f"  Gate 2 med|r|  = {med_corr:.4f}  ({'PASS' if gate2_pass else 'FAIL'})")
    print("══════════════════════════════════════════")

    # ── save results ──────────────────────────────────────────────────────────
    out_json = OUT_DIR / "kharg_gate_result.json"
    result = {
        "gate_pass":         gate_pass,
        "gate1_gap":         round(gap, 6),
        "gate1_pass":        gate1_pass,
        "gate2_med_corr":    round(med_corr, 6) if not math.isnan(med_corr) else None,
        "gate2_pass":        gate2_pass,
        "med_empty":         round(med_empty, 6),
        "med_full":          round(med_full, 6),
        "n_s2_scenes":       len(scenes),
        "n_shadow_rows":     len(sf_df),
        "n_sar_rows":        len(sar_df),
        "sar_date_min":      str(s1_min),
        "sar_date_max":      str(s1_max),
    }
    out_json.write_text(json.dumps(result, indent=2))
    print(f"\nResults saved: {out_json}")

    sf_df.to_csv(OUT_DIR / "kharg_gate_shadow_frac.csv", index=False)
    if not corr_df.empty:
        corr_df.to_csv(OUT_DIR / "kharg_gate_corr.csv", index=False)

    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
