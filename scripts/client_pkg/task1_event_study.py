"""
Task 1 — Event study: RT storage response to 2026 events.

Method:
  - For each event: compute Δstorage in window ±3 SAR passes around event date.
  - Δ = vol at +3 passes minus vol at -3 passes (directional, Mb).
  - Placebo: same metric on 20 random dates in 2021-01-01 → 2025-12-31 (peaceful period).
  - Negative control: same events on Kharg — Saudi events ≠ Iran signal.
  - Lead-time: find pass closest to event date; if SAR pass < event_date → lead.

Outputs:
  client_package/data/event_study_results.csv
  client_package/data/placebo_distribution.csv
  client_package/figures/fig1_rt_event_study.png
"""

import os, sys, random
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

CACHE_DIR  = "client_package/data"
FIG_DIR    = "client_package/figures"
PASS_WINDOW = 3    # passes either side of event
N_PLACEBO   = 20
PLACEBO_START = "2021-01-01"
PLACEBO_END   = "2025-12-31"
RANDOM_SEED   = 42

EVENTS_2026 = [
    {"date": "2026-03-02", "label": "E1: Attack/Halt",      "expected": "+BUILD"},
    {"date": "2026-06-26", "label": "E2: Ops resumed",       "expected": "-DRAW"},
    {"date": "2026-07-15", "label": "E3: Ramp ~90%",         "expected": "-DRAW"},
    {"date": "2026-09-20", "label": "E4: Pipeline diversion","expected": "-DRAW"},
    {"date": "2026-09-21", "label": "E5: Sohar STS",         "expected": "-DRAW"},
]

EXPECTED_SIGN = {
    "E1: Attack/Halt": +1,
    "E2: Ops resumed": -1,
    "E3: Ramp ~90%": -1,
    "E4: Pipeline diversion": -1,
    "E5: Sohar STS": -1,
}


def _load_series(path, name):
    if not os.path.exists(path):
        print(f"  [WARN] {name} series not found: {path}")
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
    s = df.set_index("date")["vol_mn_bbl"]
    print(f"  {name}: {len(s)} points {s.index.min().date()} → {s.index.max().date()}")
    return s


def _delta_around_event(series: pd.Series, event_date: pd.Timestamp, window: int):
    """
    Return (delta_mb, lead_days, passes_before, passes_after).
    delta = vol[+window passes] - vol[-window passes].
    Negative = drawdown.
    """
    dates = series.index
    idx_after  = dates[dates >= event_date]
    idx_before = dates[dates < event_date]

    if len(idx_before) < window or len(idx_after) < window:
        return float("nan"), None, 0, 0

    pre_date  = idx_before[-window]
    post_date = idx_after[window - 1]

    pre_vol  = series.loc[pre_date]
    post_vol = series.loc[post_date]
    delta    = post_vol - pre_vol

    # Lead-time: nearest SAR pass before event vs event date
    nearest_pre = idx_before[-1]
    lead_days = (event_date - nearest_pre).days  # positive = SAR was N days before news

    return float(delta), int(lead_days), window, window


def _placebo_distribution(series: pd.Series, window: int, n: int, seed: int):
    """Sample n random dates in peaceful period; compute |delta| for each."""
    random.seed(seed)
    eligible = series.loc[PLACEBO_START:PLACEBO_END].index
    if len(eligible) < 2 * window + 5:
        return pd.Series(dtype=float)
    chosen = random.sample(list(eligible), min(n, len(eligible)))
    deltas = []
    for d in chosen:
        delta, _, _, _ = _delta_around_event(series, d, window)
        if not np.isnan(delta):
            deltas.append(abs(delta))
    return pd.Series(deltas)


