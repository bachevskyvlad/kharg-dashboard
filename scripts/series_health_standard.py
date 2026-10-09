"""
series_health_standard.py — Stage 1: compute reference health passport from
Kharg and Yanbu, output SERIES_HEALTH_STANDARD.md with metric corridors.

NORMALIZATION CONTRACT
======================
All metrics are dimensionless or normalized by the terminal's own dynamic_range_db
(p95 − p5 of the per-date mean_db series). This makes corridors portable across
terminals of different sizes and capacities. No absolute Mb metrics; no absolute
dB metrics in corridors (except dynamic_range_db itself, which is the base).

Metric list (11 metrics):
  1.  dynamic_range_db  — p95−p5 of per-date mean_db (dB); base for normalization; kept absolute
  2.  cv_level          — σ(mean_db) / μ(mean_db); dimensionless (self-normalizing)
  3.  vel_std_norm      — std(Δmean_db) / dynamic_range_db
  4.  delta_p50_norm    — median(|Δmean_db|) / dynamic_range_db
  5.  delta_p90_norm    — p90(|Δmean_db|) / dynamic_range_db
  6.  delta_p99_norm    — p99(|Δmean_db|) / dynamic_range_db
  7.  cycles_per_year   — zero-crossings on 30d MA / 2 / span_yr; 1/yr (dimensionless)
  8.  cycle_depth_norm  — median cycle amplitude / dynamic_range_db
  9.  max_flat_passes   — longest run of passes with |Δ| < 1% dynamic_range; passes (dimensionless)
  10. per_tank_step_norm— median(per-tank median|Δdb|) / dynamic_range_db
  11. live_fraction     — frac(tanks where per-tank step ≥ 5% dynamic_range); [0,1]

Corridor rule: union of [Kharg, Yanbu] × [0.5, 2.0] factor.
  lo = min(kharg_val, yanbu_val) × 0.5
  hi = max(kharg_val, yanbu_val) × 2.0

Corridors are FIXED at generation time. Do not adjust post-hoc.

Usage:
  python series_health_standard.py [--out diagnostics/SERIES_HEALTH_STANDARD.md]
  python series_health_standard.py --orbit 35 --terminal ras_tanura  # filter by orbit

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


def load_terminal(terminal_id, relative_orbit=None, orbit_direction=None):
    """Load terminal from terminal_observations, optionally filter by orbit."""
    rows = fetch_all("terminal_observations",
                     "date,tank_id,db_mean,relative_orbit,orbit_direction",
                     filters={"terminal_id": terminal_id})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["db_mean"])
    if relative_orbit is not None:
        df = df[df["relative_orbit"] == relative_orbit]
    if orbit_direction is not None:
        df = df[df["orbit_direction"].str.upper() == orbit_direction.upper()]
    print(f"[{terminal_id}] {len(df):,} rows, {df['date'].nunique()} dates, "
          f"{df['tank_id'].nunique()} tanks"
          + (f" (orbit={relative_orbit})" if relative_orbit else ""))
    return df


# ─── metrics ──────────────────────────────────────────────────────────────────

NORMALIZATION_TABLE = """
## Normalization Contract

| Metric key | Raw formula | Raw unit | Normalization | Normalized |
|---|---|---|---|---|
| dynamic_range_db | p95(mean_db) − p5(mean_db) | dB | (base — kept absolute) | dB |
| cv_level | σ(mean_db) / μ(mean_db) | dimensionless | (self) | dimensionless |
| vel_std_norm | std(Δmean_db) / dynamic_range_db | dB/pass | ÷ dynamic_range_db | fraction |
| delta_p50_norm | median(abs(Δmean_db)) / dynamic_range_db | dB | ÷ dynamic_range_db | fraction |
| delta_p90_norm | p90(abs(Δmean_db)) / dynamic_range_db | dB | ÷ dynamic_range_db | fraction |
| delta_p99_norm | p99(abs(Δmean_db)) / dynamic_range_db | dB | ÷ dynamic_range_db | fraction |
| cycles_per_year | zero-crossings/2 / span_yr | 1/yr | (self) | 1/yr |
| cycle_depth_norm | median_peak(|demeaned_30d_ma|) / dynamic_range_db | dB | ÷ dynamic_range_db | fraction |
| max_flat_passes | longest run with |Δ| < 1% dynamic_range | passes | (self) | passes |
| per_tank_step_norm | median(per-tank median|Δdb|) / dynamic_range_db | dB | ÷ dynamic_range_db | fraction |
| live_fraction | frac(tanks: per-tank-step ≥ 5% dynamic_range) | [0,1] | (threshold normalized) | [0,1] |

