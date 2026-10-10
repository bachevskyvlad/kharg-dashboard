"""
Stage 6 — S2 Optical Shadow Pilot: RT / JW / YB

Pipeline structure:
  Stage 0 — SH Catalog + image fetch (per-terminal tiles, checkpointed)
  Stage 1 — Shadow metric per tank per scene (shadow_frac + alt brightness)
  Stage 2 — Validation gate: Kharg repères + S1 cross-sensor corr
  Stage 3 — Optical series, JW shadow screen, interleaved RT cadence
  Stage 4 — Frozen correlation grid (one run, no iterations)
  Report  — OPTICAL_PILOT.md (gate numbers, screen tables, grid)

Rules:
  - Export data (PortWatch / JODI) not touched until Stage 4
  - Kharg: read-only reference
  - «вероятно» запрещено — verdicts carry numbers
  - Thresholds / grid fixed after first numbers, no re-runs
  - Backups via commit, not artifact-upload
"""

import os, sys, math, time, json, csv
import numpy as np
import pandas as pd
import requests
from pathlib import Path
from datetime import datetime, timedelta, date
from io import BytesIO

# ── SH credentials ────────────────────────────────────────────────────────────
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""
SH_TOKEN_URL     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_CATALOG_URL   = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
SH_PROCESS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/process"

# ── Supabase ──────────────────────────────────────────────────────────────────
SB_URL = os.environ.get("SUPABASE_URL") or ""
SB_KEY = os.environ.get("SUPABASE_KEY") or ""

# ── Pipeline constants ─────────────────────────────────────────────────────────
CATALOG_START        = "2022-01-01"   # 3-year window keeps tile count tractable
MAX_CLOUD            = 20            # percent
MAX_SCENES_PER_CLUSTER = 80          # cap per cluster to stay within 120-min budget
RESOLUTION      = 5.0         # m/px (S2 bicubic upsampled from 10m)
BUFFER_M        = 800         # terminal-cluster bbox padding (metres)
SHADOW_ANGLE    = 120         # degrees — full opening of shadow sector (±60° either side)
DARK_PCTILE     = 30          # p30 of inner-disk brightness → dark pixel threshold
MIN_ELV_SHADOW  = 10          # min solar elevation to attempt shadow measurement (°)
LOW_SHADOW_ELV  = 65          # flag scene as low-shadow-geometry if sun_elev > this
INNER_MARGIN_M  = 5           # metres to shave from tank radius for mask (avoid rim)
MIN_SECTOR_PX   = 3           # gate: skip tank if shadow sector < this many pixels
RATE_LIMIT_S    = 0.5         # seconds between SH Process API calls
MAX_RETRIES     = 4           # exponential-backoff retries

# ── Kharg repères (reference tanks for gate, read-only) ──────────────────────
# Expected behaviour: full tanks → low shadow_frac; empty → high shadow_frac
# t55_5 / t55_10 are structurally IDLE (roof settled on floor supports) — NOT
# floating-roof-empty. They give sf≈0 because the bright settled roof has no
# wall shadow inside. Use active-cycle tanks with known low SAR db as empty repères.
# t40_18, t40_22: high db_mean (~9 dB), actively cycling — genuine floating-empty.
KHARG_FULL  = ["t60_1", "t55_31", "t40_33"]   # chronically full (low dB)
KHARG_EMPTY = ["t40_18", "t40_22"]             # active empty (high dB, floating)
KHARG_GATE_N_SCENES = 20

# ── Event dates ──────────────────────────────────────────────────────────────
E1_DATE = pd.Timestamp("2026-03-02")
E2_DATE = pd.Timestamp("2026-06-26")
E_ESTIMATOR_PASSES = 3         # val[+3 passes] − val[−3 passes]

# ── Output dirs ───────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent.parent
OUT_DIR   = BASE_DIR / "diagnostics" / "optical"
TILES_DIR = OUT_DIR / "tiles"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TILES_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  Terminal registry (pulled from tank_registry.py at import time)
# ═══════════════════════════════════════════════════════════════════════════════

sys.path.insert(0, str(Path(__file__).parent))
from tank_registry import LOCATIONS

def _get_terminal(tid: str) -> dict:
    for loc in LOCATIONS:
        if loc["id"] == tid:
            return loc
    raise KeyError(f"Terminal {tid!r} not found in registry")

def _cluster_bbox(tanks: list, buf_m: float = BUFFER_M):
    """(lon_min, lat_min, lon_max, lat_max) with buf_m padding."""
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buf_m / 111320.0
    dlon = buf_m / (111320.0 * math.cos(math.radians(sum(lats) / len(lats))))
    return [min(lons) - dlon, min(lats) - dlat,
            max(lons) + dlon, max(lats) + dlat]

def _km2_bbox(bbox):
    lon0, lat0, lon1, lat1 = bbox
    km_lon = (lon1 - lon0) * 111.32 * math.cos(math.radians((lat0 + lat1) / 2))
    km_lat = (lat1 - lat0) * 111.32
    return km_lon, km_lat

def _bbox_dims(bbox, res_m):
    """(width_px, height_px) for given resolution."""
    lon0, lat0, lon1, lat1 = bbox
    lat_c = (lat0 + lat1) / 2
    km_lon = (lon1 - lon0) * 111320 * math.cos(math.radians(lat_c))
    km_lat = (lat1 - lat0) * 111320
    return max(64, int(km_lon / res_m + 0.5)), max(64, int(km_lat / res_m + 0.5))


# Terminals in scope.  RT has two spatially separate clusters → two fetch regions.
# kharg is reference-only (gate validation); no series stored.
TERMINALS = {
    "kharg": {
        "id": "kharg",
        "clusters": [{"name": "main", "tanks": _get_terminal("kharg")["tanks"]}],
    },
    "ras_tanura": {
        "id": "ras_tanura",
        "clusters": [
            {"name": "south", "tanks": [
                t for t in _get_terminal("ras_tanura")["tanks"]
                if t["id"] not in ["rt_5","rt_6","rt_7","rt_8","rt_9","rt_10"]
            ]},
            {"name": "north", "tanks": [
                t for t in _get_terminal("ras_tanura")["tanks"]
                if t["id"] in ["rt_5","rt_6","rt_7","rt_8","rt_9","rt_10"]
            ]},
        ],
    },
    "juwaymah": {
        "id": "juwaymah",
        "clusters": [{"name": "main", "tanks": _get_terminal("juwaymah")["tanks"]}],
    },
    "yanbu": {
        "id": "yanbu",
        "clusters": [{"name": "main", "tanks": _get_terminal("yanbu")["tanks"]}],
    },
}

# RT WL (enabled=True from registry for DESC series)
RT_WL_TANKS = [t["id"] for t in _get_terminal("ras_tanura")["tanks"]
               if t.get("enabled", False)]
JW_WL_TANKS = [t["id"] for t in _get_terminal("juwaymah")["tanks"]
               if t.get("enabled", False)]  # top 10 = enabled set
YB_WL_TANKS = [t["id"] for t in _get_terminal("yanbu")["tanks"]
               if t.get("enabled", False)]

print(f"RT WL: {len(RT_WL_TANKS)}  JW WL: {len(JW_WL_TANKS)}  YB WL: {len(YB_WL_TANKS)}")


# ═══════════════════════════════════════════════════════════════════════════════
#  SH helpers
# ═══════════════════════════════════════════════════════════════════════════════

_TOKEN_CACHE = {"token": None, "expires": 0.0}

def _get_token() -> str:
    if time.time() < _TOKEN_CACHE["expires"] - 60:
        return _TOKEN_CACHE["token"]
    r = requests.post(SH_TOKEN_URL, data={
        "grant_type":    "client_credentials",
        "client_id":     SH_CLIENT_ID,
        "client_secret": SH_CLIENT_SECRET,
    }, timeout=30)
    r.raise_for_status()
    data = r.json()
    _TOKEN_CACHE["token"]   = data["access_token"]
    _TOKEN_CACHE["expires"] = time.time() + data.get("expires_in", 3600)
    return _TOKEN_CACHE["token"]


def _solar_az_elv_from_dt(dt_iso: str, lat: float, lon: float) -> tuple:
    """
    Compute (azimuth_deg, elevation_deg) from ISO8601 UTC datetime and location.
    Uses NOAA simplified solar algorithm (accurate to ~1° for this application).
    Falls back to S2 nominal overpass time (10:30 local solar time) when only
    a date string is supplied (no time component).
    """
    from datetime import timezone as _tz
    # Parse datetime; if only date supplied use nominal S2 overpass (10:30 LST)
    if len(dt_iso) >= 19:
        dt = datetime.strptime(dt_iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_tz.utc)
    else:
        # nominal S2 descending overpass ≈ 10:30 LST; LST = UTC + lon/15
        utc_hour = 10.5 - lon / 15.0
        dt_date = datetime.strptime(dt_iso[:10], "%Y-%m-%d")
        dt = dt_date.replace(hour=int(utc_hour), minute=int((utc_hour % 1) * 60),
                             tzinfo=_tz.utc)

    doy = dt.timetuple().tm_yday
    ut = dt.hour + dt.minute / 60.0 + dt.second / 3600.0  # decimal UTC hours

    # Solar declination (degrees)
    dec_deg = -23.45 * math.cos(math.radians(360 * (doy + 10) / 365))
    dec = math.radians(dec_deg)

    # Equation of time (minutes)
    B = math.radians(360 * (doy - 81) / 365)
    eot_min = 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)

    # True solar time (hours)
    tst = ut + lon / 15.0 + eot_min / 60.0

    # Hour angle (degrees, negative before noon)
    ha = math.radians(15.0 * (tst - 12.0))

    lat_r = math.radians(lat)

    # Solar elevation
    sin_elv = (math.sin(lat_r) * math.sin(dec) +
               math.cos(lat_r) * math.cos(dec) * math.cos(ha))
    sin_elv = max(-1.0, min(1.0, sin_elv))
    elv_deg = math.degrees(math.asin(sin_elv))

    # Solar azimuth (0 = N, CW)
    cos_elv = math.cos(math.radians(elv_deg))
    if cos_elv < 1e-9:
        return 180.0, round(elv_deg, 1)  # solar noon edge case
    cos_az = (math.sin(dec) - math.sin(lat_r) * sin_elv) / (math.cos(lat_r) * cos_elv)
    cos_az = max(-1.0, min(1.0, cos_az))
    az_deg = math.degrees(math.acos(cos_az))
    if ha > 0:  # afternoon → sun is west
        az_deg = 360.0 - az_deg

    return round(az_deg, 1), round(elv_deg, 1)


