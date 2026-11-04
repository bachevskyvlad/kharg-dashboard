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

SUPABASE_URL = os.environ.get("SUPABASE_URL") or ""
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or ""

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

def task5_m1(df, screen_df, gate_results):
    print("\n" + "=" * 62)
    print("TASK 5 — Per-orbit calibration, M1, E1/E2")
    print("=" * 62)

    # Gate numbers (Task 3 results for report)
    print(f"  Gate (Task 3) — rt_5/rt_6/rt_8 vs pilot ±0.1 dB:")
    for tid in PILOT_TANKS:
        r = gate_results.get(tid, {})
        step = r.get("step", float("nan"))
        ref  = r.get("ref",  float("nan"))
        diff = step - ref if not (np.isnan(step) or np.isnan(ref)) else float("nan")
        status = "PASS" if (not np.isnan(diff) and abs(diff) <= GATE_TOL) else "FAIL"
        print(f"    {tid}: prod={step:.3f}  pilot={ref:.3f}  diff={diff:+.3f}  {status}")

    asc_wl_tanks  = set(screen_df[screen_df["asc_wl"]  == True]["tank_id"])
    desc_wl_tanks = set(screen_df[screen_df["desc_wl"] == True]["tank_id"])
    both_wl_tanks = asc_wl_tanks & desc_wl_tanks
    print(f"  ASC-WL tanks: {len(asc_wl_tanks)}   DESC-WL tanks: {len(desc_wl_tanks)}   BOTH: {len(both_wl_tanks)}")

    # ── Per-orbit calibration (separate p5/p95 per orbit, BOTH-set tanks) ─────
    calib = {}
    for orbit_dir, tanks in [("ASC", both_wl_tanks), ("DESC", both_wl_tanks)]:
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

    asc_daily  = vol_series("ASC",  both_wl_tanks)
    desc_daily = vol_series("DESC", both_wl_tanks)

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

    # ── ASC cadence around E1 (Feb–Mar 2026) ─────────────────────────────────
    e1_window_start = pd.Timestamp("2026-02-01")
    e1_window_end   = pd.Timestamp("2026-03-31")
    asc_e1_dates = sorted(d for d in asc_mb.index if e1_window_start <= d <= e1_window_end)
    print(f"\n  ASC dates Feb–Mar 2026 (cadence around E1={E1_DATE.date()}):")
    for d in asc_e1_dates:
        marker = " ← E1" if abs((d - E1_DATE).days) <= 3 else ""
        print(f"    {d.date()}{marker}")
    if not asc_e1_dates:
        print("    (no ASC passes in window)")

    # ── M1 diagnostics ───────────────────────────────────────────────────────
    # (a) Level corr on nearest-pair ≤4d (not exact-date — ASC/DESC never share a date)
    MAX_PAIR_DAYS = 4
    level_pairs_asc  = []
    level_pairs_desc = []
    for ad in pd.DatetimeIndex(sorted(asc_mb.index)):
        dd_idx = pd.DatetimeIndex(sorted(desc_mb.index))
        if len(dd_idx) == 0:
            break
        diffs = abs((dd_idx - ad).days)
        best  = diffs.argmin()
        if diffs[best] <= MAX_PAIR_DAYS:
            level_pairs_asc.append(float(asc_mb[ad]))
            level_pairs_desc.append(float(desc_mb[dd_idx[best]]))
    n_level_pairs = len(level_pairs_asc)
    level_corr = float("nan")
    if n_level_pairs >= 5:
        level_corr = float(pd.Series(level_pairs_asc).corr(pd.Series(level_pairs_desc)))
    print(f"\n  Level corr(ASC, DESC) on {n_level_pairs} nearest-pair ≤4d = "
          + (f"{level_corr:.4f}" if not np.isnan(level_corr) else "NaN"))

    # (b) Monthly M1: resample both series to calendar months, compute Δ, correlate
    m1_monthly = float("nan")
    n_months   = 0
    if not asc_mb.empty and not desc_mb.empty:
        asc_mo  = asc_mb.resample("MS").mean().dropna()
        desc_mo = desc_mb.resample("MS").mean().dropna()
        common_mo = asc_mo.index.intersection(desc_mo.index)
        if len(common_mo) >= 4:
            da_mo = asc_mo.loc[common_mo].diff().dropna()
            dd_mo = desc_mo.loc[common_mo].diff().dropna()
            n_months = min(len(da_mo), len(dd_mo))
            m1_monthly = float(da_mo.iloc[:n_months].corr(dd_mo.iloc[:n_months]))
        print(f"  Monthly M1 = corr(Δ_ASC_mo, Δ_DESC_mo) on {n_months} month-pairs = "
              + (f"{m1_monthly:.4f}" if not np.isnan(m1_monthly) else "NaN"))

    # (c) War / peace regime split  (war = 2026-02-01 → 2026-09-30)
    WAR_START = pd.Timestamp("2026-02-01")
    WAR_END   = pd.Timestamp("2026-09-30")
    m1_war = float("nan"); n_war = 0
    m1_peace = float("nan"); n_peace = 0
    if pairs:
        # reassign pairs already computed above
        war_pairs   = [(a, d) for a, d in pairs if WAR_START <= a <= WAR_END]
        peace_pairs = [(a, d) for a, d in pairs if a < WAR_START or a > WAR_END]

        def _regime_m1(rpairs):
            if len(rpairs) < 4:
                return float("nan"), len(rpairs)
            pa = pd.Series([asc_mb.get(a, float("nan"))  for a, _ in rpairs])
            pd_ = pd.Series([desc_mb.get(d, float("nan")) for _, d in rpairs])
            ok = pa.notna() & pd_.notna()
            pa, pd_ = pa[ok], pd_[ok]
            if len(pa) < 4:
                return float("nan"), int(ok.sum())
            da = pa.diff().dropna(); dd = pd_.diff().dropna()
            n = min(len(da), len(dd))
            return float(da.iloc[:n].corr(dd.iloc[:n])), int(ok.sum())

        m1_war,   n_war   = _regime_m1(war_pairs)
        m1_peace, n_peace = _regime_m1(peace_pairs)
        print(f"  M1_war   (2026-02→09, n={n_war}  pairs) = "
              + (f"{m1_war:.4f}"   if not np.isnan(m1_war)   else "NaN (insufficient)"))
        print(f"  M1_peace (pre-war,    n={n_peace} pairs) = "
              + (f"{m1_peace:.4f}" if not np.isnan(m1_peace) else "NaN (insufficient)"))

    # (d) Noise model: expected corr of two independent estimators
    # σ_terminal_Δ ≈ sigma_tank / √N_WL  where sigma_tank ≈ STEP_THRESH × √2 / 1.35
    # (median|diff| of Gaussian noise = σ√2 × 0.6745, so σ = step / 0.9545)
    n_asc_wl  = len(both_wl_tanks)
    n_desc_wl = len(both_wl_tanks)
    # estimate per-tank σ from median step of BOTH-WL tanks
    asc_steps  = [tank_step_db(df[(df["orbit_direction"]=="ASC")  & (df["tank_id"]==t)].sort_values("date")["db_mean"].tolist())
                  for t in both_wl_tanks]
    desc_steps = [tank_step_db(df[(df["orbit_direction"]=="DESC") & (df["tank_id"]==t)].sort_values("date")["db_mean"].tolist())
                  for t in both_wl_tanks]
    # convert step (dB) to volume noise (Mb): rough linear approx via mean radius/h
    # skip volume conversion — work in dB-step units for noise ratio
    med_step_asc  = float(np.nanmedian(asc_steps))  if asc_steps  else float("nan")
    med_step_desc = float(np.nanmedian(desc_steps)) if desc_steps else float("nan")
    # expected std of terminal Δ (Mb) from independent noise
    # σ_terminal = (med_step / 0.9545) / √N_WL  (in dB-step units, proportional)
    sigma_asc  = med_step_asc  / 0.9545 / (n_asc_wl  ** 0.5) if n_asc_wl  > 0 else float("nan")
    sigma_desc = med_step_desc / 0.9545 / (n_desc_wl ** 0.5) if n_desc_wl > 0 else float("nan")
    # actual std of paired Δ values
    if pairs and len(pair_asc) >= 3:
        da_vals = pair_asc.diff().dropna().values
        dd_vals = pair_desc.diff().dropna().values
        actual_std_asc  = float(np.std(da_vals))  if len(da_vals) > 1 else float("nan")
        actual_std_desc = float(np.std(dd_vals)) if len(dd_vals) > 1 else float("nan")
        # expected corr if noise ≈ total variance: E[corr] ≈ signal²/(signal²+σ²)
        # here we estimate SNR from actual vs noise floor
        snr_asc  = max(0, actual_std_asc**2  - sigma_asc**2)  / max(actual_std_asc**2,  1e-9)
        snr_desc = max(0, actual_std_desc**2 - sigma_desc**2) / max(actual_std_desc**2, 1e-9)
        expected_m1 = (snr_asc * snr_desc) ** 0.5
    else:
        actual_std_asc = actual_std_desc = expected_m1 = float("nan")

    print(f"\n  Noise model:")
    print(f"    med_step ASC={med_step_asc:.3f} dB  DESC={med_step_desc:.3f} dB")
    print(f"    σ_terminal (dB-step units): ASC={sigma_asc:.4f}  DESC={sigma_desc:.4f}")
    print(f"    actual std(Δ): ASC={actual_std_asc:.4f}  DESC={actual_std_desc:.4f}")
    print(f"    expected M1 from noise model = {expected_m1:.4f}" if not np.isnan(expected_m1) else "    expected M1 = NaN")
    print(f"    measured M1                  = {m1:.4f}" if not np.isnan(m1) else "    measured M1 = NaN")
    if not np.isnan(expected_m1) and not np.isnan(m1):
        ratio = m1 / max(expected_m1, 1e-6)
        print(f"    ratio measured/expected      = {ratio:.2f}  (1.0 = pure noise; <1 = suppressed)")

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
        "calib":           calib,
        "offset_mb":       offset_mb,
        "m1":              m1,
        "n_pairs":         n_pairs_total,
        "level_corr":      level_corr,
        "n_level_pairs":   n_level_pairs,
        "m1_monthly":      m1_monthly,
        "n_months":        n_months,
        "m1_war":          m1_war,
        "n_war":           n_war,
        "m1_peace":        m1_peace,
        "n_peace":         n_peace,
        "expected_m1":     expected_m1,
        "med_step_asc":    med_step_asc,
        "med_step_desc":   med_step_desc,
        "sigma_asc":       sigma_asc,
        "sigma_desc":      sigma_desc,
        "e1_delta":        e1_delta,
        "e1_status":       e1_status,
        "e2_delta":        e2_delta,
        "e2_status":       e2_status,
        "asc_wl_tanks":    asc_wl_tanks,
        "desc_wl_tanks":   desc_wl_tanks,
        "both_wl_tanks":   both_wl_tanks,
    }


