"""
diag_welldepth_7_7.py — Round 7.7: Well-depth physics test + FCS criterion.

Gate chain (strict):
  Task 1: Spatial sector test — 4 Kharg tanks (2 full, 2 empty) × 4 DESC-35 scenes.
          PASS = empty tanks uniformly brighter (ALL sectors) by ≥6 dB vs full tanks.
          Gate: Task 1 must give unambiguous answer before Tasks 3–5 are interpreted.
  Task 2: Temporal rotation test — top-2 cycling Kharg tanks, 6+ scenes,
          prod/full-ring/90°/180° masks all correlated with CSV db_mean.
          Prediction if well-depth: all correlations ≥0.8.
  Task 3: FCS calibration on Kharg CSV + RT backup CSV.
          FCS = (p95 − p05 temporal dB per tank) / median intra-scene ring noise.
  Task 4: JW/YB FCS from Supabase terminal_observations (SQL fallback if 403).
          YB: exclude rows before azimuth pivot date.
  Task 5: Verdicts table + stop-line.

Security constraints:
  - No Supabase writes. Kharg tables not touched.
  - No paid API calls (Kpler/Vortexa).
  - No new catalog queries (metadata-only for Kharg DESC-35 scenes is allowed).
  - Honesty rule: if sign or magnitude doesn't match, write "не подтверждено".
  - "вероятно" and "not a blocker" are prohibited in verdicts.
  - Prod not modified.

Output: diagnostics/round7_7/WELLDEPTH_7_7.md + PNGs
"""

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from sentinelhub import (
        BBox, CRS, MimeType, SentinelHubCatalog, SentinelHubRequest,
        DataCollection, SHConfig,
    )
    _SH_OK = True
except ImportError as e:
    print(f"[WARN] sentinelhub import: {e}")
    _SH_OK = False

sys.path.insert(0, str(Path(__file__).parent))
try:
    from sar_fetch import build_crescent_masks as _prod_build_crescent_masks
    from sar_fetch import lonlat_to_pix as _prod_lonlat_to_pix
    _PROD_IMPORT_OK = True
except ImportError as e:
    print(f"[FATAL] cannot import from sar_fetch: {e}")
    _PROD_IMPORT_OK = False

# ── Constants ──────────────────────────────────────────────────────────────────
RESOLUTION   = 5
LOS_AZ_DESC  = 282
LOS_AZ_ASC   = 98
WIDTH_WIDE   = 15
SECTOR_STEP  = 15   # 24 sectors × 15° = 360°
SWEEP_STEP   = 15

CLIENT_ID     = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY", "")

if _SH_OK:
    try:
        from config import config as cfg, S1_CDSE
        if CLIENT_ID:
            cfg.sh_client_id = CLIENT_ID
        if CLIENT_SECRET:
            cfg.sh_client_secret = CLIENT_SECRET
    except ImportError:
        cfg = SHConfig()
        cfg.sh_client_id     = CLIENT_ID     or ""
        cfg.sh_client_secret = CLIENT_SECRET or ""
        cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"
        cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
        S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
            "S1_CDSE_77", service_url=cfg.sh_base_url
        )

OUT_DIR = Path("diagnostics/round7_7")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Tank geometry ──────────────────────────────────────────────────────────────
KHARG_BBOX = (50.295, 29.205, 50.340, 29.240)

# 4 etalon tanks: t60_1 + t40_33 = chronically full; t55_5 + t55_10 = idle/empty
# Coords from tank_registry.py — do NOT use hand-estimated values
KHARG_ETALON = [
    {"id": "t60_1",  "lat": 29.221638820210273, "lon": 50.315043243946754,
     "radius": 60, "h_max": 18, "status": "full",  "csv_mean_db": 2.82},
    {"id": "t40_33", "lat": 29.230947410364493, "lon": 50.31324186520479,
     "radius": 40, "h_max": 14, "status": "full",  "csv_mean_db": 3.50},
    {"id": "t55_5",  "lat": 29.23596908236423,  "lon": 50.31262041733899,
     "radius": 55, "h_max": 17, "status": "empty", "csv_mean_db": 13.02},
    {"id": "t55_10", "lat": 29.237608159424354, "lon": 50.30726790052075,
     "radius": 55, "h_max": 17, "status": "empty", "csv_mean_db": 9.89},
]

# Top-2 cycling tanks (coords from tank_registry.py)
KHARG_CYCLING = [
    {"id": "t60_2",  "lat": 29.221840470329855, "lon": 50.31352911803618,
     "radius": 60, "h_max": 18},
    {"id": "t55_17", "lat": 29.23144045674206,  "lon": 50.308740382712706,
     "radius": 55, "h_max": 17},
]

# Yanbu azimuth pivot: ascending orbit only after this date
YB_PIVOT_DATE = "2016-12-31"


# ── Sentinel Hub helpers ───────────────────────────────────────────────────────

_EVALSCRIPT_VV = """
//VERSION=3
function setup(){return{input:[{bands:["VV"]}],output:{bands:1,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return[s.VV];}
"""


def _bbox_to_size(bbox_tuple):
    min_lon, min_lat, max_lon, max_lat = bbox_tuple
    w = int((max_lon - min_lon) * 111320 / RESOLUTION)
    h = int((max_lat - min_lat) * 111320 / RESOLUTION)
    return w, h


