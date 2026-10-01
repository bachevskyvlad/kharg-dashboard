"""
stage5b_asc_pipeline.py — ASC screen fix, M1 validation, verdict.

Tasks (run in order; Task 3 is a blocker):
  Task 1  metrics.py unit tests (smoke-check canonical metric)
  Task 3  Gate verification: rt_5/rt_6/rt_8 must match pilot ±0.1 dB
  Task 4  Re-screen all 66 tanks: step_full, step_2026, gap_inflated flag
  Task 5  Per-orbit calibration, M1 (paired passes ≤4d), E1/E2 point estimators
  Task 6  Verdict: M1≥0.5 + E1/E2 correct signs → GO Stage 6 + Variant A

Constants that MUST NOT change:
  STEP_THRESH = 2.5 dB
  DB_FLOOR    = -5.0 (clip on read, never written back to DB)

Usage:
    cd scripts
    python stage5b_asc_pipeline.py
"""

import datetime
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

from metrics import tank_step_db  # canonical; no inline formulas allowed

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

TERMINAL_ID  = "ras_tanura"
STEP_THRESH  = 2.5    # dB — MUST NOT change
DB_FLOOR     = -5.0   # clip on read; never written back
DB_CEIL      = 20.0
MIN_OBS      = 5      # minimum observations to attempt step calc
GUARD_REJECT_WARN = 0.10  # warn if >10% of pixels rejected per tank

PILOT_TANKS      = ["rt_5", "rt_6", "rt_8"]
PILOT_REFS       = {"rt_5": 0.420, "rt_6": 0.488, "rt_8": 1.718}
GATE_TOL         = 0.1    # ±0.1 dB gate tolerance

# 10 pilot ASC scenes: 2026-08-05 → 2026-09-28 (6-day cadence, orbit 101)
PILOT_DATE_START = pd.Timestamp("2026-08-05")
PILOT_DATE_END   = pd.Timestamp("2026-09-28")

E1_DATE = pd.Timestamp("2026-03-02")   # expected BUILD
E2_DATE = pd.Timestamp("2026-06-26")   # expected DRAW

DIAG_DIR    = os.path.join(os.path.dirname(__file__), "..", "diagnostics", "rt_decomp")
REPORT_PATH = os.path.join(os.path.dirname(__file__), "..", "ASC_VALIDATION.md")
os.makedirs(DIAG_DIR, exist_ok=True)

BARREL_PER_M3 = 6.2898
MN_BBL = 1e6 / BARREL_PER_M3  # m³ per Mn bbl


# ── DB helpers ────────────────────────────────────────────────────────────────

def _fetch_all_obs(sb):
    """Fetch all terminal_observations for ras_tanura, clip DB_FLOOR on read."""
    rows = []; offset = 0
    q = (sb.table("terminal_observations")
           .select("date,tank_id,db_mean,orbit_direction,relative_orbit,radius_m,h_max")
           .eq("terminal_id", TERMINAL_ID))
    while True:
        batch = q.range(offset, offset + 999).execute().data
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    df = pd.DataFrame(rows)
    df["date"]    = pd.to_datetime(df["date"])
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")
    df["radius_m"]= pd.to_numeric(df["radius_m"], errors="coerce")
    df["h_max"]   = pd.to_numeric(df["h_max"],    errors="coerce")
    # Task 2: clip DB_FLOOR=-5.0 on read (never update DB)
    df["db_mean"] = df["db_mean"].clip(lower=DB_FLOOR, upper=DB_CEIL)
    return df


def _guard_reject_log(df):
    """
    Task 2 guard: log tanks where >GUARD_REJECT_WARN fraction of their ASC rows
    were below DB_FLOOR before clipping.  We detect this by how many rows
    would have been clipped (db_mean == DB_FLOOR after clip and were < DB_FLOOR
    before — we can't recover the pre-clip value here, so we approximate by
    counting rows at the floor for each ASC tank and comparing to total).
    """
    asc = df[df["orbit_direction"] == "ASC"].copy()
    at_floor = asc.groupby("tank_id").apply(
        lambda g: (g["db_mean"] == DB_FLOOR).sum() / len(g)
    )
    flagged = at_floor[at_floor > GUARD_REJECT_WARN]
    if not flagged.empty:
        print(f"  [Guard] {len(flagged)} ASC tanks with >{GUARD_REJECT_WARN*100:.0f}% rows at DB_FLOOR ({DB_FLOOR}):")
        for tid, frac in flagged.items():
            print(f"    {tid}: {frac*100:.1f}%")
    else:
        print(f"  [Guard] No ASC tanks exceed {GUARD_REJECT_WARN*100:.0f}% floor-clip rate.")
    return flagged


