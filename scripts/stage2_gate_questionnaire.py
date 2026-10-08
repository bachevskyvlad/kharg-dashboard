"""
stage2_gate_questionnaire.py — Terminal classification questionnaire for Stage 2 gate.

Classifies terminals into:
  ACCUMULATOR       — large strategic storage, builds and holds, slow drawdown
  PIPELINE-BUFFER   — fast throughput, low autocorrelation, continuous flow
  FLOW-THROUGH      — regular export cadence, intermediate cycle length

Metrics computed:
  1. CV of drawdown events (std/mean of drawdown magnitudes)
  2. Autocorrelation lag-1 of weekly volume changes
  3. Cycle length vs cadence (orbit revisit): how often exports happen relative to SAR cadence
  4. Configuration type (floating-roof count, capacity per tank)
  5. Event responsiveness (fraction of geopolitical/market events with detectable signal)
  6. Stuck ratio (fraction of tanks with SIR >12 dB for >90% of dates)

Gate: Kharg → ACCUMULATOR, Yanbu → PIPELINE-BUFFER, RT (ras_tanura) → FLOW-THROUGH
If any calibration terminal mis-classifies, thresholds are reported and must be fixed
BEFORE the questionnaire is applied to Jask or other NEEDS_BASELINE terminals.

Usage:
  cd scripts
  python stage2_gate_questionnaire.py

Kharg, Yanbu, RT are READ-ONLY reference terminals. No writes.
"""

import os, sys, json
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from config import sb
from tank_registry import LOCATIONS

OUT_DIR = Path(__file__).parent.parent / "diagnostics"
OUT_DIR.mkdir(exist_ok=True)

# ── Calibration terminals (READ-ONLY) ─────────────────────────────────────────
CALIBRATION = {
    "kharg":       {"expected": "ACCUMULATOR"},
    "yanbu":       {"expected": "PIPELINE-BUFFER"},
    "ras_tanura":  {"expected": "FLOW-THROUGH"},
}

# ── Classification thresholds ─────────────────────────────────────────────────
# These are the initial values. Gate check will report if they need adjustment.
THRESHOLDS = {
    # CV of drawdown: ACCUMULATOR has high variance (rare large draws), FLOW-THROUGH low
    "cv_drawdown_accumulator_min": 0.8,
    "cv_drawdown_flow_through_max": 0.55,
    # Autocorrelation lag-1: PIPELINE-BUFFER has near-zero (noisy), ACCUMULATOR high
    "autocorr_accumulator_min": 0.35,
    "autocorr_pipeline_max": 0.25,
    # Cycle days: ACCUMULATOR >30d, FLOW-THROUGH 12-22d, PIPELINE-BUFFER <12d
    "cycle_accumulator_min_days": 28,
    "cycle_flow_through_max_days": 25,
    "cycle_pipeline_max_days": 14,
    # Capacity utilisation variance: ACCUMULATOR high std, PIPELINE-BUFFER low
    "cap_util_std_accumulator_min": 0.06,
    "cap_util_std_pipeline_max": 0.03,
    # Stuck ratio: ACCUMULATOR tolerates more stuck tanks
    "stuck_ratio_accumulator_max": 0.15,
    "stuck_ratio_pipeline_max": 0.05,
}

MAX_CAP = {
    "kharg":      33.2,   # Mn bbl
    "yanbu":       7.0,   # approximate
    "ras_tanura": 10.5,   # approximate
}


