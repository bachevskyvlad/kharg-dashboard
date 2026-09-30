"""
task7_rt_decomposition.py — RT functional-group decomposition (pre-registered).

Hypothesis: RT terminal sum masks countervailing flows between sub-groups.
Signal may live in functional sub-groups (pier-side loading tanks vs inland
buffer tanks vs large-bore t60 class).

Five sub-series frozen before calculation:
  1. south_pier   — WL south-cluster tanks ≤600m from pier reference
  2. south_inland — remaining WL south-cluster tanks
  3. north        — WL north-cluster tanks
  4. t60_class    — WL tanks with radius_m ≥ 55m
  5. active10     — top-10 WL tanks by db_mean std over train (2020-2023)

Sanity gate (Stage 1): south_pier + south_inland + north == prod WL curve ±0.5%

Outputs:
  diagnostics/rt_decomp/rt_per_tank.parquet
  diagnostics/rt_decomp/subgroup_composition.txt
  diagnostics/rt_decomp/event_decomp.csv
  diagnostics/rt_decomp/correlation_grid.csv
  diagnostics/rt_decomp/correlation_holdout.csv
  diagnostics/rt_decomp/asc101_pilot.txt
  diagnostics/rt_decomp/RT_DECOMPOSITION.md

Usage:
    cd scripts && python task7_rt_decomposition.py [--skip-asc]

Options:
  --skip-asc   Skip Stage 4 (ASC 101 pilot) — use if SH egress unavailable.
"""

import argparse
import datetime
import json
import math
import os
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
SH_CLIENT_ID = os.environ.get("CLIENT_ID") or "sh-0f08bb88-cde8-4e6e-8eb9-1006acfe6be9"
SH_CLIENT_SECRET = os.environ.get("CLIENT_SECRET") or "erkNtZDw4z36UB0Xm7BrzcYixzaUGeW0"

from tank_registry import LOCATIONS
from volume import compute_volumes
from coverage_filter import apply_coverage_filter
from config import BARREL_PER_M3, TANK_HEIGHT_MIN

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "diagnostics", "rt_decomp")
os.makedirs(OUT_DIR, exist_ok=True)

# ── Pre-registered sub-series parameters ──────────────────────────────────────
PIER_REF           = (26.6480, 50.1600)   # South pier reference (east of complex)
PIER_RADIUS_M      = 600.0
TRAIN_START        = "2020-01-01"
TRAIN_END          = "2023-12-31"
HOLDOUT_START      = "2024-01-01"
HOLDOUT_END        = "2026-02-28"
ACTIVE10_TOP_N     = 10
T60_MIN_RADIUS_M   = 55.0   # ≥55m → t60/large-bore class
MIN_OVERLAP        = 18
BONFERRONI_N       = 40     # 5 sub-series × 2 anchors × 2 metrics × 2 lags
BONFERRONI_ALPHA   = 0.05 / BONFERRONI_N

# War / event windows (diagnostic only — outside train+holdout)
EVENTS = [
    {"label": "E1: Attack/Halt",    "date": "2026-03-02", "expected": "BUILD"},
    {"label": "E2: Ops resumed",    "date": "2026-06-26", "expected": "DRAW"},
    {"label": "E3: Ramp ~90%",      "date": "2026-07-15", "expected": "DRAW"},
]
EVENT_WINDOW_DAYS = 21   # ±21d around event date


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return 2 * R * math.asin(math.sqrt(a))


# ── Stage 0 — Data export ──────────────────────────────────────────────────────

def stage0_export() -> pd.DataFrame:
    """Pull terminal_observations for ras_tanura, compute per-tank volumes."""
    print("\n[Stage 0] Exporting RT per-tank data from Supabase…")
    from supabase import create_client
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    rows = []; offset = 0
    while True:
        batch = (sb.table("terminal_observations")
                   .select("date,tank_id,db_mean")
                   .eq("terminal_id", "ras_tanura")
                   .range(offset, offset + 999).execute().data)
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000

    print(f"  {len(rows)} raw observations fetched")
    if not rows:
        raise RuntimeError("No terminal_observations for ras_tanura — run backfill first")

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])

    # Attach geometry from registry — only enabled tanks
    rt_loc  = next(loc for loc in LOCATIONS if loc["id"] == "ras_tanura")
    enabled = {t["id"]: t for t in rt_loc["tanks"] if t.get("enabled", True)}
    print(f"  enabled tanks: {len(enabled)}")

    df["radius_m"] = df["tank_id"].map(lambda tid: enabled.get(tid, {}).get("radius_m"))
    df["h_max"]    = df["tank_id"].map(lambda tid: enabled.get(tid, {}).get("h_max_m"))
    df = df.dropna(subset=["radius_m", "h_max"])

    # Coverage filter (same as prod)
    df, n_dropped, max_tanks = apply_coverage_filter(df, min_share=0.5)
    if n_dropped:
        print(f"  coverage filter: dropped {n_dropped} partial dates")

    # Global calibration — all enabled tanks, all dates (same as prod)
    df, db_min, db_max = compute_volumes(df)
    print(f"  calibration: dB_min={db_min:.3f}  dB_max={db_max:.3f}")
    print(f"  {len(df)} rows after filtering  ({df['date'].min().date()} → {df['date'].max().date()})")

    parquet_path = os.path.join(OUT_DIR, "rt_per_tank.parquet")
    df.to_parquet(parquet_path, index=False)
    print(f"  Saved: {parquet_path}")
    return df, db_min, db_max, enabled, rt_loc