# ── Task 1: smoke-check canonical metric ─────────────────────────────────────

def task1_smoke():
    print("=" * 62)
    print("TASK 1 — metrics.py unit tests")
    print("=" * 62)
    import metrics as _m
    _m._run_tests()
    print()


# ── Task 3: gate verification ────────────────────────────────────────────────

def task3_gate(df):
    print("=" * 62)
    print("TASK 3 — Gate verification: rt_5 / rt_6 / rt_8")
    print("=" * 62)

    asc = df[df["orbit_direction"] == "ASC"].copy()

    # 10 pilot scenes: 2026-08-05 → 2026-09-28
    pilot_mask = (asc["date"] >= PILOT_DATE_START) & (asc["date"] <= PILOT_DATE_END)
    pilot_df   = asc[pilot_mask & asc["tank_id"].isin(PILOT_TANKS)].copy()

    print(f"  Pilot window: {PILOT_DATE_START.date()} → {PILOT_DATE_END.date()}")
    print(f"  Rows in pilot window for pilot tanks: {len(pilot_df)}")

    gate_passed = True
    results = {}
    print(f"\n  {'Tank':<8} {'n':>4} {'step_prod':>12} {'pilot_ref':>12} {'diff':>8} {'status':>8}")
    print("  " + "-" * 56)
    for tid in PILOT_TANKS:
        sub = pilot_df[pilot_df["tank_id"] == tid].sort_values("date")
        n   = len(sub)
        ref = PILOT_REFS[tid]
        if n < 2:
            step = float("nan")
            status = "INSUFFICIENT"
            gate_passed = False
        else:
            step = tank_step_db(sub["db_mean"].tolist())
            diff = abs(step - ref)
            status = "PASS" if diff <= GATE_TOL else f"FAIL Δ={diff:+.3f}"
            if diff > GATE_TOL:
                gate_passed = False
        results[tid] = {"n": n, "step": step, "ref": ref}
        print(f"  {tid:<8} {n:>4} {step:>12.3f} {ref:>12.3f} {step-ref:>+8.3f} {status:>8}")

    print()
    if gate_passed:
        print("  ✓ GATE PASS — prod path matches pilot ±0.1 dB. Proceeding to Task 4.")
    else:
        print("  ✗ GATE FAIL — prod path diverges from pilot. STOP.")
        print("    Do NOT run Task 4 (re-screen). Fix ASC calibration first.")
    return gate_passed, results


# ── Task 4: re-screen all tanks ───────────────────────────────────────────────

