"""
Task 2 — Kharg benchmark: SAR storage vs JODI Iran crude exports.

Method:
  - Monthly aggregate SAR Kharg daily → simple mean per month.
  - Align to JODI Iran crude exports (kbd) on same calendar month.
  - Pearson r on aligned series; also rolling 12-month r for stability.
  - Scatter: Kharg_vol_Mb vs Iran_exports_kbd.
  - Timeline: dual-axis monthly.

Note on direction:
  Higher storage → lower near-term exports expected (accumulation).
  But storage LEVEL correlates with available supply → positive r is plausible
  when Iran ramps both production and exports (2016-2019, 2022+).
  We report the actual r without forcing a sign expectation.

Outputs:
  client_package/data/kharg_jodi_aligned.csv
  client_package/figures/fig2_kharg_jodi_scatter.png
  client_package/figures/fig3_kharg_jodi_timeline.png
"""

import os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from client_pkg.fetch_jodi import fetch_jodi

CACHE_DIR = "client_package/data"
FIG_DIR   = "client_package/figures"
CORR_WINDOW = 12   # months for rolling correlation


def run():
    os.makedirs(FIG_DIR, exist_ok=True)
    print("\n[Task 2] Kharg benchmark vs JODI")

    # ── Load storage ──────────────────────────────────────────────────────────
    kharg_path = os.path.join(CACHE_DIR, "storage_kharg_daily.csv")
    if not os.path.exists(kharg_path):
        print("  [SKIP] Kharg storage unavailable")
        return {}

    kharg_df = pd.read_csv(kharg_path, parse_dates=["date"])
    kharg_df["month"] = kharg_df["date"].dt.to_period("M")
    kharg_monthly = kharg_df.groupby("month")["vol_mn_bbl"].mean().reset_index()
    kharg_monthly["month_start"] = kharg_monthly["month"].dt.to_timestamp()

    # ── Load JODI ─────────────────────────────────────────────────────────────
    iran_path = os.path.join(CACHE_DIR, "jodi_iran_exports.csv")
    if os.path.exists(iran_path):
        iran = pd.read_csv(iran_path, parse_dates=["date"])
        print(f"  JODI Iran: using cache ({len(iran)} rows)")
    else:
        print("  Fetching JODI...")
        iran, _ = fetch_jodi()

    if iran.empty:
        print("  [WARN] JODI Iran unavailable — skipping Kharg benchmark figures")
        return {"r": None, "n": 0, "note": "JODI unavailable"}

    iran["month"] = pd.to_datetime(iran["date"]).dt.to_period("M")
    iran_monthly = iran.groupby("month")["kbd"].mean().reset_index()
    iran_monthly["month_start"] = iran_monthly["month"].dt.to_timestamp()

    # ── Align ─────────────────────────────────────────────────────────────────
    merged = kharg_monthly.merge(
        iran_monthly[["month", "kbd"]], on="month", how="inner"
    ).dropna(subset=["vol_mn_bbl", "kbd"])

    n = len(merged)
    print(f"  Aligned: {n} months  {merged['month_start'].min().date()} → {merged['month_start'].max().date()}")

    if n < 12:
        print("  [WARN] Too few aligned months for meaningful correlation")
        return {"r": None, "n": n, "note": "insufficient overlap"}

    r, pval = stats.pearsonr(merged["vol_mn_bbl"], merged["kbd"])
    print(f"  Pearson r = {r:.4f}  p = {pval:.4f}  n = {n}")

    # Rolling correlation
    merged_sorted = merged.sort_values("month_start").copy()
    merged_sorted["rolling_r"] = (
        merged_sorted["vol_mn_bbl"]
        .rolling(CORR_WINDOW, min_periods=int(CORR_WINDOW * 0.7))
        .corr(merged_sorted["kbd"])
    )

    merged_sorted.to_csv(os.path.join(CACHE_DIR, "kharg_jodi_aligned.csv"), index=False)

    # ── Figure 2: Scatter ──────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(merged["vol_mn_bbl"], merged["kbd"], s=18, alpha=0.6, color="#2b6cb0")
    # OLS line
    slope, intercept, *_ = stats.linregress(merged["vol_mn_bbl"], merged["kbd"])
    xr = np.linspace(merged["vol_mn_bbl"].min(), merged["vol_mn_bbl"].max(), 100)
    ax.plot(xr, intercept + slope * xr, color="#cb2431", lw=1.5, ls="--",
            label=f"OLS (r={r:.2f}, n={n})")
    ax.set_xlabel("Kharg SAR Storage (Mb)", fontsize=10)
    ax.set_ylabel("JODI Iran Crude Exports (kb/d)", fontsize=10)
    ax.set_title("Kharg Island SAR vs Iran Crude Exports (JODI)\nMethod: monthly mean storage aligned to JODI export month",
                 fontsize=10)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    plt.tight_layout()
    fig_path = os.path.join(FIG_DIR, "fig2_kharg_jodi_scatter.png")
    plt.savefig(fig_path, dpi=150)
    plt.close()
    print(f"  Saved: {fig_path}")

    # ── Figure 3: Timeline ────────────────────────────────────────────────────
    fig, ax1 = plt.subplots(figsize=(14, 5))
    ax2 = ax1.twinx()

    ax1.plot(merged_sorted["month_start"], merged_sorted["vol_mn_bbl"],
             color="#2b6cb0", lw=1.5, label="Kharg SAR (Mb)")
    ax2.plot(merged_sorted["month_start"], merged_sorted["kbd"],
             color="#e07b2a", lw=1.2, ls="--", label="JODI Iran exports (kb/d)")

    ax1.set_ylabel("Storage (Mb)", color="#2b6cb0")
    ax2.set_ylabel("Exports (kb/d)", color="#e07b2a")
    ax1.set_title(f"Kharg SAR Storage vs JODI Iran Crude Exports  [r={r:.2f}, n={n}mo]", fontsize=11)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=8)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, fontsize=9)
    ax1.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    fig_path2 = os.path.join(FIG_DIR, "fig3_kharg_jodi_timeline.png")
    plt.savefig(fig_path2, dpi=150)
    plt.close()
    print(f"  Saved: {fig_path2}")

    return {"r": round(r, 4), "pval": round(pval, 4), "n": n}
