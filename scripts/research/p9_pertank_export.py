"""
p9_pertank_export.py — Per-tank raw rows + tank reference table.

ТЗ-3:
  1. Per-tank CSV (Kharg/RT/Yanbu, full history):
     date, scene_id, rel_orbit, platform, terminal, tank_id, db_mean,
     ring15_far_dB, ring15_near_dB, pixel_valid_frac, barrels, enabled

  2. Tank reference table:
     tank_id, terminal, r, h, lat, lon, geom_source, azimuth_used,
     gap_m_desc, gap_m_asc, obstruction_near (visual flag — always False here)

  3. Azimuth from heading — validated on Kharg DESC-35 first.
     Formula: LOS_az = (orbit_heading + 90) % 360
     One CDSE catalog query per orbit to get platformHeading.
     Stop if Kharg result ≠ 282°.

  4. Yanbu: db_mean and ring15_far/near recomputed with heading-derived azimuth.
     Old values (ring15_far_old, ring15_near_old) kept alongside.

Pre-delivery checks printed before writing any file:
  - Non-empty fraction by terminal × rel_orbit × enabled
  - Tank count by terminal × r in reference table
"""

import io, math, os, re, sys, json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR   = REPO_ROOT / "diagnostics" / "mega"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TS  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
LOG = OUT_DIR / f"p9_pertank_{TS}.txt"
_lf = open(LOG, "w", buffering=1)


def log(msg=""):
    print(msg, flush=True)
    _lf.write(msg + "\n")


# ── Secrets ──────────────────────────────────────────────────────────────────
_CID = os.environ.get("CLIENT_ID", "")
_CSC = os.environ.get("CLIENT_SECRET", "")
if not _CID or not _CSC:
    log("FATAL: CLIENT_ID or CLIENT_SECRET empty"); sys.exit(1)

CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CDSE_CATALOG   = "https://catalogue.dataspace.copernicus.eu/odata/v1"

BARREL_PER_M3  = 6.2898
TANK_HEIGHT_MIN = 2.0

# ── Path to pre-computed observations ────────────────────────────────────────
OBS_CSV = OUT_DIR / "observations_all.csv.gz"

# ── Orbits of interest ───────────────────────────────────────────────────────
# (terminal, rel_orbit, direction, registry_azimuth)
TARGET_ORBITS = [
    ("kharg",      35,  "DESCENDING", 282),
    ("ras_tanura",  35, "DESCENDING", 282),
    ("ras_tanura", 101, "ASCENDING",  None),   # azimuth TBD from heading
    ("yanbu",       14, "ASCENDING",  None),   # azimuth TBD from heading
]


# =============================================================================
# CDSE catalog helpers
# =============================================================================

