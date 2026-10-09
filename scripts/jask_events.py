"""
jask_events.py — Stage 3 events for Jask with placebo p90 significance gate.

Event definition (Export Pulse Score canon):
  Triggered when vol[t0] - vol[t1] >= DRAWDOWN_THRESHOLD_MBL
  Where 6 <= (t1 - t0 in days) <= 12

Placebo p90: sample 20 random peacetime dates (away from war_2026 and resume_2025
windows), run the same event detection, take p90 of detected Δvol as the threshold.
An event is reported only if its Δvol exceeds placebo p90.

Named event windows:
  war_2026:     2026-03-01 → 2026-06-30  (also treated as event in its own right)
  resume_2025:  2025-06-01 → 2025-10-31

Output:
  diagnostics/jask/jask_events.json
  diagnostics/jask/jask_events.md  (for human review)

Usage:
  python jask_events.py [--out diagnostics/jask/jask_events.json]
"""

import json
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from config import sb

TERMINAL_ID     = "jask"
ORBIT_REL       = 93
OUT_DIR         = Path(__file__).parent.parent / "diagnostics" / "jask"
N_PLACEBO_DATES = 20
BARREL_PER_M3   = 6.2898
TANK_HEIGHT_MIN = 2

# Event detection parameters
DRAW_THRESH_MBL = 2.0   # Mn bbl minimum drawdown to trigger event
WINDOW_MIN_DAYS = 6
WINDOW_MAX_DAYS = 12

# Named windows to exclude from placebo (keep them clean for event eval)
NAMED_WINDOWS = {
    "war_2026":    ("2026-03-01", "2026-06-30"),
    "resume_2025": ("2025-06-01", "2025-10-31"),
}

PLACEBO_EXCLUDE_BUFFER_DAYS = 30  # buffer around named windows for placebo

# ─── fetch ────────────────────────────────────────────────────────────────────

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


def load_jask_obs():
    rows = fetch_all("terminal_observations",
                     "date,tank_id,db_mean",
                     filters={"terminal_id": TERMINAL_ID, "relative_orbit": ORBIT_REL})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["db_mean"])
    return df


def load_jask_registry():
    """Return list of active (non-uncertain) tanks with geometry for volume calc."""
    sys.path.insert(0, str(Path(__file__).parent))
    import tank_registry as tr
    jask = next(t for t in tr.LOCATIONS if t["id"] == TERMINAL_ID)
    active = [t for t in jask["tanks"] if not t.get("uncertain", False)]
    return active


# ─── volume ───────────────────────────────────────────────────────────────────

def compute_volume(df: pd.DataFrame, tanks: list) -> pd.Series:
    """Compute per-date total volume using canonical formula.

    fill_ratio = (dB_max - db_mean_clipped) / (dB_max - dB_min)
    h_current  = TANK_HEIGHT_MIN + fill_ratio * (h_max - TANK_HEIGHT_MIN)
    vol_bbl    = π × r² × h_current × BARREL_PER_M3
    """
    dB_min = float(df["db_mean"].quantile(0.05))
    dB_max = float(df["db_mean"].quantile(0.95))
    if dB_max <= dB_min:
        dB_max = dB_min + 1.0

    tank_map = {t["id"]: t for t in tanks}

    rows = []
    for date, grp in df.groupby("date"):
        vol_total = 0.0
        for _, row in grp.iterrows():
            tank = tank_map.get(row["tank_id"])
            if tank is None:
                continue
            db_c   = float(np.clip(row["db_mean"], dB_min, dB_max))
            fr     = (dB_max - db_c) / (dB_max - dB_min)
            h      = TANK_HEIGHT_MIN + fr * (tank["h_max_m"] - TANK_HEIGHT_MIN)
            vol_m3 = math.pi * tank["radius_m"]**2 * h
            vol_total += vol_m3 * BARREL_PER_M3 / 1e6  # Mn bbl
        rows.append({"date": date, "vol_mn_bbl": vol_total})
    return pd.DataFrame(rows).set_index("date")["vol_mn_bbl"].sort_index()