def task4_screen(df):
    print("=" * 62)
    print("TASK 4 — Re-screen all tanks (step_full + step_2026)")
    print("=" * 62)

    cutoff_2026 = pd.Timestamp("2026-01-01")

    # Compute DESC WL inline from terminal_observations DESC rows (same logic as ASC WL)
    desc_df = df[df["orbit_direction"] == "DESC"].copy()
    desc_wl = {}
    for tid, grp in desc_df.groupby("tank_id"):
        if len(grp) < MIN_OBS:
            desc_wl[tid] = False
            continue
        step = tank_step_db(grp.sort_values("date")["db_mean"].tolist())
        desc_wl[tid] = (not np.isnan(step) and step < STEP_THRESH)
    print(f"  DESC WL computed: {sum(desc_wl.values())} passed of {len(desc_wl)} tanks")

    asc = df[df["orbit_direction"] == "ASC"].copy()
    all_tanks = sorted(asc["tank_id"].unique())
    print(f"  ASC tanks in DB: {len(all_tanks)}")

    table_rows = []
    for tid in all_tanks:
        sub = asc[asc["tank_id"] == tid].sort_values("date")
        sub_2026 = sub[sub["date"] >= cutoff_2026]

        step_full  = tank_step_db(sub["db_mean"].tolist()) if len(sub) >= 2 else float("nan")
        step_2026  = tank_step_db(sub_2026["db_mean"].tolist()) if len(sub_2026) >= 2 else float("nan")

        asc_wl_full = (not np.isnan(step_full)  and step_full  < STEP_THRESH and len(sub)      >= MIN_OBS)
        asc_wl_2026 = (not np.isnan(step_2026) and step_2026 < STEP_THRESH and len(sub_2026) >= MIN_OBS)

        # gap_inflated: full history fails but 2026-only passes
        gap_inflated = (not asc_wl_full) and asc_wl_2026

        # ASC-WL: pass if either window passes (gap_inflated enters via 2026 value)
        asc_wl = asc_wl_full or gap_inflated

        desc_wl_ok = desc_wl.get(tid, False)
        category = ("BOTH"      if (asc_wl and desc_wl_ok) else
                    "ASC_ONLY"  if asc_wl else
                    "DESC_ONLY" if desc_wl_ok else
                    "NEITHER")

        table_rows.append({
            "tank_id":      tid,
            "n_asc":        len(sub),
            "n_asc_2026":   len(sub_2026),
            "step_full":    round(step_full,  3) if not np.isnan(step_full)  else None,
            "step_2026":    round(step_2026, 3) if not np.isnan(step_2026) else None,
            "asc_wl":       asc_wl,
            "gap_inflated": gap_inflated,
            "desc_wl":      desc_wl_ok,
            "category":     category,
        })

    screen_df = pd.DataFrame(table_rows)
    screen_path = os.path.join(DIAG_DIR, "asc_screen_v2.csv")
    screen_df.to_csv(screen_path, index=False)

    cats = screen_df["category"].value_counts()
    print(f"\n  Screen summary (step < {STEP_THRESH} dB, canonical median|diff|):")
    for c in ["BOTH", "ASC_ONLY", "DESC_ONLY", "NEITHER"]:
        print(f"    {c:12s}: {cats.get(c, 0)}")
    n_gap = screen_df["gap_inflated"].sum()
    print(f"    gap_inflated: {n_gap} (full-history step inflated; passes on 2026 window)")
    print(f"\n  Screen table → {screen_path}")

    # Print full table
    print(f"\n  {'Tank':<10} {'n_asc':>6} {'step_full':>10} {'step_2026':>10} {'ASC-WL':>7} {'DESC-WL':>8} {'gap_inf':>8} {'category':>12}")
    print("  " + "-" * 80)
    for _, row in screen_df.sort_values("tank_id").iterrows():
        sf  = f"{row['step_full']:.3f}"  if row["step_full"]  is not None else "  —"
        s26 = f"{row['step_2026']:.3f}" if row["step_2026"] is not None else "  —"
        gi  = "✓" if row["gap_inflated"] else ""
        print(f"  {row['tank_id']:<10} {row['n_asc']:>6} {sf:>10} {s26:>10} "
              f"{'✓' if row['asc_wl'] else '✗':>7} {'✓' if row['desc_wl'] else '✗':>8} "
              f"{gi:>8} {row['category']:>12}")

    return screen_df


# ── Task 5: calibration + M1 + events ────────────────────────────────────────