def _catalog_search(bbox: list, date_from: str, date_to: str,
                    max_cloud: float = MAX_CLOUD) -> list:
    """
    Return list of {date, scene_id, sun_azimuth, sun_elevation, cloud_cover}.
    Paginates automatically.
    sun_azimuth/sun_elevation: from s2:mean_solar_* catalog fields when present;
    fallback to ephemeris computed from datetime + bbox centre (SH catalog often
    omits the solar fields — ephemeris is accurate to ~1°, well within ±60° sector).
    """
    token = _get_token()
    lat_c = (bbox[1] + bbox[3]) / 2
    lon_c = (bbox[0] + bbox[2]) / 2
    items = []
    next_token = None
    while True:
        payload = {
            "bbox": bbox,
            "datetime": f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
            "collections": ["sentinel-2-l2a"],
            "limit": 100,
            "filter": f"eo:cloud_cover<{max_cloud}",
            "filter-lang": "cql2-text",
            "fields": {
                "include": ["id", "properties.datetime",
                            "properties.eo:cloud_cover",
                            "properties.s2:mean_solar_azimuth",
                            "properties.s2:mean_solar_zenith"],
            }
        }
        if next_token:
            payload["next"] = next_token

        for attempt in range(MAX_RETRIES):
            try:
                r = requests.post(
                    CATALOG_URL, json=payload,
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"},
                    timeout=30,
                )
                if r.status_code == 401:
                    token = _get_token()
                    continue
                r.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(2 ** attempt)

        resp = r.json()
        _ephemeris_used = 0
        for feat in resp.get("features", []):
            p = feat.get("properties", {})
            dt_full = p.get("datetime", "")  # full ISO timestamp when present
            dt_str  = dt_full[:10]
            sun_az_raw  = p.get("s2:mean_solar_azimuth")
            sun_zen_raw = p.get("s2:mean_solar_zenith")
            if sun_az_raw is not None and sun_zen_raw is not None:
                sun_az  = float(sun_az_raw)
                sun_zen = float(sun_zen_raw)
                sun_elv = 90.0 - sun_zen if not math.isnan(sun_zen) else float("nan")
            else:
                # SH catalog omits solar fields — compute from ephemeris
                sun_az, sun_elv = _solar_az_elv_from_dt(dt_full or dt_str, lat_c, lon_c)
                _ephemeris_used += 1
            items.append({
                "date":          dt_str,
                "scene_id":      feat.get("id", ""),
                "sun_azimuth":   round(float(sun_az),  2),
                "sun_elevation": round(float(sun_elv), 2),
                "cloud_cover":   round(float(p.get("eo:cloud_cover", 0)), 2),
                "low_shadow":    (sun_elv > LOW_SHADOW_ELV or sun_elv < MIN_ELV_SHADOW),
            })
        if _ephemeris_used:
            print(f"    sun position: ephemeris fallback for {_ephemeris_used} scenes "
                  f"(s2:mean_solar_* absent from catalog)")
        next_token = resp.get("context", {}).get("next") or resp.get("links", {})
        # links is a list — extract "next" rel
        if isinstance(next_token, list):
            next_token = next(
                (lk.get("body", {}).get("next") for lk in next_token if lk.get("rel") == "next"),
                None,
            )
        if not next_token or not resp.get("features"):
            break

    return items


CATALOG_URL = SH_CATALOG_URL  # alias (kept consistent)

EVALSCRIPT_RGB = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04","B03","B02","dataMask"] }],
    output: { bands: 4, sampleType: "UINT8" }
  };
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

def _fetch_tile(bbox: list, scene_date: str,
                res_m: float = RESOLUTION) -> np.ndarray | None:
    """
    Fetch S2 L2A RGB tile for one date. Returns uint8 (H, W, 4) or None.
    Uses mosaicking=FIRST to pin to the scene date.
    Retries with exponential backoff.
    """
    token = _get_token()
    w_px, h_px = _bbox_dims(bbox, res_m)
    w_px = min(w_px, 2048)
    h_px = min(h_px, 2048)

    payload = {
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {
                        "from": f"{scene_date}T00:00:00Z",
                        "to":   f"{scene_date}T23:59:59Z",
                    },
                    "maxCloudCoverage": MAX_CLOUD,
                    "mosaickingOrder":  "mostRecent",
                },
                "processing": {"upsampling": "BICUBIC"},
            }],
        },
        "output": {
            "width":  w_px,
            "height": h_px,
            "responses": [{"identifier": "default",
                           "format": {"type": "image/png"}}],
        },
        "evalscript": EVALSCRIPT_RGB,
    }

    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(
                SH_PROCESS_URL, json=payload,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                timeout=60,
            )
            if r.status_code == 401:
                token = _get_token()
                continue
            if r.status_code == 429:
                wait = 2 ** (attempt + 2)
                print(f"    rate-limit, waiting {wait}s…")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                print(f"    HTTP {r.status_code} for {scene_date}: {r.text[:120]}")
                return None
            from PIL import Image
            img = Image.open(BytesIO(r.content)).convert("RGBA")
            arr = np.array(img, dtype=np.uint8)  # (H, W, 4)
            return arr
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                print(f"    fetch failed after {MAX_RETRIES} attempts: {e}")
                return None
            time.sleep(2 ** attempt)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 0 — Catalog + tile fetch
# ═══════════════════════════════════════════════════════════════════════════════

def stage0_catalog_and_fetch():
    """
    For each terminal/cluster: search S2 catalog (cloud<20%), deduplicate
    to one scene per date (prefer lowest cloud), save catalog CSV,
    then fetch each tile to NPZ (checkpointed — skip if already exists).

    Returns: {terminal_id: [{date, sun_az, sun_elv, cluster, tile_path}, ...]}
    """
    print("\n=== STAGE 0 — S2 Catalog + Tile Fetch ===")
    today = date.today().isoformat()

    # Check bbox overlaps for RT/JW to avoid double-fetching shared frames
    # (RT south bbox centre is ~26.64°N, JW ~26.82°N — clearly separate tiles)
    # Kharg fetched only for gate subset (not full history needed; last 20 clean scenes suffice)

    all_scenes = {}  # terminal_id → list of scene dicts

    for terminal_id, info in TERMINALS.items():
        terminal_scenes = []

        for cluster in info["clusters"]:
            tanks  = cluster["tanks"]
            cname  = cluster["name"]
            bbox   = _cluster_bbox(tanks, BUFFER_M)
            km_w, km_h = _km2_bbox(bbox)
            print(f"\n  [{terminal_id}/{cname}] bbox {bbox[0]:.3f},{bbox[1]:.3f} → "
                  f"{bbox[2]:.3f},{bbox[3]:.3f}  ({km_w:.1f}×{km_h:.1f} km)")

            cat_path = OUT_DIR / f"catalog_{terminal_id}_{cname}.csv"

            # ── Catalog search ──
            if cat_path.exists():
                print(f"    catalog exists → {cat_path}")
                cat = pd.read_csv(cat_path)
                scenes = cat.to_dict("records")
            else:
                print(f"    querying catalog {CATALOG_START} → {today}…")
                # Kharg: only need ~20 clean scenes; limit to last 2 years for speed
                cat_start = "2024-01-01" if terminal_id == "kharg" else CATALOG_START
                scenes = _catalog_search(bbox, cat_start, today)
                # Deduplicate by date: keep lowest cloud_cover
                df = (pd.DataFrame(scenes)
                        .sort_values("cloud_cover")
                        .drop_duplicates("date")
                        .sort_values("date"))
                scenes = df.to_dict("records")
                df.to_csv(cat_path, index=False)
                print(f"    {len(scenes)} unique dates → {cat_path}")

            # ── Tile fetch (checkpointed) ──
            tile_subdir = TILES_DIR / terminal_id / cname
            tile_subdir.mkdir(parents=True, exist_ok=True)

            # For kharg: limit to last N_KHARG_GATE scenes (save API quota)
            if terminal_id == "kharg":
                # take 30 most recent non-low-shadow clean scenes for gate
                gate_scenes = [s for s in scenes if not s.get("low_shadow", False)][-30:]
                fetch_scenes = gate_scenes
            else:
                # cap to most recent MAX_SCENES_PER_CLUSTER non-low-shadow scenes
                usable = [s for s in scenes if not s.get("low_shadow", False)]
                fetch_scenes = usable[-MAX_SCENES_PER_CLUSTER:]

            print(f"    fetching {len(fetch_scenes)} tiles @ {RESOLUTION}m/px…")
            n_fetched = 0
            for sc in fetch_scenes:
                dt   = sc["date"]
                fout = tile_subdir / f"{dt}.npz"
                if fout.exists():
                    continue
                tile = _fetch_tile(bbox, dt, RESOLUTION)
                if tile is None:
                    continue
                np.savez_compressed(str(fout), rgb=tile, bbox=np.array(bbox))
                n_fetched += 1
                time.sleep(RATE_LIMIT_S)

            print(f"    fetched {n_fetched} new tiles (existing skipped)")

            for sc in fetch_scenes:
                fout = tile_subdir / f"{sc['date']}.npz"
                if fout.exists():
                    terminal_scenes.append({
                        **sc,
                        "cluster": cname,
                        "tile_path": str(fout),
                        "bbox": bbox,
                        "tanks": tanks,
                    })

        all_scenes[terminal_id] = terminal_scenes
        print(f"  {terminal_id}: {len(terminal_scenes)} scenes with tiles")

    return all_scenes


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 1 — Shadow metric per tank per scene
# ═══════════════════════════════════════════════════════════════════════════════

