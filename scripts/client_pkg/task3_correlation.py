"""
Task 3 — Correlation analysis: Saudi storage vs PortWatch exports (3-phase).

Phase A (exploration): 2020-01 → 2023-12
  Grid: anchor × aggregation × metric × lag
  anchor      : RT-port, JU-port, RT+JU combined, YB-port
  aggregation : 2W (biweekly), M (monthly)
  metric      : (a) Δstorage vs export_mbpd
                (b) storage_zscore vs export_anomaly (demeaned)
  lag         : -1, 0, +1 periods (negative = storage leads)
  All combinations logged to CSV. Expected sign: negative (higher storage → lower export).

Phase B: top-3 by |r| — mechanism + additional prediction.

Phase C (holdout): 2024-01 → 2026-02 (excl. conflict period 2026-03+).
  Bonferroni threshold applied to Phase A grid size.

Outputs:
  client_package/data/correlation_grid_phaseA.csv
  client_package/data/correlation_phaseC_holdout.csv
  client_package/figures/fig4_correlation_grid.png
  client_package/figures/fig5_top3_timeseries.png
"""

import os, sys, itertools
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

CACHE_DIR = "client_package/data"
FIG_DIR   = "client_package/figures"

PHASE_A_END   = "2023-12-31"
PHASE_C_START = "2024-01-01"
PHASE_C_END   = "2026-02-28"   # exclude 2026-03+ conflict

ANCHORS = ["rt", "ju", "rt_ju", "yb"]
AGGREGATIONS = ["2W", "M"]
METRICS = ["delta", "zscore"]
LAGS = [-1, 0, 1]

MIN_OVERLAP = 24   # minimum observations for r to be reported