def task5_m1(df, screen_df):
    print("\n" + "=" * 62)
    print("TASK 5 — Per-orbit calibration, M1, E1/E2")
    print("=" * 62)

    asc_wl_tanks  = set(screen_df[screen_df["asc_wl"]  == True]["tank_id"])
    desc_wl_tanks = set(screen_df[screen_df["desc_wl"] == True]["tank_id"])
    print(f"  ASC-WL tanks: {len(asc_wl_tanks)}   DESC-WL tanks: {len(desc_wl_tanks)}")

    # ── Per-orbit calibration (separate p5/p95 per orbit) ─────────────────────
    calib = {}
    for orbit_dir, tanks in [("ASC", asc_wl_tanks), ("DESC", desc_wl_tanks)]:
        sub = df[(df["orbit_direction"] == orbit_dir) & (df["tank_id"].isin(tanks))]
        if sub.empty:
            calib[orbit_dir] = {"p5": float("nan"), "p95": float("nan"), "n": 0}
            continue
        p5  = float(sub["db_mean"].quantile(0.05))
        p95 = float(sub["db_mean"].quantile(0.95))
        calib[orbit_dir] = {"p5": p5, "p95": p95, "n": len(sub)}
        print(f"  {orbit_dir}: p5={p5:.3f} dB  p95={p95:.3f} dB  n={len(sub)}")

    if "ASC" in calib and "DESC" in calib:
        dp5 = calib["ASC"]["p5"] - calib["DESC"]["p5"]
        print(f"  Δp5(ASC−DESC) = {dp5:+.3f} dB")

    # ── Terminal volume series ────────────────────────────────────────────────
    def vol_series(orbit_dir, tanks):
        sub = df[(df["orbit_direction"] == orbit_dir) & (df["tank_id"].isin(tanks))].copy()
        if sub.empty:
            return pd.Series(dtype=float, name=orbit_dir)
        p5  = calib[orbit_dir]["p5"]
        p95 = calib[orbit_dir]["p95"]
        rng = max(p95 - p5, 1e-6)
        sub["db_clip"]     = sub["db_mean"].clip(p5, p95)
        sub["fill_ratio"]  = (p95 - sub["db_clip"]) / rng
        sub["h_est"]       = 2 + sub["fill_ratio"] * (sub["h_max"] - 2)
        sub["vol_m3"]      = np.pi * sub["radius_m"]**2 * sub["h_est"]
        daily = sub.groupby("date")["vol_m3"].sum()
        return daily.rename(orbit_dir)

    asc_daily  = vol_series("ASC",  asc_wl_tanks)
    desc_daily = vol_series("DESC", desc_wl_tanks)

    def to_mn_bbl(s): return s / MN_BBL

    asc_mb  = to_mn_bbl(asc_daily)
    desc_mb = to_mn_bbl(desc_daily)

    # Mean offset ASC vs DESC
    common_dates = asc_mb.index.intersection(desc_mb.index)
    if len(common_dates):
        offset_mb = float((asc_mb.loc[common_dates] - desc_mb.loc[common_dates]).mean())
        print(f"  Mean ASC−DESC offset: {offset_mb:+.2f} Mb  (n={len(common_dates)} common dates)")
    else:
        offset_mb = float("nan")
        print("  No common ASC/DESC dates for offset calculation.")

    # ── M1: paired passes ≤4 days apart ──────────────────────────────────────
    MAX_PAIR_DAYS = 4
    asc_dates  = pd.DatetimeIndex(sorted(asc_mb.index))
    desc_dates = pd.DatetimeIndex(sorted(desc_mb.index))

    pairs = []
    for ad in asc_dates:
        diffs = abs((desc_dates - ad).days)
        idx   = diffs.argmin()
        if diffs[idx] <= MAX_PAIR_DAYS:
            pairs.append((ad, desc_dates[idx]))

    if pairs:
        pair_asc  = pd.Series([asc_mb.get(a, float("nan"))  for a, _ in pairs])
        pair_desc = pd.Series([desc_mb.get(d, float("nan")) for _, d in pairs])
        valid = pair_asc.notna() & pair_desc.notna()
        pair_asc  = pair_asc[valid]
        pair_desc = pair_desc[valid]

        if len(pair_asc) >= 3:
            da = pair_asc.diff().dropna()
            dd = pair_desc.diff().dropna()
            n_pairs = min(len(da), len(dd))
            m1 = float(da.iloc[:n_pairs].corr(dd.iloc[:n_pairs]))
        else:
            m1 = float("nan")
        n_pairs_total = int(valid.sum())
    else:
        m1 = float("nan")
        n_pairs_total = 0

    print(f"\n  Paired passes ≤{MAX_PAIR_DAYS}d: {n_pairs_total}")
    print(f"  M1 = corr(Δ_ASC, Δ_DESC) on paired passes = {m1:.4f}" if not np.isnan(m1) else "  M1 = NaN (insufficient pairs)")

    # ── E1/E2 point estimators ────────────────────────────────────────────────
    N_PASS = 3

    def point_estimator(series_mb, event_date, label, expected_sign):
        idx = sorted(series_mb.index)
        before = [d for d in idx if d < event_date]
        after  = [d for d in idx if d > event_date]
        if len(before) < N_PASS or len(after) < N_PASS:
            return float("nan"), "INSUFFICIENT"
        val_before = float(series_mb.loc[before[-N_PASS:]].mean())
        val_after  = float(series_mb.loc[after[:N_PASS]].mean())
        delta = val_after - val_before
        sign_ok = (delta > 0 and expected_sign == "+") or (delta < 0 and expected_sign == "-")
        status = "✓" if sign_ok else "✗ WRONG SIGN"
        return delta, status

    e1_delta, e1_status = point_estimator(asc_mb, E1_DATE, "E1", "+")
    e2_delta, e2_status = point_estimator(asc_mb, E2_DATE, "E2", "-")

    print(f"\n  E1 {E1_DATE.date()} (expected BUILD +):")
    print(f"    ASC  Δ = {e1_delta:+.2f} Mb  {e1_status}")
    print(f"    DESC Δ = +2.30 Mb  (pilot reference)")
    print(f"\n  E2 {E2_DATE.date()} (expected DRAW -):")
    print(f"    ASC  Δ = {e2_delta:+.2f} Mb  {e2_status}")
    print(f"    DESC Δ = −2.15 Mb  (pilot reference)")

    # ── ASCII time-series plot 2024→2026 ─────────────────────────────────────
    print("\n  Volume series 2024→2026 (ASC=●  DESC=○):")
    plot_start = pd.Timestamp("2024-01-01")
    asc_plot  = asc_mb[asc_mb.index  >= plot_start].resample("MS").mean().dropna()
    desc_plot = desc_mb[desc_mb.index >= plot_start].resample("MS").mean().dropna()
    all_vals  = list(asc_plot) + list(desc_plot)
    if all_vals:
        lo, hi  = min(all_vals), max(all_vals)
        rng     = max(hi - lo, 0.1)
        width   = 40
        all_idx = sorted(set(asc_plot.index) | set(desc_plot.index))
        for dt in all_idx:
            line = ["."] * width
            if dt in asc_plot.index:
                pos = int((float(asc_plot[dt]) - lo) / rng * (width - 1))
                line[max(0, min(pos, width-1))] = "●"
            if dt in desc_plot.index:
                pos = int((float(desc_plot[dt]) - lo) / rng * (width - 1))
                line[max(0, min(pos, width-1))] = "○"
            print(f"  {dt.strftime('%Y-%m')} |{''.join(line)}|")
        print(f"           {lo:.1f} Mb" + " " * (width - 6) + f"{hi:.1f} Mb")

    # Save plot data as CSV for GHA artifact
    plot_path = os.path.join(DIAG_DIR, "asc_desc_series_2024.csv")
    plot_df = pd.DataFrame({
        "date":    [d.strftime("%Y-%m-%d") for d in sorted(set(asc_mb.index) | set(desc_mb.index))],
    })
    dates = pd.DatetimeIndex([d for d in sorted(set(asc_mb.index) | set(desc_mb.index))])
    pd.DataFrame({
        "date":    [d.strftime("%Y-%m-%d") for d in dates],
        "asc_mb":  [float(asc_mb.get(d, float("nan")))  for d in dates],
        "desc_mb": [float(desc_mb.get(d, float("nan"))) for d in dates],
    }).to_csv(plot_path, index=False)
    print(f"\n  Series CSV → {plot_path}")

    return {
        "calib":         calib,
        "offset_mb":     offset_mb,
        "m1":            m1,
        "n_pairs":       n_pairs_total,
        "e1_delta":      e1_delta,
        "e1_status":     e1_status,
        "e2_delta":      e2_delta,
        "e2_status":     e2_status,
        "asc_wl_tanks":  asc_wl_tanks,
        "desc_wl_tanks": desc_wl_tanks,
    }