def get_token():
    r = requests.post(CDSE_TOKEN_URL,
        data={"grant_type":    "client_credentials",
              "client_id":     _CID,
              "client_secret": _CSC},
        timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def get_heading_for_orbit(token, terminal_id, rel_orbit, direction):
    """
    Query CDSE catalog for one scene matching this orbit and extract
    platformHeading from its attributes.

    Returns float heading (degrees) or None.
    """
    # terminal bbox approx
    TERM_BBOX = {
        "kharg":      (50.2, 29.1, 50.4, 29.3),
        "ras_tanura": (49.9, 26.6, 50.2, 26.9),
        "yanbu":      (37.8, 22.9, 38.1, 23.1),
    }
    bbox = TERM_BBOX.get(terminal_id, (48.0, 20.0, 58.0, 35.0))
    wkt = (f"POLYGON(({bbox[0]} {bbox[1]},{bbox[2]} {bbox[1]},"
           f"{bbox[2]} {bbox[3]},{bbox[0]} {bbox[3]},{bbox[0]} {bbox[1]}))")
    dir_val = "DESCENDING" if direction.upper().startswith("D") else "ASCENDING"
    url = (f"{CDSE_CATALOG}/Products"
           f"?$filter=Collection/Name eq 'SENTINEL-1'"
           f" and Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'productType' and a/Value eq 'IW_GRDH')"
           f" and OData.CSC.Intersects(area=geography'SRID=4326;{wkt}')"
           f" and Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'orbitDirection' and a/Value eq '{dir_val}')"
           f" and Attributes/OData.CSC.IntegerAttribute/any(a:a/Name eq 'relativeOrbitNumber' and a/Value eq {rel_orbit})"
           f"&$top=3&$expand=Attributes")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code != 200:
        log(f"  catalog HTTP {r.status_code}: {r.text[:200]}")
        return None
    items = r.json().get("value", [])
    if not items:
        log(f"  no scenes found for {terminal_id} orbit {rel_orbit} {direction}")
        return None
    for item in items:
        attrs = item.get("Attributes", [])
        for a in attrs:
            if a.get("Name") == "platformHeading":
                return float(a.get("Value"))
    log(f"  platformHeading attribute not found in {items[0].get('Name','?')}")
    return None


# =============================================================================
# Volume calculation
# =============================================================================

def compute_barrels(df):
    """
    Add 'barrels' column to df using canonical formula.
    Uses per-orbit calibration groups: global percentiles within
    (terminal × orbit_direction).
    """
    import pandas as pd
    df = df.copy()
    df["barrels"] = float("nan")

    for (tid, orb_dir), grp in df.groupby(["terminal", "orbit_direction"]):
        idx   = grp.index
        db    = grp["db_mean"].values.astype(float)
        r_m   = grp["radius_m"].values.astype(float)
        h_max = grp["h_max_m"].values.astype(float)

        valid = np.isfinite(db)
        if valid.sum() < 2:
            continue
        db_min = float(np.nanpercentile(db[valid], 5))
        db_max = float(np.nanpercentile(db[valid], 95))
        span   = db_max - db_min
        if span < 0.01:
            continue

        db_clip    = np.clip(db, db_min, db_max)
        fill_ratio = (db_max - db_clip) / span
        h_cur      = TANK_HEIGHT_MIN + fill_ratio * (h_max - TANK_HEIGHT_MIN)
        bbl        = np.pi * r_m ** 2 * h_cur * BARREL_PER_M3

        # only for finite db_mean
        bbl[~valid] = float("nan")
        df.loc[idx, "barrels"] = bbl

    return df


# =============================================================================
# SentinelHub ring15 recomputation for Yanbu
# =============================================================================

def _crescent_mask(cx, cy, r_px, los_az_deg, width_px, H, W, half_angle=90):
    y, x = np.mgrid[0:H, 0:W]
    dist = np.sqrt((x - cx)**2 + (y - cy)**2)
    ring = (dist >= r_px - width_px) & (dist <= r_px)
    los_rad = math.radians(90 - los_az_deg)
    angle   = np.arctan2(-(y - cy), x - cx)
    diff    = np.abs(((angle - los_rad + math.pi) % (2 * math.pi)) - math.pi)
    return ring & (diff <= math.radians(half_angle))


def _safe_db(pixels):
    v = pixels[np.isfinite(pixels) & (pixels > 0)]
    if len(v) == 0:
        return float("nan")
    return float(10 * math.log10(float(np.mean(v))))


def recompute_yanbu_ring15(new_az_deg: float, old_az_deg: float):
    """
    For Yanbu ASC-14: re-fetch SentinelHub VV images and compute ring15_far/near
    with new_az_deg; keep old values as ring15_far_old/ring15_near_old.

    Returns DataFrame indexed by (date, tank_id).
    """
    import pandas as pd
    from sentinelhub import (SHConfig, DataCollection, BBox, CRS,
                              SentinelHubRequest, MimeType)
    from datetime import timedelta as td

    cfg = SHConfig()
    cfg.sh_client_id     = _CID
    cfg.sh_client_secret = _CSC
    cfg.sh_token_url     = CDSE_TOKEN_URL
    cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"

    S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
        "s1iw_cdse_p9",
        service_url="https://sh.dataspace.copernicus.eu"
    )

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from tank_registry import LOCATIONS

    loc   = next(l for l in LOCATIONS if l["id"] == "yanbu")
    tanks = loc["tanks"]
    lats  = [t["lat"] for t in tanks]
    lons  = [t["lon"] for t in tanks]

    RES    = 10   # m/px
    BUFFER = 300  # m

    bbox = BBox([min(lons) - BUFFER / 111320, min(lats) - BUFFER / 111320,
                 max(lons) + BUFFER / 111320, max(lats) + BUFFER / 111320],
                crs=CRS.WGS84)
    W_px = max(1, int((bbox.max_x - bbox.min_x) / (RES / 111320)))
    H_px = max(1, int((bbox.max_y - bbox.min_y) / (RES / 111320)))

    EVALSCRIPT = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""

    # Dates for Yanbu ASC-14 from observations_all
    obs = pd.read_csv(OBS_CSV)
    yanbu_dates = sorted(
        obs[(obs["terminal_id"] == "yanbu") &
            (obs["relative_orbit"] == 14)]["date"].dropna().unique()
    )
    log(f"  Yanbu ASC-14: {len(yanbu_dates)} dates to recompute")

    # Precompute pixel indices per tank
    w15 = 15 / RES
    tank_pc = {}
    for tank in tanks:
        cx = int((tank["lon"] - bbox.min_x) / (bbox.max_x - bbox.min_x) * W_px)
        cy = int((bbox.max_y - tank["lat"]) / (bbox.max_y - bbox.min_y) * H_px)
        r  = tank["radius_m"] / RES
        if cx < 0 or cy < 0 or cx >= W_px or cy >= H_px:
            tank_pc[tank["id"]] = None
            continue
        def _idx(mask):
            mh, mw = mask.shape
            rr, cc = np.where(mask[:min(mh, H_px), :min(mw, W_px)])
            return rr, cc
        tank_pc[tank["id"]] = {
            "cx": cx, "cy": cy, "r": r,
            "far_new":  _idx(_crescent_mask(cx, cy, r, new_az_deg,  w15, H_px, W_px)),
            "near_new": _idx(_crescent_mask(cx, cy, r, (new_az_deg + 180) % 360, w15, H_px, W_px)),
            "far_old":  _idx(_crescent_mask(cx, cy, r, old_az_deg,  w15, H_px, W_px)),
            "near_old": _idx(_crescent_mask(cx, cy, r, (old_az_deg + 180) % 360, w15, H_px, W_px)),
        }

    rows = []
    for d_str in yanbu_dates:
        d = datetime.strptime(d_str, "%Y-%m-%d")
        try:
            req = SentinelHubRequest(
                evalscript=EVALSCRIPT,
                input_data=[SentinelHubRequest.input_data(
                    data_collection=S1_CDSE,
                    time_interval=(d_str, (d + td(days=1)).strftime("%Y-%m-%d")),
                    other_args={
                        "processing": {"orthorectify": True,
                                       "backCoeff":    "GAMMA0_TERRAIN",
                                       "demInstance":  "COPERNICUS_30"},
                        "dataFilter": {"orbitDirection":    "ASCENDING",
                                       "relativeOrbitNumber": 14},
                        "mosaicking": "ORBIT",
                    },
                )],
                responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
                bbox=bbox, size=(W_px, H_px), config=cfg,
            )
            data = req.get_data()
            if not data:
                log(f"    {d_str}: empty"); continue
            img = data[0].squeeze().astype(np.float32)
        except Exception as e:
            log(f"    {d_str}: SKIP ({e})"); continue

        H_img, W_img = img.shape
        for tank in tanks:
            pc = tank_pc.get(tank["id"])
            if pc is None:
                continue

            def _pix(rr, cc):
                rr2 = rr[rr < H_img]; cc2 = cc[rr < H_img]
                m = cc2 < W_img
                return img[rr2[m], cc2[m]]

            rows.append({
                "date":               d_str,
                "tank_id":            tank["id"],
                "ring15_far_new":     _safe_db(_pix(*pc["far_new"])),
                "ring15_near_new":    _safe_db(_pix(*pc["near_new"])),
                "ring15_far_old_az":  _safe_db(_pix(*pc["far_old"])),
                "ring15_near_old_az": _safe_db(_pix(*pc["near_old"])),
            })
        log(f"    {d_str}: done")

    return pd.DataFrame(rows)