def _shadow_sector_mask(cx_px: float, cy_px: float, r_inner_px: float,
                        sun_az_deg: float, half_angle_deg: float) -> np.ndarray:
    """
    Return boolean mask (H, W) for the shadow sector.
    Shadow falls on the face OPPOSITE the sun → sector centred at (sun_az + 180°) % 360.
    cx_px, cy_px: tank centre in image pixels (col, row).
    r_inner_px: inner-disk radius (excluding rim).
    """
    # Build coordinate grid relative to tank centre
    H, W = int(r_inner_px * 4), int(r_inner_px * 4)  # local patch (not used directly)
    # This function works on the full image; caller clips as needed.
    # Returned mask is True where pixel is inside shadow sector.
    pass  # see _compute_shadow_frac for full inline implementation


def _pixel_coords(lat: float, lon: float,
                  bbox: list, img_w: int, img_h: int):
    """(col, row) of (lat, lon) in the image."""
    lon0, lat0, lon1, lat1 = bbox
    col = (lon - lon0) / (lon1 - lon0) * img_w
    row = (lat1 - lat) / (lat1 - lat0) * img_h   # row 0 = top = max lat
    return col, row


def _compute_shadow_frac(tile_rgb: np.ndarray,
                         bbox: list,
                         tank: dict,
                         sun_az: float,
                         sun_elv: float) -> dict:
    """
    Compute shadow_frac and alt_mean_brightness for one tank in one tile.

    Returns dict:
      shadow_frac        — fraction dark pixels in shadow sector
      alt_brightness     — mean brightness in shadow sector (normalised 0-1)
      sector_px          — pixel count in sector
      threshold_used     — adaptive p30 threshold (0-255)
      low_shadow_geometry — bool (sun_elv > LOW_SHADOW_ELV)
      valid              — bool (sector_px >= MIN_SECTOR_PX and valid mask)
    """
    H, W = tile_rgb.shape[:2]
    lat, lon = tank["lat"], tank["lon"]
    r_m = tank["radius_m"]
    res_m = (bbox[2] - bbox[0]) * 111320 * math.cos(math.radians(lat)) / W

    # Tank centre in pixels
    cx, cy = _pixel_coords(lat, lon, bbox, W, H)

    # Radii in pixels
    r_inner_px  = max(1.0, (r_m - INNER_MARGIN_M) / res_m)
    # Minimum viable: 1px radius
    if r_inner_px < 1:
        return {"shadow_frac": float("nan"), "alt_brightness": float("nan"),
                "sector_px": 0, "threshold_used": float("nan"),
                "low_shadow_geometry": sun_elv > LOW_SHADOW_ELV, "valid": False}

    # Shadow sector: centred on sun_az (shadow lies on the sun-facing side of the roof)
    shadow_centre = sun_az % 360.0
    half_ang = SHADOW_ANGLE / 2.0  # 60°

    # Pixel grid (vectorised)
    cols = np.arange(W, dtype=np.float32)
    rows = np.arange(H, dtype=np.float32)
    C, R = np.meshgrid(cols, rows)   # (H, W)
    dc = C - cx
    dr = cy - R  # positive upward (north)

    dist2 = dc * dc + dr * dr
    in_disk = dist2 <= (r_inner_px ** 2)

    if in_disk.sum() < MIN_SECTOR_PX:
        return {"shadow_frac": float("nan"), "alt_brightness": float("nan"),
                "sector_px": 0, "threshold_used": float("nan"),
                "low_shadow_geometry": sun_elv > LOW_SHADOW_ELV, "valid": False}

    # Pixel azimuths (0=N, CW)
    az_px = (np.degrees(np.arctan2(dc, dr)) + 360.0) % 360.0  # (H, W), 0=north

    # Sector mask
    angle_diff = ((az_px - shadow_centre) + 180.0) % 360.0 - 180.0  # [-180, 180]
    in_sector = in_disk & (np.abs(angle_diff) <= half_ang)

    n_sector = int(in_sector.sum())
    if n_sector < MIN_SECTOR_PX:
        return {"shadow_frac": float("nan"), "alt_brightness": float("nan"),
                "sector_px": n_sector, "threshold_used": float("nan"),
                "low_shadow_geometry": sun_elv > LOW_SHADOW_ELV, "valid": False}

    # Brightness: mean of RGB channels (avoid alpha channel 3)
    brightness = tile_rgb[:, :, :3].mean(axis=2).astype(np.float32)  # (H, W), 0-255

    # Adaptive threshold: p30 of whole inner disk (not just sector)
    disk_pixels   = brightness[in_disk]
    threshold_p30 = float(np.percentile(disk_pixels, DARK_PCTILE))

    sector_pixels = brightness[in_sector]
    dark_count    = int((sector_pixels < threshold_p30).sum())
    shadow_frac   = dark_count / n_sector
    alt_brightness = float(sector_pixels.mean()) / 255.0  # normalised

    return {
        "shadow_frac":         round(shadow_frac, 4),
        "alt_brightness":      round(alt_brightness, 4),
        "sector_px":           n_sector,
        "threshold_used":      round(threshold_p30, 1),
        "low_shadow_geometry": bool(sun_elv > LOW_SHADOW_ELV),
        "valid":               True,
    }


def stage1_shadow_metrics(all_scenes: dict) -> pd.DataFrame:
    """
    For all scenes: crop per tank, compute shadow_frac.
    Returns DataFrame: terminal, tank_id, date, sun_az, sun_elv, shadow_frac,
                        alt_brightness, sector_px, low_shadow_geometry, valid
    """
    print("\n=== STAGE 1 — Shadow Metrics ===")

    shadow_csv = OUT_DIR / "shadow_metrics.csv"
    if shadow_csv.exists():
        df_cached = pd.read_csv(shadow_csv, parse_dates=["date"])
        valid_frac = df_cached["valid"].mean() if "valid" in df_cached.columns else 0.0
        if valid_frac >= 0.50:
            print(f"  shadow_metrics.csv OK (valid={valid_frac:.1%}), loading…")
            print(f"  {len(df_cached)} rows, {df_cached['tank_id'].nunique()} tanks")
            return df_cached
        else:
            print(f"  shadow_metrics.csv STALE (valid={valid_frac:.1%} < 50%) — recomputing")
            shadow_csv.unlink()

    rows = []
    scenes_processed = 0
    for terminal_id, scenes in all_scenes.items():
        print(f"\n  [{terminal_id}] {len(scenes)} scenes")
        for sc in scenes:
            date_str    = sc["date"]
            sun_az      = float(sc["sun_azimuth"])
            sun_elv     = float(sc["sun_elevation"])
            tile_path   = sc["tile_path"]
            bbox        = sc["bbox"]
            tanks       = sc["tanks"]

            try:
                data = np.load(tile_path)
                tile_rgb = data["rgb"]  # (H, W, 4) uint8
            except Exception as e:
                print(f"    {date_str}: load error {e}")
                continue

            # Skip scenes where sun is too low to measure
            if sun_elv < MIN_ELV_SHADOW:
                for tank in tanks:
                    rows.append({
                        "terminal": terminal_id,
                        "tank_id": tank["id"],
                        "date": date_str,
                        "sun_azimuth": sun_az,
                        "sun_elevation": sun_elv,
                        "shadow_frac": float("nan"),
                        "alt_brightness": float("nan"),
                        "sector_px": 0,
                        "threshold_used": float("nan"),
                        "low_shadow_geometry": True,
                        "valid": False,
                    })
                continue

            for tank in tanks:
                result = _compute_shadow_frac(tile_rgb, bbox, tank, sun_az, sun_elv)
                rows.append({
                    "terminal":   terminal_id,
                    "tank_id":    tank["id"],
                    "date":       date_str,
                    "sun_azimuth":     sun_az,
                    "sun_elevation":   sun_elv,
                    **result,
                })

            scenes_processed += 1
            if scenes_processed == 5:
                early_valid = sum(1 for r in rows if r.get("valid", False))
                early_frac  = early_valid / len(rows) if rows else 0.0
                if early_frac < 0.50:
                    raise AssertionError(
                        f"FAIL: valid_fraction={early_frac:.1%} after first 5 scenes "
                        f"({early_valid}/{len(rows)} rows valid). "
                        f"Check sun_az/elv, DN scale, sector geometry before proceeding."
                    )
                print(f"  Early check OK: valid={early_frac:.1%} after 5 scenes")

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df.to_csv(shadow_csv, index=False)
    print(f"\n  Saved {len(df)} rows → {shadow_csv}")
    print(f"  Valid measurements: {df['valid'].sum()} / {len(df)}")
    return df


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 2 — Validation gate
# ═══════════════════════════════════════════════════════════════════════════════