def run():
    os.makedirs(FIG_DIR, exist_ok=True)
    print("\n[Task 1] Event study")

    rt_s     = _load_series(os.path.join(CACHE_DIR, "storage_rt_daily.csv"),    "RT storage")
    kharg_s  = _load_series(os.path.join(CACHE_DIR, "storage_kharg_daily.csv"), "Kharg storage")

    if rt_s.empty:
        print("  [SKIP] RT storage unavailable")
        return {}

    # ── Placebo distribution ─────────────────────────────────────────────────
    placebo = _placebo_distribution(rt_s, PASS_WINDOW, N_PLACEBO, RANDOM_SEED)
    placebo_p90 = float(placebo.quantile(0.90)) if not placebo.empty else float("nan")
    print(f"  Placebo |Δ| p90 = {placebo_p90:.3f} Mb  (n={len(placebo)})")
    placebo_path = os.path.join(CACHE_DIR, "placebo_distribution.csv")
    placebo.to_frame(name="abs_delta_mb").to_csv(placebo_path, index=False)

    # ── Event results ─────────────────────────────────────────────────────────
    rows = []
    for ev in EVENTS_2026:
        event_date = pd.Timestamp(ev["date"])
        delta_rt, lead, _, _ = _delta_around_event(rt_s, event_date, PASS_WINDOW)
        delta_kharg, _, _, _ = _delta_around_event(kharg_s, event_date, PASS_WINDOW) if not kharg_s.empty else (float("nan"), None, 0, 0)

        expected_sign = EXPECTED_SIGN.get(ev["label"], 0)
        obs_sign = +1 if not np.isnan(delta_rt) and delta_rt > 0 else -1 if not np.isnan(delta_rt) else 0
        verdict = "✓" if obs_sign == expected_sign else "✗" if obs_sign != 0 else "–"

        beats_placebo = abs(delta_rt) > placebo_p90 if not np.isnan(delta_rt) else False

        lead_label = (f"+{lead}d lead" if lead is not None and lead > 0
                      else "same-day" if lead == 0
                      else f"{abs(lead) if lead else '?'}d lag")

        rows.append({
            "event": ev["label"],
            "date": ev["date"],
            "expected": ev["expected"],
            "delta_rt_mb": round(delta_rt, 3) if not np.isnan(delta_rt) else "n/a",
            "delta_kharg_mb": round(delta_kharg, 3) if not np.isnan(delta_kharg) else "n/a",
            "placebo_p90": round(placebo_p90, 3),
            "beats_placebo": beats_placebo,
            "lead_time": lead_label,
            "verdict": verdict,
        })
        print(f"  {ev['label']}: Δ_RT={delta_rt:.3f} Mb  Δ_Kharg={delta_kharg:.3f} Mb  {verdict}  {lead_label}")

    results_df = pd.DataFrame(rows)
    results_path = os.path.join(CACHE_DIR, "event_study_results.csv")
    results_df.to_csv(results_path, index=False)

    # ── Figure 1: RT curve with events ────────────────────────────────────────
    fig, axes = plt.subplots(2, 1, figsize=(14, 9), gridspec_kw={"height_ratios": [3, 1]})

    ax = axes[0]
    ax.plot(rt_s.index, rt_s.values, color="#2b6cb0", lw=1.5, label="RT whitelist storage")
    ax.fill_between(rt_s.index, rt_s.values, alpha=0.10, color="#2b6cb0")

    colors = {"✓": "#22863a", "✗": "#cb2431", "–": "#888888"}
    for ev, row in zip(EVENTS_2026, rows):
        d = pd.Timestamp(ev["date"])
        v = row["verdict"]
        ax.axvline(d, color=colors.get(v, "#888"), lw=1.0, ls="--", alpha=0.8)
        # Label at top
        ymax = rt_s.max() * 1.02
        ax.text(d, ymax, ev["label"].split(":")[0], fontsize=7, color=colors.get(v, "#888"),
                ha="left", va="top", rotation=90, clip_on=True)

    ax.set_ylabel("Volume (Mb)", fontsize=10)
    ax.set_title("Ras Tanura — SAR Storage vs 2026 Events", fontsize=12, fontweight="bold")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=8)
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)

    # Event table strip
    ax2 = axes[1]
    ax2.axis("off")
    col_labels = ["Event", "Expected", "Δ RT (Mb)", "> Placebo p90", "Lead / Lag", "✓/✗"]
    table_data = [
        [r["event"].split(":")[1].strip(), r["expected"], str(r["delta_rt_mb"]),
         "Yes" if r["beats_placebo"] else "No", r["lead_time"], r["verdict"]]
        for r in rows
    ]
    tbl = ax2.table(cellText=table_data, colLabels=col_labels,
                    loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    tbl.scale(1, 1.4)

    plt.tight_layout()
    fig_path = os.path.join(FIG_DIR, "fig1_rt_event_study.png")
    plt.savefig(fig_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {fig_path}")

    return {"results": rows, "placebo_p90": placebo_p90}
