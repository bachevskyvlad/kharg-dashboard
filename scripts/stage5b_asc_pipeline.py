"""
stage5b_asc_pipeline.py — Items 2-4 of the RT/JU accuracy iteration.

Item 2: Dump ALL ASC rows from terminal_observations to CSV → backups/
Item 3: ASC screen of all 66 tanks (step<2.5 threshold), table + histogram
Item 4: Per-orbit calibration + M1 verification (corr of monthly deltas)

Usage:
    cd scripts
    python stage5b_asc_pipeline.py            # run all items
    python stage5b_asc_pipeline.py --item 2   # single item
"""

import argparse
import csv
import datetime
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

TERMINAL_ID   = "ras_tanura"
STEP_THRESH   = 2.5   # dB: WL threshold for ASC/DESC screen
DB_FLOOR      = -20.0
DB_CEIL       = 20.0

BACKUPS_DIR = os.path.join(os.path.dirname(__file__), "..", "backups")
DIAG_DIR    = os.path.join(os.path.dirname(__file__), "..", "diagnostics", "rt_decomp")
os.makedirs(BACKUPS_DIR, exist_ok=True)
os.makedirs(DIAG_DIR, exist_ok=True)

parser = argparse.ArgumentParser()
parser.add_argument("--item", type=int, default=None, help="Run only item N (2, 3, or 4)")
args = parser.parse_args()

RUN_ITEMS = {2, 3, 4} if args.item is None else {args.item}


# ── helpers ──────────────────────────────────────────────────────────────────

def _fetch_all(sb, table, **eq_filters):
    rows = []; offset = 0
    q = sb.table(table).select("*")
    for col, val in eq_filters.items():
        q = q.eq(col, val)
    while True:
        batch = q.range(offset, offset + 999).execute().data
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


# ── Item 2: CSV backup ────────────────────────────────────────────────────────

def item2_backup(sb):
    print("\n[Item 2] Backing up ALL ASC rows → backups/")
    rows = []
    offset = 0
    q = (sb.table("terminal_observations")
           .select("*")
           .eq("terminal_id", TERMINAL_ID)
           .eq("orbit_direction", "ASC"))
    while True:
        batch = q.range(offset, offset + 999).execute().data
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000

    if not rows:
        print("  No ASC rows found.")
        return

    today = datetime.date.today().isoformat()
    path = os.path.join(BACKUPS_DIR, f"asc_backup_{today}.csv")
    keys = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"  {len(rows)} ASC rows → {path}")
    return path


# ── Item 3: ASC screen ────────────────────────────────────────────────────────

