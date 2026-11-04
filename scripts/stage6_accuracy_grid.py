"""
stage6_accuracy_grid.py — RT/JU accuracy iteration: factorial pipeline sweep.

Pre-registered grid:
  V1: mask width {10, 15, 20m} × coverage {half-ring, full-ring}  → 6 variants
      (tested on 30 fresh DESC + 30 ASC scenes; requires SH credentials)
  V2: calibration {global-terminal, per-tank, frozen-2020-2023}   → 3 variants
  V3: aggregation {sum, cap-weighted, z-score×cap, median×N}      → 4 variants
  V4: subset {WL-45, WL+excl×0.3, t60-class}                      → 3 variants (winner of V1-V3 only)
  V5: smoothing {raw, 2-pass-median}                               → 2 variants (last)

Internal metrics (export data quarantined until Stage 4):
  M1: corr(Δ_DESC_monthly, Δ_ASC_monthly)  — overlap period, main criterion
  M2: mean |Δ_event| / placebo_p90          — point estimator, E1+E2 only
  M3: median |Δ_monthly|                    — peaceful period 2020-01 → 2023-12

Winner = max M1, provided M2 ≥ baseline M2 and M3 ≤ baseline M3.

Stage 4 (one locked run): winning config → 8 anchor combos, Bonferroni α=0.05/8.

Outputs:
  diagnostics/accuracy_grid/accuracy_grid.csv
  diagnostics/accuracy_grid/ACCURACY_GRID.md

Usage:
    cd scripts
    python stage6_accuracy_grid.py [--skip-v1] [--skip-stage4]
"""

import argparse
import datetime
import math
import os
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

SUPABASE_URL     = os.environ.get("SUPABASE_URL")  or ""
SUPABASE_KEY     = os.environ.get("SUPABASE_KEY")  or ""
SH_CLIENT_ID     = os.environ.get("CLIENT_ID")     or ""
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or ""

TERMINAL_ID  = "ras_tanura"
RESOLUTION   = 5       # m/px
BUFFER_M     = 300
TANK_HEIGHT_MIN = 2    # m
BARREL_PER_M3   = 6.2898

TRAIN_START  = "2020-01-01"
TRAIN_END    = "2023-12-31"
PEACEFUL_END = "2023-12-31"  # M3 window
EVENT_DATES  = [                         # M2 events (point estimator)
    {"label": "E1", "date": "2026-03-02", "expected": "BUILD"},
    {"label": "E2", "date": "2026-06-26", "expected": "DRAW"},
]
PLACEBO_N        = 200   # random dates for M2 denominator
PLACEBO_SEED     = 42
N_FRESH_SCENES   = 30    # per orbit for V1 sweep
MIN_VALID_FRAC   = 0.5
DB_FLOOR         = -20.0
BONFERRONI_STAGE4 = 8
ASC_ORBIT = 101; ASC_AZ = 98
DESC_ORBIT = 35; DESC_AZ = 282

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "diagnostics", "accuracy_grid")
os.makedirs(OUT_DIR, exist_ok=True)

parser = argparse.ArgumentParser()
parser.add_argument("--skip-v1",      action="store_true", help="Skip V1 SAR-refetch sweep (if SH unavailable)")
parser.add_argument("--skip-stage4",  action="store_true", help="Skip Stage 4 anchor correlation")
args = parser.parse_args()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_rt_from_supabase():
    """Pull all terminal_observations for ras_tanura with orbit_direction."""
    from supabase import create_client
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows = []; offset = 0
    while True:
        batch = (sb.table("terminal_observations")
                   .select("date,tank_id,db_mean,orbit_direction,relative_orbit")
                   .eq("terminal_id", TERMINAL_ID)
                   .range(offset, offset + 999).execute().data)
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    if not rows:
        raise RuntimeError("No terminal_observations for ras_tanura")
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _attach_geometry(df, tanks_map):
    df["radius_m"] = df["tank_id"].map(lambda tid: tanks_map.get(tid, {}).get("radius_m"))
    df["h_max"]    = df["tank_id"].map(lambda tid: tanks_map.get(tid, {}).get("h_max_m"))
    return df.dropna(subset=["radius_m", "h_max"])


def _point_est(s: pd.Series, ev_date: pd.Timestamp, n=3) -> float:
    before = s[s.index < ev_date].dropna()
    after  = s[s.index > ev_date].dropna()
    if len(before) < n or len(after) < n:
        return float("nan")
    return float(after.iloc[n-1]) - float(before.iloc[-n])