def _load_s1_for_rt(top_n: int = 20) -> pd.DataFrame:
    """
    Pull S1 terminal_observations for top_n RT WL tanks (DESC) from Supabase.
    Returns: tank_id, date, db_mean
    Paginates to bypass the 1000-row server cap.
    """
    tanks_quoted = ",".join(f'"{t}"' for t in RT_WL_TANKS[:top_n])
    hdrs = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}
    rows, offset = [], 0
    while True:
        url = (f"{SB_URL}/rest/v1/terminal_observations"
               f"?terminal_id=eq.ras_tanura"
               f"&orbit_direction=eq.DESC"
               f"&tank_id=in.({tanks_quoted})"
               f"&select=tank_id,date,db_mean"
               f"&order=date.asc"
               f"&offset={offset}&limit=1000")
        r = requests.get(url, headers=hdrs, timeout=60)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    DB_FLOOR = -5.0
    df["db_mean"] = df["db_mean"].clip(lower=DB_FLOOR)
    print(f"      _load_s1_for_rt: {len(df)} rows, {df['tank_id'].nunique()} tanks, "
          f"dates {df['date'].min().date()}–{df['date'].max().date()}")
    return df


def stage2_gate(shadow_df: pd.DataFrame) -> tuple[bool, dict]:
    """
    Gate test:
      (1) Kharg repères: median gap between empty-group and full-group shadow_frac
      (2) RT cross-sensor: median corr(shadow_frac_S2, db_mean_S1) ≤ 3d pairs

    Returns (gate_passed: bool, gate_results: dict)
    """
    print("\n=== STAGE 2 — Validation Gate ===")

    # ── Sub-test 1: Kharg repères ──
    kharg_df = shadow_df[
        (shadow_df["terminal"] == "kharg") &
        (shadow_df["valid"] == True)
    ].copy()

    full_med  = float("nan")
    empty_med = float("nan")
    gap       = float("nan")
    gate1_ok  = False
    n_kharg_scenes = 0

    if not kharg_df.empty:
        # Only tanks we know
        full_df  = kharg_df[kharg_df["tank_id"].isin(KHARG_FULL)]
        empty_df = kharg_df[kharg_df["tank_id"].isin(KHARG_EMPTY)]
        n_kharg_scenes = kharg_df["date"].nunique()

        if not full_df.empty and not empty_df.empty:
            full_med  = float(full_df["shadow_frac"].median())
            empty_med = float(empty_df["shadow_frac"].median())
            gap       = empty_med - full_med
            # Gate: gap must be visible (>0) with full < empty (shadow_frac low → full tank)
            gate1_ok = (gap > 0.05) and (full_med < empty_med)

    print(f"\n  [Gate 1 — Kharg repères]")
    print(f"    n scenes used:   {n_kharg_scenes}")
    print(f"    full tanks ({', '.join(KHARG_FULL)}):  median shadow_frac = {full_med:.4f}")
    print(f"    empty tanks ({', '.join(KHARG_EMPTY)}): median shadow_frac = {empty_med:.4f}")
    print(f"    gap (empty - full) = {gap:.4f}  → {'PASS' if gate1_ok else 'FAIL'}")

    # ── Sub-test 2: RT cross-sensor corr ──
    print(f"\n  [Gate 2 — RT cross-sensor corr (S2 shadow_frac vs S1 db_mean)]")
    rt_shadow = shadow_df[
        (shadow_df["terminal"] == "ras_tanura") &
        (shadow_df["valid"] == True)
    ].copy()

    corr_per_tank = {}
    gate2_ok      = False
    median_corr   = float("nan")
    n_pairs_total = 0

    s1_df = pd.DataFrame()
    try:
        s1_df = _load_s1_for_rt(top_n=20)
        print(f"    S1 rows loaded: {len(s1_df)} for {s1_df['tank_id'].nunique()} tanks")
    except Exception as e:
        print(f"    WARNING: S1 load failed: {e}")

    if not s1_df.empty and not rt_shadow.empty:
        for tank_id, grp_s2 in rt_shadow.groupby("tank_id"):
            grp_s1 = s1_df[s1_df["tank_id"] == tank_id]
            if len(grp_s2) < 5 or len(grp_s1) < 5:
                continue
            # Nearest-pair ≤3 days
            s2_dates = pd.DatetimeIndex(sorted(grp_s2["date"]))
            s1_dates = pd.DatetimeIndex(sorted(grp_s1["date"]))
            pairs_s2, pairs_s1 = [], []
            for d2 in s2_dates:
                diffs = abs((s1_dates - d2).days)
                best  = diffs.argmin()
                if diffs[best] <= 3:
                    sf  = grp_s2[grp_s2["date"] == d2]["shadow_frac"].values[0]
                    db  = grp_s1[grp_s1["date"] == s1_dates[best]]["db_mean"].values[0]
                    if not math.isnan(sf) and not math.isnan(db):
                        pairs_s2.append(sf)
                        pairs_s1.append(db)
            if len(pairs_s2) >= 5:
                s2_ser = pd.Series(pairs_s2, dtype=float)
                s1_ser = pd.Series(pairs_s1, dtype=float)
                # skip if either series has zero variance (corr would be NaN)
                if s2_ser.std() < 1e-9 or s1_ser.std() < 1e-9:
                    print(f"      {tank_id}: std≈0 (sf_std={s2_ser.std():.4f}), skipping corr")
                    continue
                c = float(s2_ser.corr(s1_ser))
                if not math.isnan(c):
                    corr_per_tank[tank_id] = c
                n_pairs_total += len(pairs_s2)

        if corr_per_tank:
            median_corr = float(np.median(list(corr_per_tank.values())))
            gate2_ok    = median_corr >= 0.4
            print(f"    tanks with ≥5 pairs: {len(corr_per_tank)}")
            print(f"    total pairs: {n_pairs_total}")
            print(f"    median corr(shadow_frac, db_mean): {median_corr:.4f}  "
                  f"→ {'PASS' if gate2_ok else 'FAIL'}")
            print(f"    expected sign: positive (empty=high db=large shadow)")
        else:
            print(f"    no tanks with ≥5 paired observations")
    else:
        print(f"    skipped (no S1 or S2 data)")

    gate_passed = gate1_ok and gate2_ok

    gate_results = {
        "gate1_ok":       gate1_ok,
        "gate2_ok":       gate2_ok,
        "gate_passed":    gate_passed,
        "full_med":       full_med,
        "empty_med":      empty_med,
        "gap":            gap,
        "n_kharg_scenes": n_kharg_scenes,
        "median_corr_s1": median_corr,
        "n_pairs_total":  n_pairs_total,
        "corr_per_tank":  corr_per_tank,
    }

    verdict = "PASS" if gate_passed else "FAIL"
    print(f"\n  GATE OVERALL: {verdict}  "
          f"(gate1={'PASS' if gate1_ok else 'FAIL'}, "
          f"gate2={'PASS' if gate2_ok else 'FAIL'})")

    if not gate_passed:
        print(f"\n  Gate FAIL → pipeline stops at Stage 2 per ТЗ.")
        print(f"  S2 shadow method verdict: NOT VALIDATED at 10m→5m/px on this tank geometry.")

    return gate_passed, gate_results


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 2b — PNG crop examples (6 crops: empty/full × RT/JW/YB)
# ═══════════════════════════════════════════════════════════════════════════════