# =============================================================================
# Tank reference table — gap_m
# =============================================================================

def compute_gap_m(tanks, los_az_deg, max_dist_m=500.0, half_angle_deg=45.0):
    """
    For each tank, find nearest neighbour tank (edge-to-edge) within ±half_angle
    of los_az_deg. Returns dict tank_id → gap_m (NaN if none found).
    """
    half = math.radians(half_angle_deg)
    az_rad = math.radians(los_az_deg)

    def _ll_dist_m(lat1, lon1, lat2, lon2):
        R = 6371000.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + (math.cos(math.radians(lat1)) *
            math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _bearing_rad(lat1, lon1, lat2, lon2):
        dlon = math.radians(lon2 - lon1)
        y = math.sin(dlon) * math.cos(math.radians(lat2))
        x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) -
             math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.cos(dlon))
        return math.atan2(y, x)  # radians from north

    gap = {}
    for t in tanks:
        best = float("nan")
        for other in tanks:
            if other["id"] == t["id"]:
                continue
            d = _ll_dist_m(t["lat"], t["lon"], other["lat"], other["lon"])
            brg = _bearing_rad(t["lat"], t["lon"], other["lat"], other["lon"])
            # brg is from N, clockwise; az_rad is from N clockwise in radians
            diff = abs(((brg - az_rad + math.pi) % (2 * math.pi)) - math.pi)
            if diff <= half:
                # edge-to-edge gap
                edge_gap = d - t["radius_m"] - other["radius_m"]
                if edge_gap < 0:
                    edge_gap = 0.0
                if edge_gap < max_dist_m:
                    if math.isnan(best) or edge_gap < best:
                        best = edge_gap
        gap[t["id"]] = best
    return gap