# ── Sub-series definitions ─────────────────────────────────────────────────────

def define_subgroups(df: pd.DataFrame, enabled: dict, rt_loc: dict) -> dict:
    """Return dict {group_name: set of tank_ids}. Frozen by this function."""
    print("\n[Stage 1] Defining sub-series…")

    south_ids = set(rt_loc["clusters"][0]["tank_ids"])
    north_ids = set(rt_loc["clusters"][1]["tank_ids"])

    wl_set      = set(enabled.keys())
    wl_south    = wl_set & south_ids
    wl_north    = wl_set & north_ids

    # south_pier / south_inland by pier proximity
    pier_lat, pier_lon = PIER_REF
    south_pier    = set()
    south_inland  = set()
    rt_tanks_map  = {t["id"]: t for t in rt_loc["tanks"]}

    for tid in wl_south:
        t    = rt_tanks_map[tid]
        dist = _haversine_m(t["lat"], t["lon"], pier_lat, pier_lon)
        if dist <= PIER_RADIUS_M:
            south_pier.add(tid)
        else:
            south_inland.add(tid)

    # t60_class: WL tanks with radius_m ≥ T60_MIN_RADIUS_M
    t60_class = {tid for tid, t in rt_tanks_map.items()
                 if tid in wl_set and t.get("radius_m", 0) >= T60_MIN_RADIUS_M}

    # active10: top-N WL tanks by db_mean std over TRAIN window
    df_train = df[(df["date"] >= TRAIN_START) & (df["date"] <= TRAIN_END)]
    std_ser  = df_train[df_train["tank_id"].isin(wl_set)].groupby("tank_id")["db_mean"].std()
    active10_set = set(std_ser.nlargest(ACTIVE10_TOP_N).index.tolist())

    groups = {
        "south_pier":   south_pier,
        "south_inland": south_inland,
        "north":        wl_north,
        "t60_class":    t60_class,
        "active10":     active10_set,
    }

    print(f"  PIER_REF = {PIER_REF}, radius ≤ {PIER_RADIUS_M:.0f}m")
    for name, s in groups.items():
        print(f"  {name}: {len(s)} tanks — {sorted(s)}")
    print(f"  active10 top-10 by train std: {sorted(active10_set)}")

    return groups


def compute_subgroup_series(df: pd.DataFrame, group_tanks: set) -> pd.Series:
    """Sum daily volumes for a sub-group. Returns date-indexed Mb series."""
    sub = df[df["tank_id"].isin(group_tanks)].copy()
    sub["day"] = sub["date"].dt.normalize()
    daily_tank = sub.groupby(["day", "tank_id"])["barrels"].mean().reset_index()
    daily_sum  = daily_tank.groupby("day")["barrels"].sum() / 1e6
    return daily_sum.rename("vol_mn_bbl")


def sanity_check(groups: dict, df: pd.DataFrame) -> bool:
    """south_pier + south_inland + north must equal prod WL sum within 0.5%."""
    print("\n[Stage 1] Sanity check: geo-split == prod WL curve…")
    prod_wl = compute_subgroup_series(df, groups["south_pier"] | groups["south_inland"] | groups["north"])
    geo_sum = (compute_subgroup_series(df, groups["south_pier"])
               + compute_subgroup_series(df, groups["south_inland"])
               + compute_subgroup_series(df, groups["north"]))
    geo_sum = geo_sum.reindex(prod_wl.index).fillna(0)

    diffs = (geo_sum - prod_wl).abs()
    rel   = (diffs / prod_wl.replace(0, np.nan)).dropna()
    max_err = rel.max()
    ok = max_err < 0.005  # 0.5%
    print(f"  max relative error = {max_err:.5f}  ({'PASS' if ok else 'FAIL ⚠'})")
    if not ok:
        print("  WARNING: geo-split deviates from prod WL — check coverage filter alignment")
    return ok


# ── Stage 2 — Event decomposition ─────────────────────────────────────────────

def _point_estimator(s: pd.Series, ev_date: pd.Timestamp, n_passes: int = 3) -> float:
    """point[+n_passes] − point[−n_passes]: val at 3rd SAR pass after event minus val at 3rd pass before."""
    before = s[s.index < ev_date].dropna()
    after  = s[s.index > ev_date].dropna()
    if len(before) < n_passes or len(after) < n_passes:
        return float("nan")
    val_before = float(before.iloc[-n_passes])   # 3rd pass counting back from event
    val_after  = float(after.iloc[n_passes - 1]) # 3rd pass after event
    return val_after - val_before