def item3_screen(sb):
    print("\n[Item 3] ASC screen — step<2.5 dB threshold")

    # Fetch all RT observations
    all_rows = _fetch_all(sb, "terminal_observations", terminal_id=TERMINAL_ID)
    if not all_rows:
        print("  No rows in terminal_observations for ras_tanura.")
        return

    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["date"])
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")
    df = df[df["db_mean"].between(DB_FLOOR, DB_CEIL)]

    def wl_passed(grp):
        """True if step (p95-p5) < STEP_THRESH for this tank+orbit."""
        if len(grp) < 5:
            return False
        return (grp["db_mean"].quantile(0.95) - grp["db_mean"].quantile(0.05)) < STEP_THRESH

    # Per-tank per-direction WL test
    results = {}
    for (tank_id, orbit_dir), grp in df.groupby(["tank_id", "orbit_direction"]):
        results[(tank_id, orbit_dir)] = wl_passed(grp)

    all_tanks = sorted(df["tank_id"].unique())
    table_rows = []
    for tank in all_tanks:
        asc_ok  = results.get((tank, "ASC"),  False)
        desc_ok = results.get((tank, "DESC"), False)
        n_asc   = len(df[(df["tank_id"] == tank) & (df["orbit_direction"] == "ASC")])
        n_desc  = len(df[(df["tank_id"] == tank) & (df["orbit_direction"] == "DESC")])
        category = ("BOTH" if (asc_ok and desc_ok) else
                    "ASC_ONLY" if asc_ok else
                    "DESC_ONLY" if desc_ok else
                    "NEITHER")
        table_rows.append({
            "tank_id": tank, "asc_wl": asc_ok, "desc_wl": desc_ok,
            "n_asc": n_asc, "n_desc": n_desc, "category": category,
        })

    screen_df = pd.DataFrame(table_rows)
    screen_path = os.path.join(DIAG_DIR, "asc_screen.csv")
    screen_df.to_csv(screen_path, index=False)
    print(f"  Screen table → {screen_path}")

    # Summary
    cats = screen_df["category"].value_counts()
    print(f"\n  WL screen summary (step<{STEP_THRESH} dB):")
    for c in ["BOTH", "ASC_ONLY", "DESC_ONLY", "NEITHER"]:
        print(f"    {c:12s}: {cats.get(c, 0)}")

    # Histogram: ASC scenes by year
    asc_df = df[df["orbit_direction"] == "ASC"].copy()
    asc_df["year"] = asc_df["date"].dt.year
    hist = (asc_df.groupby("year")["date"]
                  .nunique()
                  .sort_index()
                  .rename("n_scenes"))
    print(f"\n  ASC scenes by year:")
    for yr, n in hist.items():
        bar = "█" * (n // 5)
        print(f"    {yr}: {n:4d}  {bar}")

    hist_path = os.path.join(DIAG_DIR, "asc_scenes_by_year.csv")
    hist.to_csv(hist_path, header=True)
    print(f"  Histogram → {hist_path}")

    return screen_df


# ── Item 4: per-orbit calibration + M1 ───────────────────────────────────────

def item4_calibration_m1(sb, screen_df=None):
    print("\n[Item 4] Per-orbit calibration + M1 verification")

    all_rows = _fetch_all(sb, "terminal_observations", terminal_id=TERMINAL_ID)
    if not all_rows:
        print("  No rows.")
        return

    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["date"])
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")
    df = df[df["db_mean"].between(DB_FLOOR, DB_CEIL)]

    # Per-orbit percentile calibration
    report = {}
    for orbit_dir, grp in df.groupby("orbit_direction"):
        p5  = grp["db_mean"].quantile(0.05)
        p95 = grp["db_mean"].quantile(0.95)
        report[orbit_dir] = {"p5": p5, "p95": p95, "n_obs": len(grp)}
        print(f"  {orbit_dir}: p5={p5:.2f} dB  p95={p95:.2f} dB  n={len(grp)}")

    if "ASC" in report and "DESC" in report:
        delta_db = abs(report["ASC"]["p5"] - report["DESC"]["p5"])
        print(f"  Δ(p5) ASC-DESC: {delta_db:.2f} dB")

    # Determine ASC-WL tanks
    if screen_df is not None:
        asc_wl_tanks = set(screen_df[screen_df["asc_wl"] == True]["tank_id"])
    else:
        # recompute from data
        asc_wl_tanks = set()
        for (tank_id, od), grp in df.groupby(["tank_id", "orbit_direction"]):
            if od != "ASC":
                continue
            if len(grp) >= 5:
                step = grp["db_mean"].quantile(0.95) - grp["db_mean"].quantile(0.05)
                if step < STEP_THRESH:
                    asc_wl_tanks.add(tank_id)
    print(f"\n  ASC-WL tanks: {len(asc_wl_tanks)}")

    # Build monthly terminal series per orbit using ASC-WL tanks
    def terminal_series(orbit_dir, tanks):
        sub = df[(df["orbit_direction"] == orbit_dir) & (df["tank_id"].isin(tanks))].copy()
        if sub.empty:
            return pd.Series(dtype=float)
        p5  = report.get(orbit_dir, {}).get("p5",  sub["db_mean"].quantile(0.05))
        p95 = report.get(orbit_dir, {}).get("p95", sub["db_mean"].quantile(0.95))
        sub["db_clip"] = sub["db_mean"].clip(p5, p95)
        # fill_ratio per observation
        sub["fill_ratio"] = (p95 - sub["db_clip"]) / max(p95 - p5, 1e-6)
        sub["h_est"] = 2 + sub["fill_ratio"] * (sub["h_max"] - 2)
        sub["vol_m3"] = np.pi * sub["radius_m"]**2 * sub["h_est"]
        daily = sub.groupby("date")["vol_m3"].sum()
        monthly = daily.resample("MS").mean()
        return monthly

    # DESC uses all tanks that have desc_wl; ASC uses asc_wl tanks
    if screen_df is not None:
        desc_wl_tanks = set(screen_df[screen_df["desc_wl"] == True]["tank_id"])
    else:
        desc_wl_tanks = set()
        for (tid, od), grp in df.groupby(["tank_id", "orbit_direction"]):
            if od != "DESC":
                continue
            if len(grp) >= 5:
                step = grp["db_mean"].quantile(0.95) - grp["db_mean"].quantile(0.05)
                if step < STEP_THRESH:
                    desc_wl_tanks.add(tid)

    # Use intersection for fair M1 comparison
    common_tanks = asc_wl_tanks & desc_wl_tanks
    print(f"  Common WL tanks (ASC ∩ DESC): {len(common_tanks)}")

    asc_series  = terminal_series("ASC",  common_tanks)
    desc_series = terminal_series("DESC", common_tanks)

    # Monthly deltas
    delta_asc  = asc_series.diff().dropna()
    delta_desc = desc_series.diff().dropna()

    # Overlap period
    overlap = delta_asc.index.intersection(delta_desc.index)
    print(f"  Overlap period: {overlap.min().date() if len(overlap) else 'none'} → "
          f"{overlap.max().date() if len(overlap) else 'none'}  ({len(overlap)} months)")

    if len(overlap) < 6:
        print("  !! Insufficient overlap for M1 — need ≥6 months.")
        m1 = float("nan")
    else:
        da = delta_asc.loc[overlap]
        dd = delta_desc.loc[overlap]
        m1 = float(da.corr(dd))
        print(f"\n  M1 = corr(Δ_ASC_monthly, Δ_DESC_monthly) = {m1:.4f}")
        if m1 < 0.3:
            print("  ⚠️  M1 < 0.3 — STOP. Debug calibration before Stage 6.")
        else:
            print("  ✓  M1 ≥ 0.3 — calibration OK, Stage 6 can proceed.")

    # Check E1/E2 2026 events in ASC series
    print("\n  2026 events in ASC monthly series:")
    asc_full = terminal_series("ASC", asc_wl_tanks)
    delta_asc_full = asc_full.diff().dropna()
    events_2026 = delta_asc_full[delta_asc_full.index.year == 2026]
    if events_2026.empty:
        print("  No 2026 ASC data yet.")
    else:
        for dt, val in events_2026.items():
            tag = "▲ BUILD" if val > 0 else "▼ DRAW"
            vol_mb = val / 1e6 / 6.2898
            print(f"    {dt.strftime('%Y-%m')}: Δ={vol_mb:+.2f} Mb  {tag}")

    # Save report
    report_path = os.path.join(DIAG_DIR, "m1_calibration_report.txt")
    with open(report_path, "w") as f:
        f.write(f"Per-orbit calibration report\nGenerated: {datetime.datetime.utcnow().isoformat()}\n\n")
        for od, d in report.items():
            f.write(f"{od}: p5={d['p5']:.3f} dB  p95={d['p95']:.3f} dB  n={d['n_obs']}\n")
        f.write(f"\nASC-WL tanks: {len(asc_wl_tanks)}\n")
        f.write(f"DESC-WL tanks: {len(desc_wl_tanks)}\n")
        f.write(f"Common WL tanks: {len(common_tanks)}\n")
        f.write(f"Overlap months: {len(overlap)}\n")
        f.write(f"\nM1 = {m1:.4f}\n")
        verdict = "PASS" if (not np.isnan(m1) and m1 >= 0.3) else "FAIL/INSUFFICIENT"
        f.write(f"M1 verdict: {verdict}\n")
    print(f"\n  Report → {report_path}")

    return m1


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    from supabase import create_client
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    screen_df = None

    if 2 in RUN_ITEMS:
        item2_backup(sb)

    if 3 in RUN_ITEMS:
        screen_df = item3_screen(sb)

    if 4 in RUN_ITEMS:
        m1 = item4_calibration_m1(sb, screen_df=screen_df)
        if m1 is not None and not (hasattr(m1, '__float__') and np.isnan(m1)):
            if m1 < 0.3:
                sys.exit(1)  # signal CI failure so GHA marks the step red

    print("\n[stage5b] Done.")


if __name__ == "__main__":
    main()