# ─── event detection ──────────────────────────────────────────────────────────

def detect_events(vol: pd.Series, min_drawdown=DRAW_THRESH_MBL,
                  win_min=WINDOW_MIN_DAYS, win_max=WINDOW_MAX_DAYS) -> list:
    """Return list of {t0, t1, delta_vol, span_days} for each triggered event."""
    events = []
    dates = vol.index.tolist()
    for i, t0 in enumerate(dates):
        for t1 in dates[i+1:]:
            span = (t1 - t0).days
            if span < win_min:
                continue
            if span > win_max:
                break
            delta = float(vol[t0] - vol[t1])
            if delta >= min_drawdown:
                events.append({"t0": str(t0.date()), "t1": str(t1.date()),
                               "delta_vol_mn_bbl": round(delta, 3),
                               "span_days": span})
    return events


def placebo_p90(vol: pd.Series) -> float:
    """Sample 20 random peacetime dates, run event detection, return p90 of Δvol."""
    all_dates = vol.index.tolist()

    # Build exclude set: named windows + buffer
    exclude = set()
    for name, (s, e) in NAMED_WINDOWS.items():
        sd = pd.Timestamp(s) - pd.Timedelta(days=PLACEBO_EXCLUDE_BUFFER_DAYS)
        ed = pd.Timestamp(e) + pd.Timedelta(days=PLACEBO_EXCLUDE_BUFFER_DAYS)
        exclude.update([d for d in all_dates if sd <= d <= ed])

    peaceful = [d for d in all_dates if d not in exclude]
    if len(peaceful) < N_PLACEBO_DATES:
        print(f"  [placebo] Only {len(peaceful)} peaceful dates; using all")
        sample_dates = peaceful
    else:
        random.seed(42)
        sample_dates = sorted(random.sample(peaceful, N_PLACEBO_DATES))

    print(f"  [placebo] {len(sample_dates)} peacetime dates: "
          f"{sample_dates[0].date()} → {sample_dates[-1].date()}")

    # For each sampled t0, find all t1 within window and record drawdowns
    all_deltas = []
    for i, t0 in enumerate(sample_dates):
        for t1 in sample_dates[i+1:]:
            span = (t1 - t0).days
            if span < WINDOW_MIN_DAYS: continue
            if span > WINDOW_MAX_DAYS: break
            delta = float(vol[t0] - vol[t1]) if t0 in vol.index and t1 in vol.index else 0
            if delta > 0:
                all_deltas.append(delta)

    if not all_deltas:
        print("  [placebo] No positive drawdowns in placebo set — p90 = 0.0")
        return 0.0

    p90 = float(np.percentile(all_deltas, 90))
    print(f"  [placebo] {len(all_deltas)} drawdown pairs; p90 = {p90:.3f} Mn bbl")
    return p90


# ─── named event analysis ─────────────────────────────────────────────────────

def eval_named_events(vol: pd.Series, p90_threshold: float) -> list:
    """Evaluate war_2026 and resume_2025 as events (not polarity anchors)."""
    results = []
    for name, (start_s, end_s) in NAMED_WINDOWS.items():
        sd, ed = pd.Timestamp(start_s), pd.Timestamp(end_s)
        window_vol = vol[(vol.index >= sd) & (vol.index <= ed)]
        if len(window_vol) < 2:
            results.append({
                "name": name, "window": f"{start_s} → {end_s}",
                "n_passes": len(window_vol),
                "delta_vol_mn_bbl": None, "p90": p90_threshold,
                "verdict": "INSUFFICIENT_DATA",
            })
            continue

        # Largest drawdown within window
        max_delta = float(window_vol.iloc[0] - window_vol.min())
        verdict = "PULSE" if max_delta >= p90_threshold else "BELOW_P90"
        results.append({
            "name": name,
            "window": f"{start_s} → {end_s}",
            "n_passes": len(window_vol),
            "delta_vol_mn_bbl": round(max_delta, 3),
            "p90": round(p90_threshold, 3),
            "verdict": verdict,
        })
    return results


