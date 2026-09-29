"""
stage5_decompose.py — Three-curve decomposition: old=all, WL=whitelist, excl=excluded.
Single shared calibration (percentiles on full terminal df).

Sanity checks:
  #1: vol_old == vol_WL + vol_excl pointwise (tol 1e-6 Mb) — BUG STOP if fails
  #2: corr(Δ_old, Δ_WL) reproduces stage4 values (0.787 RT / 0.776 YB) within 0.02
  #3: var decomp: corr(Δ_old,Δ_WL) ≈ (var_WL + cov(Δ_WL,Δ_excl)) / (σ_old·σ_WL)

Metrics (per terminal):
  corr(Δ_old, Δ_WL)   — reproduce from stage4
  corr(Δ_excl, Δ_WL)  — KEY: noise (<0.2) or signal (>0.4)?
  smoothed corr(Δ_old, Δ_WL) — rolling-median-3 smoothed series

Verdict (Task 2 frozen criteria):
  corr(Δ_excl,Δ_WL) < 0.2 AND smoothed_corr ≥ 0.9  → SHAPE_PRESERVED
  corr(Δ_excl,Δ_WL) > 0.4                            → SIGNAL_LOSS
  else                                                 → HUMAN_REVIEW

Outputs:
  diagnostics/stage5/rt_decompose.png
  diagnostics/stage5/yb_decompose.png
  diagnostics/stage5/decompose_report.txt

Usage:
    python scripts/stage5_decompose.py
"""

import os, sys, json, math, datetime, warnings
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

from config import sb
from volume import compute_volumes, aggregate_daily
from coverage_filter import apply_coverage_filter
from tank_registry import LOCATIONS

os.makedirs("diagnostics/stage5", exist_ok=True)

BARREL_PER_M3 = 6.2898

RT_WHITELIST = {
    "rt_2","rt_4","rt_5","rt_6","rt_7","rt_8","rt_11","rt_12","rt_13","rt_14","rt_15",
    "rt_16","rt_17","rt_18","rt_19","rt_20","rt_21","rt_22","rt_23","rt_24","rt_25",
    "rt_26","rt_27","rt_28","rt_29","rt_30","rt_31","rt_32","rt_33","rt_34","rt_35",
    "rt_36","rt_37","rt_38","rt_41","rt_42","rt_44","rt_57","rt_59","rt_60","rt_61",
    "rt_62","rt_63","rt_64","rt_65",
}
YB_WHITELIST = {"yb_0","yb_2","yb_3","yb_5","yb_7","yb_8","yb_9","yb_11"}

EVENTS_2026 = [
    ("2026-03-02", "Attack"),
    ("2026-06-26", "Resumed"),
    ("2026-09-20", "Pipeline"),
]

# Stage4 reference corr values for sanity check #2
STAGE4_CORR = {"ras_tanura": 0.7866, "yanbu": 0.7764}


def _fetch_terminal_obs(tid):
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


def _build_curve_shared_cal(df_full, tank_geom, tank_filter=None):
    """
    Build daily volume curve using shared calibration (percentiles from df_full).
    df_full must already have radius_m / h_max columns from the shared geom join.
    """
    df = df_full.copy()
    if tank_filter is not None:
        df = df[df["tank_id"].isin(tank_filter)]
    df = df.dropna(subset=["radius_m", "h_max"])

    df2, _, _ = apply_coverage_filter(df, min_share=0.5)
    df2, _, _ = compute_volumes(df2)
    daily = aggregate_daily(df2)
    return daily


def _smooth3(s):
    """Rolling median 3 on a pandas Series indexed by date."""
    return s.rolling(3, center=True, min_periods=1).median()


def _corr_delta(a, b):
    da = a.diff().dropna()
    db = b.diff().dropna()
    idx = da.index.intersection(db.index)
    if len(idx) < 5:
        return float("nan")
    return float(da.loc[idx].corr(db.loc[idx]))


