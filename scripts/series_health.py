"""
series_health.py — Stage 2: run health gate on a terminal's SAR series.

Loads corridors from diagnostics/SERIES_HEALTH_STANDARD.md (must exist — run
series_health_standard.py first), fetches the terminal's observations, applies
the same normalization as series_health_standard.py, and compares against corridors.

Verdicts:
  PASS  — all metrics within corridors
  FLAG  — one or more metrics outside corridor, with diagnosis DEAD or WILD or REVIEW

Diagnoses:
  DEAD  — series flat/inactive (low Δ_norm, low live_fraction; or dynamic_range_db < 0.5)
  WILD  — series noisy/broken (high delta_p99_norm or vel_std_norm)
  REVIEW— out-of-corridor but neither clearly DEAD nor WILD

Exit codes: 0 = PASS, 1 = FLAG

Usage:
  python series_health.py --terminal yanbu
  python series_health.py --terminal ras_tanura --orbit 35        # DESC (expect PASS)
  python series_health.py --terminal ras_tanura --orbit 101       # ASC (expect DEAD)
  python series_health.py --terminal jask --out diagnostics/jask/health.json
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

# ─── metric keys & labels ─────────────────────────────────────────────────────

METRIC_KEYS = [
    "dynamic_range_db",
    "cv_level", "vel_std_norm", "delta_p50_norm", "delta_p90_norm", "delta_p99_norm",
    "cycles_per_year", "cycle_depth_norm", "max_flat_passes",
    "per_tank_step_norm", "live_fraction",
]

METRIC_LABELS = {
    "dynamic_range_db":   "Dynamic range (absolute, dB)",
    "cv_level":           "CV of level (σ/μ mean_db)",
    "vel_std_norm":       "Velocity std (norm by dr)",
    "delta_p50_norm":     "Δ p50 (norm by dr)",
    "delta_p90_norm":     "Δ p90 (norm by dr)",
    "delta_p99_norm":     "Δ p99 (norm by dr)",
    "cycles_per_year":    "Build-draw cycles/year",
    "cycle_depth_norm":   "Cycle depth (norm by dr)",
    "max_flat_passes":    "Max flat segment (passes)",
    "per_tank_step_norm": "Per-tank step (norm by dr)",
    "live_fraction":      "Live fraction (step≥5% dr)",
}

DEAD_METRICS = {"delta_p90_norm", "live_fraction", "cycles_per_year", "delta_p50_norm"}
WILD_METRICS  = {"delta_p99_norm", "vel_std_norm"}
DEAD_DR_ABS   = 0.5   # unconditional DEAD if dynamic_range_db < this
# Metrics where "above hi" is healthy (active cycling) — only flag below lo
UPPER_SKIP_METRICS = {"per_tank_step_norm", "live_fraction"}

# ─── load corridors ───────────────────────────────────────────────────────────

def load_corridors() -> dict:
    if not STANDARD_PATH.exists():
        print(f"ERROR: {STANDARD_PATH} not found. Run series_health_standard.py first.")
        sys.exit(1)
    text = STANDARD_PATH.read_text()
    table_section = re.search(r"\| Metric \|.*?Corridor hi \|\n((?:\|.*?\n)+)", text)
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
        for k, l in METRIC_LABELS.items():
            if l == label and lo_s not in ("n/a", "") and hi_s not in ("n/a", ""):
                try:
                    corridors[k] = (float(lo_s), float(hi_s))
                except ValueError:
                    pass
                break
    return corridors


# ─── data fetch ───────────────────────────────────────────────────────────────

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


def load_data(terminal_id: str, relative_orbit=None, tanks=None) -> pd.DataFrame:
    if terminal_id == "kharg":
        rows = fetch_all("raw_observations", "date,tank_id,db_mean")
        df = pd.DataFrame(rows)
    else:
        rows = fetch_all("terminal_observations",
                         "date,tank_id,db_mean,relative_orbit,orbit_direction",
                         filters={"terminal_id": terminal_id})
        df = pd.DataFrame(rows)
        if relative_orbit is not None and "relative_orbit" in df.columns:
            df = df[df["relative_orbit"] == relative_orbit]
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["db_mean"])
    if tanks is not None and "tank_id" in df.columns:
        df = df[df["tank_id"].isin(tanks)]
    return df


# ─── metrics (same normalization as series_health_standard.py) ───────────────

def compute_metrics(df: pd.DataFrame) -> dict:
    daily = df.groupby("date")["db_mean"].mean().sort_index().rename("mean_db")

    dynamic_range_db = float(daily.quantile(0.95) - daily.quantile(0.05))
    dr = dynamic_range_db if dynamic_range_db > 0 else np.nan
    flat_threshold = 0.01 * dr if not np.isnan(dr) else 0.1
    live_threshold = 0.05 * dr if not np.isnan(dr) else 0.5

    cv_level = float(daily.std() / daily.mean()) if daily.mean() != 0 else np.nan

    deltas_raw = daily.diff().dropna().abs()
    vel_std_norm   = float(deltas_raw.std()          / dr) if not np.isnan(dr) else np.nan
    delta_p50_norm = float(deltas_raw.quantile(0.50) / dr) if not np.isnan(dr) else np.nan
    delta_p90_norm = float(deltas_raw.quantile(0.90) / dr) if not np.isnan(dr) else np.nan
    delta_p99_norm = float(deltas_raw.quantile(0.99) / dr) if not np.isnan(dr) else np.nan

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
        cycle_depth_norm = (float(np.median(peaks)) / dr) if (peaks and not np.isnan(dr)) else np.nan
    else:
        cycle_depth_norm = np.nan

    flat_mask = (deltas_raw < flat_threshold).values
    max_flat, run = 0, 0
    for v in flat_mask:
        if v: run += 1; max_flat = max(max_flat, run)
        else: run = 0

    per_tank_steps = []
    for _, tdf in df.groupby("tank_id"):
        s = tdf.sort_values("date")["db_mean"]
        steps = s.diff().dropna().abs()
        if len(steps) >= 3:
            per_tank_steps.append(float(steps.median()))

    if per_tank_steps and not np.isnan(dr):
        per_tank_step_norm = float(np.median(per_tank_steps) / dr)
        live_fraction = float(np.mean([s >= live_threshold for s in per_tank_steps]))
    else:
        per_tank_step_norm = np.nan
        live_fraction = np.nan

    return {
        "dynamic_range_db":   dynamic_range_db,
        "cv_level":           cv_level,
        "vel_std_norm":       vel_std_norm,
        "delta_p50_norm":     delta_p50_norm,
        "delta_p90_norm":     delta_p90_norm,
        "delta_p99_norm":     delta_p99_norm,
        "cycles_per_year":    cycles_per_year,
        "cycle_depth_norm":   cycle_depth_norm,
        "max_flat_passes":    int(max_flat),
        "per_tank_step_norm": per_tank_step_norm,
        "live_fraction":      live_fraction,
    }


# ─── gate ─────────────────────────────────────────────────────────────────────

def apply_gate(metrics: dict, corridors: dict) -> dict:
    flags = {}
    dead_flags, wild_flags = [], []

    # Unconditional DEAD: dynamic_range_db too small to normalize
    dr = metrics.get("dynamic_range_db", np.nan)
    if not np.isnan(dr) and dr < DEAD_DR_ABS:
        flags["dynamic_range_db"] = {
            "value": dr, "lo": DEAD_DR_ABS, "hi": np.nan,
            "flag": "BELOW_ABS_MIN",
            "metric": METRIC_LABELS["dynamic_range_db"],
        }
        dead_flags.append(("dynamic_range_db", dr, DEAD_DR_ABS, np.nan))

    for key, (lo, hi) in corridors.items():
        val = metrics.get(key, np.nan)
        if np.isnan(val) or key == "dynamic_range_db":
            continue
        flag = None
        if val < lo:
            flag = "BELOW_LO"
            if key in DEAD_METRICS:
                dead_flags.append((key, val, lo, hi))
        elif val > hi and key not in UPPER_SKIP_METRICS:
            flag = "ABOVE_HI"
            if key in WILD_METRICS:
                wild_flags.append((key, val, lo, hi))
        if flag:
            flags[key] = {"value": val, "lo": lo, "hi": hi, "flag": flag,
                          "metric": METRIC_LABELS.get(key, key)}

    is_dead = len(dead_flags) >= 2 or ("dynamic_range_db" in flags)
    is_wild = len(wild_flags) >= 1

    if not flags:
        verdict, diagnosis = "PASS", None
    elif is_dead and not is_wild:
        verdict, diagnosis = "FLAG", "DEAD"
    elif is_wild:
        verdict, diagnosis = "FLAG", "WILD"
    else:
        verdict, diagnosis = "FLAG", "REVIEW"

    return {
        "verdict": verdict, "diagnosis": diagnosis,
        "flags": flags,
        "dead_count": len(dead_flags), "wild_count": len(wild_flags),
    }


# ─── print report ─────────────────────────────────────────────────────────────

def print_report(terminal_id: str, metrics: dict, corridors: dict, gate: dict,
                 relative_orbit=None):
    tag = f"  orbit={relative_orbit}" if relative_orbit else ""
    print(f"\n{'='*64}")
    print(f"SERIES_HEALTH — {terminal_id.upper()}{tag}")
    print(f"{'='*64}")
    print(f"  Verdict   : {gate['verdict']}")
    if gate['diagnosis']:
        print(f"  Diagnosis : {gate['diagnosis']}")

    print(f"\n  {'Metric':<38} {'Value':>10}   Corridor")
    print(f"  {'─'*60}")

    for key in METRIC_KEYS:
        val = metrics.get(key, np.nan)
        flagged = key in gate["flags"]
        marker = " ◄ FLAG" if flagged else ""
        label = METRIC_LABELS[key]
        if key == "dynamic_range_db":
            vs = f"{val:.2f} dB" if not np.isnan(val) else "n/a"
            print(f"  {label:<38} {vs:>10}   [≥{DEAD_DR_ABS:.1f} dB]{marker}")
        elif key in corridors:
            lo, hi = corridors[key]
            vs = f"{val:.4f}" if isinstance(val, float) and not np.isnan(val) else (
                 f"{val:d}" if isinstance(val, int) else "n/a")
            print(f"  {label:<38} {vs:>10}   [{lo:.4f}, {hi:.4f}]{marker}")
        else:
            vs = f"{val:.4f}" if isinstance(val, float) and not np.isnan(val) else (
                 f"{val:d}" if isinstance(val, int) else "n/a")
            print(f"  {label:<38} {vs:>10}   [no corridor]{marker}")
    print(f"{'='*64}\n")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run series health gate on a terminal")
    parser.add_argument("--terminal", required=True)
    parser.add_argument("--orbit",    type=int, default=None,
                        help="Filter to this relative_orbit (e.g. 35 for RT-DESC)")
    parser.add_argument("--tanks",    default=None,
                        help="Comma-separated tank IDs to include (e.g. af_0,af_2,af_3)")
    parser.add_argument("--out",      default=None, help="Write JSON result to this path")
    args = parser.parse_args()

    tanks = [t.strip() for t in args.tanks.split(",")] if args.tanks else None

    print(f"Loading corridors from {STANDARD_PATH}...")
    corridors = load_corridors()
    print(f"  {len(corridors)} metric corridors loaded")
    if not corridors:
        print("ERROR: no corridors loaded — check SERIES_HEALTH_STANDARD.md format.")
        sys.exit(1)

    tank_tag = f" tanks={args.tanks}" if tanks else ""
    print(f"\nFetching {args.terminal} (orbit={args.orbit or 'all'}{tank_tag})...")
    df = load_data(args.terminal, args.orbit, tanks=tanks)
    if df.empty:
        print(f"ERROR: no data for terminal='{args.terminal}' orbit={args.orbit} tanks={tanks}")
        sys.exit(1)
    print(f"  {len(df):,} rows, {df['date'].nunique()} dates, {df['tank_id'].nunique()} tanks")

    print("Computing metrics...")
    metrics = compute_metrics(df)

    gate = apply_gate(metrics, corridors)
    print_report(args.terminal, metrics, corridors, gate, args.orbit)

    result = {
        "terminal":       args.terminal,
        "relative_orbit": args.orbit,
        "tanks":          tanks,
        "verdict":        gate["verdict"],
        "diagnosis":      gate["diagnosis"],
        "flags":          gate["flags"],
        "metrics":        {k: (None if (isinstance(v, float) and np.isnan(v)) else v)
                           for k, v in metrics.items()},
    }

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=2))
        print(f"Result written to {args.out}")

    sys.exit(0 if gate["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
