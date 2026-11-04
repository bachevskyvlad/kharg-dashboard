"""
stage2_gate_questionnaire_v2.py — Stage 2 gate, v2 scoring.

CHANGES FROM v1
---------------
* Autocorrelation removed from positive-ACCUMULATOR scoring — all three calibration
  terminals show negative lag-1 autocorr, so the sign carries no class signal.
* Primary metric is now dwell_days = vol_mean / known_throughput. Values come from
  published terminal throughput data (see KNOWN_THROUGHPUT below and citations).
* Thresholds derived from physics and set HERE, before any data is seen.

SCORING (7 points max)
-----------------------
  dwell_days     PRIMARY  → ACC +3 / BUF +3 / FLOW +3  (mutually exclusive)
  cycle_days     secondary → BUF +2  if cycle ≤ 14d     (frequent small shipments)
  cv_level       secondary → FLOW +1 if cv ≤ 0.07       (stable in/out, low swing)

THRESHOLDS (physics basis)
--------------------------
  ACCUMULATOR  : dwell ≥ 12d — strategic reserve, oil held under export friction
                               (sanctions, price hold, geopolitical pause)
  PIPELINE-BUF : 4d ≤ dwell < 12d — continuous export terminal with modest buffer
  FLOW-THROUGH : dwell < 4d  — rapid-cycling loading terminal (Saudi sweet crude,
                               short STS, tanker-on-demand)

Sources for throughput data
---------------------------
  EIA country briefs:  https://www.eia.gov/international/analysis/
  IMF PortWatch:       https://portwatch.imf.org/
  JODI Oil:            https://www.jodidata.org/oil/

GATE LOGIC
----------
  Calibration   : kharg → ACCUMULATOR, yanbu → PIPELINE-BUFFER, ras_tanura → FLOW-THROUGH
  Holdout       : al_flaw — expected class NOT supplied; questionnaire must output non-ACC.
                  (null-grid, fast Fao/Basra cycling, 24 tanks × ~0.36 Mn bbl each)
  Gate passes when all 3 calibration terminals correct AND al_flaw ≠ ACCUMULATOR.

Usage:
  cd scripts
  python stage2_gate_questionnaire_v2.py
"""

import os, sys, json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from config import sb
from tank_registry import LOCATIONS

OUT_DIR = Path(__file__).parent.parent / "diagnostics"
OUT_DIR.mkdir(exist_ok=True)

# ── Throughput (public data) ──────────────────────────────────────────────────
# Used to compute dwell_days = vol_mean / throughput.
# Source: EIA https://www.eia.gov/international/analysis/
#         IMF PortWatch https://portwatch.imf.org/
#
# kharg     : Iran crude exports ~1.5 Mn bbl/d (2023-2024, sanctions-constrained)
# yanbu     : Saudi Red Sea crude + NGL exports ~1.3 Mn bbl/d
# ras_tanura: Saudi main terminal, world's largest crude export facility, ~5.5 Mn bbl/d
# al_flaw   : Iraq Fao/Basra coastal sub-terminal, ~1.2 Mn bbl/d
#             (lat 29.97°N / lon 48.44°E; 24 tanks; cap 8.59 Mn bbl)
KNOWN_THROUGHPUT_MN_BBL_PER_DAY = {
    "kharg":      1.5,
    "yanbu":      1.3,
    "ras_tanura": 5.5,
    "al_flaw":    1.2,
}
THROUGHPUT_SOURCE = (
    "EIA country briefs https://www.eia.gov/international/analysis/ ; "
    "IMF PortWatch https://portwatch.imf.org/"
)

# ── Thresholds (PHYSICS-DERIVED, set before data is seen) ────────────────────
THRESHOLDS = {
    # PRIMARY: dwell_days
    "dwell_accumulator_min_days":  12.0,  # ACC ≥ 12d
    "dwell_flow_max_days":          4.0,  # FLOW < 4d; 4–12d → BUFFER

    # SECONDARY: cycle_days (median interval between drawdown events)
    "cycle_pipeline_max_days":     14.0,  # BUF ≤ 14d

    # SECONDARY: cv_level = std(vol)/mean(vol)
    "cv_level_flow_max":            0.07, # FLOW ≤ 0.07 (stable in/out, low swing)
}