def fetch_s1(bbox_tuple, date_str, orbit="DESCENDING"):
    if not _SH_OK:
        return None, 0, 0
    bbox = BBox(bbox_tuple, crs=CRS.WGS84)
    size = _bbox_to_size(bbox_tuple)
    d = date.fromisoformat(date_str)
    try:
        req = SentinelHubRequest(
            evalscript=_EVALSCRIPT_VV,
            input_data=[SentinelHubRequest.input_data(
                data_collection=S1_CDSE,
                time_interval=(date_str, (d + timedelta(days=1)).strftime("%Y-%m-%d")),
                other_args={
                    "processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN",
                                   "demInstance": "COPERNICUS_30"},
                    "dataFilter": {"orbitDirection": orbit},
                    "mosaicking": "ORBIT",
                },
            )],
            responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
            bbox=bbox, size=size, config=cfg,
        )
        data = req.get_data()
        if not data:
            return None, size[0], size[1]
        arr = np.array(data[0], dtype=np.float32)
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        if arr.std() == 0:
            return None, size[0], size[1]
        return arr, arr.shape[1], arr.shape[0]
    except Exception as e:
        print(f"    [WARN] fetch {date_str}: {e}")
        return None, size[0], size[1]


def catalog_kharg_desc35(n=8):
    """Return n most-recent DESC-35 scene dates over Kharg (metadata only)."""
    if not _SH_OK:
        return []
    bbox = BBox(KHARG_BBOX, crs=CRS.WGS84)
    catalog = SentinelHubCatalog(config=cfg)
    try:
        scenes = list(catalog.search(
            S1_CDSE, bbox=bbox,
            time=("2024-01-01", "2026-09-27"),
            filter="sat:orbit_state = 'descending'",
            fields={"include": ["properties.datetime", "properties.sat:relative_orbit"]},
        ))
    except Exception as e:
        print(f"  [WARN] catalog: {e}")
        return []
    desc35 = [s for s in scenes
              if s.get("properties", {}).get("sat:relative_orbit") == 35]
    desc35.sort(key=lambda s: s["properties"]["datetime"], reverse=True)
    dates, seen = [], set()
    for s in desc35:
        d = s["properties"]["datetime"][:10]
        if d not in seen:
            seen.add(d)
            dates.append(d)
        if len(dates) >= n:
            break
    return dates


# ── Mask helpers ───────────────────────────────────────────────────────────────

def _build_full_ring(cx, cy, radius_m, img_w, img_h, res_m=RESOLUTION):
    """Full annulus (no directional constraint)."""
    Y, X = np.mgrid[0:img_h, 0:img_w]
    r_outer = radius_m / res_m
    r_inner = max(0.0, radius_m - WIDTH_WIDE) / res_m
    dist_sq = (X - cx)**2 + (Y - cy)**2
    return (dist_sq <= r_outer**2) & (dist_sq >= r_inner**2)


def _build_prod_mask(cx, cy, radius_m, img_w, img_h, az_deg, res_m=RESOLUTION):
    """Prod crescent mask via _prod_build_crescent_masks with synthetic bbox."""
    if not _SH_OK or not _PROD_IMPORT_OK:
        Y, X = np.mgrid[0:img_h, 0:img_w]
        r_outer = radius_m / res_m
        r_inner = max(0.0, radius_m - WIDTH_WIDE) / res_m
        dist_sq = (X - cx)**2 + (Y - cy)**2
        dx = np.sin(np.deg2rad(az_deg)); dy = -np.cos(np.deg2rad(az_deg))
        dot = (X - cx)*dx + (Y - cy)*dy
        return (dist_sq <= r_outer**2) & (dist_sq >= r_inner**2) & (dot > 0)
    syn_lon = cx * res_m / 111320.0
    syn_lat = img_h * res_m / 111320.0 - cy * res_m / 111320.0
    bbox_syn = BBox((0.0, 0.0, img_w * res_m / 111320.0, img_h * res_m / 111320.0),
                    crs=CRS.WGS84)
    td = _prod_build_crescent_masks(
        [{"id": "t", "lon": syn_lon, "lat": syn_lat, "radius": radius_m, "h_max": 18}],
        bbox_syn, img_w, img_h, los_azimuth=az_deg
    )
    return td[0]["crescent"]


def mask_mean_db(img_lin, mask):
    px = img_lin[mask]
    px = px[np.isfinite(px)]
    if px.size == 0:
        return np.nan
    return float(10 * np.log10(np.clip(np.mean(px), 1e-10, None)))


def sector_table_24(img_lin, cx, cy, radius_m, img_w, img_h, res_m=RESOLUTION):
    """
    24-sector table (0° to 345° in 15° steps) using prod mask at each azimuth.
    Returns dict az_deg → (linear_mean, db_mean).
    """
    sectors = {}
    for az in range(0, 360, SECTOR_STEP):
        mask = _build_prod_mask(cx, cy, radius_m, img_w, img_h, az, res_m)
        px = img_lin[mask]
        px = px[np.isfinite(px)]
        if px.size == 0:
            sectors[az] = (np.nan, np.nan)
        else:
            lin = float(np.mean(px))
            db  = float(10 * np.log10(np.clip(lin, 1e-10, None)))
            sectors[az] = (lin, db)
    return sectors