def fetch_series(terminal_id: str) -> pd.DataFrame:
    """Fetch weekly volume series from volume_timeseries (or kharg_daily for kharg)."""
    print(f"  Fetching series for {terminal_id} …")

    # Try terminal_observations → compute volume proxy via db_mean distribution
    # For calibration terminals we use volume_timeseries if available
    rows = []
    offset = 0
    while True:
        r = (sb.table("volume_timeseries")
               .select("date,vol_mn_bbl,aggregation")
               .eq("terminal_id", terminal_id) if False else
             sb.table("volume_timeseries")
               .select("date,vol_mn_bbl,aggregation")
               .range(offset, offset + 999)
               .execute().data)
        break  # volume_timeseries has no terminal_id filter; use kharg_daily for kharg

    # kharg: use kharg_daily
    if terminal_id == "kharg":
        offset = 0
        while True:
            r = (sb.table("kharg_daily")
                   .select("date,vol_mn_bbl")
                   .range(offset, offset + 999)
                   .execute().data)
            rows.extend(r)
            if len(r) < 1000:
                break
            offset += 1000
    else:
        # Other calibration terminals: use terminal_observations to build proxy
        # db_mean → fill_ratio → vol using registry geometry
        loc = next((l for l in LOCATIONS if l["id"] == terminal_id), None)
        if loc is None:
            print(f"    [!] {terminal_id} not in registry")
            return pd.DataFrame()
        tanks = [t for t in loc["tanks"] if not t.get("uncertain")]
        n_tanks = len(tanks)
        total_cap = sum(np.pi * t["radius_m"]**2 * t["h_max_m"] * 6.2898 / 1e6 for t in tanks)

        offset = 0
        obs = []
        while True:
            r = (sb.table("terminal_observations")
                   .select("date,db_mean")
                   .eq("terminal_id", terminal_id)
                   .range(offset, offset + 999)
                   .execute().data)
            obs.extend(r)
            if len(r) < 1000:
                break
            offset += 1000

        if not obs:
            print(f"    [!] no data for {terminal_id}")
            return pd.DataFrame()

        df_obs = pd.DataFrame(obs)
        df_obs["date"] = pd.to_datetime(df_obs["date"])
        # global percentiles for fill_ratio
        p5  = df_obs["db_mean"].quantile(0.05)
        p95 = df_obs["db_mean"].quantile(0.95)
        span = p95 - p5 if p95 > p5 else 1.0

        # per-date mean fill_ratio → proxy volume
        grp = df_obs.groupby("date")["db_mean"].mean().reset_index()
        grp["fill_ratio"] = ((p95 - grp["db_mean"].clip(p5, p95)) / span).clip(0, 1)
        # approximate: all tanks same h_max (median)
        h_med = np.median([t["h_max_m"] for t in tanks])
        h_min = 2.0
        grp["vol_mn_bbl"] = (grp["fill_ratio"] * (h_med - h_min) + h_min) * \
                            sum(np.pi * t["radius_m"]**2 for t in tanks) * 6.2898 / 1e6
        grp = grp.rename(columns={"date": "date"})[["date", "vol_mn_bbl"]]
        rows = grp.to_dict("records")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    print(f"    {len(df)} daily rows  {df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}")
    return df


def compute_metrics(terminal_id: str, df: pd.DataFrame, max_cap_mn: float) -> dict:
    """Compute classification metrics from volume series."""
    if len(df) < 30:
        return {"error": "insufficient_data"}

    # Weekly resample
    df_w = df.set_index("date")["vol_mn_bbl"].resample("W").mean().dropna()
    vol = df_w.values

    # 1. Drawdown events: consecutive weekly drops ≥ 0.5 Mn bbl
    changes = np.diff(vol)
    drawdowns = changes[changes < -0.5]
    cv_drawdown = (np.std(drawdowns) / abs(np.mean(drawdowns))) if len(drawdowns) >= 3 else np.nan
    n_drawdown_events = len(drawdowns)

    # 2. Autocorrelation lag-1 of weekly changes
    autocorr = pd.Series(changes).autocorr(lag=1) if len(changes) > 2 else np.nan

    # 3. Cycle length: median interval between drawdown events
    drawdown_idx = np.where(changes < -0.5)[0]
    if len(drawdown_idx) >= 2:
        intervals = np.diff(drawdown_idx) * 7  # weeks → days
        cycle_days = float(np.median(intervals))
    else:
        cycle_days = np.nan

    # 4. Capacity utilisation std
    cap_util = vol / max_cap_mn if max_cap_mn > 0 else vol
    cap_util_std = float(np.std(cap_util))
    cap_util_mean = float(np.mean(cap_util))

    # 5. Stuck ratio (tanks where db_mean is chronically high — use raw_observations for kharg)
    # For proxy: fraction of time vol is in bottom 10% of range
    vol_range = np.ptp(vol)
    stuck_proxy = float(np.mean(vol < (np.min(vol) + 0.05 * vol_range))) if vol_range > 0 else 0.0

    return {
        "n_weeks": len(df_w),
        "vol_mean_mn": round(float(np.mean(vol)), 2),
        "vol_std_mn": round(float(np.std(vol)), 2),
        "cap_util_mean": round(cap_util_mean, 3),
        "cap_util_std": round(cap_util_std, 3),
        "n_drawdown_events": int(n_drawdown_events),
        "cv_drawdown": round(float(cv_drawdown), 3) if not np.isnan(cv_drawdown) else None,
        "autocorr_lag1": round(float(autocorr), 3) if not np.isnan(autocorr) else None,
        "cycle_days_median": round(cycle_days, 1) if not np.isnan(cycle_days) else None,
        "stuck_proxy": round(stuck_proxy, 3),
    }


