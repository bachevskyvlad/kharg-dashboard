"""
stage4_rt_curve_compare.py — RT curve: all-66 vs whitelist-45, 2026 events overlay.

Reads terminal_observations from Supabase (no writes), applies compute pipeline
twice with different tank sets, outputs comparison PNG and metrics.

Also runs YB 8-tank whitelist curve for sanity check.

Metrics computed:
  (a) mean level shift (Mb) and its std over time — constant shift = shape preserved
  (b) Pearson correlation of day-over-day Δ series (expected >0.9)
  (c) 2026 events preserved: March build, July drawdown, September build
  (d) median |Δ| noise comparison: WL must be ≤ old (or not significantly worse)

Output:
  diagnostics/stage4/rt_curve_compare.png
  diagnostics/stage4/rt_curve_compare.txt   (metrics + verdict)
  diagnostics/stage4/yb_wl_curve.png

Usage:
    python scripts/stage4_rt_curve_compare.py
"""

import os
import sys
import json
import math
import datetime
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    print("[WARN] matplotlib not installed — PNG output disabled. pip install matplotlib")

from config import sb
from volume import compute_volumes, aggregate_daily
from coverage_filter import apply_coverage_filter
from tank_registry import LOCATIONS

os.makedirs("diagnostics/stage4", exist_ok=True)

BARREL_PER_M3 = 6.2898

# 2026 events for overlay (date, label)
EVENTS_2026 = [
    ("2026-03-02", "Attack"),
    ("2026-06-26", "Resumed"),
    ("2026-09-20", "Pipeline"),
]

# Whitelist tank ids from stage3
RT_WHITELIST = {
    "rt_2","rt_4","rt_5","rt_6","rt_7","rt_8","rt_11","rt_12","rt_13","rt_14","rt_15",
    "rt_16","rt_17","rt_18","rt_19","rt_20","rt_21","rt_22","rt_23","rt_24","rt_25",
    "rt_26","rt_27","rt_28","rt_29","rt_30","rt_31","rt_32","rt_33","rt_34","rt_35",
    "rt_36","rt_37","rt_38","rt_41","rt_42","rt_44","rt_57","rt_59","rt_60","rt_61",
    "rt_62","rt_63","rt_64","rt_65",
}

YB_WHITELIST = {"yb_0","yb_2","yb_3","yb_5","yb_7","yb_8","yb_9","yb_11"}


def _fetch_terminal_obs(tid):
    """Fetch all terminal_observations rows for a terminal."""
    all_obs = []; offset = 0
    while True:
        batch = (sb.table("terminal_observations")
                   .select("date,tank_id,db_mean")
                   .eq("terminal_id", tid)
                   .range(offset, offset + 999).execute().data)
        all_obs.extend(batch)
        if len(batch) < 1000: break
        offset += 1000
    return all_obs


def _build_curve(all_obs, tank_geom, tank_filter=None):
    """Compute daily volume curve (Mb) from raw observations."""
    df = pd.DataFrame(all_obs)
    if df.empty:
        return pd.DataFrame(columns=["day", "vol_mn_bbl"])
    df["date"] = pd.to_datetime(df["date"])

    if tank_filter is not None:
        df = df[df["tank_id"].isin(tank_filter)]

    df["radius_m"] = df["tank_id"].map(lambda t: tank_geom.get(t, {}).get("radius_m"))
    df["h_max"]    = df["tank_id"].map(lambda t: tank_geom.get(t, {}).get("h_max_m"))
    df = df.dropna(subset=["radius_m", "h_max"])

    df, n_dropped, max_tanks = apply_coverage_filter(df, min_share=0.5)

    df, _, _ = compute_volumes(df)
    daily = aggregate_daily(df)
    daily["vol_mn_bbl"] = daily["vol_mn_bbl"] / 1.0  # already in Mn bbl
    return daily