def prod_mask_mean(img_lin, cx, cy, radius_m, img_w, img_h,
                   az_deg=LOS_AZ_DESC, res_m=RESOLUTION):
    """Prod-mask mean linear and dB at a specific azimuth (default prod 282°)."""
    mask = _build_prod_mask(cx, cy, radius_m, img_w, img_h, az_deg, res_m)
    px = img_lin[mask]
    px = px[np.isfinite(px)]
    if px.size == 0:
        return np.nan, np.nan, 0
    lin = float(np.mean(px))
    return lin, float(10 * np.log10(np.clip(lin, 1e-10, None))), px.size


def tank_cx_cy(tank, bbox_tuple, img_w, img_h):
    bbox = BBox(bbox_tuple, crs=CRS.WGS84) if _SH_OK else None
    if bbox is None or not _PROD_IMPORT_OK:
        # fallback manual
        min_lon, min_lat, max_lon, max_lat = bbox_tuple
        cx = (tank["lon"] - min_lon) / (max_lon - min_lon) * img_w
        cy = (max_lat - tank["lat"]) / (max_lat - min_lat) * img_h
        return cx, cy
    return _prod_lonlat_to_pix(tank["lon"], tank["lat"], bbox, img_w, img_h)


# ── Task 1 ─────────────────────────────────────────────────────────────────────

def task1_spatial(scenes4, lines):
    lines.append("\n## Task 1 — Spatial sector test (well-depth hypothesis)\n")
    lines.append("Prediction: empty tanks (t55_5, t55_10) brighter than full tanks "
                 "(t60_1, t40_33) in ALL 24 sectors by ≥6 dB.\n")

    if not scenes4:
        lines.append("[FINDING] No scenes available — Task 1 SKIPPED\n")
        return "INSUFFICIENT DATA"

    full_tanks  = [t for t in KHARG_ETALON if t["status"] == "full"]
    empty_tanks = [t for t in KHARG_ETALON if t["status"] == "empty"]
    all_tanks   = KHARG_ETALON

    # Per-scene sector tables for each tank
    # scene_data[date][tank_id] = {az: db}
    scene_data = {}
    scene_imgs = {}

    for d_str in scenes4:
        print(f"  Task1: fetching {d_str} …")
        img, iw, ih = fetch_s1(KHARG_BBOX, d_str, orbit="DESCENDING")
        if img is None:
            print(f"  [WARN] {d_str} no data")
            continue
        scene_imgs[d_str] = (img, iw, ih)
        scene_data[d_str] = {}
        for tank in all_tanks:
            cx, cy = tank_cx_cy(tank, KHARG_BBOX, iw, ih)
            scene_data[d_str][tank["id"]] = sector_table_24(img, cx, cy,
                                                             tank["radius"], iw, ih)

    if not scene_data:
        lines.append("[FINDING] All scene fetches failed — Task 1 SKIPPED\n")
        return "INSUFFICIENT DATA"

    # ── Sign-check on first scene only (t60_1 vs t55_5) ──────────────────────
    first_date = list(scene_data.keys())[0]
    img_ref0, iw0, ih0 = scene_imgs[first_date]
    lines.append(f"\n### Sign-check — scene {first_date}: raw linear + dB per sector\n")
    lines.append("Cross-reference: CSV global means t60_1=2.82 dB (full), "
                 "t55_5=13.02 dB (empty/idle).\n")
    lines.append("If sign is correct: prod-mask mean for t55_5 >> t60_1.\n\n")

    for tank in [t for t in KHARG_ETALON if t["id"] in ("t60_1", "t55_5")]:
        cx, cy = tank_cx_cy(tank, KHARG_BBOX, iw0, ih0)
        lin_prod, db_prod, npx = prod_mask_mean(img_ref0, cx, cy, tank["radius"], iw0, ih0)
        csv_mean = tank.get("csv_mean_db", "?")
        match = ""
        if np.isfinite(db_prod) and isinstance(csv_mean, float):
            diff = abs(db_prod - csv_mean)
            # sign check: full should be low dB, empty should be high dB
            if tank["status"] == "full":
                match = "OK (low dB as expected)" if db_prod < 6 else f"MISMATCH (expected <6 dB, got {db_prod:.2f})"
            else:
                match = "OK (high dB as expected)" if db_prod > 8 else f"MISMATCH (expected >8 dB, got {db_prod:.2f})"
        lines.append(f"**{tank['id']}** ({tank['status']}): "
                     f"prod-mask linear={lin_prod:.5f}, dB={db_prod:.2f}, "
                     f"N_px={npx}, CSV_mean={csv_mean} dB — {match}\n\n")

        # Per-sector raw table for sign-check
        sdata = scene_data[first_date][tank["id"]]
        lines.append(f"| Az° | linear mean | dB mean |\n"
                     f"|-----|------------|--------|\n")
        for az in range(0, 360, SECTOR_STEP):
            lin_v, db_v = sdata.get(az, (np.nan, np.nan))
            lin_s = f"{lin_v:.5f}" if np.isfinite(lin_v) else "NaN"
            db_s  = f"{db_v:.2f}"  if np.isfinite(db_v)  else "NaN"
            lines.append(f"| {az:3d} | {lin_s} | {db_s} |\n")
        lines.append("\n")

        # Crop with ring overlay
        _save_tank_crop_with_ring(img_ref0, cx, cy, tank["radius"], iw0, ih0,
                                  tank["id"], first_date,
                                  OUT_DIR / f"t1_ring_{tank['id']}_{first_date}.png")

    # ── Per-scene sector Δ tables ────────────────────────────────────────────
    overall_verdict_scenes = []

    for d_str, tdata in scene_data.items():
        sector_deltas = {}
        all_sectors_pass = True
        for az in range(0, 360, SECTOR_STEP):
            full_vals  = [tdata[t["id"]][az][1] for t in full_tanks
                          if np.isfinite(tdata[t["id"]][az][1])]
            empty_vals = [tdata[t["id"]][az][1] for t in empty_tanks
                          if np.isfinite(tdata[t["id"]][az][1])]
            if not full_vals or not empty_vals:
                sector_deltas[az] = np.nan
                all_sectors_pass = False
                continue
            delta = np.mean(empty_vals) - np.mean(full_vals)
            sector_deltas[az] = delta
            if delta < 6.0:
                all_sectors_pass = False

        valid_deltas = [v for v in sector_deltas.values() if np.isfinite(v)]
        min_delta = min(valid_deltas) if valid_deltas else np.nan
        max_delta = max(valid_deltas) if valid_deltas else np.nan
        mean_delta = float(np.mean(valid_deltas)) if valid_deltas else np.nan

        scene_pass = all_sectors_pass and (not np.isnan(min_delta)) and (min_delta >= 6.0)
        overall_verdict_scenes.append(scene_pass)
        verdict_str = "PASS" if scene_pass else "FAIL"

        lines.append(f"\n### Scene {d_str} — {verdict_str}\n")
        lines.append(f"| Az° | Δ dB (empty−full) |\n|-----|------------------|\n")
        for az in range(0, 360, SECTOR_STEP):
            v = sector_deltas.get(az, np.nan)
            lines.append(f"| {az:3d} | {v:+.2f} |\n" if np.isfinite(v)
                         else f"| {az:3d} | NaN |\n")
        lines.append(f"\nMin Δ={min_delta:+.2f} dB  Max Δ={max_delta:+.2f} dB  "
                     f"Mean Δ={mean_delta:+.2f} dB\n")

        img_ref, iw, ih = scene_imgs[d_str]
        _save_sector_plot(tdata, all_tanks, d_str,
                          OUT_DIR / f"t1_sectors_{d_str}.png")

        for tank in all_tanks:
            cx, cy = tank_cx_cy(tank, KHARG_BBOX, iw, ih)
            _save_tank_crop_with_ring(img_ref, cx, cy, tank["radius"], iw, ih,
                                      tank["id"], d_str,
                                      OUT_DIR / f"t1_crop_{tank['id']}_{d_str}.png")

    n_pass = sum(overall_verdict_scenes)
    n_total = len(overall_verdict_scenes)
    final = "PASS" if n_pass >= 3 and n_total >= 3 else "FAIL"
    if n_total < 3:
        final = "INSUFFICIENT DATA"

    lines.append(f"\n**Task 1 final: {final}** ({n_pass}/{n_total} scenes pass "
                 f"all-sector Δ ≥ 6 dB criterion)\n")
    if final == "FAIL":
        lines.append("[FINDING] Well-depth spatial prediction NOT CONFIRMED — "
                     "empty tanks are NOT uniformly brighter than full tanks in all sectors.\n")
    elif final == "PASS":
        lines.append("[FINDING] Well-depth spatial prediction CONFIRMED — "
                     "empty tanks brighter in all 24 sectors by ≥6 dB on ≥3/4 scenes.\n")

    return final