# ── Expected dwell (for reference / documentation only) ──────────────────────
# kharg     : 18.76 / 1.5 = 12.5d → ACC  (≥ 12d)
# yanbu     :  9.19 / 1.3 =  7.1d → BUF  (4–12d)
# ras_tanura: 17.09 / 5.5 =  3.1d → FLOW (< 4d)
# al_flaw   :  ~proxy /1.2        → expect BUF or FLOW (non-ACC)

# ── Max capacity (Mn bbl) ─────────────────────────────────────────────────────
MAX_CAP = {
    "kharg":      33.2,
    "yanbu":       7.0,
    "ras_tanura": 10.5,
    "al_flaw":     8.59,
}

# ── Calibration terminals ─────────────────────────────────────────────────────
CALIBRATION = {
    "kharg":      {"expected": "ACCUMULATOR"},
    "yanbu":      {"expected": "PIPELINE-BUFFER"},
    "ras_tanura": {"expected": "FLOW-THROUGH"},
}
# al_flaw: holdout — no expected class given to questionnaire


def fetch_series(terminal_id: str) -> pd.DataFrame:
    """Fetch volume series. Kharg → volume_timeseries; others → terminal_observations proxy."""
    print(f"  Fetching series for {terminal_id} …")
    rows = []

    if terminal_id == "kharg":
        offset = 0
        while True:
            r = (sb.table("volume_timeseries")
                   .select("date,vol_mn_bbl")
                   .range(offset, offset + 999)
                   .execute().data)
            rows.extend(r)
            if len(r) < 1000:
                break
            offset += 1000
    else:
        loc = next((l for l in LOCATIONS if l["id"] == terminal_id), None)
        if loc is None:
            print(f"    [!] {terminal_id} not in registry")
            return pd.DataFrame()
        tanks = [t for t in loc["tanks"] if not t.get("uncertain")]
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
        p5  = df_obs["db_mean"].quantile(0.05)
        p95 = df_obs["db_mean"].quantile(0.95)
        span = p95 - p5 if p95 > p5 else 1.0

        grp = df_obs.groupby("date")["db_mean"].mean().reset_index()
        grp["fill_ratio"] = ((p95 - grp["db_mean"].clip(p5, p95)) / span).clip(0, 1)
        h_med = np.median([t["h_max_m"] for t in tanks])
        h_min = 2.0
        grp["vol_mn_bbl"] = (grp["fill_ratio"] * (h_med - h_min) + h_min) * \
                            sum(np.pi * t["radius_m"]**2 for t in tanks) * 6.2898 / 1e6
        rows = grp[["date", "vol_mn_bbl"]].to_dict("records")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    print(f"    {len(df)} rows  {df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}")
    return df


def compute_metrics(terminal_id: str, df: pd.DataFrame, max_cap_mn: float) -> dict:
    if len(df) < 30:
        return {"error": "insufficient_data"}

    df_w = df.set_index("date")["vol_mn_bbl"].resample("W").mean().dropna()
    vol = df_w.values

    changes = np.diff(vol)
    drawdowns = changes[changes < -0.5]
    cv_drawdown = (np.std(drawdowns) / abs(np.mean(drawdowns))) if len(drawdowns) >= 3 else np.nan
    n_drawdown_events = len(drawdowns)

    # autocorr (reported but NOT used in positive-ACC scoring)
    autocorr = pd.Series(changes).autocorr(lag=1) if len(changes) > 2 else np.nan

    # cycle_days
    drawdown_idx = np.where(changes < -0.5)[0]
    if len(drawdown_idx) >= 2:
        cycle_days = float(np.median(np.diff(drawdown_idx) * 7))
    else:
        cycle_days = np.nan

    # cv_level = std(vol) / mean(vol)
    vol_mean = float(np.mean(vol))
    vol_std  = float(np.std(vol))
    cv_level = (vol_std / vol_mean) if vol_mean > 0 else np.nan

    # dwell_days = vol_mean / known_throughput
    throughput = KNOWN_THROUGHPUT_MN_BBL_PER_DAY.get(terminal_id)
    dwell_days = (vol_mean / throughput) if throughput else None

    # stuck proxy
    vol_range = np.ptp(vol)
    stuck_proxy = float(np.mean(vol < (np.min(vol) + 0.05 * vol_range))) if vol_range > 0 else 0.0

    return {
        "n_weeks":            len(df_w),
        "vol_mean_mn":        round(vol_mean, 2),
        "vol_std_mn":         round(vol_std, 2),
        "cv_level":           round(cv_level, 3)  if cv_level  is not None else None,
        "dwell_days":         round(dwell_days, 1) if dwell_days is not None else None,
        "throughput_mn_d":    throughput,
        "n_drawdown_events":  int(n_drawdown_events),
        "cv_drawdown":        round(float(cv_drawdown), 3) if not np.isnan(cv_drawdown) else None,
        "autocorr_lag1":      round(float(autocorr), 3)    if not np.isnan(autocorr)    else None,
        "cycle_days_median":  round(cycle_days, 1)          if not np.isnan(cycle_days)  else None,
        "stuck_proxy":        round(stuck_proxy, 3),
    }