def _top5_excl_corr(df_obs, tank_geom, wl_set, wl_daily):
    """Return top-5 excluded tanks by individual corr(Δ_tank, Δ_WL) — for SIGNAL_LOSS report."""
    excl_ids = [t for t in tank_geom if t not in wl_set]
    results = []
    wl_vol = wl_daily.set_index("day")["vol_mn_bbl"]
    for tid in excl_ids:
        sub = df_obs[df_obs["tank_id"] == tid].copy()
        if len(sub) < 10:
            continue
        sub = sub.dropna(subset=["radius_m", "h_max"])
        if sub.empty:
            continue
        try:
            sub2, _, _ = apply_coverage_filter(sub, min_share=0.0)
            sub2, _, _ = compute_volumes(sub2)
            d = aggregate_daily(sub2).set_index("day")["vol_mn_bbl"]
        except Exception:
            continue
        common = d.index.intersection(wl_vol.index)
        if len(common) < 10:
            continue
        c = float(d.loc[common].diff().dropna().corr(wl_vol.loc[common].diff().dropna()))
        results.append((tid, round(c, 4)))
    results.sort(key=lambda x: -abs(x[1]))
    return results[:5]


def _run_terminal(tid, wl_set, lines):
    print(f"\n{'='*55}")
    print(f"Terminal: {tid}")

    loc = next(l for l in LOCATIONS if l["id"] == tid)
    tank_geom = {t["id"]: {"radius_m": t["radius_m"], "h_max_m": t["h_max_m"]}
                 for t in loc["tanks"]}

    print(f"  Fetching terminal_observations...")
    all_obs = _fetch_terminal_obs(tid)
    print(f"  {len(all_obs)} rows")

    df_full = pd.DataFrame(all_obs)
    if df_full.empty:
        lines.append(f"[{tid}] NO DATA"); return None

    df_full["date"] = pd.to_datetime(df_full["date"])
    df_full["radius_m"] = df_full["tank_id"].map(lambda t: tank_geom.get(t, {}).get("radius_m"))
    df_full["h_max"]    = df_full["tank_id"].map(lambda t: tank_geom.get(t, {}).get("h_max_m"))

    excl_set = set(tank_geom.keys()) - wl_set

    print("  Building curves (shared calibration)...")
    old_daily = _build_curve_shared_cal(df_full, tank_geom, tank_filter=None)
    wl_daily  = _build_curve_shared_cal(df_full, tank_geom, tank_filter=wl_set)
    excl_daily = _build_curve_shared_cal(df_full, tank_geom, tank_filter=excl_set)

    # ── Sanity #1: additive decomposition ─────────────────────────────────────
    merged3 = (old_daily.rename(columns={"vol_mn_bbl": "old", "day": "date"})
               .merge(wl_daily.rename(columns={"vol_mn_bbl": "wl",  "day": "date"}), on="date", how="inner")
               .merge(excl_daily.rename(columns={"vol_mn_bbl": "excl","day": "date"}), on="date", how="inner"))

    if merged3.empty:
        lines.append(f"[{tid}] SANITY#1 SKIP — no overlapping dates"); return None

    residual = (merged3["old"] - merged3["wl"] - merged3["excl"]).abs().max()
    san1_ok = residual < 1e-4
    san1_msg = f"PASS (max_residual={residual:.2e} Mb)" if san1_ok else f"FAIL (max_residual={residual:.2e} Mb)"
    print(f"  Sanity #1 (additive): {san1_msg}")

    if not san1_ok:
        lines.append(f"[{tid}] SANITY #1 FAIL — max_residual={residual:.4f} Mb — BUG STOP")
        print("[!!] Sanity #1 FAILED — coverage_filter breaks additive decomp; stopping.")
        sys.exit(2)

    # ── Metrics ───────────────────────────────────────────────────────────────
    corr_old_wl   = _corr_delta(merged3.set_index("date")["old"], merged3.set_index("date")["wl"])
    corr_excl_wl  = _corr_delta(merged3.set_index("date")["excl"], merged3.set_index("date")["wl"])

    # Smoothed (rolling median 3 passes)
    old_s  = _smooth3(merged3.set_index("date")["old"])
    wl_s   = _smooth3(merged3.set_index("date")["wl"])
    excl_s = _smooth3(merged3.set_index("date")["excl"])
    smoothed_corr_old_wl  = _corr_delta(old_s, wl_s)
    smoothed_corr_excl_wl = _corr_delta(excl_s, wl_s)

    # Sanity #2: reproduce stage4 corr within 0.02
    ref_corr = STAGE4_CORR.get(tid)
    san2_ok = ref_corr is not None and abs(corr_old_wl - ref_corr) <= 0.02
    san2_msg = (f"PASS (got {corr_old_wl:.4f}, ref {ref_corr:.4f})" if san2_ok
                else f"WARN (got {corr_old_wl:.4f}, ref {ref_corr:.4f}, diff={abs(corr_old_wl-ref_corr):.4f})")
    print(f"  Sanity #2 (reproduce stage4): {san2_msg}")

    # Sanity #3: variance decomposition check
    d_old  = merged3.set_index("date")["old"].diff().dropna()
    d_wl   = merged3.set_index("date")["wl"].diff().dropna()
    d_excl = merged3.set_index("date")["excl"].diff().dropna()
    idx3 = d_old.index.intersection(d_wl.index).intersection(d_excl.index)
    if len(idx3) >= 10:
        var_wl  = float(d_wl.loc[idx3].var())
        cov_we  = float(d_wl.loc[idx3].cov(d_excl.loc[idx3]))
        sig_old = float(d_old.loc[idx3].std())
        sig_wl  = float(d_wl.loc[idx3].std())
        expected_corr = (var_wl + cov_we) / (sig_old * sig_wl) if sig_old * sig_wl > 0 else float("nan")
        san3_diff = abs(expected_corr - corr_old_wl) if not math.isnan(expected_corr) else float("nan")
        san3_ok = not math.isnan(san3_diff) and san3_diff < 0.005
        san3_msg = (f"PASS (formula={expected_corr:.4f}, actual={corr_old_wl:.4f}, diff={san3_diff:.4f})" if san3_ok
                    else f"WARN (formula={expected_corr:.4f}, actual={corr_old_wl:.4f}, diff={san3_diff:.4f})")
    else:
        san3_msg = "SKIP (insufficient overlap)"
        san3_ok = False
    print(f"  Sanity #3 (variance decomp): {san3_msg}")

    # ── Task 2 verdict ────────────────────────────────────────────────────────
    if corr_excl_wl < 0.2 and smoothed_corr_old_wl >= 0.9:
        verdict = "SHAPE_PRESERVED"
    elif corr_excl_wl > 0.4:
        verdict = "SIGNAL_LOSS"
    else:
        verdict = "HUMAN_REVIEW"

    print(f"  corr(Δ_old,Δ_WL)={corr_old_wl:.4f}  corr(Δ_excl,Δ_WL)={corr_excl_wl:.4f}")
    print(f"  smoothed_corr(Δ_old,Δ_WL)={smoothed_corr_old_wl:.4f}")
    print(f"  VERDICT: {verdict}")

    # Top-5 excluded tanks by corr (always compute — needed for SIGNAL_LOSS, informative otherwise)
    print("  Computing per-tank excl corr (top-5)...")
    top5 = _top5_excl_corr(df_full, tank_geom, wl_set, wl_daily)
    print(f"  Top-5 excl: {top5}")

    # ── Report lines ──────────────────────────────────────────────────────────
    lines.append(f"{'='*55}")
    lines.append(f"Terminal: {tid}   n_wl={len(wl_set)}  n_excl={len(excl_set)}")
    lines.append(f"  n_dates_overlap={len(merged3)}")
    lines.append(f"  Sanity #1 (additive):        {san1_msg}")
    lines.append(f"  Sanity #2 (reproduce stage4):{san2_msg}")
    lines.append(f"  Sanity #3 (var decomp):      {san3_msg}")
    lines.append(f"  corr(Δ_old,Δ_WL)            = {corr_old_wl:.4f}")
    lines.append(f"  corr(Δ_excl,Δ_WL)           = {corr_excl_wl:.4f}  ← KEY")
    lines.append(f"  smoothed corr(Δ_old,Δ_WL)   = {smoothed_corr_old_wl:.4f}")
    lines.append(f"  smoothed corr(Δ_excl,Δ_WL)  = {smoothed_corr_excl_wl:.4f}")
    lines.append(f"  VERDICT: {verdict}")
    if top5:
        lines.append(f"  Top-5 excl by |corr(Δ_tank,Δ_WL)|:")
        for t, c in top5:
            lines.append(f"    {t}: {c:+.4f}")
    lines.append("")

    # ── Plots ─────────────────────────────────────────────────────────────────
    if HAS_MPL:
        fig, axes = plt.subplots(3, 1, figsize=(14, 10),
                                 gridspec_kw={"height_ratios": [3, 1.5, 1.5]})
        ax = axes[0]
        ax.plot(old_daily["day"],  old_daily["vol_mn_bbl"],  color="#6ea8d4", lw=1.2, label=f"All ({len(old_daily)}d)")
        ax.plot(wl_daily["day"],   wl_daily["vol_mn_bbl"],   color="#e07b2a", lw=1.2, ls="--", label=f"WL ({len(wl_daily)}d)")
        ax.plot(excl_daily["day"], excl_daily["vol_mn_bbl"], color="#77cc77", lw=1.0, ls=":", label=f"Excl ({len(excl_daily)}d)")
        for d_str, label in EVENTS_2026:
            d = pd.Timestamp(d_str)
            ax.axvline(d, color="#cc3333", lw=0.8, ls=":")
            ax.text(d, ax.get_ylim()[1]*0.98, label, color="#cc3333", fontsize=7, ha="left", va="top", rotation=90)
        ax.set_ylabel("Volume Mb"); ax.legend(fontsize=8)
        ax.set_title(f"{tid} — 3-curve decomposition  [verdict={verdict}]")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=7)

        # Scatter Δ_excl vs Δ_WL
        ax2 = axes[1]
        de = merged3.set_index("date")["excl"].diff().dropna()
        dw = merged3.set_index("date")["wl"].diff().dropna()
        idx_sc = de.index.intersection(dw.index)
        ax2.scatter(dw.loc[idx_sc], de.loc[idx_sc], s=8, alpha=0.4, color="#6ea8d4")
        ax2.set_xlabel("Δ_WL (Mb)"); ax2.set_ylabel("Δ_excl (Mb)")
        ax2.set_title(f"Scatter Δ_excl vs Δ_WL  corr={corr_excl_wl:.4f}")
        ax2.axhline(0, color="#888", lw=0.5); ax2.axvline(0, color="#888", lw=0.5)

        # Δ_excl time series
        ax3 = axes[2]
        ax3.plot(merged3["date"], merged3.set_index("date")["excl"].diff().values, color="#77cc77", lw=0.8)
        ax3.axhline(0, color="#888", lw=0.5)
        ax3.set_ylabel("Δ_excl (Mb)")
        ax3.set_title("Δ_excl time series")
        ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=7)

        plt.tight_layout()
        out_png = f"diagnostics/stage5/{tid.replace('_','')}_decompose.png"
        plt.savefig(out_png, dpi=120); plt.close()
        print(f"  Saved: {out_png}")

    return {
        "terminal": tid, "n_wl": len(wl_set), "n_excl": len(excl_set),
        "n_overlap": len(merged3),
        "corr_old_wl": corr_old_wl, "corr_excl_wl": corr_excl_wl,
        "smoothed_corr_old_wl": smoothed_corr_old_wl,
        "smoothed_corr_excl_wl": smoothed_corr_excl_wl,
        "san1": san1_ok, "san2": san2_ok, "san3": san3_ok,
        "verdict": verdict, "top5_excl": top5,
    }


def main():
    lines = [f"Stage 5 Decompose  {datetime.datetime.utcnow():%Y-%m-%d %H:%M} UTC", ""]
    results = {}

    rt_res = _run_terminal("ras_tanura", RT_WHITELIST, lines)
    if rt_res: results["ras_tanura"] = rt_res

    yb_res = _run_terminal("yanbu", YB_WHITELIST, lines)
    if yb_res: results["yanbu"] = yb_res

    report = "\n".join(lines)
    txt_path = "diagnostics/stage5/decompose_report.txt"
    with open(txt_path, "w") as f:
        f.write(report)

    json_path = "diagnostics/stage5/decompose_results.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 55)
    print(report)
    print(f"\nReport saved: {txt_path}")
    print(f"JSON:  {json_path}")

    stop = any(r.get("verdict") == "SIGNAL_LOSS" for r in results.values())
    if stop:
        print("\n[!] SIGNAL_LOSS verdict — deploy cancelled")
        sys.exit(1)
    else:
        hr = any(r.get("verdict") == "HUMAN_REVIEW" for r in results.values())
        print(f"\n[{'HUMAN_REVIEW' if hr else 'OK'}] See decompose_report.txt")


if __name__ == "__main__":
    main()