# ── Task 2 ─────────────────────────────────────────────────────────────────────

def task2_temporal(scenes6, kharg_csv_path, lines):
    lines.append("\n## Task 2 — Temporal rotation test (cycling tanks)\n")
    lines.append("Tanks: t60_2 (std=6.16 dB), t55_17 (std=5.56 dB)\n")
    lines.append("Masks: prod (282°), full ring, 90°-rotated (12°), 180°-rotated (102°)\n")
    lines.append("Prediction if well-depth: all mask correlations ≥0.8 with CSV db_mean\n")

    if not scenes6:
        lines.append("[FINDING] No scenes available — Task 2 SKIPPED\n")
        return

    df_kharg = pd.read_csv(kharg_csv_path)
    df_kharg["date"] = pd.to_datetime(df_kharg["date"])

    for tank in KHARG_CYCLING:
        tid = tank["id"]
        lines.append(f"\n### {tid}\n")

        csv_dates = []
        db_prod_list, db_ring_list, db_rot90_list, db_rot180_list, db_csv_list = [], [], [], [], []

        for d_str in scenes6:
            # Get CSV reference value
            row = df_kharg[(df_kharg["tank_id"] == tid) &
                           (df_kharg["date"] == pd.to_datetime(d_str))]
            if row.empty:
                print(f"  Task2: {tid} {d_str} no CSV match — skip")
                continue

            img, iw, ih = fetch_s1(KHARG_BBOX, d_str, orbit="DESCENDING")
            if img is None:
                continue

            cx, cy = tank_cx_cy(tank, KHARG_BBOX, iw, ih)

            # prod mask at 282°
            m_prod  = _build_prod_mask(cx, cy, tank["radius"], iw, ih, LOS_AZ_DESC)
            # full ring
            m_ring  = _build_full_ring(cx, cy, tank["radius"], iw, ih)
            # 90° rotated: (282 + 90) % 360 = 12°
            m_rot90 = _build_prod_mask(cx, cy, tank["radius"], iw, ih,
                                       (LOS_AZ_DESC + 90) % 360)
            # 180° rotated: (282 + 180) % 360 = 102°
            m_rot180 = _build_prod_mask(cx, cy, tank["radius"], iw, ih,
                                        (LOS_AZ_DESC + 180) % 360)

            csv_dates.append(d_str)
            db_prod_list.append(mask_mean_db(img, m_prod))
            db_ring_list.append(mask_mean_db(img, m_ring))
            db_rot90_list.append(mask_mean_db(img, m_rot90))
            db_rot180_list.append(mask_mean_db(img, m_rot180))
            db_csv_list.append(float(row.iloc[0]["db_mean"]))

        n = len(csv_dates)
        if n < 3:
            lines.append(f"[FINDING] Only {n} matched scenes — insufficient for correlation\n")
            continue

        def safe_corr(a, b):
            a, b = np.array(a), np.array(b)
            valid = np.isfinite(a) & np.isfinite(b)
            if valid.sum() < 3:
                return np.nan
            return float(np.corrcoef(a[valid], b[valid])[0, 1])

        r_prod  = safe_corr(db_prod_list,  db_csv_list)
        r_ring  = safe_corr(db_ring_list,  db_csv_list)
        r_rot90 = safe_corr(db_rot90_list, db_csv_list)
        r_rot180= safe_corr(db_rot180_list,db_csv_list)

        lines.append(f"N scenes matched = {n}\n\n")
        lines.append("| Mask | Corr with CSV db_mean | Well-depth pred (≥0.8) |\n")
        lines.append("|------|-----------------------|------------------------|\n")
        for label, r in [("prod (282°)", r_prod), ("full ring", r_ring),
                          ("rot+90 (12°)", r_rot90), ("rot+180 (102°)", r_rot180)]:
            verdict = "CONFIRMED" if (not np.isnan(r) and r >= 0.8) else "не подтверждено"
            lines.append(f"| {label} | {r:.3f} | {verdict} |\n")

        # Time-series plot
        _save_temporal_plot(csv_dates,
                            db_prod_list, db_ring_list, db_rot90_list, db_rot180_list,
                            db_csv_list, tid, r_prod, r_ring, r_rot90, r_rot180,
                            OUT_DIR / f"t2_temporal_{tid}.png")