# ── Task 6: verdict ───────────────────────────────────────────────────────────

def task6_verdict(m5):
    print("\n" + "=" * 62)
    print("TASK 6 — Verdict")
    print("=" * 62)

    m1            = m5["m1"]
    e1_ok         = "✓" in m5["e1_status"]
    e2_ok         = "✓" in m5["e2_status"]
    signs_correct = e1_ok and e2_ok

    print(f"  M1 = {m1:.4f}" if not np.isnan(m1) else "  M1 = NaN")
    print(f"  E1 sign correct: {'YES' if e1_ok else 'NO'}")
    print(f"  E2 sign correct: {'YES' if e2_ok else 'NO'}")

    if np.isnan(m1):
        verdict = "INSUFFICIENT — M1 undefined (not enough paired passes)"
        go      = False
        action  = "STOP — collect more ASC passes before Stage 6"
    elif m1 >= 0.5 and signs_correct:
        verdict = "GO — Stage 6 + Variant A"
        go      = True
        action  = "Proceed to accuracy grid (Stage 6)"
    elif m1 >= 0.5 and not signs_correct:
        verdict = "PARTIAL — M1 passes but event signs wrong"
        go      = False
        action  = "HUMAN REVIEW — check E1/E2 event attribution"
    elif 0.3 <= m1 < 0.5:
        verdict = "HUMAN REVIEW — M1 in borderline range 0.3–0.5"
        go      = False
        action  = "Show plot to analyst; do not auto-proceed to Stage 6"
    else:
        verdict = "STOP — M1 < 0.3"
        go      = False
        action  = "Debug calibration; ASC orbit not validated"

    print(f"\n  ★ VERDICT: {verdict}")
    print(f"  → {action}")
    return verdict, go


