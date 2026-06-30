"""
Calibrate the Kharg SAR volume model (dB_min/dB_max percentile clip, TANK_HEIGHT_MIN)
against UANI monthly Iran crude export estimates.

Standalone / experimental - does NOT touch the production pipeline or data/ files.
Run only via calibration.yml (workflow_dispatch), or locally with Supabase network access.

Why not just rescale the existing volume output:
Pearson correlation is invariant to linear transforms (a*x+b), so linearly
rescaling the already-computed volume series cannot change its correlation
with exports. To actually move the correlation, we have to change the
nonlinear parameters of the physical model (dB clip bounds, min tank height)
that are applied per-tank, before summation.

Method:
1. Pull raw_observations (date, tank_id, db_mean, radius_m, h_max) from Supabase.
2. Recompute monthly-aggregated volume for a grid of (dB_pctl_lo, dB_pctl_hi, h_min).
3. Time-based train/test split (first 70% of overlapping months = train,
   last 30% = test) to avoid overfitting to noise in only ~40 months of data.
4. Pick the params maximizing |r| with UANI exports on train; report r on
   train vs test, alongside the current production baseline.
5. Also build a weekly view: monthly exports are step-interpolated to weekly
   (each week gets its containing month's export value) since UANI has no
   finer resolution; report weekly correlation as a secondary diagnostic.
"""
import json
import itertools
from pathlib import Path

import pandas as pd
import numpy as np
from scipy.stats import pearsonr
from supabase import create_client

SUPABASE_URL = "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

BARREL_PER_M3 = 6.2898
ROOT = Path(__file__).parent
UANI_PATH = ROOT / "iran_exports_uani.json"
RESULTS_PATH = ROOT / "calibration_results.json"

# baseline (current production) params, from scripts/main_script.py
BASE_PCTL_LO, BASE_PCTL_HI, BASE_HMIN = 0.05, 0.95, 2.0

# Tanks with mean dB >14 across full history — structurally idle, bias the global percentile clip
IDLE_TANKS = {"t55_5", "t55_10"}

GRID_PCTL = [(0.01, 0.99), (0.02, 0.98), (0.05, 0.95), (0.10, 0.90)]
GRID_HMIN = [0.0, 1.0, 2.0, 3.0, 4.0]