# ── Task 3 ─────────────────────────────────────────────────────────────────────

def task3_fcs(kharg_csv_path, rt_csv_path, lines):
    lines.append("\n## Task 3 — FCS calibration (Kharg CSV + RT backup CSV)\n")
    lines.append("FCS = (p95 − p05 temporal dB per tank) / median intra-scene ring noise\n")

    df_k = pd.read_csv(kharg_csv_path)

    # ── Intra-scene ring noise: for each (date, tank_id) compute std across sectors
    # Proxy: use std of per-date db_mean across all tanks of same date (scene noise)
    # Better proxy: per-date std of db_mean values (each tank treated as one obs)
    # FCS per tank = temporal spread / typical scene noise
    scene_noise = df_k.groupby("date")["db_mean"].std().median()
    if np.isnan(scene_noise) or scene_noise == 0:
        scene_noise = 1.0  # fallback to avoid division by zero
    lines.append(f"Kharg median intra-scene noise (std across tanks per date) = "
                 f"{scene_noise:.3f} dB\n\n")

    def compute_fcs(df, label):
        results = []
        for tid, grp in df.groupby("tank_id"):
            vals = grp["db_mean"].dropna().values
            if len(vals) < 5:
                continue
            p95, p05 = np.percentile(vals, 95), np.percentile(vals, 5)
            fcs = (p95 - p05) / scene_noise
            results.append({"tank_id": tid, "n_dates": len(vals),
                             "p05": p05, "p95": p95,
                             "temporal_range": p95 - p05, "fcs": fcs})
        return pd.DataFrame(results).sort_values("fcs", ascending=False)

    df_fcs_k = compute_fcs(df_k, "Kharg")

    # Explicit worked example for t60_2 (highest FCS cycling tank)
    ex = df_fcs_k[df_fcs_k["tank_id"] == "t60_2"]
    if not ex.empty:
        r = ex.iloc[0]
        lines.append(f"**Worked example — t60_2:**\n")
        lines.append(f"  numerator   = p95 − p05 = {r.p95:.3f} − {r.p05:.3f} = {r.temporal_range:.3f} dB\n")
        lines.append(f"  denominator = median intra-scene noise = {scene_noise:.3f} dB\n")
        lines.append(f"  FCS = {r.temporal_range:.3f} / {scene_noise:.3f} = **{r.fcs:.3f}**\n\n")

    lines.append("### Kharg FCS (top 10)\n\n")
    lines.append("| tank_id | N dates | p05 dB | p95 dB | range dB | FCS |\n")
    lines.append("|---------|---------|--------|--------|----------|-----|\n")
    for _, row in df_fcs_k.head(10).iterrows():
        lines.append(f"| {row.tank_id} | {row.n_dates} | {row.p05:.2f} | "
                     f"{row.p95:.2f} | {row.temporal_range:.2f} | {row.fcs:.2f} |\n")

    # Calibrate threshold: natural break between cycling and idle tanks
    fcs_vals = df_fcs_k["fcs"].values
    threshold = _calibrate_threshold(fcs_vals)
    lines.append(f"\nCalibrated FCS threshold = **{threshold:.2f}** "
                 f"(median + 1σ of Kharg distribution)\n")

    n_above = int((fcs_vals >= threshold).sum())
    n_total = len(fcs_vals)
    lines.append(f"Kharg: {n_above}/{n_total} tanks above threshold\n")

    # Histogram
    _save_fcs_histogram(fcs_vals, threshold, "Kharg (47 tanks)",
                        OUT_DIR / "t3_fcs_kharg.png")

    # ── RT validation
    if rt_csv_path.exists():
        df_rt = pd.read_csv(rt_csv_path)
        rt_scene_noise = df_rt.groupby("date")["db_mean"].std().median()
        if np.isnan(rt_scene_noise) or rt_scene_noise == 0:
            rt_scene_noise = scene_noise  # use Kharg noise as fallback

        def compute_fcs_rt(df):
            results = []
            for tid, grp in df.groupby("tank_id"):
                vals = grp["db_mean"].dropna().values
                if len(vals) < 5:
                    continue
                p95, p05 = np.percentile(vals, 95), np.percentile(vals, 5)
                fcs = (p95 - p05) / rt_scene_noise
                results.append({"tank_id": tid, "n_dates": len(vals),
                                 "p05": p05, "p95": p95,
                                 "temporal_range": p95 - p05, "fcs": fcs})
            return pd.DataFrame(results).sort_values("fcs", ascending=False)

        df_fcs_rt = compute_fcs_rt(df_rt)
        lines.append(f"\n### RT validation (backup CSV, scene noise = "
                     f"{rt_scene_noise:.3f} dB)\n\n")
        lines.append("| tank_id | N dates | range dB | FCS |\n")
        lines.append("|---------|---------|----------|-----|\n")
        for _, row in df_fcs_rt.head(10).iterrows():
            above = "ABOVE" if row.fcs >= threshold else "below"
            lines.append(f"| {row.tank_id} | {row.n_dates} | "
                         f"{row.temporal_range:.2f} | {row.fcs:.2f} ({above}) |\n")

        rt_above = int((df_fcs_rt["fcs"].values >= threshold).sum())
        lines.append(f"\nRT: {rt_above}/{len(df_fcs_rt)} tanks above threshold "
                     f"(Kharg-calibrated threshold={threshold:.2f})\n")
        _save_fcs_histogram(df_fcs_rt["fcs"].values, threshold, "Ras Tanura (66 tanks)",
                            OUT_DIR / "t3_fcs_rt.png")
    else:
        lines.append("\n[FINDING] RT backup CSV not found — RT validation SKIPPED\n")
        threshold = _calibrate_threshold(fcs_vals)

    return threshold, df_fcs_k