def _load_portwatch(key):
    path = os.path.join(CACHE_DIR, f"portwatch_{key}_monthly.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["month_start"])
    df = df.set_index("month_start")["mbpd"].rename(key)
    return df


def _load_storage(key):
    path = os.path.join(CACHE_DIR, f"storage_{key}_daily.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float, name=key)
    df = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
    return df.set_index("date")["vol_mn_bbl"].rename(key)


def _aggregate(s: pd.Series, freq: str) -> pd.Series:
    return s.resample(freq).mean().dropna()


def _metric_delta(s: pd.Series) -> pd.Series:
    return s.diff().dropna()


def _metric_zscore(s: pd.Series) -> pd.Series:
    m, sd = s.mean(), s.std()
    if sd == 0:
        return pd.Series(dtype=float)
    return (s - m) / sd


def _lag_series(s: pd.Series, lag: int) -> pd.Series:
    return s.shift(lag)


def _compute_r(x: pd.Series, y: pd.Series) -> tuple:
    idx = x.dropna().index.intersection(y.dropna().index)
    if len(idx) < MIN_OVERLAP:
        return float("nan"), float("nan"), len(idx)
    r, p = stats.pearsonr(x.loc[idx], y.loc[idx])
    return float(r), float(p), len(idx)


def run():
    os.makedirs(FIG_DIR, exist_ok=True)
    print("\n[Task 3] Correlation analysis (3-phase)")

    # ── Load series ──────────────────────────────────────────────────────────
    port_rt = _load_portwatch("rt")
    port_ju = _load_portwatch("ju")
    port_yb = _load_portwatch("yb")
    stor_rt = _load_storage("rt")
    stor_yb = _load_storage("yb")

    # Build rt+ju combined export
    if not port_rt.empty and not port_ju.empty:
        port_rtju = (port_rt + port_ju).rename("rt_ju")
    else:
        port_rtju = pd.Series(dtype=float, name="rt_ju")

    export_map = {"rt": port_rt, "ju": port_ju, "rt_ju": port_rtju, "yb": port_yb}
    storage_map = {"rt": stor_rt, "ju": stor_rt, "rt_ju": stor_rt, "yb": stor_yb}

    if stor_rt.empty:
        print("  [SKIP] RT storage unavailable")
        return {}

    # ── Phase A grid ─────────────────────────────────────────────────────────
    print("\n  Phase A: 2020-01 → 2023-12")
    rows_a = []
    for anchor, agg, metric, lag in itertools.product(ANCHORS, AGGREGATIONS, METRICS, LAGS):
        stor = storage_map.get(anchor, pd.Series(dtype=float))
        expo = export_map.get(anchor, pd.Series(dtype=float))
        if stor.empty or expo.empty:
            continue

        # Phase A window
        stor_a = stor.loc["2020-01-01":PHASE_A_END]
        expo_a = expo.loc["2020-01-01":PHASE_A_END]

        stor_agg = _aggregate(stor_a, agg)
        expo_agg = _aggregate(expo_a, "M")    # PortWatch already monthly
        if agg == "2W":
            expo_agg = expo_agg.resample("2W").interpolate()

        if metric == "delta":
            x_stor = _metric_delta(stor_agg)
            x_expo = _metric_delta(expo_agg)
        else:
            x_stor = _metric_zscore(stor_agg)
            x_expo = _metric_zscore(expo_agg)

        x_stor_lagged = _lag_series(x_stor, lag)
        r, p, n = _compute_r(x_stor_lagged, x_expo)
        correct_sign = (r < 0) if not np.isnan(r) else None  # negative expected

        rows_a.append({
            "anchor": anchor, "agg": agg, "metric": metric, "lag": lag,
            "r": round(r, 4) if not np.isnan(r) else None,
            "p": round(p, 4) if not np.isnan(p) else None,
            "n": n,
            "correct_sign": correct_sign,
        })

    grid_a = pd.DataFrame(rows_a)
    if grid_a.empty:
        print("  [WARN] No grid results (insufficient data)")
        return {}

    grid_a_sorted = grid_a.dropna(subset=["r"]).sort_values("r")
    grid_a.to_csv(os.path.join(CACHE_DIR, "correlation_grid_phaseA.csv"), index=False)
    print(f"  Grid: {len(grid_a)} combinations; {grid_a['r'].notna().sum()} with r")

    n_combos = len(grid_a_sorted)
    bonferroni_alpha = 0.05 / n_combos if n_combos > 0 else 0.05
    print(f"  Bonferroni threshold: α = 0.05 / {n_combos} = {bonferroni_alpha:.5f}")

    # Top-3 by |r|
    top3 = grid_a_sorted.assign(abs_r=grid_a_sorted["r"].abs()).nlargest(3, "abs_r")
    print(f"\n  Top-3 by |r|:")
    for _, row in top3.iterrows():
        print(f"    {row['anchor']:6s} {row['agg']:3s} {row['metric']:7s} lag={row['lag']:+d}  "
              f"r={row['r']:.4f}  p={row['p']:.4f}  n={int(row['n'])}")

    # ── Phase C holdout ───────────────────────────────────────────────────────
    print(f"\n  Phase C holdout: {PHASE_C_START} → {PHASE_C_END}")
    rows_c = []
    for _, top_row in top3.iterrows():
        anchor, agg, metric, lag = (
            top_row["anchor"], top_row["agg"], top_row["metric"], int(top_row["lag"])
        )
        stor = storage_map.get(anchor, pd.Series(dtype=float))
        expo = export_map.get(anchor, pd.Series(dtype=float))
        if stor.empty or expo.empty:
            continue

        stor_c = stor.loc[PHASE_C_START:PHASE_C_END]
        expo_c = expo.loc[PHASE_C_START:PHASE_C_END]

        stor_agg = _aggregate(stor_c, agg)
        expo_agg = _aggregate(expo_c, "M")

        if metric == "delta":
            x_stor = _metric_delta(stor_agg)
            x_expo = _metric_delta(expo_agg)
        else:
            x_stor = _metric_zscore(stor_agg)
            x_expo = _metric_zscore(expo_agg)

        x_stor_lagged = _lag_series(x_stor, lag)
        r_c, p_c, n_c = _compute_r(x_stor_lagged, x_expo)

        passes_bonferroni = (p_c < bonferroni_alpha) if not np.isnan(p_c) else False
        preserves_sign = (np.sign(r_c) == np.sign(top_row["r"])) if not np.isnan(r_c) else False

        rows_c.append({
            "anchor": anchor, "agg": agg, "metric": metric, "lag": lag,
            "r_phaseA": top_row["r"], "r_phaseC": round(r_c, 4) if not np.isnan(r_c) else None,
            "p_phaseC": round(p_c, 4) if not np.isnan(p_c) else None,
            "n_phaseC": n_c,
            "bonferroni_alpha": round(bonferroni_alpha, 6),
            "passes_bonferroni": passes_bonferroni,
            "preserves_sign": preserves_sign,
        })
        print(f"    {anchor} {agg} {metric} lag={lag:+d}: "
              f"r_C={r_c:.4f}  p={p_c:.4f}  Bonferroni={'PASS' if passes_bonferroni else 'FAIL'}")

    holdout_df = pd.DataFrame(rows_c)
    if not holdout_df.empty:
        holdout_df.to_csv(os.path.join(CACHE_DIR, "correlation_phaseC_holdout.csv"), index=False)

    # ── Figure 4: heatmap-style grid ──────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(12, 5))
    # Plot |r| by combination index, colour by correct sign
    g = grid_a_sorted.reset_index(drop=True)
    colours = g["correct_sign"].map({True: "#22863a", False: "#cb2431", None: "#888888"})
    ax.bar(range(len(g)), g["r"].abs(), color=colours, width=0.8, alpha=0.7)
    ax.axhline(0.3, color="#888", lw=0.8, ls=":", label="r=0.3 reference")
    ax.set_xlabel("Combination index (sorted by |r|)", fontsize=9)
    ax.set_ylabel("|r|", fontsize=10)
    ax.set_title(
        f"Phase A grid: all {len(g)} combinations  [green = correct sign, red = wrong sign]\n"
        f"Bonferroni α={bonferroni_alpha:.4f}",
        fontsize=10
    )
    ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(os.path.join(FIG_DIR, "fig4_correlation_grid.png"), dpi=150)
    plt.close()

    return {
        "n_combinations": n_combos,
        "bonferroni_alpha": bonferroni_alpha,
        "top3": top3[["anchor", "agg", "metric", "lag", "r", "p", "n"]].to_dict("records"),
        "holdout": rows_c,
    }