def classify(metrics: dict) -> str:
    """Apply threshold rules to assign terminal type."""
    if "error" in metrics:
        return "UNKNOWN"

    cv  = metrics.get("cv_drawdown")
    ac  = metrics.get("autocorr_lag1")
    cyc = metrics.get("cycle_days_median")
    cap_std = metrics.get("cap_util_std")

    score = {"ACCUMULATOR": 0, "PIPELINE-BUFFER": 0, "FLOW-THROUGH": 0}

    # CV of drawdown
    if cv is not None:
        if cv >= THRESHOLDS["cv_drawdown_accumulator_min"]:
            score["ACCUMULATOR"] += 2
        elif cv <= THRESHOLDS["cv_drawdown_flow_through_max"]:
            score["FLOW-THROUGH"] += 2
        else:
            score["PIPELINE-BUFFER"] += 1

    # Autocorrelation
    if ac is not None:
        if ac >= THRESHOLDS["autocorr_accumulator_min"]:
            score["ACCUMULATOR"] += 2
        elif ac <= THRESHOLDS["autocorr_pipeline_max"]:
            score["PIPELINE-BUFFER"] += 2
        else:
            score["FLOW-THROUGH"] += 1

    # Cycle days
    if cyc is not None:
        if cyc >= THRESHOLDS["cycle_accumulator_min_days"]:
            score["ACCUMULATOR"] += 2
        elif cyc <= THRESHOLDS["cycle_pipeline_max_days"]:
            score["PIPELINE-BUFFER"] += 2
        else:
            score["FLOW-THROUGH"] += 2

    # Cap util std
    if cap_std is not None:
        if cap_std >= THRESHOLDS["cap_util_std_accumulator_min"]:
            score["ACCUMULATOR"] += 1
        elif cap_std <= THRESHOLDS["cap_util_std_pipeline_max"]:
            score["PIPELINE-BUFFER"] += 1

    best = max(score, key=score.get)
    return best


# ── Main ──────────────────────────────────────────────────────────────────────
print("=" * 60)
print("STAGE-2 GATE QUESTIONNAIRE — Calibration run")
print("=" * 60)

results = {}
all_pass = True

for tid, info in CALIBRATION.items():
    expected = info["expected"]
    cap = MAX_CAP.get(tid, 10.0)
    print(f"\n── {tid.upper()} (expected: {expected}) ──")

    df = fetch_series(tid)
    if df.empty:
        print(f"  [!] No data — SKIP")
        results[tid] = {"expected": expected, "actual": "NO_DATA", "pass": False}
        all_pass = False
        continue

    metrics = compute_metrics(tid, df, cap)
    actual  = classify(metrics)
    passed  = (actual == expected)
    all_pass = all_pass and passed

    results[tid] = {
        "expected": expected,
        "actual":   actual,
        "pass":     passed,
        "metrics":  metrics,
    }

    gate_sym = "✓" if passed else "✗ FAIL"
    print(f"  Actual: {actual}  [{gate_sym}]")
    for k, v in metrics.items():
        if k == "error":
            continue
        print(f"    {k:<25} = {v}")

# ── Gate summary ──────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("GATE RESULT")
print("=" * 60)
print(f"  {'Terminal':<15} {'Expected':<18} {'Actual':<18} {'Pass'}")
print(f"  {'-'*60}")
for tid, r in results.items():
    sym = "PASS" if r["pass"] else "FAIL"
    print(f"  {tid:<15} {r['expected']:<18} {r['actual']:<18} {sym}")

if all_pass:
    print("\n  GATE PASSED — thresholds validated on all 3 calibration terminals.")
    print("  Safe to apply questionnaire to Jask and other NEEDS_BASELINE terminals.")
else:
    print("\n  GATE FAILED — one or more calibration terminals mis-classified.")
    print("  Thresholds must be adjusted BEFORE applying to new terminals.")
    print("\n  Current thresholds:")
    for k, v in THRESHOLDS.items():
        print(f"    {k:<45} = {v}")

# ── Write outputs ─────────────────────────────────────────────────────────────
output = {
    "run": datetime.utcnow().isoformat(),
    "gate_passed": all_pass,
    "thresholds": THRESHOLDS,
    "terminals": results,
}
# make json-serialisable
def _serial(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    return str(o)

(OUT_DIR / "stage2_gate_result.json").write_text(json.dumps(output, indent=2, default=_serial))
print(f"\nWritten: diagnostics/stage2_gate_result.json")

lines = [
    "# Stage 2 Gate Questionnaire — Calibration Run",
    f"\nRun: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC  ",
    f"Gate passed: {all_pass}  ",
    "",
    "## Results",
    "",
    "| Terminal | Expected | Actual | Pass |",
    "|---|---|---|---|",
]
for tid, r in results.items():
    sym = "✓" if r["pass"] else "✗"
    lines.append(f"| {tid} | {r['expected']} | {r['actual']} | {sym} |")

lines += ["", "## Metrics", "", "| Terminal | Metric | Value |", "|---|---|---|"]
for tid, r in results.items():
    for k, v in r.get("metrics", {}).items():
        lines.append(f"| {tid} | {k} | {v} |")

lines += ["", "## Thresholds", "", "| Threshold | Value |", "|---|---|"]
for k, v in THRESHOLDS.items():
    lines.append(f"| {k} | {v} |")

if not all_pass:
    lines += ["", "## Action required",
              "",
              "Gate failed. Adjust thresholds in `THRESHOLDS` dict before proceeding to new terminals."]

(OUT_DIR / "STAGE2_GATE.md").write_text("\n".join(lines))
print(f"Written: diagnostics/STAGE2_GATE.md")
print("\nDone.")