def _calibrate_threshold(fcs_vals):
    """Median + 1σ as natural break between cycling and idle tanks."""
    return float(np.median(fcs_vals) + np.std(fcs_vals))


# ── Task 4 ─────────────────────────────────────────────────────────────────────

def task4_jw_yb_fcs(fcs_threshold, lines):
    lines.append("\n## Task 4 — JW/YB FCS from Supabase terminal_observations\n")

    sb = None
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            from supabase import create_client
            sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        except Exception as e:
            lines.append(f"[WARN] Supabase init: {e}\n")

    # Try to load from Supabase; fall back to SQL stub file
    def load_terminal(terminal_id, n_rows_expected, pivot_date=None):
        rows = []
        source = "supabase"
        if sb:
            try:
                offset = 0
                while True:
                    batch = (sb.table("terminal_observations")
                               .select("date,tank_id,db_mean")
                               .eq("terminal_id", terminal_id)
                               .range(offset, offset + 999)
                               .execute().data)
                    rows.extend(batch)
                    if len(batch) < 1000:
                        break
                    offset += 1000
                print(f"  Supabase {terminal_id}: {len(rows)} rows")
            except Exception as e:
                print(f"  [WARN] Supabase {terminal_id}: {e}")
                rows = []
                source = "unavailable"
        else:
            source = "unavailable"

        if not rows:
            lines.append(f"[FINDING] {terminal_id}: Supabase unavailable (403 or no data). "
                         f"Task 4 {terminal_id} INSUFFICIENT DATA.\n")
            return None, source

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        if pivot_date:
            df = df[df["date"] > pd.to_datetime(pivot_date)]
        return df, source

    # ── Juwaymah
    df_jw, src_jw = load_terminal("juwaymah", 6004)
    if df_jw is not None:
        _report_fcs_for_site(df_jw, "Juwaymah", fcs_threshold, src_jw, lines,
                             OUT_DIR / "t4_fcs_juwaymah.png")

    # ── Yanbu (exclude pre-pivot rows)
    df_yb, src_yb = load_terminal("yanbu", 3984, pivot_date=YB_PIVOT_DATE)
    if df_yb is not None:
        lines.append(f"(YB rows after azimuth pivot {YB_PIVOT_DATE}: {len(df_yb)})\n")
        _report_fcs_for_site(df_yb, "Yanbu", fcs_threshold, src_yb, lines,
                             OUT_DIR / "t4_fcs_yanbu.png")