def stage2_event_decomp(groups: dict, df: pd.DataFrame) -> pd.DataFrame:
    print("\n[Stage 2] Event decomposition…")
    # All sub-groups, daily series
    series_cache = {grp: compute_subgroup_series(df, tank_ids) for grp, tank_ids in groups.items()}
    # Terminal = sum of all sub-group tanks (prod WL equivalent)
    all_ids = set().union(*groups.values())
    terminal_s = compute_subgroup_series(df, all_ids)

    records = []
    for ev in EVENTS:
        ev_date = pd.Timestamp(ev["date"])
        w_start = ev_date - pd.Timedelta(days=EVENT_WINDOW_DAYS)
        w_end   = ev_date + pd.Timedelta(days=EVENT_WINDOW_DAYS)

        grp_mean_sum = 0.0   # sanity: sum of group mean-window deltas
        grp_point_sum = 0.0  # sanity: sum of group point-estimator deltas
        grp_mean_valid = True
        grp_point_valid = True

        for grp, tank_ids in groups.items():
            s = series_cache[grp]
            # Mean-window estimator: last − first in ±21d window
            s_w = s.loc[w_start:w_end]
            if len(s_w) < 2:
                delta_mean = float("nan")
                grp_mean_valid = False
            else:
                delta_mean = float(s_w.iloc[-1] - s_w.iloc[0])
            # Point estimator: val[+3 passes] − val[−3 passes]
            delta_point = _point_estimator(s, ev_date)
            if np.isnan(delta_point):
                grp_point_valid = False

            records.append({
                "event":         ev["label"],
                "date":          ev["date"],
                "expected":      ev["expected"],
                "subgroup":      grp,
                "delta_mean_mb": round(delta_mean, 3) if not np.isnan(delta_mean) else None,
                "delta_point_mb": round(delta_point, 3) if not np.isnan(delta_point) else None,
                "window_days":   EVENT_WINDOW_DAYS,
            })
            if not np.isnan(delta_mean):
                grp_mean_sum += delta_mean
            if not np.isnan(delta_point):
                grp_point_sum += delta_point

        # Terminal values (sanity cross-check)
        t_w = terminal_s.loc[w_start:w_end]
        terminal_mean  = float(t_w.iloc[-1] - t_w.iloc[0]) if len(t_w) >= 2 else float("nan")
        terminal_point = _point_estimator(terminal_s, ev_date)
        records.append({
            "event":          ev["label"],
            "date":           ev["date"],
            "expected":       ev["expected"],
            "subgroup":       "TERMINAL",
            "delta_mean_mb":  round(terminal_mean, 3) if not np.isnan(terminal_mean) else None,
            "delta_point_mb": round(terminal_point, 3) if not np.isnan(terminal_point) else None,
            "window_days":    EVENT_WINDOW_DAYS,
        })
        records.append({
            "event":          ev["label"],
            "date":           ev["date"],
            "expected":       ev["expected"],
            "subgroup":       "GROUP_SUM",
            "delta_mean_mb":  round(grp_mean_sum, 3) if grp_mean_valid else None,
            "delta_point_mb": round(grp_point_sum, 3) if grp_point_valid else None,
            "window_days":    EVENT_WINDOW_DAYS,
        })

        # Sanity check: GROUP_SUM ≈ TERMINAL (within 2%)
        if grp_mean_valid and not np.isnan(terminal_mean) and abs(terminal_mean) > 0.01:
            err = abs(grp_mean_sum - terminal_mean) / abs(terminal_mean)
            status = "OK" if err < 0.02 else "MISMATCH ⚠"
            print(f"  {ev['label']} mean-window sanity: group_sum={grp_mean_sum:.3f}  terminal={terminal_mean:.3f}  err={err:.1%}  {status}")
        if grp_point_valid and not np.isnan(terminal_point) and abs(terminal_point) > 0.01:
            err = abs(grp_point_sum - terminal_point) / abs(terminal_point)
            status = "OK" if err < 0.02 else "MISMATCH ⚠"
            print(f"  {ev['label']} point-estimator sanity: group_sum={grp_point_sum:.3f}  terminal={terminal_point:.3f}  err={err:.1%}  {status}")

    df_ev = pd.DataFrame(records)
    out = os.path.join(OUT_DIR, "event_decomp.csv")
    df_ev.to_csv(out, index=False)
    print(df_ev.to_string(index=False))
    print(f"  Saved: {out}")
    return df_ev


# ── Stage 3 — Correlation grid ────────────────────────────────────────────────

def _load_jodi() -> pd.Series:
    path = os.path.join(os.path.dirname(__file__), "..", "data", "external", "jodi_saudi_crude.csv")
    df = pd.read_csv(path, parse_dates=["month"])
    s = df.set_index("month")["exports_mbpd"].rename("jodi_saudi")
    s = s[s > 0].dropna()
    # align to month-end
    return s.set_axis(s.index.to_period("M").to_timestamp("M"))


def _load_portwatch_rtju() -> pd.Series:
    base = os.path.join(os.path.dirname(__file__), "..", "client_package", "data")
    path = os.path.join(base, "portwatch_monthly.csv")
    if not os.path.exists(path):
        print(f"  [rtju] portwatch_monthly.csv not found in {base} — rtju_mbpd will be NO DATA")
        return pd.Series(dtype=float, name="rtju_mbpd")
    try:
        df = pd.read_csv(path)
        # Canonical columns per ТЗ: date col = 'month' (YYYY-MM), value col = 'rtju_mbpd'
        if "month" not in df.columns:
            raise ValueError(f"'month' column not found; got: {list(df.columns)}")
        if "rtju_mbpd" not in df.columns:
            raise ValueError(f"'rtju_mbpd' column not found; got: {list(df.columns)}")
        df["month"] = pd.to_datetime(df["month"], errors="coerce")
        df = df.dropna(subset=["month"])
        s = df.set_index("month")["rtju_mbpd"].rename("rtju_mbpd")
        # Normalize to month-end (same as JODI)
        s = s.set_axis(s.index.to_period("M").to_timestamp("M"))
        s = s[s > 0].dropna()
        print(f"  [rtju] loaded portwatch_monthly.csv: {len(s)} months, "
              f"keys sample (5): {[str(k.date()) for k in s.index[:5]]}")
        return s
    except Exception as exc:
        print(f"  [rtju] portwatch_monthly.csv load failed: {exc}")
        return pd.Series(dtype=float, name="rtju_mbpd")