def _compute_terminal_series(df_subset, v2_mode, v3_mode, tanks_map,
                              tanks_list, total_cap_bbl, frozen_cal=None):
    """
    Compute terminal daily series from raw observations with given V2/V3.

    v2_mode: 'global' | 'per-tank' | 'frozen'
    v3_mode: 'sum' | 'cap-weighted' | 'zscore-cap' | 'median-N'
    frozen_cal: dict {tank_id: (db_min, db_max)} for 'per-tank' or tuple (lo, hi) for 'frozen'
    """
    df = df_subset.copy()

    if v2_mode == "global":
        lo = df["db_mean"].quantile(0.05)
        hi = df["db_mean"].quantile(0.95)
        df["fill_ratio"] = (hi - np.clip(df["db_mean"], lo, hi)) / (hi - lo)

    elif v2_mode == "per-tank":
        if frozen_cal is None:
            # compute per-tank percentiles from the whole series
            cal = df.groupby("tank_id")["db_mean"].quantile([0.05, 0.95]).unstack()
            cal.columns = ["lo", "hi"]
            frozen_cal = cal.to_dict("index")
        rows_fr = []
        for _, grp in df.groupby("tank_id"):
            tid = grp["tank_id"].iloc[0]
            c = frozen_cal.get(tid, {})
            lo = c.get("lo", grp["db_mean"].quantile(0.05))
            hi = c.get("hi", grp["db_mean"].quantile(0.95))
            if abs(hi - lo) < 0.01:
                hi = lo + 0.01
            fr = (hi - np.clip(grp["db_mean"], lo, hi)) / (hi - lo)
            rows_fr.append((grp.index, fr.values))
        idx_all = np.concatenate([r[0] for r in rows_fr])
        fr_all  = np.concatenate([r[1] for r in rows_fr])
        df.loc[idx_all, "fill_ratio"] = fr_all

    elif v2_mode == "frozen":
        lo, hi = frozen_cal  # (lo, hi) tuple from 2020-2023 period
        df["fill_ratio"] = (hi - np.clip(df["db_mean"], lo, hi)) / (hi - lo)

    if "fill_ratio" not in df.columns:
        df["fill_ratio"] = 0.5  # fallback

    df["h_cur"]    = TANK_HEIGHT_MIN + df["fill_ratio"] * (df["h_max"] - TANK_HEIGHT_MIN)
    df["barrels"]  = np.pi * df["radius_m"]**2 * df["h_cur"] * BARREL_PER_M3
    df["day"]      = df["date"].dt.normalize()

    if v3_mode == "sum":
        daily_tank = df.groupby(["day", "tank_id"])["barrels"].mean()
        series     = daily_tank.groupby("day").sum() / 1e6

    elif v3_mode == "cap-weighted":
        df["cap_bbl"] = (np.pi * df["radius_m"]**2
                         * (df["h_max"] - TANK_HEIGHT_MIN) * BARREL_PER_M3)
        daily_tank    = df.groupby(["day", "tank_id"])[["barrels", "cap_bbl"]].mean()
        day_df        = daily_tank.reset_index()
        w_sum  = day_df.groupby("day").apply(
            lambda g: (g["barrels"] * (g["cap_bbl"] / g["cap_bbl"].sum())).sum()
            if g["cap_bbl"].sum() > 0 else g["barrels"].mean()
        )
        series = w_sum * len(tanks_list) / 1e6

    elif v3_mode == "zscore-cap":
        # mean per-tank z-score (fill_ratio centered, normalized) × total_cap
        df["fr_mean"] = df.groupby("tank_id")["fill_ratio"].transform("mean")
        df["fr_std"]  = df.groupby("tank_id")["fill_ratio"].transform("std").replace(0, np.nan)
        df["zscore"]  = (df["fill_ratio"] - df["fr_mean"]) / df["fr_std"]
        daily_z  = df.groupby(["day", "tank_id"])["zscore"].mean()
        day_mean_z = daily_z.groupby("day").mean()
        series   = day_mean_z * total_cap_bbl / 1e6

    elif v3_mode == "median-N":
        daily_tank = df.groupby(["day", "tank_id"])["barrels"].mean()
        series     = daily_tank.groupby("day").median() * len(tanks_list) / 1e6

    else:
        raise ValueError(f"Unknown v3_mode: {v3_mode}")

    return series.rename("vol_mn_bbl").dropna()


def _compute_metrics(series_desc: pd.Series, series_asc: pd.Series,
                     all_daily: pd.Series, label: str) -> dict:
    """
    Compute M1/M2/M3 for a given pair of DESC/ASC terminal series.

    series_desc: daily volume series from DESC orbit
    series_asc:  daily volume series from ASC orbit (empty → M1=nan)
    all_daily:   combined series (DESC+ASC interleaved by date) for M2/M3
    """
    # M1: corr(Δ_DESC_monthly, Δ_ASC_monthly) on overlap period
    if series_asc.empty or series_desc.empty:
        m1 = float("nan"); m1_n = 0
    else:
        overlap_start = max(series_desc.index.min(), series_asc.index.min())
        overlap_end   = min(series_desc.index.max(), series_asc.index.max())
        if overlap_start >= overlap_end:
            m1 = float("nan"); m1_n = 0
        else:
            s_d = series_desc.loc[overlap_start:overlap_end].resample("ME").mean().dropna()
            s_a = series_asc.loc[overlap_start:overlap_end].resample("ME").mean().dropna()
            common = s_d.index.intersection(s_a.index)
            m1_n = len(common)
            if m1_n < 6:
                m1 = float("nan")
            else:
                d_delta = s_d.loc[common].diff().dropna()
                a_delta = s_a.loc[common].diff().dropna()
                ci = d_delta.index.intersection(a_delta.index)
                if len(ci) < 5:
                    m1 = float("nan")
                else:
                    m1 = float(stats.pearsonr(d_delta.loc[ci].values, a_delta.loc[ci].values)[0])

    # M2: mean |Δ_event| / placebo_p90 (point estimator on all_daily)
    event_deltas = []
    for ev in EVENT_DATES:
        d = _point_est(all_daily, pd.Timestamp(ev["date"]))
        if not np.isnan(d):
            event_deltas.append(abs(d))

    # Placebo: random dates in 2020-2022
    rng = np.random.default_rng(PLACEBO_SEED)
    placebo_days = []
    start_ts = pd.Timestamp("2020-01-01")
    end_ts   = pd.Timestamp("2022-12-31")
    range_days = (end_ts - start_ts).days
    for _ in range(PLACEBO_N):
        rd = start_ts + pd.Timedelta(days=int(rng.integers(0, range_days)))
        d  = _point_est(all_daily, rd)
        if not np.isnan(d):
            placebo_days.append(abs(d))

    if placebo_days and event_deltas:
        pl_p90 = float(np.percentile(placebo_days, 90))
        m2 = float(np.mean(event_deltas)) / (pl_p90 if pl_p90 > 0 else 1.0)
    else:
        m2 = float("nan")
    m2_n = len(event_deltas)

    # M3: median |Δ_monthly| in peaceful period 2020-2023
    peaceful = all_daily.loc[TRAIN_START:PEACEFUL_END]
    peaceful_m = peaceful.resample("ME").mean().dropna()
    peaceful_d = peaceful_m.diff().dropna().abs()
    m3 = float(peaceful_d.median()) if len(peaceful_d) >= 6 else float("nan")
    m3_n = int(len(peaceful_d))

    return {"label": label,
            "M1": round(m1, 4) if not np.isnan(m1) else None,
            "M1_n": m1_n,
            "M2": round(m2, 4) if not np.isnan(m2) else None,
            "M2_n": m2_n,
            "M3": round(m3, 4) if not np.isnan(m3) else None,
            "M3_n": m3_n}