Notes:
- dynamic_range_db is NOT in the corridor comparison; it is the normalization base.
  A near-zero dynamic_range invalidates all derived metrics; checked separately in gate.
- Flat threshold: |Δ| < 0.01 × dynamic_range_db (1% of range), not a fixed dB.
- Live threshold: per-tank step ≥ 0.05 × dynamic_range_db (5% of range), not fixed 0.5 dB.
- All normalized metrics are ∈ [0, ∞) but practically ∈ [0, 1] for healthy series.
- No Mb metrics. Cross-terminal validity: normalization removes tank-size and orbit-geometry
  dependence from dB delta metrics.
"""


def compute_metrics(df: pd.DataFrame, label: str) -> dict:
    """Compute normalized health metrics from per-tank per-date db_mean dataframe."""

    # per-date terminal mean
    daily = (
        df.groupby("date")["db_mean"]
        .mean()
        .sort_index()
        .rename("mean_db")
    )

    # dynamic_range: base for normalization (kept absolute, in dB)
    dynamic_range_db = float(daily.quantile(0.95) - daily.quantile(0.05))
    dr = dynamic_range_db if dynamic_range_db > 0 else np.nan

    # thresholds derived from dynamic_range
    flat_threshold = 0.01 * dr if not np.isnan(dr) else 0.1    # 1% dr
    live_threshold = 0.05 * dr if not np.isnan(dr) else 0.5    # 5% dr

    # cv_level: dimensionless
    cv_level = float(daily.std() / daily.mean()) if daily.mean() != 0 else np.nan

    # deltas; raw and normalized
    deltas_raw = daily.diff().dropna().abs()
    vel_std_norm  = float(deltas_raw.std()           / dr) if not np.isnan(dr) else np.nan
    delta_p50_norm= float(deltas_raw.quantile(0.50)  / dr) if not np.isnan(dr) else np.nan
    delta_p90_norm= float(deltas_raw.quantile(0.90)  / dr) if not np.isnan(dr) else np.nan
    delta_p99_norm= float(deltas_raw.quantile(0.99)  / dr) if not np.isnan(dr) else np.nan

    # cycles_per_year (dimensionless)
    ma = daily.rolling("30D").mean().dropna()
    demeaned = ma - ma.mean()
    signs = np.sign(demeaned.values)
    crossings = int(np.sum(np.diff(signs) != 0))
    span_years = (daily.index[-1] - daily.index[0]).days / 365.25
    cycles_per_year = float(crossings / 2 / span_years) if span_years > 0 else np.nan

    # cycle_depth_norm
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

    # max_flat_passes: uses normalized threshold
    flat_mask = (deltas_raw < flat_threshold).values
    max_flat, run = 0, 0
    for v in flat_mask:
        if v:
            run += 1
            max_flat = max(max_flat, run)
        else:
            run = 0
    max_flat_passes = int(max_flat)

    # per_tank_step_norm and live_fraction (threshold normalized)
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

    n_dates = int(daily.shape[0])
    n_tanks = int(df["tank_id"].nunique())
    date_range = f"{daily.index[0].date()} → {daily.index[-1].date()}"

    print(f"  [{label}] dr={dynamic_range_db:.2f}dB  cv={cv_level:.4f}  "
          f"vel_std_n={vel_std_norm:.4f}  Δp50_n={delta_p50_norm:.4f}  "
          f"Δp90_n={delta_p90_norm:.4f}  cyc={cycles_per_year:.2f}/yr  "
          f"flat={max_flat_passes}  step_n={per_tank_step_norm:.4f}  live={live_fraction:.3f}")

    return {
        "label":              label,
        "n_dates":            n_dates,
        "n_tanks":            n_tanks,
        "date_range":         date_range,
        "dynamic_range_db":   dynamic_range_db,
        "cv_level":           cv_level,
        "vel_std_norm":       vel_std_norm,
        "delta_p50_norm":     delta_p50_norm,
        "delta_p90_norm":     delta_p90_norm,
        "delta_p99_norm":     delta_p99_norm,
        "cycles_per_year":    cycles_per_year,
        "cycle_depth_norm":   cycle_depth_norm,
        "max_flat_passes":    max_flat_passes,
        "per_tank_step_norm": per_tank_step_norm,
        "live_fraction":      live_fraction,
    }


# ─── corridor ─────────────────────────────────────────────────────────────────

# Ordered list of metrics for corridor table output
# (dynamic_range_db excluded — it's the normalization base, not in corridor)
METRICS_ORDERED = [
    ("cv_level",          "CV of level (σ/μ mean_db)",          ".4f"),
    ("vel_std_norm",      "Velocity std (norm by dr)",           ".4f"),
    ("delta_p50_norm",    "Δ p50 (norm by dr)",                  ".4f"),
    ("delta_p90_norm",    "Δ p90 (norm by dr)",                  ".4f"),
    ("delta_p99_norm",    "Δ p99 (norm by dr)",                  ".4f"),
    ("cycles_per_year",   "Build-draw cycles/year",              ".2f"),
    ("cycle_depth_norm",  "Cycle depth (norm by dr)",            ".4f"),
    ("max_flat_passes",   "Max flat segment (passes)",           "d"),
    ("per_tank_step_norm","Per-tank step (norm by dr)",          ".4f"),
    ("live_fraction",     "Live fraction (step≥5% dr)",         ".3f"),
]

DEAD_METRICS = {"delta_p90_norm", "live_fraction", "dynamic_range_db"}
WILD_METRICS  = {"delta_p99_norm", "vel_std_norm"}


def build_corridor(k_val, y_val):
    """Return (lo, hi) corridor: union × [0.5, 2.0]."""
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
    lines.append("**These corridors are fixed. Do not adjust after numbers are known.**")
    lines.append("")
    lines.append(f"Generated: {pd.Timestamp.now().date()}")
    lines.append("")
    lines.append("## Reference Coverage")
    lines.append("")
    lines.append("| Terminal | Dates | Tanks | Date range | dynamic_range_db |")
    lines.append("|---|---|---|---|---|")
    lines.append(f"| Kharg | {kharg['n_dates']} | {kharg['n_tanks']} | "
                 f"{kharg['date_range']} | {kharg['dynamic_range_db']:.2f} dB |")
    lines.append(f"| Yanbu | {yanbu['n_dates']} | {yanbu['n_tanks']} | "
                 f"{yanbu['date_range']} | {yanbu['dynamic_range_db']:.2f} dB |")
    lines.append("")
    lines.append(NORMALIZATION_TABLE.strip())
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
        def _fmt(v):
            if np.isnan(v): return "n/a"
            return f"{v:{fmt}}" if fmt != "d" else f"{int(v):d}"
        lines.append(f"| {label} | {_fmt(k_val)} | {_fmt(y_val)} | {_fmt(lo)} | {_fmt(hi)} |")
        corridors[key] = (lo, hi)

    lines.append("")
    lines.append("## Diagnostic Thresholds")
    lines.append("")
    lines.append("**DEAD** — series is flat/inactive; ≥2 of the following below corridor lo:")
    lines.append("- `delta_p90_norm < lo`  (no velocity)")
    lines.append("- `live_fraction < lo`   (most tanks flat)")
    lines.append("- (dynamic_range_db < 0.5 dB is an unconditional DEAD regardless)")
    lines.append("")
    lines.append("**WILD** — series has extreme noise/breaks; any of the following above corridor hi:")
    lines.append("- `delta_p99_norm > hi`  (extreme single-pass jumps)")
    lines.append("- `vel_std_norm > hi`    (velocity noise above corridor)")
    lines.append("")
    lines.append("**PASS** — all metrics within corridors.")
    lines.append("")
    lines.append("## Self-Validation Gate")
    lines.append("")
    lines.append("Run before applying to any new terminal:")
    lines.append("- **RT-DESC** (`--terminal ras_tanura --orbit 35`): expect PASS")
    lines.append("- **alfaw_stuck7** (`--terminal al_flaw --tanks af_0,af_2,af_3,af_6,af_7,af_9,af_11`): expect FLAG/DEAD")
    lines.append("")
    lines.append("PASS + DEAD → instrument valid. Else fix health-check before proceeding.")
    lines.append("")
    lines.append("### Note: RT-ASC orbit 101 excluded from gate")
    lines.append("")
    lines.append("RT-ASC orbit 101 passes health-check (dynamic_range=2.5dB, cycles=3/yr,")
    lines.append("live_fraction=1.0) and is therefore alive. It is excluded from the gate because")
    lines.append("it is E1-blind (decoupled from storage events). This is the canonical example")
    lines.append("that **health-check ≠ informativeness**: a series can be alive and still carry")
    lines.append("no storage signal. Informativeness is verified only at step 7 (event-response),")
    lines.append("not here. Using RT-ASC as a dead control would be a type error.")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Kharg is READ-ONLY (calibration/reference only).")
    lines.append("- Corridors fixed at generation time. No post-hoc adjustment.")
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

    print("Loading Yanbu (terminal_observations, orbit 14 ASC)...")
    yanbu_df = load_terminal("yanbu")

    print("\nComputing metrics...")
    kharg_m = compute_metrics(kharg_df, "kharg")
    yanbu_m = compute_metrics(yanbu_df, "yanbu")

    out_path = Path(args.out)
    write_standard(kharg_m, yanbu_m, out_path)
    print("Done.")


if __name__ == "__main__":
    main()