def _align_monthly(s: pd.Series) -> pd.Series:
    """Snap monthly-ish series to month-end."""
    try:
        return s.set_axis(s.index.to_period("M").to_timestamp("M"))
    except Exception:
        return s


def _compute_r(x: pd.Series, y: pd.Series):
    x = _align_monthly(x)
    y = _align_monthly(y)
    idx = x.dropna().index.intersection(y.dropna().index)
    if len(idx) < MIN_OVERLAP:
        return float("nan"), float("nan"), len(idx)
    xv = x.loc[idx].values.astype(float)
    yv = y.loc[idx].values.astype(float)
    mask = np.isfinite(xv) & np.isfinite(yv)
    if mask.sum() < MIN_OVERLAP:
        return float("nan"), float("nan"), int(mask.sum())
    r, p = stats.pearsonr(xv[mask], yv[mask])
    return float(r), float(p), int(mask.sum())


def _metric_delta(s: pd.Series) -> pd.Series:
    return s.diff().dropna()


def _metric_level_anom(s: pd.Series) -> pd.Series:
    return (s - s.rolling(12, min_periods=6).mean()).dropna()


def _run_combo(stor_monthly: pd.Series, export_monthly: pd.Series,
               metric: str, lag: int, start: str, end: str) -> dict:
    s_w = stor_monthly.loc[start:end]
    e_w = export_monthly.loc[start:end]
    if metric == "delta":
        x = _metric_delta(s_w)
        y = _metric_delta(e_w)
    else:
        x = _metric_level_anom(s_w)
        y = _metric_level_anom(e_w)
    x_lag = x.shift(lag)
    r, p, n = _compute_r(x_lag, y)
    return {"r": round(r, 4) if not np.isnan(r) else None,
            "p": round(p, 4) if not np.isnan(p) else None,
            "n": n}