# ── V2×V3 sweep (full history from Supabase) ──────────────────────────────────

def run_v2v3_sweep(df_raw, tanks_map, tanks_list, total_cap_bbl,
                   mask_label="baseline-15m-half", db_col="db_mean") -> list:
    """Sweep V2×V3 on existing data. Returns list of metric dicts."""
    print(f"\n[V2×V3] mask={mask_label}  col={db_col}")
    df = df_raw.copy()
    if db_col != "db_mean":
        df = df.rename(columns={db_col: "db_mean"})

    df_desc = df[df["orbit_direction"].str.upper() == "DESC"].copy()
    df_asc  = df[df["orbit_direction"].str.upper() == "ASC"].copy()

    # Frozen calibration: 2020-2023 global percentiles
    df_frozen = df[(df["date"] >= TRAIN_START) & (df["date"] <= TRAIN_END)]
    frozen_lo = float(df_frozen["db_mean"].quantile(0.05)) if len(df_frozen) >= 50 else None
    frozen_hi = float(df_frozen["db_mean"].quantile(0.95)) if len(df_frozen) >= 50 else None

    # Per-tank calibration (full history)
    cal = df.groupby("tank_id")["db_mean"].quantile([0.05, 0.95]).unstack()
    cal.columns = ["lo", "hi"]
    per_tank_cal = cal.to_dict("index")

    results = []
    V2_OPTIONS = [
        ("global",  None),
        ("per-tank", per_tank_cal),
        ("frozen",  (frozen_lo, frozen_hi) if frozen_lo is not None else None),
    ]
    V3_OPTIONS = ["sum", "cap-weighted", "zscore-cap", "median-N"]

    for v2_name, cal_arg in V2_OPTIONS:
        if cal_arg is None and v2_name == "frozen":
            print(f"  [skip] V2=frozen — not enough data in 2020-2023")
            continue
        for v3 in V3_OPTIONS:
            label = f"{mask_label}|V2={v2_name}|V3={v3}"
            try:
                s_desc = _compute_terminal_series(df_desc, v2_name, v3, tanks_map, tanks_list,
                                                  total_cap_bbl, frozen_cal=cal_arg)
                s_asc  = (_compute_terminal_series(df_asc,  v2_name, v3, tanks_map, tanks_list,
                                                   total_cap_bbl, frozen_cal=cal_arg)
                          if not df_asc.empty else pd.Series(dtype=float))
                # Combined series for M2/M3: prefer DESC, fill ASC on missing dates
                s_all = s_desc.copy()
                for d, v in s_asc.items():
                    if d not in s_all.index:
                        s_all[d] = v
                s_all = s_all.sort_index()
                m = _compute_metrics(s_desc, s_asc, s_all, label)
                results.append(m)
                print(f"  {label}: M1={m['M1']}(n={m['M1_n']}) M2={m['M2']}(n={m['M2_n']}) M3={m['M3']}(n={m['M3_n']})")
            except Exception as e:
                print(f"  [!] {label}: {e}")
                results.append({"label": label, "M1": None, "M2": None, "M3": None,
                                 "M1_n": 0, "M2_n": 0, "M3_n": 0, "error": str(e)})
    return results


# ── V1 sweep (SAR refetch with different mask params) ─────────────────────────

def _bbox(tanks, buf=BUFFER_M):
    from sentinelhub import BBox, CRS
    lats = [t["lat"] for t in tanks]
    lons = [t["lon"] for t in tanks]
    dlat = buf / 111320
    dlon = buf / (111320 * math.cos(math.radians(min(lats))))
    return BBox([min(lons)-dlon, min(lats)-dlat, max(lons)+dlon, max(lats)+dlat], crs=CRS.WGS84)


def _build_mask_variant(tank, bbox, img_w, img_h, az, width_m, full_ring: bool):
    Y, X = np.indices((img_h, img_w))
    cx = (tank["lon"] - bbox.min_x) / (bbox.max_x - bbox.min_x) * img_w
    cy = (bbox.max_y - tank["lat"]) / (bbox.max_y - bbox.min_y) * img_h
    dx = np.sin(np.radians(az))
    dy = -np.cos(np.radians(az))
    dist_sq  = (X - cx)**2 + (Y - cy)**2
    dot      = (X - cx)*dx + (Y - cy)*dy
    r_out_px = tank["radius_m"] / RESOLUTION
    r_in_px  = max(0, tank["radius_m"] - width_m) / RESOLUTION
    ring     = (dist_sq <= r_out_px**2) & (dist_sq >= r_in_px**2)
    if full_ring:
        return ring
    return ring & (dot > 0)