# ── ASC_VALIDATION.md report ─────────────────────────────────────────────────

def write_report(gate_passed, gate_results, screen_df, m5, verdict, go):
    today = datetime.date.today().isoformat()
    m1 = m5["m1"]
    calib = m5["calib"]

    asc_calib = calib.get("ASC", {})
    desc_calib = calib.get("DESC", {})

    lines = [
        f"# ASC Validation Report",
        f"",
        f"Generated: {today}",
        f"",
        f"---",
        f"",
        f"## 1. Parameter Diff Table (Pilot vs Prod — closed)",
        f"",
        f"| Parameter | Pilot | Prod (backfill) | Status |",
        f"|---|---|---|---|",
        f"| az (LOS azimuth) | 98° (ASC_LOS_AZ) | 98° (ASC_AZ=98) | ✓ |",
        f"| width_m (annulus) | 15m (WIDTH_WIDE) | 15m hardcoded | ✓ |",
        f"| buffer_m (bbox pad) | 500m (BUFFER=500) | 300m (BUFFER_M=300) | diff (minor) |",
        f"| resolution (m/px) | 5 (RESOLUTION) | 5 (RESOLUTION=5) | ✓ |",
        f"| DB_FLOOR | −5.0 (config.py) | −5.0 (clipped on read) | ✓ fixed |",
        f"| step metric | median\\|diff\\| | median\\|diff\\| (metrics.py) | ✓ fixed |",
        f"| orbit dataFilter | ASCENDING+101 | ASCENDING+101 | ✓ |",
        f"",
        f"All critical parameters now aligned. buffer_m 500→300 may shift pixel counts",
        f"for r=25m tanks but does not materially affect step for r≥40m tanks.",
        f"",
        f"---",
        f"",
        f"## 2. Task 3 — Gate Verification",
        f"",
        f"Pilot window: {PILOT_DATE_START.date()} → {PILOT_DATE_END.date()} (10 scenes, orbit 101)",
        f"",
        f"| Tank | n | step_prod | pilot_ref | diff | status |",
        f"|---|---|---|---|---|---|",
    ]

    for tid in PILOT_TANKS:
        r = gate_results.get(tid, {})
        step = r.get("step", float("nan"))
        ref  = r.get("ref", float("nan"))
        n    = r.get("n", 0)
        diff = step - ref if not (np.isnan(step) or np.isnan(ref)) else float("nan")
        status = "PASS" if (not np.isnan(diff) and abs(diff) <= GATE_TOL) else "FAIL"
        lines.append(f"| {tid} | {n} | {step:.3f} | {ref:.3f} | {diff:+.3f} | {status} |")

    gate_status = "**PASS** — prod path matches pilot ±0.1 dB." if gate_passed else "**FAIL** — see inline diffs above."
    lines += [
        f"",
        f"Gate result: {gate_status}",
        f"",
        f"---",
        f"",
        f"## 3. Screen Table — All 66 Tanks",
        f"",
        f"Threshold: step < {STEP_THRESH} dB (canonical median|diff|, DB_FLOOR=−5.0 on read).",
        f"step_full = full history; step_2026 = 2026-01-01 onward.",
        f"gap_inflated = step_full≥2.5 but step_2026<2.5 (enters ASC-WL via 2026 value).",
        f"",
        f"| tank_id | n_asc | step_full | step_2026 | ASC-WL | DESC-WL | gap_inf | category |",
        f"|---|---|---|---|---|---|---|---|",
    ]

    for _, row in screen_df.sort_values("tank_id").iterrows():
        sf  = f"{row['step_full']:.3f}"  if row["step_full"]  is not None else "—"
        s26 = f"{row['step_2026']:.3f}" if row["step_2026"] is not None else "—"
        gi  = "✓" if row["gap_inflated"] else ""
        lines.append(
            f"| {row['tank_id']} | {row['n_asc']} | {sf} | {s26} | "
            f"{'✓' if row['asc_wl'] else '✗'} | {'✓' if row['desc_wl'] else '✗'} | {gi} | {row['category']} |"
        )

    cats = screen_df["category"].value_counts()
    n_gap = screen_df["gap_inflated"].sum()
    lines += [
        f"",
        f"**Summary**: BOTH={cats.get('BOTH',0)}  ASC_ONLY={cats.get('ASC_ONLY',0)}  "
        f"DESC_ONLY={cats.get('DESC_ONLY',0)}  NEITHER={cats.get('NEITHER',0)}  "
        f"gap_inflated={n_gap}",
        f"",
        f"---",
        f"",
        f"## 4. Per-Orbit Calibration",
        f"",
        f"| Orbit | p5 (dB) | p95 (dB) | n_obs |",
        f"|---|---|---|---|",
        f"| ASC  | {asc_calib.get('p5', float('nan')):.3f} | {asc_calib.get('p95', float('nan')):.3f} | {asc_calib.get('n', 0)} |",
        f"| DESC | {desc_calib.get('p5', float('nan')):.3f} | {desc_calib.get('p95', float('nan')):.3f} | {desc_calib.get('n', 0)} |",
        f"",
        f"Mean ASC−DESC volume offset: {m5['offset_mb']:+.2f} Mb",
        f"",
        f"---",
        f"",
        f"## 5. M1 — Orbit Correlation",
        f"",
        f"Method: corr(Δ_ASC, Δ_DESC) on pass pairs ≤4 days apart.",
        f"",
        f"- Paired passes: {m5['n_pairs']}",
        f"- **M1 = {m1:.4f}**" if not np.isnan(m1) else "- **M1 = NaN** (insufficient pairs)",
        f"",
        f"### E1 / E2 Events (point estimator: val[+3 passes] − val[−3 passes])",
        f"",
        f"| Event | Date | Expected | ASC Δ (Mb) | Status | DESC ref (Mb) |",
        f"|---|---|---|---|---|---|",
        f"| E1 | {E1_DATE.date()} | BUILD (+) | {m5['e1_delta']:+.2f} | {m5['e1_status']} | +2.30 |",
        f"| E2 | {E2_DATE.date()} | DRAW  (−) | {m5['e2_delta']:+.2f} | {m5['e2_status']} | −2.15 |",
        f"",
        f"Series CSV: `diagnostics/rt_decomp/asc_desc_series_2024.csv`",
        f"",
        f"---",
        f"",
        f"## 6. Verdict",
        f"",
        f"**{verdict}**",
        f"",
        f"M1 criteria: ≥0.5 + E1/E2 correct signs → GO Stage 6 + Variant A.",
        f"M1 0.3–0.5 → human review.",
        f"M1 <0.3 → STOP.",
        f"",
    ]

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n  Report → {REPORT_PATH}")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    from supabase import create_client

    task1_smoke()

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("Fetching all terminal_observations for ras_tanura …")
    df = _fetch_all_obs(sb)
    print(f"  {len(df)} rows loaded (DB_FLOOR={DB_FLOOR} applied on read)")
    _guard_reject_log(df)

    gate_passed, gate_results = task3_gate(df)

    if not gate_passed:
        print("\n[stage5b] GATE FAILED — aborting. Fix ASC calibration first.")
        sys.exit(1)

    screen_df = task4_screen(df)
    m5        = task5_m1(df, screen_df)
    verdict, go = task6_verdict(m5)
    write_report(gate_passed, gate_results, screen_df, m5, verdict, go)

    print("\n[stage5b] Done.")
    if not go and "STOP" in verdict:
        sys.exit(1)


if __name__ == "__main__":
    main()