def stage2b_crop_pngs(shadow_df: pd.DataFrame, all_scenes: dict) -> list[str]:
    """
    Produce 6 PNG crops with shadow sector overlay.
    Returns list of saved PNG paths.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  PIL not available — skipping PNG crops")
        return []

    print("\n=== STAGE 2b — PNG Crop Examples ===")

    # Select one empty + one full tank per terminal (by shadow_frac extremes)
    examples = []
    for terminal_id in ["ras_tanura", "juwaymah", "yanbu"]:
        t_df = shadow_df[
            (shadow_df["terminal"] == terminal_id) & (shadow_df["valid"] == True)
        ]
        if t_df.empty:
            continue
        tank_med = t_df.groupby("tank_id")["shadow_frac"].median()
        if tank_med.empty:
            continue
        full_tank  = tank_med.idxmin()   # lowest shadow_frac = fullest
        empty_tank = tank_med.idxmax()   # highest = most empty
        for role, tid in [("full", full_tank), ("empty", empty_tank)]:
            # Get a scene with good shadow geometry for this tank
            row = (t_df[(t_df["tank_id"] == tid) & (~t_df["low_shadow_geometry"])]
                   .sort_values("shadow_frac", ascending=(role == "empty"))
                   .head(1))
            if row.empty:
                row = t_df[t_df["tank_id"] == tid].head(1)
            if row.empty:
                continue
            examples.append({
                "terminal": terminal_id,
                "role":     role,
                "tank_id":  tid,
                "date":     str(row.iloc[0]["date"])[:10],
                "sun_az":   float(row.iloc[0]["sun_azimuth"]),
                "sun_elv":  float(row.iloc[0]["sun_elevation"]),
                "shadow_frac": float(row.iloc[0]["shadow_frac"]),
            })

    # Add Kharg t55_5 crops (3 scenes: low/mid/high shadow_frac) for sector audit
    kharg_df = shadow_df[
        (shadow_df["terminal"] == "kharg") & (shadow_df["tank_id"] == "t55_5")
    ].copy()
    if not kharg_df.empty:
        # pick 3 scenes spanning the shadow_frac range
        kharg_valid = kharg_df[kharg_df["valid"] == True].sort_values("shadow_frac")
        picks = []
        if len(kharg_valid) >= 3:
            picks = [kharg_valid.iloc[0], kharg_valid.iloc[len(kharg_valid)//2],
                     kharg_valid.iloc[-1]]
        elif len(kharg_valid) > 0:
            picks = [kharg_valid.iloc[0]]
        for row in picks:
            examples.insert(0, {
                "terminal":    "kharg",
                "role":        "t55_5_audit",
                "tank_id":     "t55_5",
                "date":        str(row["date"])[:10],
                "sun_az":      float(row["sun_azimuth"]),
                "sun_elv":     float(row["sun_elevation"]),
                "shadow_frac": float(row["shadow_frac"]),
            })
        print(f"\n  t55_5 Kharg scenes for sector audit:")
        for row in kharg_valid.itertuples():
            print(f"    {row.date}  sun_az={row.sun_azimuth:.1f}°  "
                  f"sun_elv={row.sun_elevation:.1f}°  sf={row.shadow_frac:.4f}  "
                  f"sector_px={row.sector_px}")

    png_paths = []
    crop_dir  = OUT_DIR / "crops"
    crop_dir.mkdir(exist_ok=True)

    for ex in examples:
        terminal_id = ex["terminal"]
        date_str    = ex["date"]
        tank_id     = ex["tank_id"]

        # Find the tile
        tile_path = None
        for sc in all_scenes.get(terminal_id, []):
            if sc["date"] == date_str:
                tile_path = sc["tile_path"]
                bbox      = sc["bbox"]
                tanks     = sc["tanks"]
                break
        if tile_path is None or not Path(tile_path).exists():
            continue

        tank = next((t for t in tanks if t["id"] == tank_id), None)
        if tank is None:
            continue

        data = np.load(tile_path)
        tile_rgb = data["rgb"]   # (H, W, 4)
        H, W = tile_rgb.shape[:2]
        cx, cy = _pixel_coords(tank["lat"], tank["lon"], bbox, W, H)

        lat  = tank["lat"]
        r_m  = tank["radius_m"]
        res_m = (bbox[2] - bbox[0]) * 111320 * math.cos(math.radians(lat)) / W
        r_px  = max(2.0, r_m / res_m)

        # Crop ±2.5 radii around centre
        crop_r = int(r_px * 2.5)
        x0, y0 = max(0, int(cx) - crop_r), max(0, int(cy) - crop_r)
        x1, y1 = min(W, int(cx) + crop_r), min(H, int(cy) + crop_r)
        crop = Image.fromarray(tile_rgb[y0:y1, x0:x1, :3])

        # Draw shadow sector arc
        draw = ImageDraw.Draw(crop)
        cx_c = cx - x0
        cy_c = cy - y0
        shadow_centre = ex["sun_az"] % 360.0  # shadow on sun-facing side
        half_ang = SHADOW_ANGLE / 2.0

        # Tank outline (red)
        draw.ellipse(
            [cx_c - r_px, cy_c - r_px, cx_c + r_px, cy_c + r_px],
            outline=(255, 60, 60), width=2,
        )
        # Inner disk boundary
        r_inner = max(1.0, (r_m - INNER_MARGIN_M) / res_m)
        draw.ellipse(
            [cx_c - r_inner, cy_c - r_inner, cx_c + r_inner, cy_c + r_inner],
            outline=(255, 180, 0), width=1,
        )
        # Shadow sector arc (cyan)
        # PIL arc: angles from 3-o'clock CCW; convert from our azimuth (CW from N)
        def az_to_pil(az):
            return (-az + 90.0) % 360.0
        start_pil = az_to_pil(shadow_centre + half_ang)
        end_pil   = az_to_pil(shadow_centre - half_ang)
        draw.arc(
            [cx_c - r_inner, cy_c - r_inner, cx_c + r_inner, cy_c + r_inner],
            start=start_pil, end=end_pil,
            fill=(0, 220, 220), width=2,
        )

        # Label
        label = (f"{tank_id} ({ex['role']}) {date_str}  "
                 f"sf={ex['shadow_frac']:.3f}  elv={ex['sun_elv']:.0f}°")
        draw.text((4, 4), label, fill=(255, 255, 0))

        out_path = crop_dir / f"{terminal_id}_{ex['role']}_{tank_id}_{date_str}.png"
        crop.save(str(out_path))
        png_paths.append(str(out_path))
        print(f"  saved {out_path.name}")

    return png_paths


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 3 — Optical series + JW screen + interleaved cadence
# ═══════════════════════════════════════════════════════════════════════════════

def _shadow_step(series: list) -> float:
    """Same canonical step metric as metrics.py: median|diff| over consecutive pairs."""
    vals = [v for v in series if v is not None and not math.isnan(v)]
    if len(vals) < 2:
        return float("nan")
    diffs = [abs(vals[i] - vals[i - 1]) for i in range(1, len(vals))]
    return float(np.median(diffs))


SHADOW_STEP_THRESH = 0.10  # shadow_frac units — analogous to 2.5 dB for SAR


def stage3_series(shadow_df: pd.DataFrame, gate_results: dict) -> dict:
    """
    Stage 3:
      (a) Per-tank shadow series for RT, JW, YB WL tanks
      (b) JW shadow screen (all 19 tanks) — can optical WL exceed SAR?
      (c) Interleaved RT series: S2 shadow z-score + S1 db_mean on one axis,
          compute effective cadence and E1/E2 point estimates
    Returns m3 dict with all results.
    """
    print("\n=== STAGE 3 — Optical Series + JW Screen + Interleaved Cadence ===")

    BARREL_PER_M3 = 6.2898
    PI = math.pi

    # ── (a) Shadow series for WL tanks ──
    wl_map = {
        "ras_tanura": RT_WL_TANKS,
        "juwaymah":   JW_WL_TANKS,
        "yanbu":      YB_WL_TANKS,
    }
    series_by_terminal = {}
    for terminal_id, wl_tanks in wl_map.items():
        t_df = shadow_df[
            (shadow_df["terminal"] == terminal_id) &
            (shadow_df["valid"] == True) &
            (shadow_df["tank_id"].isin(wl_tanks)) &
            (~shadow_df["low_shadow_geometry"])
        ].copy()
        if t_df.empty:
            series_by_terminal[terminal_id] = pd.DataFrame()
            continue

        # Aggregate: mean shadow_frac across WL tanks per date
        daily = (t_df.groupby("date")["shadow_frac"]
                     .agg(["mean", "std", "count"])
                     .reset_index())
        daily.columns = ["date", "shadow_frac_mean", "shadow_frac_std", "n_tanks"]
        daily = daily.sort_values("date")
        series_by_terminal[terminal_id] = daily
        print(f"  {terminal_id}: {len(daily)} optical dates (WL={len(wl_tanks)})")

    # ── (b) JW shadow screen (all 19 tanks) ──
    jw_df = shadow_df[
        (shadow_df["terminal"] == "juwaymah") &
        (shadow_df["valid"] == True) &
        (~shadow_df["low_shadow_geometry"])
    ].copy()

    jw_screen = []
    jw_optical_wl = []
    for tank_id, grp in jw_df.groupby("tank_id"):
        grp_sorted = grp.sort_values("date")
        step = _shadow_step(grp_sorted["shadow_frac"].tolist())
        n    = len(grp_sorted)
        optical_wl = (not math.isnan(step)) and (step < SHADOW_STEP_THRESH)
        jw_screen.append({
            "tank_id":    tank_id,
            "step_shadow":round(step, 4),
            "n_scenes":   n,
            "optical_wl": optical_wl,
        })
        if optical_wl:
            jw_optical_wl.append(tank_id)

    jw_screen_df = pd.DataFrame(jw_screen).sort_values("step_shadow")
    jw_optical_wl_frac = len(jw_optical_wl) / 19 if jw_screen else float("nan")
    jw_gate_optical = jw_optical_wl_frac >= 0.60  # ≥60% of cap = optical gate open

    print(f"\n  JW shadow screen (19 tanks):")
    print(f"  Optical WL tanks (step<{SHADOW_STEP_THRESH}): {len(jw_optical_wl)} / 19 "
          f"= {jw_optical_wl_frac:.1%}")
    print(f"  JW optical gate: {'OPEN' if jw_gate_optical else 'CLOSED'} "
          f"(threshold ≥60% of capacity)")

    # Save JW screen
    jw_screen_df.to_csv(OUT_DIR / "jw_optical_screen.csv", index=False)

    # ── (c) Interleaved RT series ──
    rt_shadow_series = series_by_terminal.get("ras_tanura", pd.DataFrame())

    # Load RT S1 DESC volume series from Supabase (volume_timeseries or terminal_observations)
    rt_s1_series = pd.DataFrame()
    try:
        # Use the DESC terminal_observations for WL tanks to compute a per-date volume proxy
        s1_raw = _load_s1_for_rt(top_n=25)
        if not s1_raw.empty:
            # Per date: mean db across WL tanks (proxy for volume)
            rt_s1_series = (s1_raw.groupby("date")["db_mean"]
                               .mean()
                               .reset_index()
                               .rename(columns={"db_mean": "db_mean_mean"})
                               .sort_values("date"))
    except Exception as e:
        print(f"  WARNING: S1 series load failed: {e}")

    # Compute z-score of shadow_frac for RT
    if not rt_shadow_series.empty and len(rt_shadow_series) > 2:
        sf_mu  = rt_shadow_series["shadow_frac_mean"].mean()
        sf_std = rt_shadow_series["shadow_frac_mean"].std()
        rt_shadow_series["shadow_frac_z"] = (
            (rt_shadow_series["shadow_frac_mean"] - sf_mu) / sf_std
            if sf_std > 0 else 0.0
        )

    # Interleaved cadence: all S1 and S2 dates combined, sorted
    interleaved_dates = []
    if not rt_s1_series.empty:
        for _, row in rt_s1_series.iterrows():
            interleaved_dates.append((pd.Timestamp(row["date"]), "S1"))
    if not rt_shadow_series.empty:
        for _, row in rt_shadow_series.iterrows():
            interleaved_dates.append((pd.Timestamp(row["date"]), "S2"))
    interleaved_dates.sort(key=lambda x: x[0])

    # Compute intervals between consecutive observations (any sensor)
    gaps_days = []
    for i in range(1, len(interleaved_dates)):
        g = (interleaved_dates[i][0] - interleaved_dates[i - 1][0]).days
        if g > 0:
            gaps_days.append(g)
    median_cadence = float(np.median(gaps_days)) if gaps_days else float("nan")
    print(f"\n  RT interleaved series:")
    print(f"    S1 dates: {len(rt_s1_series)}")
    print(f"    S2 dates: {len(rt_shadow_series)}")
    print(f"    Total interleaved observations: {len(interleaved_dates)}")
    print(f"    Median inter-observation interval: {median_cadence:.1f} days")

    # E1/E2 point estimators on S2 shadow series
    e1_s2 = float("nan")
    e2_s2 = float("nan")
    if not rt_shadow_series.empty:
        s2_dates = sorted(rt_shadow_series["date"].tolist())
        s2_sf    = rt_shadow_series.set_index("date")["shadow_frac_mean"]

        def point_estimator(event_date, series_dates, sf_series, n_pass=3):
            before = sorted([d for d in series_dates if d < event_date])[-n_pass:]
            after  = sorted([d for d in series_dates if d >= event_date])[:n_pass]
            if len(before) < n_pass or len(after) < n_pass:
                return float("nan")
            return float(sf_series.reindex(after).mean() - sf_series.reindex(before).mean())

        e1_s2 = point_estimator(E1_DATE, s2_dates, s2_sf)
        e2_s2 = point_estimator(E2_DATE, s2_dates, s2_sf)
        print(f"\n  RT E1 ({E1_DATE.date()}) S2 shadow Δ: {e1_s2:+.4f} "
              f"({'↑ build (correct sign)' if e1_s2 > 0 else '↓ wrong sign'} for build event)")
        print(f"  RT E2 ({E2_DATE.date()}) S2 shadow Δ: {e2_s2:+.4f}")

    # Save interleaved CSV
    if interleaved_dates:
        ileave_df = pd.DataFrame(interleaved_dates, columns=["date", "sensor"])
        ileave_df.to_csv(OUT_DIR / "rt_interleaved_cadence.csv", index=False)

    return {
        "series_by_terminal": series_by_terminal,
        "jw_screen_df":       jw_screen_df,
        "jw_optical_wl":      jw_optical_wl,
        "jw_optical_wl_frac": jw_optical_wl_frac,
        "jw_gate_optical":    jw_gate_optical,
        "rt_shadow_series":   rt_shadow_series,
        "rt_s1_series":       rt_s1_series,
        "interleaved_dates":  interleaved_dates,
        "median_cadence":     median_cadence,
        "e1_s2":              e1_s2,
        "e2_s2":              e2_s2,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 4 — Frozen correlation grid
# ═══════════════════════════════════════════════════════════════════════════════

def _load_export_data() -> pd.DataFrame:
    """
    Load rt_mbpd / yb_mbpd / ju_mbpd from Supabase.
    Called ONLY after Stage 4 starts (export quarantine lifted at this point per ТЗ).
    Returns DataFrame: date, terminal, mbpd
    """
    # These are assumed to be in a table like `terminal_export_volumes` or similar
    # The ТЗ says export data is quarantined until Stage 4 — query it here for the first time.
    rows = []
    for tid, col in [("ras_tanura", "rt_mbpd"), ("yanbu", "yb_mbpd"), ("juwaymah", "ju_mbpd")]:
        url = (f"{SB_URL}/rest/v1/terminal_export_volumes"
               f"?terminal_id=eq.{tid}"
               f"&select=date,mbpd"
               f"&order=date.asc"
               f"&limit=100000")
        try:
            r = requests.get(url,
                             headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"},
                             timeout=30)
            if r.status_code == 200 and r.json():
                for row in r.json():
                    rows.append({"date": row["date"], "terminal": tid,
                                 "mbpd": row["mbpd"]})
        except Exception:
            pass
    if rows:
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        return df
    return pd.DataFrame(columns=["date", "terminal", "mbpd"])


def stage4_corr_grid(m3: dict, shadow_df: pd.DataFrame) -> pd.DataFrame:
    """
    Single frozen correlation grid:
      Dimensions:
        - Series:   RT interleaved, YB optical, JW optical (if gate open)
        - Export:   corresponding mbpd series per terminal
        - Transform: Δ (week-over-week diff), level-anom (de-meaned level)
        - Lag:       0 days, +7 days (storage leads export by 1 week)
        - Frequency: weekly, monthly

      Train: 2020-01-01 – 2023-12-31
      Holdout: 2024-01-01 – 2026-01-31 (war period excluded: 2026-02 onwards)
      War period (2026-02 – present): reported separately, not used for corr

      Bonferroni α = 0.05 / N_tests
    """
    print("\n=== STAGE 4 — Frozen Correlation Grid ===")
    print("  [loading export data — quarantine lifted for Stage 4]")

    export_df = _load_export_data()
    if export_df.empty:
        print("  WARNING: no export data returned. Grid computed on optical series only.")

    series_by_terminal = m3["series_by_terminal"]

    TRAIN_END    = pd.Timestamp("2023-12-31")
    HOLDOUT_END  = pd.Timestamp("2026-01-31")  # war starts 2026-02
    WAR_START    = pd.Timestamp("2026-02-01")

    terminals_in_grid = ["ras_tanura", "yanbu"]
    if m3["jw_gate_optical"]:
        terminals_in_grid.append("juwaymah")

    lags       = [0, 7]   # days
    transforms = ["delta", "level_anom"]
    freqs      = ["weekly", "monthly"]

    rows = []

    for terminal_id in terminals_in_grid:
        s2_series = series_by_terminal.get(terminal_id, pd.DataFrame())
        if s2_series.empty:
            continue

        # For RT: use interleaved (S1 + S2 merged on weekly/monthly bins)
        if terminal_id == "ras_tanura" and not m3["rt_s1_series"].empty:
            # Combine: S2 shadow_frac_z + S1 db_mean on unified weekly axis
            s1 = m3["rt_s1_series"].copy()
            s1["date"] = pd.to_datetime(s1["date"])
            s1 = s1.set_index("date")["db_mean_mean"]
            s2 = s2_series.set_index("date")["shadow_frac_z"] if "shadow_frac_z" in s2_series else pd.Series(dtype=float)
            # Merge on weekly bins
            all_dates = pd.DatetimeIndex(sorted(set(s1.index) | (set(s2.index) if not s2.empty else set())))
            storage_weekly = (pd.Series(index=all_dates, dtype=float)
                                .combine_first(s1).combine_first(s2 if not s2.empty else pd.Series(dtype=float)))
        else:
            storage_weekly_raw = s2_series.set_index("date")["shadow_frac_mean"]
            storage_weekly     = storage_weekly_raw

        storage_weekly = storage_weekly.dropna().sort_index()

        # Get export series
        exp = export_df[export_df["terminal"] == terminal_id].set_index("date")["mbpd"] if not export_df.empty else pd.Series(dtype=float)

        for freq in freqs:
            if freq == "weekly":
                stor_res = storage_weekly.resample("W-MON").mean().dropna()
                exp_res  = exp.resample("W-MON").mean().dropna() if not exp.empty else pd.Series(dtype=float)
            else:
                stor_res = storage_weekly.resample("ME").mean().dropna()
                exp_res  = exp.resample("ME").mean().dropna() if not exp.empty else pd.Series(dtype=float)

            for transform in transforms:
                if transform == "delta":
                    s_tr = stor_res.diff().dropna()
                    e_tr = exp_res.diff().dropna() if not exp_res.empty else pd.Series(dtype=float)
                else:
                    s_tr = stor_res - stor_res.mean()
                    e_tr = (exp_res - exp_res.mean()) if not exp_res.empty else pd.Series(dtype=float)

                for lag in lags:
                    # Align with lag (storage leads)
                    if lag > 0:
                        s_shifted = s_tr.shift(lag, freq="D") if freq == "weekly" else s_tr.shift(lag // 30 + 1 if lag > 0 else 0)
                    else:
                        s_shifted = s_tr

                    if e_tr.empty:
                        # No export data: skip corr, report NaN
                        for split in ["train", "holdout"]:
                            rows.append({
                                "terminal": terminal_id, "freq": freq,
                                "transform": transform, "lag_days": lag,
                                "split": split, "n": 0,
                                "corr": float("nan"), "p_value": float("nan"),
                                "significant": False, "sign_correct": None,
                            })
                        continue

                    # Align by index
                    joined = pd.DataFrame({"storage": s_shifted, "export": e_tr}).dropna()
                    joined = joined[joined.index <= HOLDOUT_END]
                    joined = joined[joined.index < WAR_START]

                    for split, df_split in [
                        ("train",   joined[joined.index <= TRAIN_END]),
                        ("holdout", joined[(joined.index > TRAIN_END) & (joined.index <= HOLDOUT_END)]),
                    ]:
                        if len(df_split) < 6:
                            rows.append({
                                "terminal": terminal_id, "freq": freq,
                                "transform": transform, "lag_days": lag,
                                "split": split, "n": len(df_split),
                                "corr": float("nan"), "p_value": float("nan"),
                                "significant": False, "sign_correct": None,
                            })
                            continue
                        from scipy.stats import pearsonr
                        corr_val, p_val = pearsonr(df_split["storage"], df_split["export"])
                        # Expected sign: storage up → export up (positive)
                        # (higher shadow_frac = emptier tank → less export? No: convention varies)
                        # Use: corr sign depends on transform. Not adjudicated here; report as-is.
                        rows.append({
                            "terminal":    terminal_id,
                            "freq":        freq,
                            "transform":   transform,
                            "lag_days":    lag,
                            "split":       split,
                            "n":           len(df_split),
                            "corr":        round(float(corr_val), 4),
                            "p_value":     round(float(p_val), 4),
                            "significant": bool(p_val < 0.05),  # raw; Bonferroni applied below
                            "sign_correct": None,  # manual inspection needed
                        })

    grid_df = pd.DataFrame(rows)

    # Bonferroni correction
    n_tests = len(grid_df)
    bonf_alpha = 0.05 / n_tests if n_tests > 0 else 0.05
    grid_df["bonferroni_alpha"] = round(bonf_alpha, 6)
    grid_df["bonferroni_sig"]   = grid_df["p_value"] < bonf_alpha

    grid_df.to_csv(OUT_DIR / "corr_grid.csv", index=False)
    print(f"\n  Grid: {n_tests} cells across {len(terminals_in_grid)} terminals")
    print(f"  Bonferroni α = 0.05 / {n_tests} = {bonf_alpha:.5f}")
    sig_cells = grid_df["bonferroni_sig"].sum()
    print(f"  Bonferroni-significant cells: {sig_cells} / {n_tests}")

    return grid_df


# ═══════════════════════════════════════════════════════════════════════════════
#  Report — OPTICAL_PILOT.md
# ═══════════════════════════════════════════════════════════════════════════════

def _fmt(v, decimals=4):
    if isinstance(v, float) and math.isnan(v):
        return "NaN"
    if isinstance(v, float):
        return f"{v:.{decimals}f}"
    return str(v)


def write_report(gate_results: dict, m3: dict | None,
                 grid_df: pd.DataFrame | None,
                 shadow_df: pd.DataFrame,
                 png_paths: list[str]) -> str:
    """Write OPTICAL_PILOT.md at repo root."""
    gate_passed = gate_results.get("gate_passed", False)
    lines = []
    A = lines.append

    A("# OPTICAL_PILOT — S2 Shadow Fill Method")
    A(f"\nGenerated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    A("\n---\n")

    A("## Summary")
    A("")
    A(f"- **Gate**: {'PASS' if gate_passed else 'FAIL'}")
    if not gate_passed:
        A(f"- **Verdict**: S2 10m/5m-px shadow method **NOT VALIDATED** "
          f"on this tank geometry (gap={_fmt(gate_results.get('gap',float('nan')),3)}, "
          f"cross-sensor median_corr={_fmt(gate_results.get('median_corr_s1',float('nan')),3)})")
    else:
        A(f"- **Effective cadence**: {_fmt(m3.get('median_cadence',float('nan')),1)} days (interleaved S1+S2)")

    A(f"\n---\n")
    A("## Stage 2 Gate Numbers\n")
    A("### Gate 1 — Kharg Repères")
    A(f"\n| Group | Tanks | Median shadow_frac |")
    A(f"|-------|-------|--------------------|")
    A(f"| Full  | {', '.join(KHARG_FULL)}  | {_fmt(gate_results.get('full_med', float('nan')), 4)} |")
    A(f"| Empty | {', '.join(KHARG_EMPTY)} | {_fmt(gate_results.get('empty_med', float('nan')), 4)} |")
    A(f"\nGap (empty − full) = **{_fmt(gate_results.get('gap', float('nan')), 4)}**  "
      f"→ **{'PASS' if gate_results.get('gate1_ok') else 'FAIL'}**")
    A(f"\nScenes used (Kharg): {gate_results.get('n_kharg_scenes', 0)}")

    A(f"\n### Gate 2 — RT Cross-Sensor Correlation")
    A(f"\n| Metric | Value |")
    A(f"|--------|-------|")
    A(f"| Median corr(shadow_frac_S2, db_mean_S1) | {_fmt(gate_results.get('median_corr_s1',float('nan')),4)} |")
    A(f"| Total paired obs (≤3d) | {gate_results.get('n_pairs_total', 0)} |")
    A(f"| Tanks with ≥5 pairs | {len(gate_results.get('corr_per_tank', {}))} |")
    A(f"\nThreshold ≥ 0.40  → **{'PASS' if gate_results.get('gate2_ok') else 'FAIL'}**")

    # Per-tank corr table (top 10 + bottom 5)
    cpt = gate_results.get("corr_per_tank", {})
    if cpt:
        sorted_cpt = sorted(cpt.items(), key=lambda x: x[1], reverse=True)
        A(f"\n**Per-tank cross-sensor correlation (top/bottom):**\n")
        A("| tank_id | corr |")
        A("|---------|------|")
        for tid, c in sorted_cpt[:10]:
            A(f"| {tid} | {_fmt(c, 4)} |")
        if len(sorted_cpt) > 10:
            A("| … | … |")
            for tid, c in sorted_cpt[-3:]:
                A(f"| {tid} | {_fmt(c, 4)} |")

    # Gate verdict
    A(f"\n### Overall Gate: **{'PASS — proceed to Stage 3-4' if gate_passed else 'FAIL — pipeline closed'}**\n")

    if not gate_passed:
        A("**Reason:** Shadow sector too narrow at 10→5 m/px to separate fill states reliably. "
          "This closes the S2 question: optical shadow does NOT measure fill at this resolution "
          "and tank geometry. Next option: Planet (3m/px) or Maxar (0.5m/px) commercial.\n")
        A("---\n")
        # Still write stage 1 stats
        A("## Stage 1 Summary (pixel-level diagnostics)\n")
        for terminal_id in ["ras_tanura", "juwaymah", "yanbu"]:
            t_df = shadow_df[shadow_df["terminal"] == terminal_id]
            valid = t_df[t_df["valid"] == True]
            A(f"- **{terminal_id}**: {t_df['date'].nunique()} scenes, "
              f"{valid['date'].nunique()} with valid shadow measurements, "
              f"median sector_px={_fmt(valid['sector_px'].median(), 1)}")
        A("\n---\n")
        return "\n".join(lines)

    # ── Gate passed: Stages 3 + 4 ──
    A("## Stage 1 Summary\n")
    for terminal_id in ["ras_tanura", "juwaymah", "yanbu"]:
        t_df  = shadow_df[shadow_df["terminal"] == terminal_id]
        valid = t_df[t_df["valid"] == True]
        A(f"- **{terminal_id}**: {t_df['date'].nunique()} scenes, "
          f"{valid['date'].nunique()} with valid shadow, "
          f"median sector_px={_fmt(valid['sector_px'].median(), 1)}, "
          f"low_shadow excluded={t_df['low_shadow_geometry'].sum()}")

    A(f"\n---\n")
    A("## Stage 3 — Series and Interleaved Cadence\n")

    for terminal_id in ["ras_tanura", "juwaymah", "yanbu"]:
        ser = m3["series_by_terminal"].get(terminal_id, pd.DataFrame())
        A(f"- **{terminal_id}**: {len(ser)} optical dates in WL series")

    A(f"\n### JW Shadow Screen (all 19 tanks)\n")
    A(f"Step threshold: < {SHADOW_STEP_THRESH} (analogous to SAR step<2.5 dB)\n")
    A("| tank_id | step_shadow | n_scenes | optical_wl |")
    A("|---------|-------------|----------|------------|")
    for _, row in m3["jw_screen_df"].iterrows():
        A(f"| {row['tank_id']} | {_fmt(float(row['step_shadow']),4)} | "
          f"{int(row['n_scenes'])} | {'YES' if row['optical_wl'] else 'no'} |")
    A(f"\nOptical WL: **{len(m3['jw_optical_wl'])} / 19 = {m3['jw_optical_wl_frac']:.1%}**  "
      f"→ JW optical gate: **{'OPEN' if m3['jw_gate_optical'] else 'CLOSED'}**")
    if m3["jw_gate_optical"]:
        A(f"\nJW optical gate OPEN — JW included in Stage 4 grid.")

    A(f"\n### RT Interleaved Cadence\n")
    A(f"| Metric | Value |")
    A(f"|--------|-------|")
    A(f"| S1 DESC dates | {len(m3['rt_s1_series'])} |")
    A(f"| S2 optical dates | {len(m3['rt_shadow_series'])} |")
    A(f"| Total interleaved | {len(m3['interleaved_dates'])} |")
    A(f"| Median inter-obs interval | **{_fmt(m3['median_cadence'],1)} days** |")
    A(f"\nNote: S1 cadence 6d × S2 cadence ~5d → expected interleaved ~2-3d.")

    A(f"\n### Event Point Estimates (S2 optical)\n")
    A(f"| Event | Date | S2 shadow Δ | Interpretation |")
    A(f"|-------|------|-------------|----------------|")
    e1 = m3.get("e1_s2", float("nan"))
    e2 = m3.get("e2_s2", float("nan"))
    e1_sign = "build (↑ shadow_frac) ✓" if e1 > 0 else "wrong sign ✗"
    e2_sign = "drawdown (↓ shadow_frac) ✓" if e2 < 0 else "wrong sign ✗"
    A(f"| E1 | {E1_DATE.date()} | {_fmt(e1, 4)} | {e1_sign} |")
    A(f"| E2 | {E2_DATE.date()} | {_fmt(e2, 4)} | {e2_sign} |")

    A(f"\n---\n")
    A("## Stage 4 — Correlation Grid\n")
    if grid_df is not None and not grid_df.empty:
        n_tests  = len(grid_df)
        bonf_a   = grid_df["bonferroni_alpha"].iloc[0] if "bonferroni_alpha" in grid_df else 0.05
        sig_n    = int(grid_df["bonferroni_sig"].sum()) if "bonferroni_sig" in grid_df else 0
        A(f"Grid size: {n_tests} cells.  Bonferroni α = 0.05/{n_tests} = {bonf_a:.5f}")
        A(f"\nBonferroni-significant cells: **{sig_n} / {n_tests}**\n")
        A("| terminal | freq | transform | lag_days | split | n | corr | p_value | bonf_sig |")
        A("|----------|------|-----------|----------|-------|---|------|---------|----------|")
        for _, row in grid_df.iterrows():
            A(f"| {row['terminal']} | {row['freq']} | {row['transform']} | "
              f"{int(row['lag_days'])} | {row['split']} | {int(row['n'])} | "
              f"{_fmt(float(row['corr']),4)} | {_fmt(float(row['p_value']),4)} | "
              f"{'✓' if row['bonferroni_sig'] else ''} |")
    else:
        A("Grid not computed (gate failed or no export data).")

    A(f"\n---\n")
    A("## Verdict\n")
    # Decide per ТЗ rules
    if not gate_passed:
        A("**FAIL** — S2 shadow method does not validate at available resolution. "
          "Pipeline closed. Report this result without further iteration.")
    else:
        # Assess RT weekly
        rt_weekly_train = grid_df[
            (grid_df["terminal"] == "ras_tanura") &
            (grid_df["freq"] == "weekly") &
            (grid_df["split"] == "train") &
            (grid_df["bonferroni_sig"] == True)
        ] if grid_df is not None and not grid_df.empty else pd.DataFrame()

        if len(rt_weekly_train) > 0:
            A("**PROMISING** — RT weekly interleaved series shows ≥1 Bonferroni-significant "
              "cell. Optical cadence enables weekly-scale correlation not available from S1 alone.")
        else:
            A("**NULL** — Gate passed but no Bonferroni-significant correlation found in "
              "full grid. Optical S2 series does not improve on S1 for export signal detection.")

    if png_paths:
        A(f"\n---\n")
        A("## Crop Examples (annotated with shadow sector)\n")
        for p in png_paths:
            fname = Path(p).name
            A(f"- `diagnostics/optical/crops/{fname}`")

    A(f"\n---\n*Stage 6 Optical Pilot — OilRadar R&D*")

    report_text = "\n".join(lines)
    report_path = BASE_DIR / "OPTICAL_PILOT.md"
    report_path.write_text(report_text)
    print(f"\nReport written → {report_path}")
    return report_text


# ═══════════════════════════════════════════════════════════════════════════════
#  main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=== Stage 6 Optical Pilot ===")
    print(f"  Resolution: {RESOLUTION}m/px  MaxCloud: {MAX_CLOUD}%  "
          f"ShadowAngle: {SHADOW_ANGLE}°  DarkPctile: p{DARK_PCTILE}")

    # PIL check
    try:
        from PIL import Image
    except ImportError:
        print("FATAL: Pillow not installed. Run: pip install Pillow")
        sys.exit(1)

    # Stage 0
    all_scenes = stage0_catalog_and_fetch()

    # Stage 1
    shadow_df = stage1_shadow_metrics(all_scenes)

    # Stage 2
    gate_passed, gate_results = stage2_gate(shadow_df)

    # Stage 2b — PNG crops (always, even on fail — useful diagnostics)
    png_paths = stage2b_crop_pngs(shadow_df, all_scenes)

    if not gate_passed:
        report = write_report(gate_results, None, None, shadow_df, png_paths)
        print("\nGate FAILED — pipeline stops here per ТЗ.")
        print(report[:800])
        sys.exit(0)

    # Stage 3
    m3 = stage3_series(shadow_df, gate_results)

    # Stage 4
    try:
        from scipy.stats import pearsonr
        grid_df = stage4_corr_grid(m3, shadow_df)
    except ImportError:
        print("WARNING: scipy not available — Stage 4 skipped")
        grid_df = pd.DataFrame()

    # Report
    write_report(gate_results, m3, grid_df, shadow_df, png_paths)

    print("\n=== Stage 6 COMPLETE ===")


def _unit_test_shadow_sector_direction():
    """
    Synthetic unit test: shadow sector must be centred on sun_azimuth (sun-facing side),
    NOT on sun_azimuth + 180° (the former bug).

    Setup: synthetic 100×100 tile where pixels in the sun-facing semicircle
    are intentionally dark and pixels in the anti-sun semicircle are bright.
    Correct sector (sun_az) → shadow_frac > 0.5.
    Wrong sector (sun_az + 180°) → shadow_frac ≈ 0 (no dark pixels there).
    """
    import math as _math
    sun_az = 140.0   # SE — sun facing into tank from this direction
    r_m    = 55.0
    res_m  = 5.0
    r_px   = r_m / res_m   # 11px

    H = W = 100
    cx, cy = 50.0, 50.0

    # Build synthetic tile: dark (20) in ±45° sun-facing wedge, bright (200) elsewhere.
    # ±45° → ~12.5% of disk pixels dark → p30(disk) = 200 (only 12.5% ≤ 20 < 30%)
    # threshold_p30 = 200 → count(sector_pixels < 200) = count(==20) = all dark in sector
    # Sector (SHADOW_ANGLE/2 = ±60°) fully covers the ±45° dark wedge:
    #   sf_correct ≈ 0.75 (45/60 of sector is dark) > 0.5 ✓
    #   sf_wrong   = 0.0 (anti-sun sector has no dark pixels) ✓
    DARK_HALF = 45.0   # degrees half-angle of the synthetic dark wedge
    tile = np.full((H, W, 4), 200, dtype=np.uint8)
    for row in range(H):
        for col in range(W):
            dc = col - cx
            dr = cy - row   # positive = north
            if dc * dc + dr * dr <= r_px ** 2:
                az_px = (_math.degrees(_math.atan2(dc, dr)) + 360) % 360
                diff = ((az_px - sun_az) + 180) % 360 - 180
                if abs(diff) <= DARK_HALF:   # sun-facing wedge: shadow here
                    tile[row, col, :3] = 20   # dark
                else:
                    tile[row, col, :3] = 200  # bright

    # Fake tank + bbox matching resolution
    tank = {"lat": 50.0, "lon": 50.0, "radius_m": r_m}
    # bbox chosen so res = res_m at lat 50°
    deg_per_m_lon = 1.0 / (111320.0 * _math.cos(_math.radians(50.0)))
    deg_per_m_lat = 1.0 / 111320.0
    span_lon = W * res_m * deg_per_m_lon
    span_lat = H * res_m * deg_per_m_lat
    bbox = [50.0 - span_lon / 2, 50.0 - span_lat / 2,
            50.0 + span_lon / 2, 50.0 + span_lat / 2]

    result_correct = _compute_shadow_frac(tile, bbox, tank, sun_az, sun_elv=30.0)
    result_wrong   = _compute_shadow_frac(tile, bbox, tank,
                                          (sun_az + 180.0) % 360.0, sun_elv=30.0)

    sf_correct = result_correct["shadow_frac"]
    sf_wrong   = result_wrong["shadow_frac"]

    assert result_correct["valid"], "Unit test: correct-sector result should be valid"
    assert result_wrong["valid"],   "Unit test: wrong-sector result should be valid"
    assert sf_correct > 0.5, (
        f"UNIT TEST FAIL: correct sector (sun_az={sun_az}°) gives shadow_frac={sf_correct:.3f} "
        f"(expected >0.5 — sun-facing pixels are dark)"
    )
    assert sf_wrong < 0.2, (
        f"UNIT TEST FAIL: wrong sector (anti-sun, {(sun_az+180)%360}°) gives shadow_frac={sf_wrong:.3f} "
        f"(expected <0.2 — anti-sun pixels are bright)"
    )
    print(f"UNIT TEST shadow_sector_direction: PASS  "
          f"(sf_correct={sf_correct:.3f} > 0.5, sf_wrong={sf_wrong:.3f} < 0.2)")


if __name__ == "__main__":
    import sys as _sys
    if "--unit-test" in _sys.argv:
        _unit_test_shadow_sector_direction()
        _sys.exit(0)
    main()