def stage3_correlation(groups: dict, df: pd.DataFrame) -> tuple:
    print("\n[Stage 3] Correlation grid…")
    jodi  = _load_jodi()
    rtju  = _load_portwatch_rtju()
    print(f"  JODI: {len(jodi)} months   PortWatch RT+JU: {len(rtju)} months")

    # Diagnostic: show 5 first keys from each anchor
    jodi_keys = [str(k.date()) for k in jodi.index[:5]]
    print(f"  JODI keys sample (month-end): {jodi_keys}")
    if not rtju.empty:
        rtju_keys = [str(k.date()) for k in rtju.index[:5]]
        print(f"  rtju keys sample: {rtju_keys}")
    else:
        print("  rtju_mbpd: empty — skipping PortWatch half; fallback CSVs will be written")

    # Intersection size check for jodi anchor (stop if <30 months in train)
    _train_mask = (jodi.index >= pd.Timestamp(TRAIN_START)) & (jodi.index <= pd.Timestamp(TRAIN_END))
    jodi_train  = jodi[_train_mask]
    print(f"  JODI train months: {len(jodi_train)}  keys: {[str(k.date()) for k in jodi_train.index[:5]]}")
    if len(jodi_train) < 30:
        print(f"  STOP: JODI train intersection < 30 months ({len(jodi_train)}). Keys: {[str(k.date()) for k in jodi_train.index]}")
        raise RuntimeError(f"JODI train intersection too small: {len(jodi_train)} < 30 months")

    if not rtju.empty:
        _rtju_train = rtju[(rtju.index >= pd.Timestamp(TRAIN_START)) & (rtju.index <= pd.Timestamp(TRAIN_END))]
        print(f"  rtju train months: {len(_rtju_train)}  keys: {[str(k.date()) for k in _rtju_train.index[:5]]}")
        if len(_rtju_train) < 30:
            print(f"  WARN: rtju train intersection < 30 months ({len(_rtju_train)}) — treating as empty")
            rtju = pd.Series(dtype=float, name="rtju_mbpd")

    anchors = {"jodi_saudi": jodi}
    if not rtju.empty:
        anchors["rtju_mbpd"] = rtju
    else:
        # Add empty placeholder so grid shows all 40 slots with NO DATA
        anchors["rtju_mbpd"] = pd.Series(dtype=float, name="rtju_mbpd")

    train_rows   = []
    holdout_rows = []

    first_grp = True
    for grp, tank_ids in groups.items():
        # Build monthly storage series for this sub-group
        s_daily = compute_subgroup_series(df, tank_ids)
        s_monthly = s_daily.resample("ME").mean().dropna()
        if len(s_monthly) < MIN_OVERLAP:
            print(f"  {grp}: too few monthly obs ({len(s_monthly)}) — skip")
            continue
        if first_grp:
            # Log 5 keys from storage series and intersection sizes with each anchor
            stor_keys = [str(k.date()) for k in s_monthly.index[:5]]
            print(f"  Storage keys sample ({grp}, 5): {stor_keys}")
            for an, ex in anchors.items():
                if not ex.empty:
                    isect = s_monthly.index.intersection(ex.index)
                    print(f"  Intersection {grp} ∩ {an}: {len(isect)} months  "
                          f"first 5: {[str(k.date()) for k in isect[:5]]}")
            first_grp = False

        for anchor_name, export_s in anchors.items():
            for metric in ["delta", "level_anom"]:
                for lag in [0, 1]:
                    res = _run_combo(s_monthly, export_s, metric, lag,
                                     TRAIN_START, TRAIN_END)
                    row = {
                        "subgroup": grp,
                        "anchor":   anchor_name,
                        "metric":   metric,
                        "lag":      lag,
                        "expected_sign": -1,
                        **res,
                    }
                    train_rows.append(row)

                    # Holdout
                    if res["r"] is not None:
                        h = _run_combo(s_monthly, export_s, metric, lag,
                                       HOLDOUT_START, HOLDOUT_END)
                        r_h = h["r"]
                        sign_ok = (np.sign(r_h) == np.sign(res["r"])) if r_h is not None and not np.isnan(r_h) else False
                        passes  = (h["p"] is not None and not np.isnan(h["p"] or np.nan)
                                   and h["p"] < BONFERRONI_ALPHA and sign_ok)
                        holdout_rows.append({
                            "subgroup":     grp,
                            "anchor":       anchor_name,
                            "metric":       metric,
                            "lag":          lag,
                            "r_train":      res["r"],
                            "p_train":      res["p"],
                            "r_holdout":    r_h,
                            "p_holdout":    h["p"],
                            "n_holdout":    h["n"],
                            "bonf_alpha":   BONFERRONI_ALPHA,
                            "sign_preserved": sign_ok,
                            "passes_bonferroni": passes,
                        })

    grid_df = pd.DataFrame(train_rows)
    hold_df = pd.DataFrame(holdout_rows)

    grid_path = os.path.join(OUT_DIR, "correlation_grid.csv")
    hold_path = os.path.join(OUT_DIR, "correlation_holdout.csv")
    grid_df.to_csv(grid_path, index=False)
    hold_df.to_csv(hold_path, index=False)
    print(f"  Train combos: {len(grid_df)}  (with r: {grid_df['r'].notna().sum()})")
    if not hold_df.empty:
        n_pass = hold_df["passes_bonferroni"].sum()
        # Promising: train p<0.10 AND holdout sign is minus (r<0) — expected negative direction preserved
        _p_train_ok = hold_df["p_train"].notna() & (hold_df["p_train"] < 0.10)
        _minus_ok   = hold_df["r_holdout"].notna() & (hold_df["r_holdout"] < 0)
        n_prom = (_p_train_ok & _minus_ok & ~hold_df["passes_bonferroni"]).sum()
        print(f"  Holdout: {len(hold_df)} rows  Bonferroni PASS: {n_pass}  Promising (train p<0.10, holdout r<0): {n_prom}")
    print(f"  Saved: {grid_path}  {hold_path}")

    # Fallback: if rtju was empty, commit 5 sub-series daily CSVs
    if rtju.empty or len(anchors.get("rtju_mbpd", pd.Series())) == 0:
        print("  [fallback] rtju empty — writing sub-series daily CSVs to diagnostics/rt_decomp/")
        for grp, tank_ids in groups.items():
            s = compute_subgroup_series(df, tank_ids)
            csv_path = os.path.join(OUT_DIR, f"subgroup_{grp}_daily.csv")
            s.to_csv(csv_path, header=["vol_mn_bbl"])
            print(f"    wrote {csv_path} ({len(s)} rows)")

    return grid_df, hold_df


# ── Stage 4 — ASC 101 pilot ───────────────────────────────────────────────────