def _metrics(old_daily, wl_daily):
    """Return metrics dict comparing old vs whitelist curves."""
    merged = pd.merge(
        old_daily.rename(columns={"vol_mn_bbl": "old", "day": "date"}),
        wl_daily.rename(columns={"vol_mn_bbl": "wl",  "day": "date"}),
        on="date", how="inner"
    )
    if len(merged) < 10:
        return {"error": f"only {len(merged)} overlapping dates"}

    shift   = merged["old"] - merged["wl"]
    d_old   = merged["old"].diff().dropna()
    d_wl    = merged["wl"].diff().dropna()
    corr_idx = d_old.index.intersection(d_wl.index)

    corr = float(d_old.loc[corr_idx].corr(d_wl.loc[corr_idx]))
    noise_old = float(d_old.abs().median())
    noise_wl  = float(d_wl.abs().median())

    # Event checks: sign of Δ in event windows
    def _window_delta(df_col, df_date, start, end):
        mask = (df_date >= pd.Timestamp(start)) & (df_date <= pd.Timestamp(end))
        sub = df_col[mask]
        return float(sub.iloc[-1] - sub.iloc[0]) if len(sub) >= 2 else float("nan")

    ev = {}
    for d, label in EVENTS_2026:
        start = pd.Timestamp(d) - pd.Timedelta(days=7)
        end   = pd.Timestamp(d) + pd.Timedelta(days=21)
        ev[label] = {
            "old": _window_delta(merged["old"], merged["date"], start, end),
            "wl":  _window_delta(merged["wl"],  merged["date"], start, end),
        }

    return {
        "n_dates":        len(merged),
        "mean_shift_mb":  round(float(shift.mean()), 3),
        "std_shift_mb":   round(float(shift.std()),  3),
        "delta_corr":     round(corr, 4),
        "noise_old_mb":   round(noise_old, 4),
        "noise_wl_mb":    round(noise_wl,  4),
        "events":         ev,
    }


def _verdict(m):
    if "error" in m:
        return "STOP", m["error"]
    issues = []
    if m["delta_corr"] < 0.9:
        issues.append(f"Δ-corr={m['delta_corr']:.3f} < 0.9")
    if m["noise_wl_mb"] > m["noise_old_mb"] * 1.2:
        issues.append(f"WL noise {m['noise_wl_mb']:.4f} > old {m['noise_old_mb']:.4f} ×1.2")
    for label, ev in m.get("events", {}).items():
        old_sign = math.copysign(1, ev["old"]) if ev["old"] and not math.isnan(ev["old"]) else None
        wl_sign  = math.copysign(1, ev["wl"])  if ev["wl"]  and not math.isnan(ev["wl"])  else None
        if old_sign and wl_sign and old_sign != wl_sign:
            issues.append(f"event '{label}' sign flip old={ev['old']:+.2f} wl={ev['wl']:+.2f}")
    if issues:
        return "STOP", "; ".join(issues)
    return "SHAPE_PRESERVED", ""


def _plot_curves(old_daily, wl_daily, title, out_path):
    if not HAS_MPL:
        return
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]})

    ax = axes[0]
    ax.plot(old_daily["day"], old_daily["vol_mn_bbl"], color="#6ea8d4", lw=1.2,
            label=f"All tanks (n={len(old_daily)}d)")
    ax.plot(wl_daily["day"], wl_daily["vol_mn_bbl"],  color="#e07b2a", lw=1.2, ls="--",
            label=f"Whitelist (n={len(wl_daily)}d)")

    for d_str, label in EVENTS_2026:
        d = pd.Timestamp(d_str)
        ax.axvline(d, color="#cc3333", lw=0.8, ls=":")
        ax.text(d, ax.get_ylim()[1] * 0.98, label, color="#cc3333",
                fontsize=7, ha="left", va="top", rotation=90)

    ax.set_ylabel("Volume Mb")
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=7)

    # Bottom panel: shift
    merged = pd.merge(
        old_daily.rename(columns={"vol_mn_bbl": "old", "day": "date"}),
        wl_daily.rename(columns={"vol_mn_bbl": "wl",  "day": "date"}),
        on="date", how="inner"
    )
    if not merged.empty:
        shift = merged["old"] - merged["wl"]
        axes[1].fill_between(merged["date"], shift, alpha=0.4, color="#6ea8d4")
        axes[1].axhline(shift.mean(), color="#333", lw=0.8, ls="--", label=f"mean={shift.mean():.2f}Mb")
        axes[1].set_ylabel("Old − WL (Mb)")
        axes[1].legend(fontsize=7)
        axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        axes[1].xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(axes[1].xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=7)

    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()
    print(f"  Saved: {out_path}")