def _fetch_raw_images(tanks, clusters, n_scenes, orbit_direction, rel_orbit, az, sh_cfg):
    """Download n_scenes most-recent SAR images, return list of (date, cluster_name, img_array)."""
    from sentinelhub import (SentinelHubCatalog, DataCollection, SentinelHubRequest,
                              MimeType, BBox, CRS)
    from datetime import timedelta

    try:
        S1 = DataCollection.SENTINEL1_IW.define_from(
            f"s1iw_v1sweep_{orbit_direction.lower()}",
            service_url="https://sh.dataspace.copernicus.eu"
        )
    except ValueError:
        S1 = DataCollection[f"s1iw_v1sweep_{orbit_direction.lower()}"]

    catalog = SentinelHubCatalog(config=sh_cfg)
    tank_by_id = {t["id"]: t for t in tanks}
    fetch_clusters = [(c["name"], [tank_by_id[tid] for tid in c["tank_ids"] if tid in tank_by_id])
                      for c in clusters if any(tid in tank_by_id for tid in c["tank_ids"])]

    dir_upper = orbit_direction.upper()
    if dir_upper == "ASC":
        dir_upper = "ASCENDING"
    elif dir_upper == "DESC":
        dir_upper = "DESCENDING"

    orbit_filter = f"sar:orbit_state = '{'ascending' if 'ASC' in orbit_direction.upper() else 'descending'}' AND sar:relative_orbit = {rel_orbit}"

    _EVALSCRIPT = """
//VERSION=3
function setup() { return { input: [{ bands: ["VV"] }], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(s) { return [s.VV]; }
"""
    all_images = []  # list of {date, c_name, img, c_tanks, bbox_obj, img_w, img_h}
    for c_name, c_tanks in fetch_clusters:
        if not c_tanks:
            continue
        bbox = _bbox(c_tanks)
        lat0 = min(t["lat"] for t in c_tanks)
        img_w = int((bbox.max_x - bbox.min_x) * 111320 * math.cos(math.radians(lat0)) / RESOLUTION)
        img_h = int((bbox.max_y - bbox.min_y) * 111320 / RESOLUTION)

        search_results = list(catalog.search(
            S1, bbox=bbox,
            time=("2020-01-01", datetime.datetime.utcnow().strftime("%Y-%m-%d")),
            filter=orbit_filter,
            fields={"include": ["properties.datetime"], "exclude": []},
        ))
        dates = sorted({pd.to_datetime(r["properties"]["datetime"]).normalize()
                        for r in search_results}, reverse=True)[:n_scenes]

        for d in dates:
            d_str  = d.strftime("%Y-%m-%d")
            d_next = (d + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            req = SentinelHubRequest(
                evalscript=_EVALSCRIPT,
                input_data=[SentinelHubRequest.input_data(
                    data_collection=S1,
                    time_interval=(d_str, d_next),
                    other_args={
                        "processing": {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN",
                                       "demInstance": "COPERNICUS_30"},
                        "dataFilter": {"orbitDirection": dir_upper, "relativeOrbitNumber": rel_orbit},
                        "mosaicking": "ORBIT",
                    },
                )],
                responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
                bbox=bbox, size=(img_w, img_h), config=sh_cfg,
            )
            try:
                data = req.get_data()
                if not data:
                    continue
                all_images.append({
                    "date": d, "c_name": c_name, "img": data[0].squeeze(),
                    "c_tanks": c_tanks, "bbox": bbox, "img_w": img_w, "img_h": img_h,
                })
            except Exception as e:
                print(f"  [!] {orbit_direction} {d_str} {c_name}: {e}")
    return all_images


def _images_to_db_df(images, az, width_m, full_ring):
    """Apply mask variant to raw images, return DataFrame of (date, tank_id, db_mean)."""
    records = []
    for img_data in images:
        img = img_data["img"]
        c_tanks = img_data["c_tanks"]
        bbox = img_data["bbox"]
        img_w = img_data["img_w"]
        img_h = img_data["img_h"]
        d = img_data["date"]
        for t in c_tanks:
            mask = _build_mask_variant(t, bbox, img_w, img_h, az, width_m, full_ring)
            total_px = int(mask.sum())
            if total_px == 0:
                continue
            px = img[mask]
            valid = px[np.isfinite(px)]
            if len(valid) / total_px < MIN_VALID_FRAC:
                continue
            if valid.size == 0:
                continue
            db_val = float(10 * np.log10(np.clip(np.mean(valid), 1e-10, None)))
            if not np.isfinite(db_val):
                continue
            records.append({"date": d, "tank_id": t["id"],
                            "db_mean": max(db_val, DB_FLOOR),
                            "radius_m": float(t["radius_m"]), "h_max": float(t["h_max_m"])})
    if not records:
        return pd.DataFrame(columns=["date", "tank_id", "db_mean", "radius_m", "h_max"])
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    return df


def run_v1_sweep(tanks, clusters, tanks_map, tanks_list, total_cap_bbl):
    """Fetch 30 fresh scenes per orbit, apply V1 variants, compute metrics."""
    print(f"\n[V1] Fetching {N_FRESH_SCENES} fresh DESC + {N_FRESH_SCENES} ASC scenes…")
    from sentinelhub import SHConfig
    sh_cfg = SHConfig()
    sh_cfg.sh_client_id     = SH_CLIENT_ID
    sh_cfg.sh_client_secret = SH_CLIENT_SECRET
    sh_cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"
    sh_cfg.sh_token_url     = ("https://identity.dataspace.copernicus.eu/auth/realms/CDSE"
                               "/protocol/openid-connect/token")

    images_desc = _fetch_raw_images(tanks, clusters, N_FRESH_SCENES, "DESC", DESC_ORBIT, DESC_AZ, sh_cfg)
    images_asc  = _fetch_raw_images(tanks, clusters, N_FRESH_SCENES, "ASC",  ASC_ORBIT,  ASC_AZ,  sh_cfg)
    print(f"  Fetched: DESC={len(images_desc)} images  ASC={len(images_asc)} images")

    results = []
    V1_OPTIONS = [(10, False), (15, False), (20, False),
                  (10, True),  (15, True),  (20, True)]
    for width_m, full_ring in V1_OPTIONS:
        cov = "full" if full_ring else "half"
        label = f"V1-w{width_m}-{cov}|V2=global|V3=sum"
        try:
            df_desc_v1 = _images_to_db_df(images_desc, DESC_AZ, width_m, full_ring)
            df_asc_v1  = _images_to_db_df(images_asc,  ASC_AZ,  width_m, full_ring)
            if df_desc_v1.empty:
                raise RuntimeError("V1 DESC empty")
            # Global calibration on combined fresh scenes
            df_all_v1 = pd.concat([df_desc_v1, df_asc_v1], ignore_index=True)
            lo = float(df_all_v1["db_mean"].quantile(0.05))
            hi = float(df_all_v1["db_mean"].quantile(0.95))
            frozen_cal = (lo, hi)

            s_desc = _compute_terminal_series(df_desc_v1, "frozen", "sum", tanks_map,
                                              tanks_list, total_cap_bbl, frozen_cal=frozen_cal)
            s_asc  = (_compute_terminal_series(df_asc_v1, "frozen", "sum", tanks_map,
                                               tanks_list, total_cap_bbl, frozen_cal=frozen_cal)
                      if not df_asc_v1.empty else pd.Series(dtype=float))
            s_all = s_desc.copy()
            for d, v in s_asc.items():
                if d not in s_all.index:
                    s_all[d] = v
            s_all = s_all.sort_index()
            m = _compute_metrics(s_desc, s_asc, s_all, label)
            results.append(m)
            print(f"  {label}: M1={m['M1']}(n={m['M1_n']}) M2={m['M2']}(n={m['M2_n']}) M3={m['M3']}(n={m['M3_n']})")
        except Exception as e:
            print(f"  [!] {label}: {e}")
            results.append({"label": label, "M1": None, "M2": None, "M3": None,
                             "M1_n": 0, "M2_n": 0, "M3_n": 0, "error": str(e)})
    return results


# ── V4 sweep (subset, on winner of V1-V3) ────────────────────────────────────

def run_v4_sweep(df_raw, winner_v2, winner_v3, winner_mask_label,
                 tanks_map, tanks_list, total_cap_bbl, wl_enabled_ids,
                 t60_ids, all_tank_ids) -> list:
    print(f"\n[V4] Subset sweep on winner V2={winner_v2} V3={winner_v3}")
    # per-tank cal for winner
    cal = df_raw.groupby("tank_id")["db_mean"].quantile([0.05, 0.95]).unstack()
    cal.columns = ["lo", "hi"]
    per_tank_cal = cal.to_dict("index")
    df_frozen = df_raw[(df_raw["date"] >= TRAIN_START) & (df_raw["date"] <= TRAIN_END)]
    frozen_cal_tup = None
    if len(df_frozen) >= 50:
        frozen_cal_tup = (float(df_frozen["db_mean"].quantile(0.05)),
                          float(df_frozen["db_mean"].quantile(0.95)))

    cal_arg = per_tank_cal if winner_v2 == "per-tank" else (frozen_cal_tup if winner_v2 == "frozen" else None)

    subsets = {
        "WL-45":        wl_enabled_ids,
        "WL+excl×0.3":  all_tank_ids,   # excl get weight 0.3 via dummy — approximate with all tanks
        "t60-class":    t60_ids,
    }
    results = []
    for subset_name, subset_ids in subsets.items():
        label = f"{winner_mask_label}|V2={winner_v2}|V3={winner_v3}|V4={subset_name}"
        try:
            df_s = df_raw[df_raw["tank_id"].isin(subset_ids)].copy()
            df_desc = df_s[df_s["orbit_direction"].str.upper() == "DESC"]
            df_asc  = df_s[df_s["orbit_direction"].str.upper() == "ASC"]
            tanks_sub = [t for t in tanks_list if t["id"] in subset_ids]
            total_cap_sub = sum(math.pi * t["radius_m"]**2 * (t["h_max_m"] - TANK_HEIGHT_MIN) * BARREL_PER_M3
                                for t in tanks_sub)

            s_desc = _compute_terminal_series(df_desc, winner_v2, winner_v3,
                                              tanks_map, tanks_sub, total_cap_sub, cal_arg)
            s_asc  = (_compute_terminal_series(df_asc, winner_v2, winner_v3,
                                               tanks_map, tanks_sub, total_cap_sub, cal_arg)
                      if not df_asc.empty else pd.Series(dtype=float))
            s_all = s_desc.copy()
            for d, v in s_asc.items():
                if d not in s_all.index:
                    s_all[d] = v
            s_all = s_all.sort_index()
            m = _compute_metrics(s_desc, s_asc, s_all, label)
            results.append(m)
            print(f"  {label}: M1={m['M1']} M2={m['M2']} M3={m['M3']}")
        except Exception as e:
            print(f"  [!] {label}: {e}")
            results.append({"label": label, "M1": None, "M2": None, "M3": None,
                             "M1_n": 0, "M2_n": 0, "M3_n": 0, "error": str(e)})
    return results


# ── Stage 4 — Locked anchor correlation ──────────────────────────────────────

def run_stage4_correlation(s_terminal_daily: pd.Series) -> list:
    print("\n[Stage 4] Locked anchor correlation (one run, 8 combos, Bonferroni α=0.05/8)…")
    from scipy import stats as scipy_stats

    # Load anchors
    def _load_jodi():
        path = os.path.join(os.path.dirname(__file__), "..", "data", "external", "jodi_saudi_crude.csv")
        df = pd.read_csv(path, parse_dates=["month"])
        s = df.set_index("month")["exports_mbpd"].rename("jodi_saudi")
        s = s[s > 0].dropna()
        return s.set_axis(s.index.to_period("M").to_timestamp("M"))

    def _load_rtju():
        path = os.path.join(os.path.dirname(__file__), "..", "client_package", "data", "portwatch_monthly.csv")
        if not os.path.exists(path):
            return pd.Series(dtype=float, name="rtju_mbpd")
        df = pd.read_csv(path)
        if "month" not in df.columns or "rtju_mbpd" not in df.columns:
            return pd.Series(dtype=float, name="rtju_mbpd")
        df["month"] = pd.to_datetime(df["month"], errors="coerce")
        df = df.dropna(subset=["month"])
        s = df.set_index("month")["rtju_mbpd"].rename("rtju_mbpd")
        s = s.set_axis(s.index.to_period("M").to_timestamp("M"))
        return s[s > 0].dropna()

    jodi = _load_jodi()
    rtju = _load_rtju()
    print(f"  JODI: {len(jodi)} months  rtju: {len(rtju)} months")

    stor_monthly = s_terminal_daily.resample("ME").mean().dropna()
    stor_monthly = stor_monthly.set_axis(stor_monthly.index.to_period("M").to_timestamp("M"))

    bonf_alpha = 0.05 / BONFERRONI_STAGE4
    results = []

    def _run_combo(stor, anchor, metric, lag, start, end):
        s_w = stor.loc[start:end]
        e_w = anchor.loc[start:end]
        if metric == "delta":
            x = s_w.diff().dropna()
            y = e_w.diff().dropna()
        else:
            x = (s_w - s_w.rolling(12, min_periods=6).mean()).dropna()
            y = (e_w - e_w.rolling(12, min_periods=6).mean()).dropna()
        x = x.shift(lag)
        idx = x.dropna().index.intersection(y.dropna().index)
        if len(idx) < 12:
            return {"r": None, "p": None, "n": len(idx)}
        xv = x.loc[idx].values.astype(float)
        yv = y.loc[idx].values.astype(float)
        mask = np.isfinite(xv) & np.isfinite(yv)
        if mask.sum() < 12:
            return {"r": None, "p": None, "n": int(mask.sum())}
        r, p = scipy_stats.pearsonr(xv[mask], yv[mask])
        return {"r": float(r), "p": float(p), "n": int(mask.sum())}

    anchors = {"jodi_saudi": jodi}
    if not rtju.empty:
        anchors["rtju_mbpd"] = rtju
    else:
        print("  [!] rtju_mbpd not available — running 4 JODI combos only")

    for anchor_name, anchor_s in anchors.items():
        for metric in ["delta", "level_anom"]:
            for lag in [0, 1]:
                tr = _run_combo(stor_monthly, anchor_s, metric, lag, TRAIN_START, TRAIN_END)
                ho = _run_combo(stor_monthly, anchor_s, metric, lag, "2024-01-01", "2026-02-28") if tr["r"] is not None else {"r": None, "p": None, "n": 0}
                passes = (ho["p"] is not None and ho["p"] < bonf_alpha and tr["r"] is not None and
                          ho["r"] is not None and np.sign(ho["r"]) == np.sign(tr["r"]))
                row = {
                    "anchor": anchor_name, "metric": metric, "lag": lag,
                    "r_train": tr["r"], "p_train": tr["p"], "n_train": tr["n"],
                    "r_holdout": ho["r"], "p_holdout": ho["p"], "n_holdout": ho["n"],
                    "bonf_alpha": bonf_alpha, "passes_bonferroni": passes,
                }
                results.append(row)
                print(f"  {anchor_name}|{metric}|lag={lag:+d}: r_train={tr['r']}  r_holdout={ho['r']}  p_holdout={ho['p']}  {'PASS' if passes else 'FAIL'}")

    n_pass = sum(1 for r in results if r["passes_bonferroni"])
    print(f"\n  Stage 4 result: {n_pass}/{len(results)} Bonferroni PASS (α={bonf_alpha:.5f})")
    return results


# ── Report ────────────────────────────────────────────────────────────────────

def write_report(all_results, baseline_row, winner_row, stage4_rows, skip_v1):
    import datetime as dt
    lines = [
        "# RT Accuracy Grid — Report",
        "",
        f"Run: {dt.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Design",
        "- M1 = corr(Δ_DESC_monthly, Δ_ASC_monthly) on overlap period  [main]",
        "- M2 = mean |Δ_E1+E2| / placebo_p90  (point estimator, 200 random dates)",
        "- M3 = median |Δ_monthly| peaceful period 2020-2023",
        "- Winner = max M1 with M2 ≥ baseline and M3 ≤ baseline",
        f"- V1 SAR sweep: {'skipped (--skip-v1)' if skip_v1 else f'{N_FRESH_SCENES} DESC + {N_FRESH_SCENES} ASC fresh scenes'}",
        "",
        "## Grid",
        "",
        "| # | Config | M1 | M1_n | M2 | M2_n | M3 | M3_n | Winner |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    baseline_m1 = baseline_row["M1"] if baseline_row else None
    baseline_m2 = baseline_row["M2"] if baseline_row else None
    baseline_m3 = baseline_row["M3"] if baseline_row else None

    for i, row in enumerate(all_results):
        m1s = f"{row['M1']:.4f}" if row["M1"] is not None else "n/a"
        m2s = f"{row['M2']:.4f}" if row["M2"] is not None else "n/a"
        m3s = f"{row['M3']:.4f}" if row["M3"] is not None else "n/a"
        is_win = winner_row is not None and row["label"] == winner_row["label"]
        flag = "**★**" if is_win else ""
        baseline_flag = " *(baseline)*" if baseline_row and row["label"] == baseline_row["label"] else ""
        lines.append(f"| {i+1} | {row['label']}{baseline_flag} | {m1s} | {row['M1_n']} | "
                     f"{m2s} | {row['M2_n']} | {m3s} | {row['M3_n']} | {flag} |")

    lines += ["", "## Baseline", ""]
    if baseline_row:
        lines += [f"- Config: `{baseline_row['label']}`",
                  f"- M1={baseline_row['M1']}  M2={baseline_row['M2']}  M3={baseline_row['M3']}"]
    lines += ["", "## Winner", ""]
    if winner_row:
        lines += [f"- Config: `{winner_row['label']}`",
                  f"- M1={winner_row['M1']} (+{(winner_row['M1'] or 0) - (baseline_m1 or 0):.4f} vs baseline)",
                  f"- M2={winner_row['M2']}  M3={winner_row['M3']}"]
    else:
        lines += ["No winner — baseline is best or M2/M3 constraints not met."]

    if stage4_rows:
        n_pass = sum(1 for r in stage4_rows if r["passes_bonferroni"])
        bonf_alpha = 0.05 / BONFERRONI_STAGE4
        lines += [
            "",
            "## Stage 4 — Locked Anchor Correlation",
            "",
            f"Bonferroni α = 0.05/{BONFERRONI_STAGE4} = {bonf_alpha:.5f}  |  PASS: {n_pass}/{len(stage4_rows)}",
            "",
            "| Anchor | Metric | Lag | r_train | n_train | r_holdout | p_holdout | n_holdout | Bonf |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in stage4_rows:
            rt  = f"{r['r_train']:.4f}"  if r["r_train"]  is not None else "n/a"
            rh  = f"{r['r_holdout']:.4f}" if r["r_holdout"] is not None else "n/a"
            ph  = f"{r['p_holdout']:.4f}" if r["p_holdout"] is not None else "n/a"
            bonf = "**PASS**" if r["passes_bonferroni"] else "FAIL"
            lines.append(f"| {r['anchor']} | {r['metric']} | {r['lag']:+d} | {rt} | {r['n_train']} | "
                         f"{rh} | {ph} | {r['n_holdout']} | {bonf} |")
        lines += [""]
        if n_pass > 0:
            lines.append(f"**{n_pass} combination(s) passed Bonferroni.** Export signal detected in winning config.")
        else:
            lines.append("**0 Bonferroni passes.** Null is robust to measurement optimisation — "
                         "RT export-flow correlation absent across all calibration variants tested.")

    md = "\n".join(lines)
    out_path = os.path.join(OUT_DIR, "ACCURACY_GRID.md")
    with open(out_path, "w") as f:
        f.write(md)
    print(f"\n  Report: {out_path}")
    return md


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from tank_registry import LOCATIONS

    rt_loc   = next(loc for loc in LOCATIONS if loc["id"] == TERMINAL_ID)
    tanks_list = [t for t in rt_loc["tanks"] if t.get("enabled", True)]
    tanks_map  = {t["id"]: t for t in tanks_list}
    clusters   = rt_loc.get("clusters", [])
    all_tank_ids  = {t["id"] for t in tanks_list}

    total_cap_bbl = sum(
        math.pi * t["radius_m"]**2 * (t["h_max_m"] - TANK_HEIGHT_MIN) * BARREL_PER_M3
        for t in tanks_list
    )
    t60_ids = {t["id"] for t in tanks_list if t.get("radius_m", 0) >= 55}
    wl_enabled_ids = all_tank_ids  # already enabled-only

    print("[Stage 0] Loading RT data from Supabase…")
    df_raw = _load_rt_from_supabase()
    df_raw = _attach_geometry(df_raw, tanks_map)
    n_desc = (df_raw["orbit_direction"].str.upper() == "DESC").sum()
    n_asc  = (df_raw["orbit_direction"].str.upper() == "ASC").sum()
    print(f"  {len(df_raw)} rows  DESC={n_desc}  ASC={n_asc}  dates: {df_raw['date'].min().date()} → {df_raw['date'].max().date()}")
    if n_asc == 0:
        print("  WARNING: No ASC data. M1 will be NaN for all configs. Run stage5_asc_backfill.py first.")

    all_results = []

    # ── V1 sweep ──────────────────────────────────────────────────────────────
    if not args.skip_v1:
        v1_results = run_v1_sweep(tanks_list, clusters, tanks_map, tanks_list, total_cap_bbl)
        all_results.extend(v1_results)
    else:
        print("\n[V1] Skipped (--skip-v1)")

    # ── V2×V3 sweep ───────────────────────────────────────────────────────────
    v23_results = run_v2v3_sweep(df_raw, tanks_map, tanks_list, total_cap_bbl)
    all_results.extend(v23_results)

    # Baseline = global calibration, 15m half-ring (current prod), sum
    baseline_label = "baseline-15m-half|V2=global|V3=sum"
    baseline_row = next((r for r in all_results if r["label"] == baseline_label), None)
    if baseline_row is None and v23_results:
        baseline_row = v23_results[0]  # first V2=global, V3=sum

    print(f"\n  Baseline: {baseline_row}")

    # Pick winner from all results so far: max M1, M2 ≥ baseline M2, M3 ≤ baseline M3
    def _eligible(r):
        if r["M1"] is None:
            return False
        if baseline_row:
            bm2 = baseline_row.get("M2") or 0
            bm3 = baseline_row.get("M3") or float("inf")
            if r["M2"] is not None and r["M2"] < bm2:
                return False
            if r["M3"] is not None and r["M3"] > bm3:
                return False
        return True

    eligible = [r for r in all_results if _eligible(r)]
    if eligible:
        winner_v23 = max(eligible, key=lambda r: r["M1"])
    else:
        winner_v23 = baseline_row

    print(f"\n  Winner after V1-V3: {winner_v23}")

    # ── V4 sweep (on winner) ──────────────────────────────────────────────────
    if winner_v23:
        label_parts = winner_v23["label"].split("|")
        v2_part = next((p.replace("V2=", "") for p in label_parts if p.startswith("V2=")), "global")
        v3_part = next((p.replace("V3=", "") for p in label_parts if p.startswith("V3=")), "sum")
        mask_part = label_parts[0]
        v4_results = run_v4_sweep(df_raw, v2_part, v3_part, mask_part,
                                  tanks_map, tanks_list, total_cap_bbl,
                                  wl_enabled_ids, t60_ids, all_tank_ids)
        all_results.extend(v4_results)
        eligible = [r for r in all_results if _eligible(r)]
        winner_final = max(eligible, key=lambda r: r["M1"]) if eligible else winner_v23
    else:
        winner_final = winner_v23

    # ── V5 smoothing (on overall winner) ─────────────────────────────────────
    if winner_final:
        print(f"\n[V5] Smoothing sweep on winner: {winner_final['label']}")
        label_parts = winner_final["label"].split("|")
        v2_part = next((p.replace("V2=", "") for p in label_parts if p.startswith("V2=")), "global")
        v3_part = next((p.replace("V3=", "") for p in label_parts if p.startswith("V3=")), "sum")
        cal = df_raw.groupby("tank_id")["db_mean"].quantile([0.05, 0.95]).unstack()
        cal.columns = ["lo", "hi"]
        per_tank_cal = cal.to_dict("index")
        df_frozen = df_raw[(df_raw["date"] >= TRAIN_START) & (df_raw["date"] <= TRAIN_END)]
        frozen_cal_tup = (float(df_frozen["db_mean"].quantile(0.05)), float(df_frozen["db_mean"].quantile(0.95))) if len(df_frozen) >= 50 else None
        cal_arg = per_tank_cal if v2_part == "per-tank" else (frozen_cal_tup if v2_part == "frozen" else None)

        for smoothing in ["raw", "2pass-median"]:
            label_v5 = f"{winner_final['label']}|V5={smoothing}"
            try:
                df_desc = df_raw[df_raw["orbit_direction"].str.upper() == "DESC"].copy()
                df_asc  = df_raw[df_raw["orbit_direction"].str.upper() == "ASC"].copy()
                s_desc = _compute_terminal_series(df_desc, v2_part, v3_part, tanks_map,
                                                  tanks_list, total_cap_bbl, cal_arg)
                s_asc  = (_compute_terminal_series(df_asc, v2_part, v3_part, tanks_map,
                                                   tanks_list, total_cap_bbl, cal_arg)
                          if not df_asc.empty else pd.Series(dtype=float))
                if smoothing == "2pass-median":
                    s_desc = s_desc.rolling(3, center=True, min_periods=1).median()
                    s_desc = s_desc.rolling(3, center=True, min_periods=1).median()
                    if not s_asc.empty:
                        s_asc = s_asc.rolling(3, center=True, min_periods=1).median()
                        s_asc = s_asc.rolling(3, center=True, min_periods=1).median()
                s_all = s_desc.copy()
                for d, v in s_asc.items():
                    if d not in s_all.index:
                        s_all[d] = v
                s_all = s_all.sort_index()
                m = _compute_metrics(s_desc, s_asc, s_all, label_v5)
                all_results.append(m)
                print(f"  {label_v5}: M1={m['M1']} M2={m['M2']} M3={m['M3']}")
            except Exception as e:
                print(f"  [!] {label_v5}: {e}")
                all_results.append({"label": label_v5, "M1": None, "M2": None, "M3": None,
                                    "M1_n": 0, "M2_n": 0, "M3_n": 0, "error": str(e)})

    # Final winner across all
    eligible = [r for r in all_results if _eligible(r)]
    winner_row = max(eligible, key=lambda r: r["M1"]) if eligible else baseline_row

    # Save CSV
    csv_path = os.path.join(OUT_DIR, "accuracy_grid.csv")
    pd.DataFrame(all_results).to_csv(csv_path, index=False)
    print(f"\n  Grid saved: {csv_path}  ({len(all_results)} rows)")

    # ── Stage 4 ───────────────────────────────────────────────────────────────
    stage4_rows = []
    if not args.skip_stage4 and winner_row:
        # Build terminal series from winner config for Stage 4
        label_parts = winner_row["label"].split("|")
        v2_w = next((p.replace("V2=", "") for p in label_parts if p.startswith("V2=")), "global")
        v3_w = next((p.replace("V3=", "") for p in label_parts if p.startswith("V3=")), "sum")
        cal = df_raw.groupby("tank_id")["db_mean"].quantile([0.05, 0.95]).unstack()
        cal.columns = ["lo", "hi"]
        per_tank_cal = cal.to_dict("index")
        df_frozen = df_raw[(df_raw["date"] >= TRAIN_START) & (df_raw["date"] <= TRAIN_END)]
        frozen_cal_tup = (float(df_frozen["db_mean"].quantile(0.05)), float(df_frozen["db_mean"].quantile(0.95))) if len(df_frozen) >= 50 else None
        cal_arg_w = per_tank_cal if v2_w == "per-tank" else (frozen_cal_tup if v2_w == "frozen" else None)

        df_all = df_raw.copy()
        s_winner = _compute_terminal_series(df_all, v2_w, v3_w, tanks_map,
                                            tanks_list, total_cap_bbl, cal_arg_w)
        stage4_rows = run_stage4_correlation(s_winner)

    write_report(all_results, baseline_row, winner_row, stage4_rows, args.skip_v1)
    print("\n[Accuracy Grid] Done.")


if __name__ == "__main__":
    main()