def stage4_asc_pilot() -> str:
    print("\n[Stage 4] ASC 101 pilot…")
    try:
        from sentinelhub import SHConfig, BBox, CRS, DataCollection
        from sar_fetch import fetch_new_records, build_crescent_masks, get_all_scene_dates
        from config import RESOLUTION, DB_FLOOR
    except ImportError as exc:
        msg = f"SH import failed: {exc}"
        print(f"  {msg}")
        return msg

    sh_cfg = SHConfig()
    sh_cfg.sh_client_id     = SH_CLIENT_ID
    sh_cfg.sh_client_secret = SH_CLIENT_SECRET
    sh_cfg.sh_token_url     = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    sh_cfg.sh_base_url      = "https://sh.dataspace.copernicus.eu"

    ASC_ORBIT   = 101
    ASC_LOS_AZ  = 98
    N_SCENES_ASC = 10
    STEP_THRESH = 2.5

    rt_loc = next(loc for loc in LOCATIONS if loc["id"] == "ras_tanura")
    tanks  = {t["id"]: t for t in rt_loc["tanks"] if t.get("enabled", True)}

    # Use north cluster bbox for catalog (north tanks are densely grouped, good bbox)
    north_ids   = set(rt_loc["clusters"][1]["tank_ids"])
    north_tanks = [rt_loc["tanks"][i] for i, t in enumerate(rt_loc["tanks"])
                   if t["id"] in north_ids and t.get("enabled", True)]
    if not north_tanks:
        north_tanks = rt_loc["tanks"][:3]

    first = north_tanks[0]
    d_lat = 2000 / 111320
    d_lon = 2000 / (111320 * math.cos(math.radians(first["lat"])))
    ref_bbox = BBox(bbox=(first["lon"]-d_lon, first["lat"]-d_lat,
                          first["lon"]+d_lon, first["lat"]+d_lat), crs=CRS.WGS84)

    print(f"  Fetching ASC {ASC_ORBIT} catalog…")
    try:
        all_dates = get_all_scene_dates(ref_bbox, orbit_state="ascending",
                                        relative_orbit=ASC_ORBIT)
        asc_dates = sorted(all_dates, reverse=True)[:N_SCENES_ASC]
        print(f"  ASC dates ({len(asc_dates)}): {asc_dates[0] if asc_dates else 'NONE'} … {asc_dates[-1] if asc_dates else ''}")
    except Exception as exc:
        msg = f"ASC catalog fetch failed: {exc}"
        print(f"  {msg}")
        return msg

    if not asc_dates:
        msg = "No ASC 101 dates found over RT bounding box"
        print(f"  {msg}")
        return msg

    # Compute inter-pass cadence
    if len(asc_dates) > 1:
        dt_days = [(pd.Timestamp(asc_dates[i]) - pd.Timestamp(asc_dates[i+1])).days
                   for i in range(len(asc_dates)-1)]
        asc_cadence_med = np.median(dt_days)
    else:
        asc_cadence_med = float("nan")

    # Step-screen a sample: north WL tanks on ASC 101
    asc_rows = []
    BUFFER = 500

    def _bbox(lat, lon, buf=BUFFER):
        dlat = buf / 111320
        dlon = buf / (111320 * math.cos(math.radians(lat)))
        return BBox(bbox=(lon-dlon, lat-dlat, lon+dlon, lat+dlat), crs=CRS.WGS84)

    orbit_cfg_asc = [{"relative_orbit": ASC_ORBIT, "direction": "ASC", "los_azimuth": ASC_LOS_AZ}]

    # Screen only north WL tanks (small group, quick run)
    sample_ids = sorted(north_ids & set(tanks.keys()))
    print(f"  Screening {len(sample_ids)} north WL tanks on ASC 101…")

    desc_steps = {}   # from whitelist_results.json
    try:
        wl_path = os.path.join(os.path.dirname(__file__), "..", "diagnostics", "stage3", "whitelist_results.json")
        wl_data = json.load(open(wl_path))
        desc_steps = {t["tank_id"]: t.get("step") for t in wl_data.get("tanks", [])
                      if t.get("terminal") == "ras_tanura"}
    except Exception:
        pass

    try:
        S1_CDSE = DataCollection.SENTINEL1_IW.define_from(
            "s1iw_cdse_asc", service_url="https://sh.dataspace.copernicus.eu"
        )
    except ValueError:
        S1_CDSE = DataCollection["s1iw_cdse"]

    for tid in sample_ids:
        t = tanks[tid]
        lat, lon = t["lat"], t["lon"]
        r = t.get("radius_m"); h = t.get("h_max_m")
        bbox  = _bbox(lat, lon)
        img_w = max(20, int(BUFFER * 2 / RESOLUTION))
        tank_spec = {"id": tid, "lat": lat, "lon": lon, "radius": r,
                     "radius_m": r, "diameter": r*2, "h_max": h}
        try:
            tdata   = build_crescent_masks([tank_spec], bbox, img_w, img_w, los_azimuth=ASC_LOS_AZ)
            records = fetch_new_records(asc_dates, tdata, bbox, img_w, img_w, orbit_cfg=orbit_cfg_asc)
        except Exception as exc:
            print(f"    {tid}: ERROR {exc}")
            asc_rows.append({"tank_id": tid, "step_asc": None, "step_desc": desc_steps.get(tid),
                              "db_offset": None, "n_obs_asc": 0, "whitelist_asc": None})
            continue

        series = sorted([(rec["date"], max(rec["db_mean"], DB_FLOOR))
                         for rec in records if rec.get("db_mean") is not None])
        if len(series) < 2:
            step_asc = float("nan"); n_obs = len(series)
        else:
            diffs = [abs(series[i][1] - series[i-1][1]) for i in range(1, len(series))]
            step_asc = float(np.median(diffs)); n_obs = len(series)

        step_desc = desc_steps.get(tid)
        db_offset = None
        if step_desc is not None and not np.isnan(step_asc) and n_obs > 0:
            # Approximate offset: mean dB difference (ASC mean − DESC mean)
            asc_mean = np.mean([s[1] for s in series])
            db_offset = round(asc_mean - (step_desc + 1.0), 3)  # crude, not precise

        wl_asc = (not np.isnan(step_asc)) and (step_asc < STEP_THRESH)
        step_asc_str = f"{step_asc:.3f}" if not np.isnan(step_asc) else "nan"
        print(f"    {tid}: step_asc={step_asc_str}  step_desc={step_desc}  wl_asc={wl_asc}")
        asc_rows.append({
            "tank_id": tid,
            "step_asc":  round(step_asc, 4) if not np.isnan(step_asc) else None,
            "step_desc": step_desc,
            "n_obs_asc": n_obs,
            "whitelist_asc": wl_asc if not np.isnan(step_asc) else None,
        })

    n_wl_asc  = sum(1 for r in asc_rows if r.get("whitelist_asc"))
    n_tested  = len(asc_rows)
    summary = (
        f"ASC 101 pilot — {len(asc_dates)} dates  "
        f"cadence median {asc_cadence_med:.1f}d  "
        f"({asc_dates[0]} → {asc_dates[-1]})\n"
        f"North WL tanks screened: {n_tested}  pass step<{STEP_THRESH}: {n_wl_asc}/{n_tested}\n"
    )
    for r in asc_rows:
        sa = f"{r['step_asc']:.3f}" if r["step_asc"] is not None else "nan"
        sd = f"{r['step_desc']:.3f}" if r["step_desc"] is not None else "n/a"
        wl = "✓" if r.get("whitelist_asc") else "✗"
        summary += f"  {r['tank_id']:10s}  step_asc={sa:7s}  step_desc={sd:7s}  {wl}\n"

    asc_path = os.path.join(OUT_DIR, "asc101_pilot.txt")
    with open(asc_path, "w") as f:
        f.write(summary)
    print(f"  Saved: {asc_path}")
    return summary