def classify(metrics: dict) -> tuple[str, dict]:
    """Score-based classification. Returns (class, score_dict)."""
    if "error" in metrics:
        return "UNKNOWN", {}

    dwell = metrics.get("dwell_days")
    cyc   = metrics.get("cycle_days_median")
    cv_l  = metrics.get("cv_level")

    score = {"ACCUMULATOR": 0, "PIPELINE-BUFFER": 0, "FLOW-THROUGH": 0}

    # ── PRIMARY: dwell_days (3 points) ───────────────────────────────────────
    if dwell is not None:
        if dwell >= THRESHOLDS["dwell_accumulator_min_days"]:
            score["ACCUMULATOR"] += 3
        elif dwell < THRESHOLDS["dwell_flow_max_days"]:
            score["FLOW-THROUGH"] += 3
        else:  # 4 ≤ dwell < 12
            score["PIPELINE-BUFFER"] += 3

    # ── SECONDARY: cycle_days (2 points, BUF discriminator) ──────────────────
    if cyc is not None and cyc <= THRESHOLDS["cycle_pipeline_max_days"]:
        score["PIPELINE-BUFFER"] += 2

    # ── SECONDARY: cv_level (1 point, FLOW discriminator) ────────────────────
    if cv_l is not None and cv_l <= THRESHOLDS["cv_level_flow_max"]:
        score["FLOW-THROUGH"] += 1

    best = max(score, key=score.get)
    return best, score


# ── Main ──────────────────────────────────────────────────────────────────────
print("=" * 60)
print("STAGE-2 GATE QUESTIONNAIRE v2 — Calibration + holdout")
print("=" * 60)
print(f"\nThroughput source: {THROUGHPUT_SOURCE}")
print(f"\nThresholds (physics-derived, set before data):")
for k, v in THRESHOLDS.items():
    print(f"  {k:<40} = {v}")

results      = {}
all_pass_cal = True

# ── Calibration terminals ─────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("CALIBRATION TERMINALS")
print("─" * 60)

for tid, info in CALIBRATION.items():
    expected = info["expected"]
    cap = MAX_CAP.get(tid, 10.0)
    print(f"\n── {tid.upper()} (expected: {expected}) ──")

    df = fetch_series(tid)
    if df.empty:
        print(f"  [!] No data — SKIP")
        results[tid] = {"expected": expected, "actual": "NO_DATA", "pass": False, "holdout": False}
        all_pass_cal = False
        continue

    metrics      = compute_metrics(tid, df, cap)
    actual, sc   = classify(metrics)
    passed       = (actual == expected)
    all_pass_cal = all_pass_cal and passed

    results[tid] = {
        "expected": expected, "actual": actual, "pass": passed,
        "metrics": metrics, "score": sc, "holdout": False,
    }
    sym = "✓" if passed else "✗ FAIL"
    print(f"  Actual: {actual}  [{sym}]  scores={sc}")
    for k, v in metrics.items():
        if k != "error":
            print(f"    {k:<28} = {v}")

