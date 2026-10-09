"""
series_health_standard.py — Stage 1: compute reference health passport from
Kharg and Yanbu, output SERIES_HEALTH_STANDARD.md with metric corridors.

Metrics computed (per-terminal, from raw per-tank db_mean series):
  1. cv_level          — CV of per-date mean_db across all tanks (stddev/mean)
  2. vel_std           — std of pass-to-pass Δmean_db (velocity stddev, dB/pass)
  3. delta_p50         — median |Δmean_db| between consecutive dates
  4. delta_p90         — 90th pct |Δmean_db|
  5. delta_p99         — 99th pct |Δmean_db|
  6. dynamic_range     — p95(mean_db) − p5(mean_db) over full history
  7. cycles_per_year   — build-draw cycles per year (zero-crossing count on
                         demeaned 30d MA, divided by span in years)
  8. cycle_depth_db    — median depth of completed build/draw cycles (dB)
  9. max_flat_days     — longest run of consecutive passes with |Δ| < 0.1 dB
 10. per_tank_step     — median of (per-tank median step_db) across all active tanks
 11. live_fraction     — fraction of tanks with step_db >= 0.5 (not dead-flat)

Corridor rule: union of [Kharg, Yanbu] × [0.5, 2.0] factor.
  lo = min(kharg_val, yanbu_val) × 0.5
  hi = max(kharg_val, yanbu_val) × 2.0

Usage:
  python series_health_standard.py [--out diagnostics/SERIES_HEALTH_STANDARD.md]

Requires: SUPABASE_URL, SUPABASE_KEY env vars (or defaults from config.py).
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from config import sb

OUT_DEFAULT = Path(__file__).parent.parent / "diagnostics" / "SERIES_HEALTH_STANDARD.md"

# ─── fetch helpers ────────────────────────────────────────────────────────────

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


def load_kharg():
    """Load Kharg from raw_observations (all tanks, all dates)."""
    rows = fetch_all("raw_observations", "date,tank_id,db_mean")
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["db_mean"])
    print(f"[kharg] {len(df):,} rows, {df['date'].nunique()} dates, {df['tank_id'].nunique()} tanks")
    return df


def load_terminal(terminal_id):
    """Load terminal from terminal_observations."""
    rows = fetch_all("terminal_observations", "date,tank_id,db_mean",
                     filters={"terminal_id": terminal_id})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["db_mean"])
    print(f"[{terminal_id}] {len(df):,} rows, {df['date'].nunique()} dates, {df['tank_id'].nunique()} tanks")
    return df


# ─── metrics ──────────────────────────────────────────────────────────────────

def compute_metrics(df: pd.DataFrame, label: str) -> dict:
    """Compute health metrics from per-tank per-date db_mean dataframe."""

    # daily mean_db across all tanks
    daily = (
        df.groupby("date")["db_mean"]
        .mean()
        .sort_index()
        .rename("mean_db")
    )

    # 1. CV of level
    cv_level = float(daily.std() / daily.mean()) if daily.mean() != 0 else np.nan

    # 2-5. Velocity and deltas
    deltas = daily.diff().dropna().abs()
    vel_std   = float(deltas.std())
    delta_p50 = float(deltas.quantile(0.50))
    delta_p90 = float(deltas.quantile(0.90))
    delta_p99 = float(deltas.quantile(0.99))

    # 6. Dynamic range
    dynamic_range = float(daily.quantile(0.95) - daily.quantile(0.05))

    # 7-8. Build-draw cycles per year via zero-crossings on 30d MA
    ma = daily.rolling("30D").mean().dropna()
    demeaned = ma - ma.mean()
    signs = np.sign(demeaned.values)
    crossings = int(np.sum(np.diff(signs) != 0))
    span_years = (daily.index[-1] - daily.index[0]).days / 365.25
    cycles_per_year = float(crossings / 2 / span_years) if span_years > 0 else np.nan

    # Cycle depth: for each crossing, record amplitude
    if len(demeaned) > 2:
        peaks = []
        current_sign = signs[0]
        current_max = abs(demeaned.values[0])
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

    # 9. Max flat segment: longest run of |Δ| < 0.1
    raw_deltas = daily.diff().dropna().abs()
    flat_mask = (raw_deltas < 0.1).values
    max_flat = 0
    run = 0
    for v in flat_mask:
        if v:
            run += 1
            max_flat = max(max_flat, run)
        else:
            run = 0
    # convert to days: each step is one pass (~6d); approximate
    # We'll report in passes, not days, since pass cadence varies
    max_flat_passes = int(max_flat)

    # 10-11. Per-tank step + live fraction
    per_tank_steps = []
    for tank_id, tdf in df.groupby("tank_id"):
        s = tdf.sort_values("date")["db_mean"]
        steps = s.diff().dropna().abs()
        if len(steps) >= 3:
            per_tank_steps.append(float(steps.median()))

    per_tank_step   = float(np.median(per_tank_steps)) if per_tank_steps else np.nan
    live_fraction   = float(np.mean([s >= 0.5 for s in per_tank_steps])) if per_tank_steps else np.nan

    n_dates = int(daily.shape[0])
    n_tanks = int(df["tank_id"].nunique())
    date_range = f"{daily.index[0].date()} → {daily.index[-1].date()}"

    print(f"  [{label}] cv={cv_level:.4f}  vel_std={vel_std:.3f}  Δp50={delta_p50:.3f}  "
          f"Δp90={delta_p90:.3f}  dr={dynamic_range:.2f}  cyc={cycles_per_year:.2f}/yr  "
          f"flat={max_flat_passes}  step={per_tank_step:.3f}  live={live_fraction:.2f}")

    return {
        "label":          label,
        "n_dates":        n_dates,
        "n_tanks":        n_tanks,
        "date_range":     date_range,
        "cv_level":       cv_level,
        "vel_std":        vel_std,
        "delta_p50":      delta_p50,
        "delta_p90":      delta_p90,
        "delta_p99":      delta_p99,
        "dynamic_range":  dynamic_range,
        "cycles_per_year": cycles_per_year,
        "cycle_depth_db": cycle_depth_db,
        "max_flat_passes": max_flat_passes,
        "per_tank_step":  per_tank_step,
        "live_fraction":  live_fraction,
    }


# ─── corridor ─────────────────────────────────────────────────────────────────

METRICS_ORDERED = [
    ("cv_level",         "CV of level (σ/μ mean_db)",   ".4f"),
    ("vel_std",          "Velocity std (dB/pass)",       ".3f"),
    ("delta_p50",        "|Δ| p50 (dB)",                ".3f"),
    ("delta_p90",        "|Δ| p90 (dB)",                ".3f"),
    ("delta_p99",        "|Δ| p99 (dB)",                ".3f"),
    ("dynamic_range",    "Dynamic range (p95−p5, dB)",  ".2f"),
    ("cycles_per_year",  "Build-draw cycles/year",       ".2f"),
    ("cycle_depth_db",   "Cycle depth (dB)",             ".2f"),
    ("max_flat_passes",  "Max flat segment (passes)",    "d"),
    ("per_tank_step",    "Per-tank median step (dB)",    ".3f"),
    ("live_fraction",    "Live fraction (step≥0.5)",     ".3f"),
]


def build_corridor(k_val, y_val):
    """Return (lo, hi) corridor: union of both refs × [0.5, 2.0]."""
    if np.isnan(k_val) or np.isnan(y_val):
        return np.nan, np.nan
    lo = min(k_val, y_val) * 0.5
    hi = max(k_val, y_val) * 2.0
    return lo, hi


# ─── markdown output ──────────────────────────────────────────────────────────

def write_standard(kharg: dict, yanbu: dict, out_path: Path):
    lines = []
    lines.append("# SERIES_HEALTH_STANDARD")
    lines.append("")
    lines.append("Reference health passport from Kharg (Iran) and Yanbu (Saudi Arabia).")
    lines.append("Corridors = union of both references × [0.5, 2.0].")
    lines.append("These corridors are **fixed** — do not adjust after numbers are known.")
    lines.append("")
    lines.append(f"Generated: {pd.Timestamp.now().date()}")
    lines.append("")
    lines.append("## Reference Coverage")
    lines.append("")
    lines.append(f"| Terminal | Dates | Tanks | Date range |")
    lines.append(f"|---|---|---|---|")
    lines.append(f"| Kharg    | {kharg['n_dates']} | {kharg['n_tanks']} | {kharg['date_range']} |")
    lines.append(f"| Yanbu    | {yanbu['n_dates']} | {yanbu['n_tanks']} | {yanbu['date_range']} |")
    lines.append("")
    lines.append("## Standard Metric Corridors")
    lines.append("")
    lines.append("| Metric | Kharg | Yanbu | Corridor lo | Corridor hi |")
    lines.append("|---|---|---|---|---|")

    corridors = {}
    for key, label, fmt in METRICS_ORDERED:
        k_val = kharg[key]
        y_val = yanbu[key]
        lo, hi = build_corridor(k_val, y_val)
        if fmt == "d":
            k_s = f"{k_val:d}" if not np.isnan(k_val) else "n/a"
            y_s = f"{y_val:d}" if not np.isnan(y_val) else "n/a"
            lo_s = f"{lo:.0f}" if not np.isnan(lo) else "n/a"
            hi_s = f"{hi:.0f}" if not np.isnan(hi) else "n/a"
        else:
            k_s  = f"{k_val:{fmt}}" if not np.isnan(k_val) else "n/a"
            y_s  = f"{y_val:{fmt}}" if not np.isnan(y_val) else "n/a"
            lo_s = f"{lo:{fmt}}" if not np.isnan(lo) else "n/a"
            hi_s = f"{hi:{fmt}}" if not np.isnan(hi) else "n/a"
        lines.append(f"| {label} | {k_s} | {y_s} | {lo_s} | {hi_s} |")
        corridors[key] = (lo, hi)

    lines.append("")
    lines.append("## Diagnostic Thresholds")
    lines.append("")
    lines.append("A terminal series is flagged DEAD when **all** of:")
    lines.append("- `delta_p90 < corridor_lo(delta_p90)` (no real variation)")
    lines.append("- `live_fraction < corridor_lo(live_fraction)` (most tanks flat)")
    lines.append("- `dynamic_range < corridor_lo(dynamic_range)` (no db range)")
    lines.append("")
    lines.append("A terminal series is flagged WILD when **any** of:")
    lines.append("- `delta_p99 > corridor_hi(delta_p99)` (extreme jumps present)")
    lines.append("- `vel_std > corridor_hi(vel_std)` (velocity noise above corridor)")
    lines.append("- `live_fraction > corridor_hi(live_fraction)` — impossible (capped 1.0)")
    lines.append("")
    lines.append("Neither DEAD nor WILD → PASS.")
    lines.append("")
    lines.append("## Self-Validation Gate")
    lines.append("")
    lines.append("Run `series_health.py` on two known test cases before any new terminal:")
    lines.append("- **RT-DESC** (Ras Tanura DESC): expect PASS")
    lines.append("- **RT-ASC-archive** (ASC-only archive): expect DEAD")
    lines.append("")
    lines.append("If either test case fails, corridors must be re-examined before proceeding.")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Kharg is READ-ONLY. Used as calibration/reference only.")
    lines.append("- Corridors are fixed at generation time. Do not adjust post-hoc.")
    lines.append("- FLAG always reported with metric value and corridor numbers.")
    lines.append("- «вероятно» prohibited in all pipeline output.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    print(f"\n[output] Written to {out_path}")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Compute SERIES_HEALTH_STANDARD from Kharg + Yanbu")
    parser.add_argument("--out", default=str(OUT_DEFAULT), help="Output .md path")
    args = parser.parse_args()

    print("Loading Kharg (raw_observations)...")
    kharg_df = load_kharg()

    print("Loading Yanbu (terminal_observations)...")
    yanbu_df = load_terminal("yanbu")

    print("\nComputing metrics...")
    kharg_m = compute_metrics(kharg_df, "kharg")
    yanbu_m = compute_metrics(yanbu_df, "yanbu")

    out_path = Path(args.out)
    write_standard(kharg_m, yanbu_m, out_path)
    print("Done.")


if __name__ == "__main__":
    main()