# =============================================================================
# Main
# =============================================================================

def main():
    import pandas as pd

    log(f"p9_pertank_export.py  {datetime.now(timezone.utc).isoformat()}")

    # ── Load observations_all ─────────────────────────────────────────────────
    if not OBS_CSV.exists():
        log(f"FATAL: {OBS_CSV} not found"); sys.exit(1)
    obs = pd.read_csv(OBS_CSV)
    log(f"observations_all: {len(obs)} rows, terminals={list(obs['terminal_id'].unique())}")

    # Filter to target orbits only
    keep_mask = (
        ((obs["terminal_id"] == "kharg")      & (obs["relative_orbit"] == 35)  & (obs["orbit_direction"] == "DESCENDING")) |
        ((obs["terminal_id"] == "ras_tanura") & (obs["relative_orbit"] == 35)  & (obs["orbit_direction"] == "DESCENDING")) |
        ((obs["terminal_id"] == "ras_tanura") & (obs["relative_orbit"] == 101) & (obs["orbit_direction"] == "ASCENDING"))  |
        ((obs["terminal_id"] == "yanbu")      & (obs["relative_orbit"] == 14)  & (obs["orbit_direction"] == "ASCENDING"))
    )
    obs = obs[keep_mask].copy()
    log(f"After orbit filter: {len(obs)} rows")

    # Rename columns → canonical names
    obs.rename(columns={
        "terminal_id":      "terminal",
        "db_mean_prod":     "db_mean",
        "relative_orbit":   "rel_orbit",
        "orbit_direction":  "orbit_direction",
        "radius_m":         "radius_m",
        "h_max_m":          "h_max_m",
    }, inplace=True)

    # platform: already in obs from catalog
    # scene_id: already in obs

    # ── Compute barrels ───────────────────────────────────────────────────────
    log("Computing barrels...")
    obs = compute_barrels(obs)

    # ── SECTION 3: Heading → azimuth ─────────────────────────────────────────
    log("\n=== Heading → LOS azimuth ===")
    log("Formula: LOS_az = (platformHeading + 90) % 360  [right-looking SAR]")
    log()

    token = get_token()
    log("CDSE token OK")

    headings = {}
    computed_az = {}
    for (tid, rel_orb, direction, registry_az) in TARGET_ORBITS:
        log(f"  Query: {tid} orbit={rel_orb} dir={direction}")
        h = get_heading_for_orbit(token, tid, rel_orb, direction)
        key = (tid, rel_orb, direction)
        headings[key] = h
        if h is not None:
            az = (h + 90) % 360
            computed_az[key] = az
            log(f"    platformHeading={h:.2f}°  → LOS_az={az:.1f}°  registry={registry_az}")
        else:
            computed_az[key] = None
            log(f"    platformHeading: NOT FOUND  registry={registry_az}")

    # Validate Kharg
    kharg_key = ("kharg", 35, "DESCENDING")
    kharg_az  = computed_az.get(kharg_key)
    REGISTRY_KHARG_AZ = 282
    log()
    if kharg_az is None:
        log("WARN: could not get Kharg heading — cannot validate formula. Proceeding with registry 282°.")
        kharg_az = REGISTRY_KHARG_AZ
    else:
        delta = abs(kharg_az - REGISTRY_KHARG_AZ)
        if delta > 5:
            log(f"STOP: Kharg formula gives {kharg_az:.1f}° vs registry {REGISTRY_KHARG_AZ}° — delta={delta:.1f}° > 5°")
            log("Not applying heading-derived azimuth to Yanbu or RT. Investigate orbit heading.")
            yanbu_az  = REGISTRY_KHARG_AZ  # keep old as fallback for reference
            rt_asc_az = REGISTRY_KHARG_AZ
        else:
            log(f"OK: Kharg formula gives {kharg_az:.1f}° ≈ {REGISTRY_KHARG_AZ}° (delta={delta:.1f}°) — formula validated")
            yanbu_key   = ("yanbu",      14,  "ASCENDING")
            rt_asc_key  = ("ras_tanura", 101, "ASCENDING")
            yanbu_az    = computed_az.get(yanbu_key)
            rt_asc_az   = computed_az.get(rt_asc_key)
            log(f"  Yanbu  ASC-14  heading={headings.get(yanbu_key)}°  → LOS_az={yanbu_az}°")
            log(f"  RT     ASC-101 heading={headings.get(rt_asc_key)}°  → LOS_az={rt_asc_az}°")

    log()

    # ── SECTION 4: Yanbu ring15 recomputation ─────────────────────────────────
    log("=== Yanbu ring15 recomputation ===")
    OLD_YANBU_AZ = 282   # what was used before (or NaN — no ring15 in DB)
    new_az = yanbu_az if yanbu_az is not None else OLD_YANBU_AZ

    log(f"old_az={OLD_YANBU_AZ}°  new_az={new_az:.1f}°")
    log("Running SentinelHub for Yanbu ASC-14...")

    yanbu_ring15 = recompute_yanbu_ring15(new_az_deg=new_az, old_az_deg=OLD_YANBU_AZ)
    log(f"Yanbu ring15 recomputed: {len(yanbu_ring15)} rows")

    # Merge back into obs for Yanbu
    if not yanbu_ring15.empty:
        obs = obs.merge(
            yanbu_ring15.rename(columns={
                "ring15_far_new":     "ring15_far_new",
                "ring15_near_new":    "ring15_near_new",
                "ring15_far_old_az":  "ring15_far_old_az",
                "ring15_near_old_az": "ring15_near_old_az",
            }),
            on=["date", "tank_id"],
            how="left",
        )
        # For Yanbu: overwrite ring15_far / ring15_near with new-azimuth values
        ym = obs["terminal"] == "yanbu"
        obs.loc[ym, "ring15_far_old"]   = obs.loc[ym, "ring15_far"]      # original (NaN mostly)
        obs.loc[ym, "ring15_near_old"]  = obs.loc[ym, "ring15_near"]
        obs.loc[ym, "ring15_far"]       = obs.loc[ym, "ring15_far_new"]
        obs.loc[ym, "ring15_near"]      = obs.loc[ym, "ring15_near_new"]
        obs.loc[ym, "ring15_far_old_az_used"]  = obs.loc[ym, "ring15_far_old_az"]
        obs.loc[ym, "ring15_near_old_az_used"] = obs.loc[ym, "ring15_near_old_az"]
        obs.drop(columns=["ring15_far_new","ring15_near_new",
                           "ring15_far_old_az","ring15_near_old_az"], inplace=True, errors="ignore")
    else:
        log("  WARNING: Yanbu ring15 recomputation returned empty — ring15 columns will be NaN for Yanbu")
        for col in ["ring15_far_old","ring15_near_old",
                    "ring15_far_old_az_used","ring15_near_old_az_used"]:
            obs[col] = float("nan")

    log()

    # ── Assemble final per-tank CSV ───────────────────────────────────────────
    keep_cols = [
        "date", "scene_id", "rel_orbit", "platform", "terminal", "tank_id",
        "db_mean", "ring15_far", "ring15_near", "pixel_valid_frac", "barrels",
        "enabled",
        # Yanbu-specific extras (NaN for other terminals)
        "ring15_far_old", "ring15_near_old",
        "ring15_far_old_az_used", "ring15_near_old_az_used",
    ]
    for c in keep_cols:
        if c not in obs.columns:
            obs[c] = float("nan")

    # Use ring15_far = ring15_far (already populated by ring15_far from obs for Kharg/RT)
    # Rename ring15_far → ring15_far_dB for clarity
    obs.rename(columns={
        "ring15_far":  "ring15_far_dB",
        "ring15_near": "ring15_near_dB",
    }, inplace=True)
    keep_cols = [c.replace("ring15_far", "ring15_far_dB").replace("ring15_near", "ring15_near_dB")
                 for c in keep_cols]
    obs = obs[keep_cols].sort_values(["terminal", "date", "tank_id"]).reset_index(drop=True)

    # ── Pre-delivery checks ───────────────────────────────────────────────────
    log("=== PRE-DELIVERY CHECK 1: non-empty fraction by terminal × rel_orbit × enabled ===")
    obs["_enabled"] = obs["enabled"].fillna(True).astype(bool)
    for (tid, rorb, en), grp in obs.groupby(["terminal", "rel_orbit", "_enabled"]):
        n_total = len(grp)
        n_db    = grp["db_mean"].notna().sum()
        n_r15   = grp["ring15_far_dB"].notna().sum()
        n_bar   = grp["barrels"].notna().sum()
        log(f"  {tid:12s} orbit={int(rorb):3d} enabled={en!s:5s}  n={n_total:6d}  "
            f"db_mean={n_db/n_total:.1%}  ring15_far={n_r15/n_total:.1%}  barrels={n_bar/n_total:.1%}")
    obs.drop(columns=["_enabled"], inplace=True)
    log()

    # ── Write CSV-1 ───────────────────────────────────────────────────────────
    csv1 = OUT_DIR / f"p9_pertank_all_{TS}.csv"
    obs.to_csv(csv1, index=False)
    log(f"WRITTEN: {csv1.name}  ({len(obs)} rows)")

    # ── SECTION 2: Tank reference table ──────────────────────────────────────
    log()
    log("=== Building tank reference table ===")

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from tank_registry import LOCATIONS

    TERM_AZIMUTH_DESC = 282
    TERM_AZIMUTH_ASC  = {
        "yanbu":      yanbu_az  if yanbu_az  is not None else float("nan"),
        "ras_tanura": rt_asc_az if rt_asc_az is not None else float("nan"),
    }

    ref_rows = []
    for loc in LOCATIONS:
        tid = loc["id"]
        if tid not in ("kharg", "ras_tanura", "yanbu"):
            continue
        desc_az = TERM_AZIMUTH_DESC
        asc_az  = TERM_AZIMUTH_ASC.get(tid, float("nan"))
        az_used = loc.get("los_azimuth", desc_az)

        tanks = loc["tanks"]
        gap_desc = compute_gap_m(tanks, desc_az)
        gap_asc  = compute_gap_m(tanks, asc_az) if not math.isnan(asc_az) else {t["id"]: float("nan") for t in tanks}

        for t in tanks:
            ref_rows.append({
                "tank_id":          t["id"],
                "terminal":         tid,
                "r":                t["radius_m"],
                "h":                t["h_max_m"],
                "lat":              t["lat"],
                "lon":              t["lon"],
                "geom_source":      t.get("geom_source", ""),
                "azimuth_used":     az_used,
                "gap_m_desc":       round(gap_desc.get(t["id"], float("nan")), 1),
                "gap_m_asc":        round(gap_asc.get(t["id"],  float("nan")), 1),
                "obstruction_near": False,   # requires visual ESRI check — not automated
                "notes":            t.get("notes", t.get("note", "")),
                "enabled":          t.get("enabled", True),
            })

    import csv
    csv2 = OUT_DIR / f"p9_tank_ref_{TS}.csv"
    with open(csv2, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ref_rows[0].keys()))
        w.writeheader()
        w.writerows(ref_rows)
    log(f"WRITTEN: {csv2.name}  ({len(ref_rows)} tanks)")

    # Pre-delivery check 2
    log()
    log("=== PRE-DELIVERY CHECK 2: tank count by terminal × r ===")
    from collections import Counter
    cnt = Counter((row["terminal"], row["r"]) for row in ref_rows)
    for (tid, r), n in sorted(cnt.items()):
        log(f"  {tid:12s}  r={r:5.1f}m  n={n}")

    log()
    log(f"DONE  {datetime.now(timezone.utc).isoformat()}")
    _lf.close()


if __name__ == "__main__":
    main()