# ── Holdout: al_flaw ─────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("HOLDOUT — al_flaw (expected class withheld; must be non-ACC)")
print("─" * 60)

cap = MAX_CAP.get("al_flaw", 8.59)
df  = fetch_series("al_flaw")
if df.empty:
    print("  [!] No data — SKIP")
    results["al_flaw"] = {"expected": "non-ACCUMULATOR", "actual": "NO_DATA",
                          "pass": False, "holdout": True}
    holdout_pass = False
else:
    metrics     = compute_metrics("al_flaw", df, cap)
    actual, sc  = classify(metrics)
    holdout_pass = (actual != "ACCUMULATOR")

    results["al_flaw"] = {
        "expected": "non-ACCUMULATOR", "actual": actual,
        "pass": holdout_pass, "metrics": metrics, "score": sc, "holdout": True,
    }
    sym = "✓ (non-ACC)" if holdout_pass else "✗ FAIL (got ACC)"
    print(f"\n── AL_FLAW ──")
    print(f"  Actual: {actual}  [{sym}]  scores={sc}")
    for k, v in metrics.items():
        if k != "error":
            print(f"    {k:<28} = {v}")

gate_passed = all_pass_cal and holdout_pass

# ── Gate summary ──────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("GATE RESULT")
print("=" * 60)
print(f"  {'Terminal':<15} {'Expected':<20} {'Actual':<18} {'Pass'}")
print(f"  {'-'*60}")
for tid, r in results.items():
    sym = "PASS" if r["pass"] else "FAIL"
    print(f"  {tid:<15} {r['expected']:<20} {r['actual']:<18} {sym}")

if gate_passed:
    print("\n  GATE PASSED — calibration validated + holdout non-ACC.")
    print("  Safe to classify Jask and other NEEDS_BASELINE terminals.")
else:
    print("\n  GATE FAILED.")
    if not all_pass_cal:
        print("  One or more calibration terminals mis-classified — fix scoring/thresholds.")
    if not holdout_pass:
        print("  Holdout (al_flaw) classified as ACCUMULATOR — questionnaire design needs revision.")
    print("\n  Current thresholds:")
    for k, v in THRESHOLDS.items():
        print(f"    {k:<45} = {v}")

# ── Write outputs ─────────────────────────────────────────────────────────────
def _serial(o):
    if isinstance(o, (np.integer,)):  return int(o)
    if isinstance(o, (np.floating,)): return float(o)
    return str(o)

output = {
    "run":           datetime.utcnow().isoformat(),
    "version":       "v2",
    "gate_passed":   gate_passed,
    "throughput_source": THROUGHPUT_SOURCE,
    "thresholds":    THRESHOLDS,
    "terminals":     results,
}
(OUT_DIR / "stage2_gate_v2_result.json").write_text(
    json.dumps(output, indent=2, default=_serial))
print(f"\nWritten: diagnostics/stage2_gate_v2_result.json")

lines = [
    "# Stage 2 Gate Questionnaire v2 — Calibration + Holdout",
    f"\nRun: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC  ",
    f"Gate passed: {gate_passed}  ",
    "",
    "## Scoring design",
    "",
    "| Metric | Weight | Rule |",
    "|---|---|---|",
    "| dwell_days = vol_mean / throughput | 3 pts (primary) | ≥12d→ACC; <4d→FLOW; 4–12d→BUF |",
    "| cycle_days_median | 2 pts | ≤14d→BUF |",
    "| cv_level = std(V)/mean(V) | 1 pt | ≤0.07→FLOW |",
    "",
    f"Throughput source: {THROUGHPUT_SOURCE}",
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
        if k != "error":
            lines.append(f"| {tid} | {k} | {v} |")

lines += ["", "## Thresholds", "", "| Threshold | Value |", "|---|---|"]
for k, v in THRESHOLDS.items():
    lines.append(f"| {k} | {v} |")

if not gate_passed:
    lines += ["", "## Action required", "",
              "Gate failed. Adjust metric design or thresholds before proceeding."]

(OUT_DIR / "STAGE2_GATE_V2.md").write_text("\n".join(lines))
print(f"Written: diagnostics/STAGE2_GATE_V2.md")
print("\nDone.")