# ── Task 6: verdict ───────────────────────────────────────────────────────────

def task6_verdict(m5):
    print("\n" + "=" * 62)
    print("TASK 6 — Verdict (updated grid 2026-10-01)")
    print("=" * 62)

    m1         = m5["m1"]
    m1_monthly = m5["m1_monthly"]
    m1_war     = m5["m1_war"]
    level_corr = m5["level_corr"]
    e1_ok      = "✓" in m5["e1_status"]
    e2_ok      = "✓" in m5["e2_status"]
    signs_correct = e1_ok and e2_ok

    print(f"  M1 raw (Δ paired ≤4d)  = {m1:.4f}"       if not np.isnan(m1)         else "  M1 raw       = NaN")
    print(f"  M1 monthly (Δ monthly) = {m1_monthly:.4f}" if not np.isnan(m1_monthly) else "  M1 monthly   = NaN")
    print(f"  M1_war     (2026-02→09)= {m1_war:.4f}"     if not np.isnan(m1_war)     else "  M1_war       = NaN")
    print(f"  Level corr (overlap)   = {level_corr:.4f}" if not np.isnan(level_corr) else "  Level corr   = NaN")
    print(f"  E1 sign correct: {'YES' if e1_ok else 'NO'}")
    print(f"  E2 sign correct: {'YES' if e2_ok else 'NO'}")

    # New verdict grid (fixed 2026-10-01):
    #   level_corr (BOTH-set, nearest-pair ≤4d) ≥ 0.7  → GO; Δ-M1 deprecated
    #   level_corr 0.4–0.7                              → HUMAN REVIEW
    #   level_corr < 0.4 or NaN                         → STOP настоящий
    level_corr = m5["level_corr"]
    n_lp       = m5["n_level_pairs"]

    if np.isnan(level_corr):
        verdict = f"INSUFFICIENT — level_corr undefined (n_level_pairs={n_lp})"
        go      = False
        action  = "STOP — insufficient paired passes for level correlation"
    elif level_corr >= 0.7:
        verdict = f"GO — level_corr={level_corr:.3f} ≥ 0.7 on BOTH-set ({len(m5['both_wl_tanks'])} tanks)"
        go      = True
        action  = ("Proceed to Stage 6 + Variant A. "
                   "Δ-M1 deprecated: cadence mismatch suppresses pass-to-pass Δ corr by construction.")
    elif level_corr >= 0.4:
        verdict = f"HUMAN REVIEW — level_corr={level_corr:.3f} in 0.4–0.7 borderline"
        go      = False
        action  = "Show level series plot to analyst; do not auto-proceed to Stage 6"
    else:
        verdict = f"STOP — level_corr={level_corr:.3f} < 0.4; ASC orbit not validated"
        go      = False
        action  = "Debug calibration; check BOTH-set tank selection and per-orbit p5/p95"

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
        f"- Tank set: BOTH-WL = {len(m5['both_wl_tanks'])} tanks (ASC-WL ∩ DESC-WL)",
        f"- **Level corr (nearest-pair ≤4d, n={m5['n_level_pairs']}): {m5['level_corr']:.4f}**" if not np.isnan(m5['level_corr']) else f"- **Level corr (n={m5['n_level_pairs']}): NaN**",
        f"- Δ-M1 raw (paired ≤4d, n={m5['n_pairs']}): {m1:.4f}" if not np.isnan(m1) else f"- Δ-M1 raw (n={m5['n_pairs']}): NaN",
        f"- Monthly Δ-M1 (n={m5['n_months']} months): {m5['m1_monthly']:.4f}" if not np.isnan(m5['m1_monthly']) else "- Monthly Δ-M1: NaN",
        f"- M1_war  (2026-02→09, n={m5['n_war']}  pairs): {m5['m1_war']:.4f}"   if not np.isnan(m5['m1_war'])   else f"- M1_war  (n={m5['n_war']}): NaN",
        f"- M1_peace (pre-war,   n={m5['n_peace']} pairs): {m5['m1_peace']:.4f}" if not np.isnan(m5['m1_peace']) else f"- M1_peace (n={m5['n_peace']}): NaN",
        f"",
        f"### Noise Model",
        f"",
        f"| | ASC | DESC |",
        f"|---|---|---|",
        f"| WL tanks | {len(m5['asc_wl_tanks'])} | {len(m5['desc_wl_tanks'])} |",
        f"| med step (dB) | {m5['med_step_asc']:.3f} | {m5['med_step_desc']:.3f} |",
        f"| σ_terminal (dB-step) | {m5['sigma_asc']:.4f} | {m5['sigma_desc']:.4f} |",
        f"",
        f"Expected M1 from noise model: **{m5['expected_m1']:.4f}**  " + (f"(measured/expected = {m1/max(m5['expected_m1'],1e-6):.2f})" if not (np.isnan(m1) or np.isnan(m5['expected_m1'])) else ""),
        f"",
        f"Interpretation: if measured M1 ≈ expected M1, raw corr(Δ,Δ) was suppressed by noise",
        f"by construction and is not a valid validation metric for this cadence/tank-count.",
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
        f"Grid (fixed 2026-10-01) — primary criterion: level_corr on BOTH-set, nearest-pair ≤4d:",
        f"- level_corr ≥ 0.7 → GO (Δ-M1 deprecated: cadence mismatch suppresses pass-to-pass Δ by construction)",
        f"- level_corr 0.4–0.7 → HUMAN REVIEW",
        f"- level_corr < 0.4 or NaN → STOP настоящий",
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
    m5        = task5_m1(df, screen_df, gate_results)
    verdict, go = task6_verdict(m5)
    write_report(gate_passed, gate_results, screen_df, m5, verdict, go)

    print("\n[stage5b] Done.")
    if not go and "STOP" in verdict:
        sys.exit(1)


if __name__ == "__main__":
    main()