def fetch_table_daily_volume(table, date_filter=None):
    """Generic fetch for tables that already store date + vol_mn_bbl (kharg_daily, volume_timeseries)."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    all_rows = []
    page, size = 0, 1000
    while True:
        q = sb.table(table).select("date,vol_mn_bbl").order("date").range(page * size, page * size + size - 1)
        if date_filter:
            for col, val in date_filter.items():
                q = q.eq(col, val)
        rows = q.execute().data
        all_rows.extend(rows)
        if len(rows) < size:
            break
        page += 1
    if not all_rows:
        return None
    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["date"])
    df["vol_mn_bbl"] = pd.to_numeric(df["vol_mn_bbl"], errors="coerce")
    return df.dropna().sort_values("date")


def fetch_raw_observations():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    all_rows = []
    page, size = 0, 1000
    while True:
        r = (
            sb.table("raw_observations")
            .select("date,tank_id,db_mean,radius_m,h_max")
            .order("date")
            .range(page * size, page * size + size - 1)
            .execute()
        )
        rows = r.data
        all_rows.extend(rows)
        if len(rows) < size:
            break
        page += 1
    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["date"])
    for c in ["db_mean", "radius_m", "h_max"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna()


def compute_volume(df, pctl_lo, pctl_hi, h_min, exclude_idle=False):
    # Compute percentile clip bounds — optionally from active tanks only
    if exclude_idle and "tank_id" in df.columns:
        ref = df[~df["tank_id"].isin(IDLE_TANKS)]
    else:
        ref = df
    db_lo = ref["db_mean"].quantile(pctl_lo)
    db_hi = ref["db_mean"].quantile(pctl_hi)
    db_clipped = df["db_mean"].clip(db_lo, db_hi)
    fill_ratio = (db_hi - db_clipped) / (db_hi - db_lo)
    h_current = h_min + fill_ratio * (df["h_max"] - h_min)
    vol_bbl = np.pi * df["radius_m"] ** 2 * h_current * BARREL_PER_M3
    out = df[["date"]].copy()
    out["vol_bbl"] = vol_bbl
    daily = out.groupby("date")["vol_bbl"].sum().reset_index()
    daily["vol_mn_bbl"] = daily["vol_bbl"] / 1e6
    return daily[["date", "vol_mn_bbl"]]


def monthly_series(daily):
    m = daily.copy()
    m["month"] = m["date"].dt.to_period("M").dt.to_timestamp()
    return m.groupby("month")["vol_mn_bbl"].mean().reset_index()


def weekly_series(daily):
    w = daily.set_index("date")["vol_mn_bbl"].resample("W").mean().reset_index()
    return w


def load_uani():
    d = json.loads(UANI_PATH.read_text())
    s = pd.Series(d["monthly_exports_bpd"])
    s.index = pd.to_datetime(s.index)
    df = s.sort_index().reset_index()
    df.columns = ["month", "exports_bpd"]
    return df


def correlate(vol_monthly, exports_monthly):
    merged = pd.merge(vol_monthly, exports_monthly, on="month", how="inner").dropna()
    merged = merged.sort_values("month")
    if len(merged) < 4:
        return merged, None, None
    r, p = pearsonr(merged["vol_mn_bbl"], merged["exports_bpd"])
    return merged, r, p


def train_test_split_by_time(merged, train_frac=0.7):
    n = len(merged)
    cut = max(4, int(n * train_frac))
    return merged.iloc[:cut], merged.iloc[cut:]


def interpolate_exports_to_weekly(weekly_dates, exports_monthly):
    ex = exports_monthly.set_index("month")["exports_bpd"]
    out = []
    for d in weekly_dates:
        month_start = d.to_period("M").to_timestamp()
        val = ex.get(month_start, np.nan)
        out.append(val)
    return pd.Series(out, index=weekly_dates)


def extended_period_correlation(exports_monthly):
    """Try kharg_daily / volume_timeseries for a longer history than raw_observations,
    and correlate against UANI on the widest available overlap."""
    print("\nChecking for longer history in kharg_daily / volume_timeseries...")
    candidates = []
    try:
        kd = fetch_table_daily_volume("kharg_daily")
        if kd is not None and len(kd):
            candidates.append(("kharg_daily", kd))
    except Exception as e:
        print(f"kharg_daily not available: {e}")
    try:
        vt = fetch_table_daily_volume("volume_timeseries", date_filter={"aggregation": "daily"})
        if vt is not None and len(vt):
            candidates.append(("volume_timeseries (daily)", vt))
    except Exception as e:
        print(f"volume_timeseries not available: {e}")

    result = None
    for name, daily in candidates:
        print(f"{name}: {len(daily)} rows, {daily['date'].min().date()} - {daily['date'].max().date()}")
        monthly = monthly_series(daily)
        merged, r, p = correlate(monthly, exports_monthly)
        if r is None:
            continue
        print(f"  -> correlation vs UANI: r={r:.3f}, p={p:.3f}, n={len(merged)} months "
              f"({merged['month'].min().date()} - {merged['month'].max().date()})")
        if result is None or len(merged) > result["n"]:
            result = {
                "source": name, "r": r, "p": p, "n": len(merged),
                "start": str(merged["month"].min().date()), "end": str(merged["month"].max().date()),
            }
    return result


def main():
    exports_monthly = load_uani()

    extended = extended_period_correlation(exports_monthly)

    print("\nFetching raw_observations from Supabase...")
    raw = fetch_raw_observations()
    print(f"Rows: {len(raw)}, dates: {raw['date'].min().date()} - {raw['date'].max().date()}")

    # --- baseline (production params) ---
    base_daily = compute_volume(raw, BASE_PCTL_LO, BASE_PCTL_HI, BASE_HMIN)
    base_monthly = monthly_series(base_daily)
    base_merged, base_r, base_p = correlate(base_monthly, exports_monthly)
    print(f"\nBaseline (prod params {BASE_PCTL_LO}/{BASE_PCTL_HI}, h_min={BASE_HMIN}): "
          f"r={base_r:.4f}, p={base_p:.4f}, n={len(base_merged)}")

    # --- baseline with idle tanks excluded from percentile clip ---
    base_filt_daily = compute_volume(raw, BASE_PCTL_LO, BASE_PCTL_HI, BASE_HMIN, exclude_idle=True)
    base_filt_monthly = monthly_series(base_filt_daily)
    base_filt_merged, base_filt_r, base_filt_p = correlate(base_filt_monthly, exports_monthly)
    print(f"Baseline + exclude idle ({sorted(IDLE_TANKS)}): "
          f"r={base_filt_r:.4f}, p={base_filt_p:.4f}, n={len(base_filt_merged)}")

    # --- grid search on train split only (with and without idle exclusion) ---
    best = None
    best_filtered = None
    for (lo, hi), hmin in itertools.product(GRID_PCTL, GRID_HMIN):
        for excl in [False, True]:
            daily = compute_volume(raw, lo, hi, hmin, exclude_idle=excl)
            monthly = monthly_series(daily)
            merged, r, p = correlate(monthly, exports_monthly)
            if r is None:
                continue
            train, test = train_test_split_by_time(merged)
            if len(train) < 4 or len(test) < 3:
                continue
            r_train, p_train = pearsonr(train["vol_mn_bbl"], train["exports_bpd"])
            r_test, p_test = pearsonr(test["vol_mn_bbl"], test["exports_bpd"])
            candidate = {
                "pctl_lo": lo, "pctl_hi": hi, "h_min": hmin,
                "exclude_idle": excl,
                "r_train": r_train, "p_train": p_train,
                "r_test": r_test, "p_test": p_test,
                "n_train": len(train), "n_test": len(test),
            }
            target = best_filtered if excl else best
            if target is None or abs(r_train) > abs(target["r_train"]):
                if excl:
                    best_filtered = candidate
                else:
                    best = candidate

    print("\nBest params on TRAIN split (standard):")
    print(json.dumps(best, indent=2))
    print("\nBest params on TRAIN split (idle tanks excluded from clip):")
    print(json.dumps(best_filtered, indent=2))

    # --- weekly diagnostic using best params ---
    weekly_result = None
    if best is not None:
        best_daily = compute_volume(raw, best["pctl_lo"], best["pctl_hi"], best["h_min"])
        weekly = weekly_series(best_daily)
        weekly_exports = interpolate_exports_to_weekly(weekly["date"], exports_monthly)
        wmerged = weekly.copy()
        wmerged["exports_bpd"] = weekly_exports.values
        wmerged = wmerged.dropna()
        if len(wmerged) >= 4:
            r_w, p_w = pearsonr(wmerged["vol_mn_bbl"], wmerged["exports_bpd"])
            weekly_result = {"r": r_w, "p": p_w, "n": len(wmerged)}
            print(f"\nWeekly (step-interpolated exports), calibrated params: r={r_w:.3f}, p={p_w:.3f}, n={len(wmerged)}")

    results = {
        "extended_period": extended,
        "baseline": {
            "pctl_lo": BASE_PCTL_LO, "pctl_hi": BASE_PCTL_HI, "h_min": BASE_HMIN,
            "exclude_idle": False,
            "r": base_r, "p": base_p, "n": len(base_merged),
        },
        "baseline_filtered": {
            "pctl_lo": BASE_PCTL_LO, "pctl_hi": BASE_PCTL_HI, "h_min": BASE_HMIN,
            "exclude_idle": True,
            "r": base_filt_r, "p": base_filt_p, "n": len(base_filt_merged),
        },
        "best_calibrated": best,
        "best_calibrated_filtered": best_filtered,
        "weekly_diagnostic": weekly_result,
    }
    RESULTS_PATH.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