def main():
    lines = []
    lines.append(f"Stage 4 RT/YB Curve Comparison  {datetime.datetime.utcnow():%Y-%m-%d %H:%M} UTC")
    lines.append("")

    # ── RT ────────────────────────────────────────────────────────────────────
    print("Fetching RT terminal_observations...")
    rt_loc = next(loc for loc in LOCATIONS if loc["id"] == "ras_tanura")
    rt_geom = {t["id"]: {"radius_m": t["radius_m"], "h_max_m": t["h_max_m"]}
               for t in rt_loc["tanks"]}
    rt_obs = _fetch_terminal_obs("ras_tanura")
    print(f"  {len(rt_obs)} rows")

    print("Building RT all-66 curve...")
    rt_old = _build_curve(rt_obs, rt_geom, tank_filter=None)

    print("Building RT whitelist-45 curve...")
    rt_wl = _build_curve(rt_obs, rt_geom, tank_filter=RT_WHITELIST)

    rt_m = _metrics(rt_old, rt_wl)
    rt_verdict, rt_issue = _verdict(rt_m)
    print(f"  RT metrics: {json.dumps(rt_m, indent=2)}")
    print(f"  RT verdict: {rt_verdict}  {rt_issue}")

    _plot_curves(rt_old, rt_wl,
                 f"Ras Tanura — all 66 vs whitelist 45  [{rt_verdict}]",
                 "diagnostics/stage4/rt_curve_compare.png")

    # ── YB ────────────────────────────────────────────────────────────────────
    print("\nFetching YB terminal_observations...")
    yb_loc = next(loc for loc in LOCATIONS if loc["id"] == "yanbu")
    yb_geom = {t["id"]: {"radius_m": t["radius_m"], "h_max_m": t["h_max_m"]}
               for t in yb_loc["tanks"]}
    yb_obs = _fetch_terminal_obs("yanbu")
    print(f"  {len(yb_obs)} rows")

    print("Building YB all-12 curve...")
    yb_old = _build_curve(yb_obs, yb_geom, tank_filter=None)

    print("Building YB whitelist-8 curve...")
    yb_wl = _build_curve(yb_obs, yb_geom, tank_filter=YB_WHITELIST)

    yb_m = _metrics(yb_old, yb_wl)
    yb_verdict, yb_issue = _verdict(yb_m)
    print(f"  YB metrics: {json.dumps(yb_m, indent=2)}")
    print(f"  YB verdict: {yb_verdict}  {yb_issue}")

    _plot_curves(yb_old, yb_wl,
                 f"Yanbu — all 12 vs whitelist 8  [{yb_verdict}]",
                 "diagnostics/stage4/yb_curve_compare.png")

    # ── Text report ───────────────────────────────────────────────────────────
    for tid, m, verdict, issue in [("ras_tanura", rt_m, rt_verdict, rt_issue),
                                    ("yanbu",      yb_m, yb_verdict, yb_issue)]:
        lines.append(f"{'='*55}")
        lines.append(f"Terminal: {tid}   Verdict: {verdict}  {issue}")
        if "error" not in m:
            lines.append(f"  n_dates={m['n_dates']}  mean_shift={m['mean_shift_mb']:+.3f}Mb"
                         f"  std_shift={m['std_shift_mb']:.3f}Mb")
            lines.append(f"  Δ-corr={m['delta_corr']:.4f}  "
                         f"noise_old={m['noise_old_mb']:.4f}Mb  noise_wl={m['noise_wl_mb']:.4f}Mb")
            lines.append("  Events (28-day window Δ Mb):")
            for label, ev in m.get("events", {}).items():
                lines.append(f"    {label:12s}  old={ev['old']:+.2f}  wl={ev['wl']:+.2f}")
        lines.append("")

    report = "\n".join(lines)
    txt_path = "diagnostics/stage4/rt_curve_compare.txt"
    with open(txt_path, "w") as f:
        f.write(report)

    print("\n" + "=" * 55)
    print(report)
    print(f"\nReport saved: {txt_path}")

    # Exit non-zero if any STOP verdict
    if rt_verdict == "STOP" or yb_verdict == "STOP":
        print("\n[!] STOP verdict — review before deploying enabled flags")
        sys.exit(1)
    else:
        print("\n[OK] Both curves SHAPE_PRESERVED — safe to proceed to Task 3 deploy")


if __name__ == "__main__":
    main()