def _report_fcs_for_site(df, site_name, threshold, source, lines, plot_path):
    scene_noise = df.groupby("date")["db_mean"].std().median()
    if np.isnan(scene_noise) or scene_noise == 0:
        scene_noise = 1.0

    results = []
    for tid, grp in df.groupby("tank_id"):
        vals = grp["db_mean"].dropna().values
        if len(vals) < 5:
            continue
        p95, p05 = np.percentile(vals, 95), np.percentile(vals, 5)
        fcs = (p95 - p05) / scene_noise
        results.append({"tank_id": tid, "n_dates": len(vals), "fcs": fcs})

    if not results:
        lines.append(f"[FINDING] {site_name}: insufficient data for FCS\n")
        return

    df_fcs = pd.DataFrame(results).sort_values("fcs", ascending=False)
    n_above = int((df_fcs["fcs"] >= threshold).sum())
    n_total = len(df_fcs)

    lines.append(f"\n### {site_name} (source: {source}, "
                 f"scene noise={scene_noise:.3f} dB)\n\n")
    lines.append(f"Tanks above FCS threshold ({threshold:.2f}): {n_above}/{n_total}\n\n")
    lines.append("| tank_id | N dates | FCS | Above threshold |\n")
    lines.append("|---------|---------|-----|----------------|\n")
    for _, row in df_fcs.iterrows():
        above = "YES" if row.fcs >= threshold else "no"
        lines.append(f"| {row.tank_id} | {row.n_dates} | {row.fcs:.2f} | {above} |\n")

    _save_fcs_histogram(df_fcs["fcs"].values, threshold, f"{site_name}", plot_path)


# ── Task 5 ─────────────────────────────────────────────────────────────────────

def task5_verdicts(t1_verdict, lines):
    lines.append("\n## Task 5 — Verdicts\n")

    lines.append("\n### Well-depth hypothesis assessment\n\n")
    lines.append("| Site | Task | Verdict |\n")
    lines.append("|------|------|---------|\n")
    lines.append(f"| Kharg | T1 spatial (empty vs full, all sectors) | {t1_verdict} |\n")
    lines.append(f"| Kharg | T2 temporal (all-rotation correlation ≥0.8) | see T2 table |\n")
    lines.append(f"| Kharg | T3 FCS calibration | see T3 histogram |\n")
    lines.append(f"| RT    | T3 FCS validation | see T3 histogram |\n")
    lines.append(f"| JW    | T4 FCS from Supabase | see T4 table |\n")
    lines.append(f"| YB    | T4 FCS from Supabase (post-pivot only) | see T4 table |\n")

    lines.append("\n### Well-depth implications for prod code\n\n")
    lines.append("If Task 1 PASS: well-depth hypothesis supported — full/empty tank dB "
                 "contrast is visible across all azimuths, not only in the shadow direction. "
                 "This means the prod crescent mask direction (282°) is NOT the sole cause "
                 "of the dB fill-proxy signal. Future A/B test (not in this round): "
                 "compare vol estimates from prod mask vs full-ring mask on historical data.\n\n")
    lines.append("If Task 1 FAIL: well-depth hypothesis NOT supported — the dB contrast "
                 "is azimuth-dependent. Prod crescent mask direction remains the critical "
                 "geometric choice; JW/YB orbit geometry must be verified before applying "
                 "the pipeline to those sites.\n\n")
    lines.append("No prod code changes in this round. Comments above are hypotheses "
                 "for future investigation only.\n")

    lines.append("\n---\n")
    lines.append("## Stop-line\n\n")
    lines.append(
        "Round 7.7 diagnostic complete. "
        "All findings are observational only. "
        "No prod code was modified. "
        "No Supabase writes were made. "
        "No paid API calls were made. "
        "FCS threshold is calibrated on Kharg data only and must be re-validated "
        "on live Sentinel Hub imagery before any operational use. "
        "JW/YB verdicts depend on Supabase availability in GHA; "
        "if Task 4 shows INSUFFICIENT DATA, re-run with valid secrets.\n"
    )


# ── Plot helpers ───────────────────────────────────────────────────────────────

def _save_sector_plot(scene_tdata, tanks, date_str, outpath):
    fig, axes = plt.subplots(1, len(tanks), figsize=(5 * len(tanks), 4), subplot_kw=dict(polar=True))
    if len(tanks) == 1:
        axes = [axes]
    for ax, tank in zip(axes, tanks):
        tid = tank["id"]
        sdata = scene_tdata.get(tid, {})
        # sdata values are (linear, dB) tuples
        azs_rad = np.deg2rad(list(sdata.keys()))
        dbs = [v[1] for v in sdata.values()]
        valid = [(a, d) for a, d in zip(azs_rad, dbs) if np.isfinite(d)]
        if valid:
            aa, dd = zip(*valid)
            ax.plot(list(aa) + [aa[0]], list(dd) + [dd[0]], "b-o", markersize=3)
        label = tank.get("status", "")
        ax.set_title(f"{tid}\n({label})", fontsize=9)
    fig.suptitle(f"Sector dB profiles — {date_str}", fontsize=10)
    plt.tight_layout()
    plt.savefig(outpath, dpi=100)
    plt.close()