# ─── main ─────────────────────────────────────────────────────────────────────

import math  # for vol calc

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Jask events with placebo p90")
    parser.add_argument("--out", default=str(OUT_DIR / "jask_events.json"))
    args = parser.parse_args()

    print("Loading Jask observations...")
    df = load_jask_obs()
    print(f"  {len(df):,} rows, {df['date'].nunique()} dates, {df['tank_id'].nunique()} tanks")

    print("Loading registry (active tanks only)...")
    tanks = load_jask_registry()
    print(f"  {len(tanks)} active tanks (non-uncertain)")

    print("Computing volume series...")
    vol = compute_volume(df, tanks)
    print(f"  {len(vol)} dates; vol range: [{vol.min():.3f}, {vol.max():.3f}] Mn bbl")

    print("\nComputing placebo p90...")
    p90 = placebo_p90(vol)

    print("\nDetecting all events (canon threshold 2.0 Mn bbl, window 6-12d)...")
    all_events = detect_events(vol)
    gated_events = [e for e in all_events if e["delta_vol_mn_bbl"] >= p90]
    print(f"  All events: {len(all_events)}  |  After placebo p90 gate: {len(gated_events)}")

    print("\nEvaluating named windows as events...")
    named = eval_named_events(vol, p90)
    for ev in named:
        print(f"  {ev['name']}: Δvol={ev['delta_vol_mn_bbl']}  p90={ev['p90']}  verdict={ev['verdict']}")

    result = {
        "terminal":         TERMINAL_ID,
        "orbit":            ORBIT_REL,
        "n_dates":          int(len(vol)),
        "vol_range_mn_bbl": [round(float(vol.min()), 3), round(float(vol.max()), 3)],
        "active_tanks":     len(tanks),
        "placebo_p90":      round(p90, 3),
        "n_events_raw":     len(all_events),
        "n_events_gated":   len(gated_events),
        "events_gated":     gated_events,
        "named_events":     named,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    print(f"\nEvents written to {out_path}")

    # Markdown summary
    md_path = out_path.with_suffix(".md")
    lines = [
        "# Jask Events — Placebo P90 Gate",
        "",
        f"Terminal: {TERMINAL_ID} | Orbit: rel={ORBIT_REL} DESC | Active tanks: {len(tanks)}",
        f"Volume range: {vol.min():.3f} → {vol.max():.3f} Mn bbl",
        f"Placebo p90: **{round(p90, 3)} Mn bbl** (N={N_PLACEBO_DATES} peaceful dates)",
        "",
        f"Raw events (≥2.0 Mn bbl in 6–12d): {len(all_events)}",
        f"Events after placebo p90 gate: **{len(gated_events)}**",
        "",
        "## Named Window Events",
        "",
        "| Window | Period | Passes | Δvol | p90 | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    for ev in named:
        dv = f"{ev['delta_vol_mn_bbl']:.3f}" if ev['delta_vol_mn_bbl'] is not None else "n/a"
        lines.append(f"| {ev['name']} | {ev['window']} | {ev['n_passes']} | "
                     f"{dv} Mn bbl | {ev['p90']:.3f} | **{ev['verdict']}** |")
    lines += ["", "## Gated Events (≥ placebo p90)", ""]
    if gated_events:
        lines += ["| t0 | t1 | Δvol (Mn bbl) | Span (days) |",
                  "|---|---|---|---|"]
        for ev in sorted(gated_events, key=lambda x: x["delta_vol_mn_bbl"], reverse=True):
            lines.append(f"| {ev['t0']} | {ev['t1']} | {ev['delta_vol_mn_bbl']:.3f} | {ev['span_days']} |")
    else:
        lines.append("*No events exceed placebo p90.*")
    lines += [
        "",
        "## Notes",
        "",
        "- Polarity assumed direct (lower dB = more oil) — see JASK_POLARITY.md",
        "- war_2026 and resume_2025 evaluated as events, not polarity anchors",
        "- All event verdicts pending human review of panel images",
    ]
    md_path.write_text("\n".join(lines) + "\n")
    print(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
