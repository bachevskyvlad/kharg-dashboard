"""
series_health.py — Stage 2: run health gate on a terminal's SAR series.

Reads corridors from diagnostics/SERIES_HEALTH_STANDARD.md (must exist; run
series_health_standard.py first), then fetches the terminal's observations,
computes the same 11 metrics, and compares against corridors.

Verdict:
  PASS  — all metrics within corridors
  FLAG  — one or more metrics outside corridor, with diagnosis DEAD or WILD

Diagnoses:
  DEAD  — series is flat/inactive (low Δ, low live_fraction, low dynamic_range)
  WILD  — series has extreme noise/breaks (high Δp99, high vel_std)

Usage:
  python series_health.py --terminal yanbu        # runs against Supabase
  python series_health.py --terminal kharg        # reads raw_observations
  python series_health.py --terminal jask --out report.json

Self-validation test cases (run before any new terminal):
  python series_health.py --terminal ras_tanura   # expect PASS
  # ASC archive test requires --asc-only flag (future)

Output: JSON verdict + optional --out path.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from config import sb

STANDARD_PATH = Path(__file__).parent.parent / "diagnostics" / "SERIES_HEALTH_STANDARD.md"


# ─── load corridors from markdown ─────────────────────────────────────────────

METRIC_KEYS = [
    "cv_level", "vel_std", "delta_p50", "delta_p90", "delta_p99",
    "dynamic_range", "cycles_per_year", "cycle_depth_db",
    "max_flat_passes", "per_tank_step", "live_fraction",
]

METRIC_LABELS = {
    "cv_level":         "CV of level (σ/μ mean_db)",
    "vel_std":          "Velocity std (dB/pass)",
    "delta_p50":        "|Δ| p50 (dB)",
    "delta_p90":        "|Δ| p90 (dB)",
    "delta_p99":        "|Δ| p99 (dB)",
    "dynamic_range":    "Dynamic range (p95−p5, dB)",
    "cycles_per_year":  "Build-draw cycles/year",
    "cycle_depth_db":   "Cycle depth (dB)",
    "max_flat_passes":  "Max flat segment (passes)",
    "per_tank_step":    "Per-tank median step (dB)",
    "live_fraction":    "Live fraction (step≥0.5)",
}

DEAD_METRICS = {"delta_p90", "live_fraction", "dynamic_range"}
WILD_METRICS = {"delta_p99", "vel_std"}


def load_corridors() -> dict:
    """Parse corridors table from SERIES_HEALTH_STANDARD.md."""
    if not STANDARD_PATH.exists():
        print(f"ERROR: {STANDARD_PATH} not found. Run series_health_standard.py first.")
        sys.exit(1)
    text = STANDARD_PATH.read_text()

    # Find table section
    table_section = re.search(
        r"\| Metric \|.*?\n((?:\|.*?\n)+)", text
    )
    if not table_section:
        print("ERROR: Could not parse corridors table from SERIES_HEALTH_STANDARD.md")
        sys.exit(1)

    corridors = {}
    for line in table_section.group(1).strip().split("\n"):
        parts = [p.strip() for p in line.split("|")[1:-1]]
        if len(parts) < 5:
            continue
        label = parts[0]
        lo_s, hi_s = parts[3], parts[4]
        # match to key by label
        matched_key = None
        for k, l in METRIC_LABELS.items():
            if l == label:
                matched_key = k
                break
        if matched_key and lo_s != "n/a" and hi_s != "n/a":
            try:
                corridors[matched_key] = (float(lo_s), float(hi_s))
            except ValueError:
                pass
    return corridors


# ─── data fetch (same as series_health_standard.py) ──────────────────────────

def fetch_all(table, select, filters=None):
    rows = []
    offset = 0
    while True:
        q = sb.table(table).select(select)
        if filters:
            for k, v in filters.items():
                q = q.eq(k, v)
        batch = q.range(offset, offset + 999).execute().data
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def load_data(terminal_id: str) -> pd.DataFrame:
    if terminal_id == "kharg":
        rows = fetch_all("raw_observations", "date,tank_id,db_mean")
    else:
        rows = fetch_all("terminal_observations", "date,tank_id,db_mean",
                         filters={"terminal_id": terminal_id})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["db_mean"])
    return df


# ─── metrics (identical logic to series_health_standard.py) ──────────────────

def compute_metrics(df: pd.DataFrame) -> dict:
    daily = (
        df.groupby("date")["db_mean"]
        .mean()
        .sort_index()
        .rename("mean_db")
    )
    cv_level      = float(daily.std() / daily.mean()) if daily.mean() != 0 else np.nan
    deltas        = daily.diff().dropna().abs()
    vel_std       = float(deltas.std())
    delta_p50     = float(deltas.quantile(0.50))
    delta_p90     = float(deltas.quantile(0.90))
    delta_p99     = float(deltas.quantile(0.99))
    dynamic_range = float(daily.quantile(0.95) - daily.quantile(0.05))

    ma = daily.rolling("30D").mean().dropna()
    demeaned = ma - ma.mean()
    signs = np.sign(demeaned.values)
    crossings = int(np.sum(np.diff(signs) != 0))
    span_years = (daily.index[-1] - daily.index[0]).days / 365.25
    cycles_per_year = float(crossings / 2 / span_years) if span_years > 0 else np.nan

    if len(demeaned) > 2:
        peaks, current_sign, current_max = [], signs[0], abs(demeaned.values[0])
        for i in range(1, len(signs)):
            if signs[i] == current_sign or signs[i] == 0:
                current_max = max(current_max, abs(demeaned.values[i]))
            else:
                peaks.append(current_max)
                current_sign = signs[i]
                current_max = abs(demeaned.values[i])
        cycle_depth_db = float(np.median(peaks)) if peaks else np.nan
    else:
        cycle_depth_db = np.nan

    raw_deltas = daily.diff().dropna().abs()
    flat_mask  = (raw_deltas < 0.1).values
    max_flat, run = 0, 0
    for v in flat_mask:
        if v:
            run += 1
            max_flat = max(max_flat, run)
        else:
            run = 0

    per_tank_steps = []
    for _, tdf in df.groupby("tank_id"):
        s = tdf.sort_values("date")["db_mean"]
        steps = s.diff().dropna().abs()
        if len(steps) >= 3:
            per_tank_steps.append(float(steps.median()))

    per_tank_step = float(np.median(per_tank_steps)) if per_tank_steps else np.nan
    live_fraction = float(np.mean([s >= 0.5 for s in per_tank_steps])) if per_tank_steps else np.nan

    return {
        "cv_level": cv_level, "vel_std": vel_std,
        "delta_p50": delta_p50, "delta_p90": delta_p90, "delta_p99": delta_p99,
        "dynamic_range": dynamic_range, "cycles_per_year": cycles_per_year,
        "cycle_depth_db": cycle_depth_db, "max_flat_passes": int(max_flat),
        "per_tank_step": per_tank_step, "live_fraction": live_fraction,
    }


# ─── gate ─────────────────────────────────────────────────────────────────────

def apply_gate(metrics: dict, corridors: dict) -> dict:
    """Apply DEAD/WILD gate. Returns verdict dict with per-metric flags."""
    flags = {}
    dead_flags, wild_flags = [], []

    for key, (lo, hi) in corridors.items():
        val = metrics.get(key, np.nan)
        if np.isnan(val):
            continue
        flag = None
        if val < lo:
            flag = "BELOW_LO"
            if key in DEAD_METRICS:
                dead_flags.append((key, val, lo, hi))
        elif val > hi:
            flag = "ABOVE_HI"
            if key in WILD_METRICS:
                wild_flags.append((key, val, lo, hi))
        if flag:
            flags[key] = {"value": val, "lo": lo, "hi": hi, "flag": flag,
                          "metric": METRIC_LABELS.get(key, key)}

    # Diagnose
    is_dead = len(dead_flags) >= 2  # 2 of 3 DEAD metrics out of corridor
    is_wild = len(wild_flags) >= 1  # any WILD metric out of corridor

    if not flags:
        verdict = "PASS"
        diagnosis = None
    elif is_dead and not is_wild:
        verdict = "FLAG"
        diagnosis = "DEAD"
    elif is_wild:
        verdict = "FLAG"
        diagnosis = "WILD"
    else:
        verdict = "FLAG"
        diagnosis = "REVIEW"  # out-of-corridor but neither clearly DEAD nor WILD

    return {
        "verdict":    verdict,
        "diagnosis":  diagnosis,
        "flags":      flags,
        "dead_count": len(dead_flags),
        "wild_count": len(wild_flags),
    }


# ─── print report ─────────────────────────────────────────────────────────────

def print_report(terminal_id: str, metrics: dict, corridors: dict, gate: dict):
    print(f"\n{'='*60}")
    print(f"SERIES_HEALTH — {terminal_id.upper()}")
    print(f"{'='*60}")
    print(f"  Verdict   : {gate['verdict']}")
    if gate['diagnosis']:
        print(f"  Diagnosis : {gate['diagnosis']}")
    print(f"\n  Metric                              Value     Corridor")
    print(f"  {'─'*55}")
    for key in METRIC_KEYS:
        val = metrics.get(key, np.nan)
        if key in corridors:
            lo, hi = corridors[key]
            flagged = key in gate["flags"]
            marker = " ◄ FLAG" if flagged else ""
            if isinstance(val, float):
                print(f"  {METRIC_LABELS[key]:<36} {val:>8.3f}   [{lo:.3f}, {hi:.3f}]{marker}")
            else:
                print(f"  {METRIC_LABELS[key]:<36} {val:>8}   [{lo:.3f}, {hi:.3f}]{marker}")
        else:
            if isinstance(val, float):
                print(f"  {METRIC_LABELS[key]:<36} {val:>8.3f}   [no corridor]")
            else:
                print(f"  {METRIC_LABELS[key]:<36} {val:>8}   [no corridor]")
    print(f"{'='*60}\n")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run series health gate on a terminal")
    parser.add_argument("--terminal", required=True, help="Terminal ID (e.g. jask, yanbu, kharg)")
    parser.add_argument("--out", default=None, help="Write JSON result to this path")
    args = parser.parse_args()

    print(f"Loading corridors from {STANDARD_PATH}...")
    corridors = load_corridors()
    if not corridors:
        print("ERROR: No corridors loaded. Check SERIES_HEALTH_STANDARD.md format.")
        sys.exit(1)
    print(f"  Corridors loaded: {len(corridors)} metrics")

    print(f"\nFetching {args.terminal} data...")
    df = load_data(args.terminal)
    if df.empty:
        print(f"ERROR: No data found for terminal '{args.terminal}'.")
        sys.exit(1)
    print(f"  {len(df):,} rows, {df['date'].nunique()} dates, {df['tank_id'].nunique()} tanks")

    print("\nComputing metrics...")
    metrics = compute_metrics(df)

    gate = apply_gate(metrics, corridors)
    print_report(args.terminal, metrics, corridors, gate)

    result = {
        "terminal":  args.terminal,
        "verdict":   gate["verdict"],
        "diagnosis": gate["diagnosis"],
        "flags":     gate["flags"],
        "metrics":   {k: (None if np.isnan(v) else v)
                      for k, v in metrics.items()
                      if isinstance(v, float)},
        "metrics_int": {k: v for k, v in metrics.items() if isinstance(v, int)},
    }

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=2))
        print(f"Result written to {args.out}")

    # Exit code: 0 = PASS, 1 = FLAG
    sys.exit(0 if gate["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