def _save_tank_crop_with_ring(img_lin, cx, cy, radius_m, img_w, img_h,
                               tank_id, date_str, outpath):
    """SAR crop with full-ring and prod-mask (282°) contours overlaid."""
    r_px = int(radius_m / RESOLUTION * 2.5)
    x0 = max(0, int(cx) - r_px); x1 = min(img_lin.shape[1], int(cx) + r_px)
    y0 = max(0, int(cy) - r_px); y1 = min(img_lin.shape[0], int(cy) + r_px)
    crop = img_lin[y0:y1, x0:x1]
    crop_db = 10 * np.log10(np.clip(crop, 1e-10, None))

    ring_full = _build_full_ring(cx, cy, radius_m, img_w, img_h)[y0:y1, x0:x1]
    ring_prod = _build_prod_mask(cx, cy, radius_m, img_w, img_h, LOS_AZ_DESC)[y0:y1, x0:x1]

    # Annotate: mean dB inside prod mask
    prod_mask_full = _build_prod_mask(cx, cy, radius_m, img_w, img_h, LOS_AZ_DESC)
    _, db_prod, npx = prod_mask_mean(img_lin, cx, cy, radius_m, img_w, img_h)

    fig, ax = plt.subplots(figsize=(5, 5))
    vmin = np.nanpercentile(crop_db, 5)
    vmax = np.nanpercentile(crop_db, 95)
    ax.imshow(crop_db, cmap="gray", origin="upper", vmin=vmin, vmax=vmax)
    # full ring = green contour
    ax.contour(ring_full.astype(float), levels=[0.5], colors=["lime"], linewidths=1.5)
    # prod mask (282°) = red contour
    ax.contour(ring_prod.astype(float), levels=[0.5], colors=["red"], linewidths=1.5)
    ax.set_title(f"{tank_id} | {date_str}\nprod-mask dB={db_prod:.2f} (N={npx})\n"
                 f"green=full ring, red=prod mask 282°", fontsize=8)
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(outpath, dpi=120)
    plt.close()


def _save_temporal_plot(dates, prod, ring, rot90, rot180, csv_vals,
                        tank_id, r_prod, r_ring, r_rot90, r_rot180, outpath):
    fig, ax = plt.subplots(figsize=(10, 5))
    xs = range(len(dates))
    ax.plot(xs, csv_vals, "k-o", label="CSV db_mean", linewidth=2)
    ax.plot(xs, prod,   "b-s", label=f"prod 282° r={r_prod:.2f}")
    ax.plot(xs, ring,   "g-^", label=f"full ring r={r_ring:.2f}")
    ax.plot(xs, rot90,  "r-v", label=f"rot+90°  r={r_rot90:.2f}")
    ax.plot(xs, rot180, "m-D", label=f"rot+180° r={r_rot180:.2f}")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(dates, rotation=45, ha="right", fontsize=7)
    ax.set_ylabel("dB")
    ax.set_title(f"{tank_id} — temporal mask comparison")
    ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(outpath, dpi=100)
    plt.close()


def _save_fcs_histogram(fcs_vals, threshold, title, outpath):
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(fcs_vals, bins=20, color="steelblue", edgecolor="white")
    ax.axvline(threshold, color="red", linestyle="--", label=f"threshold={threshold:.2f}")
    ax.set_xlabel("FCS")
    ax.set_ylabel("count")
    ax.set_title(f"FCS distribution — {title}")
    ax.legend()
    plt.tight_layout()
    plt.savefig(outpath, dpi=100)
    plt.close()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="1,2,3,4,5")
    args = parser.parse_args()
    tasks = [int(t) for t in args.tasks.split(",")]

    kharg_csv = Path("public/kharg_raw_observations.csv")
    rt_csv    = Path("backups/ras_tanura_pre_rebuild_2026-09-22/terminal_observations.csv")

    lines = []
    lines.append("# Round 7.7 — Well-depth physics test + FCS criterion\n\n")

    # Always fetch scenes (needed for T1 and T2)
    print("Fetching Kharg DESC-35 scene catalog …")
    scenes_all = catalog_kharg_desc35(n=8)
    print(f"  Found {len(scenes_all)} scenes: {scenes_all}")
    scenes4 = scenes_all[:4]
    scenes6 = scenes_all[:6]

    t1_verdict = "INSUFFICIENT DATA"
    fcs_threshold = 3.0  # fallback
    df_fcs_k = None

    if 1 in tasks:
        t1_verdict = task1_spatial(scenes4, lines)

    if 2 in tasks:
        task2_temporal(scenes6, kharg_csv, lines)

    if 3 in tasks:
        result = task3_fcs(kharg_csv, rt_csv, lines)
        if result:
            fcs_threshold, df_fcs_k = result

    if 4 in tasks:
        task4_jw_yb_fcs(fcs_threshold, lines)

    if 5 in tasks:
        task5_verdicts(t1_verdict, lines)

    # Write markdown report
    md_path = OUT_DIR / "WELLDEPTH_7_7.md"
    md_path.write_text("".join(lines), encoding="utf-8")
    print(f"\nReport written to {md_path}")
    print(f"PNGs in {OUT_DIR}/")

    # Smoke-test: verify at least one PNG was produced
    pngs = list(OUT_DIR.glob("*.png"))
    print(f"PNGs produced: {len(pngs)}")
    for p in pngs:
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