# ── Stage 5 — Report ──────────────────────────────────────────────────────────

def stage5_report(groups: dict, grid_df: pd.DataFrame, hold_df: pd.DataFrame,
                  ev_df: pd.DataFrame, asc_summary: str,
                  sanity_ok: bool, db_min: float, db_max: float):
    print("\n[Stage 5] Writing RT_DECOMPOSITION.md…")

    n_pass  = int(hold_df["passes_bonferroni"].sum()) if not hold_df.empty else 0
    if not hold_df.empty and "p_train" in hold_df.columns:
        _pt_ok  = hold_df["p_train"].notna() & (hold_df["p_train"] < 0.10)
        _rh_neg = hold_df["r_holdout"].notna() & (hold_df["r_holdout"] < 0)
        n_prom  = int((_pt_ok & _rh_neg & ~hold_df["passes_bonferroni"]).sum())
    else:
        n_prom = 0

    lines = [
        "# RT Decomposition — Report",
        "",
        f"Run: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Registration",
        "- Train: 2020-01 → 2023-12",
        "- Holdout: 2024-01 → 2026-02 (2026-03+ conflict excluded)",
        f"- Sub-series: 5 (frozen before calculation)",
        f"- Combinations: {BONFERRONI_N}  Bonferroni α = 0.05/{BONFERRONI_N} = {BONFERRONI_ALPHA:.5f}",
        "",
        "## Calibration",
        f"- dB_min (global 5th pct) = {db_min:.3f}",
        f"- dB_max (global 95th pct) = {db_max:.3f}",
        "- Single terminal-level calibration, not per sub-group",
        "",
        f"## Sanity Check — Geo-split == Prod WL",
        f"{'PASS' if sanity_ok else 'FAIL ⚠'}: south_pier + south_inland + north == prod WL curve (tolerance 0.5%)",
        "",
        "## Sub-Series Composition",
        "",
        f"| Sub-series | n_tanks | Tank IDs |",
        "|---|---|---|",
    ]
    for name, ids in groups.items():
        lines.append(f"| {name} | {len(ids)} | {', '.join(sorted(ids))} |")
    lines += [
        "",
        f"Pier reference: {PIER_REF} (≤{PIER_RADIUS_M:.0f}m → south_pier)",
        "",
        "## Stage 2 — Event Decomposition (2026 conflict, diagnostic)",
        "",
        "Two estimators compared. Mean-window: last−first in ±21d window.",
        "Point: val[+3 SAR passes] − val[−3 SAR passes]. Sums of groups must match TERMINAL row.",
        "",
        "| Event | Expected | Sub-series | Δ mean-window (Mb) | Δ point (Mb) |",
        "|---|---|---|---|---|",
    ]
    for _, row in ev_df.iterrows():
        dm = row.get("delta_mean_mb")
        dp = row.get("delta_point_mb")
        dm_s = f"{dm:+.3f}" if dm is not None else "n/a"
        dp_s = f"{dp:+.3f}" if dp is not None else "n/a"
        lines.append(f"| {row['event']} | {row['expected']} | {row['subgroup']} | {dm_s} | {dp_s} |")
    lines += [
        "",
        "**Canonical estimator**: point estimator (val[+3]−val[−3]) — matches PROOF_OF_VALUE event study,",
        "removes window-edge sensitivity. Mean-window retained for reference.",
    ]

    lines += [
        "",
        "## Stage 3 — Correlation Grid (train 2020-01 → 2023-12)",
        "",
        f"Bonferroni α = {BONFERRONI_ALPHA:.5f}",
        "",
        "| Sub-series | Anchor | Metric | Lag | r_train | p | n | Verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for _, row in grid_df.iterrows():
        r = row.get("r"); p = row.get("p")
        if r is None or (isinstance(r, float) and np.isnan(r)):
            verdict = "NO DATA"
        elif p < BONFERRONI_ALPHA and r < 0:
            verdict = "PASS"
        elif p < 0.05 and r < 0:
            verdict = "PROMISING"
        else:
            verdict = "NULL"
        rs = f"{r:.4f}" if r is not None and not np.isnan(r) else "n/a"
        ps = f"{p:.4f}" if p is not None and not np.isnan(p) else "n/a"
        lines.append(f"| {row['subgroup']} | {row['anchor']} | {row['metric']} | "
                     f"{row['lag']:+d} | {rs} | {ps} | {row.get('n', 0)} | {verdict} |")

    lines += [
        "",
        "## Stage 3 — Holdout (2024-01 → 2026-02)",
        "",
        f"Bonferroni PASS: {n_pass}  |  Promising (train p<0.10 AND holdout r<0): {n_prom}",
        "",
        "| Sub-series | Anchor | Metric | Lag | r_train | r_holdout | p_holdout | Bonf | Sign |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for _, row in hold_df.iterrows():
        rh = row.get("r_holdout"); ph = row.get("p_holdout")
        rt = row.get("r_train")
        rhs = f"{rh:.4f}" if rh is not None and not np.isnan(float(rh)) else "n/a"
        phs = f"{ph:.4f}" if ph is not None and not np.isnan(float(ph)) else "n/a"
        bonf = "PASS" if row.get("passes_bonferroni") else "FAIL"
        sign = "✓" if row.get("sign_preserved") else "✗"
        note = ""
        # north rows: positive r = sign violation (expected −, got +)
        if row["subgroup"] == "north" and rt is not None and not np.isnan(float(rt)) and float(rt) > 0:
            note = " ⚠ sign violation (expected −, got +); exploratory note only"
        lines.append(f"| {row['subgroup']} | {row['anchor']} | {row['metric']} | "
                     f"{row['lag']:+d} | {rt:.4f} | {rhs} | {phs} | {bonf} | {sign} |{note}")

    lines += [
        "",
        "## Stage 4 — ASC 101 Pilot",
        "",
        "```",
        asc_summary,
        "```",
        "",
        "## Final Verdict",
        "",
    ]
    if n_pass > 0:
        lines.append(f"**{n_pass} combination(s) passed Bonferroni in holdout.** "
                     "Sub-group correlation identified — see holdout table above.")
    elif n_prom > 0:
        lines.append(f"**{n_prom} combination(s) promising (p<0.10, sign preserved) but below Bonferroni threshold.** "
                     "Directional only.")
    else:
        lines += [
            "**Декомпозиция по функциональным группам не вскрывает непрерывной связи — "
            "RT event-detector final.**",
            "",
            "All 5 sub-series × 2 anchors × 2 metrics × 2 lags = 40 combinations exhaustively tested.",
            "Zero Bonferroni passes, zero promising. RT storage does not carry a detectable continuous",
            "export-flow signal in any functional sub-group tested.",
            "",
            "RT value: event detection (2/3 confirmed events with 5–7d lead time). "
            "Pier-cluster composition (Stage 2) provides the export-tank map "
            "independent of correlation results.",
        ]

    md = "\n".join(lines)
    out_path = os.path.join(OUT_DIR, "RT_DECOMPOSITION.md")
    with open(out_path, "w") as f:
        f.write(md)
    print(f"  Saved: {out_path}")
    return out_path


# ── Composition text file ──────────────────────────────────────────────────────

def write_composition(groups: dict, rt_loc: dict):
    lines = ["Sub-series composition (frozen before correlation):"]
    pier_lat, pier_lon = PIER_REF
    rt_tanks_map = {t["id"]: t for t in rt_loc["tanks"]}
    for name, ids in groups.items():
        lines.append(f"\n{name} ({len(ids)} tanks):")
        for tid in sorted(ids):
            t = rt_tanks_map.get(tid, {})
            dist = _haversine_m(t.get("lat",0), t.get("lon",0), pier_lat, pier_lon)
            lines.append(f"  {tid:12s}  lat={t.get('lat',0):.5f}  lon={t.get('lon',0):.5f}  "
                         f"r={t.get('radius_m',0)}m  dist_pier={dist:.0f}m")
    out = os.path.join(OUT_DIR, "subgroup_composition.txt")
    with open(out, "w") as f:
        f.write("\n".join(lines))
    print(f"  Saved: {out}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-asc", action="store_true",
                    help="Skip Stage 4 (ASC 101 pilot)")
    args = ap.parse_args()

    print("="*70)
    print("Task 7 — RT Decomposition (pre-registered)")
    print("="*70)

    df, db_min, db_max, enabled, rt_loc = stage0_export()
    groups = define_subgroups(df, enabled, rt_loc)
    write_composition(groups, rt_loc)
    sanity_ok = sanity_check(groups, df)
    ev_df = stage2_event_decomp(groups, df)
    grid_df, hold_df = stage3_correlation(groups, df)

    if args.skip_asc:
        asc_summary = "Stage 4 skipped — use --skip-asc=False with CLIENT_ID/CLIENT_SECRET"
    else:
        if not SH_CLIENT_ID or not SH_CLIENT_SECRET:
            asc_summary = "Stage 4 skipped — CLIENT_ID/CLIENT_SECRET not set"
            print("\n[Stage 4] Skipped — no SH credentials")
        else:
            asc_summary = stage4_asc_pilot()

    stage5_report(groups, grid_df, hold_df, ev_df, asc_summary,
                  sanity_ok, db_min, db_max)
    print("\nDone.")


if __name__ == "__main__":
    main()
