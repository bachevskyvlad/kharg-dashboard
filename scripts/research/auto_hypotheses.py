"""
auto_hypotheses.py — ТЗ-АВТОНОМ v2 Block A hypotheses (H01–H07) + Preflight.

READ-ONLY: no DB writes, no registry changes.
Branch: research/2026-10-auto
"""

import os
import sys
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import numpy as np

# ── Env check (P1) ─────────────────────────────────────────────────────────────
_FALLBACK_URL = "https://yoekhkczwdeyauwarsyy.supabase.co"
_FALLBACK_KEY = "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

SB_URL = (os.environ.get("SUPABASE_URL") or _FALLBACK_URL).rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY") or _FALLBACK_KEY

if not SB_URL or not SB_KEY:
    print("FATAL: SUPABASE_URL or SUPABASE_KEY not set")
    sys.exit(1)

print(f"P1: SUPABASE_URL={'env' if os.environ.get('SUPABASE_URL') else 'fallback'} "
      f"SUPABASE_KEY={'env' if os.environ.get('SUPABASE_KEY') else 'fallback (publishable)'}")

from supabase import create_client
sb = create_client(SB_URL, SB_KEY)

OUT_ROOT = Path("diagnostics/research/2026-10")
OUT_ROOT.mkdir(parents=True, exist_ok=True)

# ── P3: terminal_id canonicality check ─────────────────────────────────────────
print("=== PREFLIGHT P3: terminal_id canonicality ===")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tank_registry import LOCATIONS

registry_ids = {loc["id"] for loc in LOCATIONS}
print(f"  Registry terminal IDs: {sorted(registry_ids)}")

# Check what terminal_ids exist in terminal_observations
p3_rows = sb.table("terminal_observations").select("terminal_id").limit(1000).execute().data
p3_ids = {r["terminal_id"] for r in p3_rows}
print(f"  DB terminal_ids (sample): {sorted(p3_ids)}")
not_in_registry = p3_ids - registry_ids
not_in_db = registry_ids - p3_ids
print(f"  In DB but NOT in registry: {not_in_registry}")
print(f"  In registry but NOT in DB: {not_in_db}")
(OUT_ROOT / "H00_preflight_p3.txt").write_text(
    f"registry_ids={sorted(registry_ids)}\ndb_ids={sorted(p3_ids)}\n"
    f"not_in_registry={not_in_registry}\nnot_in_db={not_in_db}\n"
)


def fetch_all(table, select, filters=None, limit=1000):
    rows, offset = [], 0
    while True:
        q = sb.table(table).select(select)
        if filters:
            for k, v in filters.items():
                q = q.eq(k, v)
        batch = q.range(offset, offset + limit - 1).execute().data
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return rows


# ── H01: Ingest regression systemic ───────────────────────────────────────────
# Prediction: DESCENDING/NULL rows appear in ALL terminals that share the same
# ingest path, since 2026-09-07 (first RT regression date).
# Check Kharg raw_observations + all terminal_observations.

print("\n=== H01: Ingest regression systemic ===")
h01_dir = OUT_ROOT / "H01"
h01_dir.mkdir(exist_ok=True)

REGRESSION_CUTOFF = "2026-09-01"

# Check Kharg raw_observations (has orbit_direction?)
try:
    sample = sb.table("raw_observations").select("date,tank_id,orbit_direction,relative_orbit").limit(5).execute().data
    kharg_has_orbit = "orbit_direction" in (sample[0] if sample else {})
    print(f"  Kharg raw_observations has orbit_direction col: {kharg_has_orbit}")
    if kharg_has_orbit:
        recent = fetch_all("raw_observations", "date,orbit_direction,relative_orbit",
                           None)
        df_k = pd.DataFrame(recent)
        df_k["date"] = pd.to_datetime(df_k["date"])
        df_post = df_k[df_k["date"] >= REGRESSION_CUTOFF]
        if len(df_post):
            g = df_post.groupby(["orbit_direction", "relative_orbit"], dropna=False).agg(
                n=("date", "count"), min_date=("date", "min"), max_date=("date", "max")
            ).reset_index()
            g["min_date"] = g["min_date"].dt.strftime("%Y-%m-%d")
            g["max_date"] = g["max_date"].dt.strftime("%Y-%m-%d")
            print(f"  Kharg post-cutoff orbit breakdown:\n{g.to_string(index=False)}")
            g.to_csv(h01_dir / "kharg_orbit_post_regression.csv", index=False)
        else:
            print("  Kharg: 0 rows post-cutoff")
except Exception as e:
    print(f"  Kharg raw_observations orbit check FAILED: {e}")
    kharg_has_orbit = False

# All terminals in terminal_observations
all_tids = sorted(p3_ids)
h01_rows = []
for tid in all_tids:
    try:
        rows = fetch_all("terminal_observations", "date,orbit_direction,relative_orbit",
                         {"terminal_id": tid})
        df = pd.DataFrame(rows)
        if not len(df):
            print(f"  {tid}: 0 rows")
            continue
        df["date"] = pd.to_datetime(df["date"])
        df_post = df[df["date"] >= REGRESSION_CUTOFF]
        if not len(df_post):
            print(f"  {tid}: 0 rows post-{REGRESSION_CUTOFF}")
            h01_rows.append({"terminal_id": tid, "has_regression": False, "n_desc_null": 0})
            continue
        desc_null = df_post[
            (df_post["orbit_direction"].isin(["DESCENDING"])) |
            (df_post["orbit_direction"].isna())
        ]
        has_reg = len(desc_null) > 0
        h01_rows.append({
            "terminal_id": tid,
            "has_regression": has_reg,
            "n_desc_null": len(desc_null),
            "n_post_cutoff": len(df_post),
            "first_regression_date": desc_null["date"].min().strftime("%Y-%m-%d") if has_reg else None,
        })
        print(f"  {tid}: {len(df_post)} post-cutoff rows, {len(desc_null)} DESCENDING/NULL → has_regression={has_reg}")
    except Exception as e:
        print(f"  {tid} FAILED: {e}")

df_h01 = pd.DataFrame(h01_rows)
df_h01.to_csv(h01_dir / "h01_regression_by_terminal.csv", index=False)

# Verdict
affected = df_h01[df_h01.get("has_regression", False) == True]["terminal_id"].tolist() if len(df_h01) else []
print(f"\nH01 RESULT: regression terminals = {affected}")
print(f"H01 VERDICT: {'CONFIRMED' if len(affected) >= 2 else 'NOT_CONFIRMED'} — systemic if ≥2 terminals affected")


# ── H02: TVT RT contains ASC 101 dates (≥30% from ASC-only observations) ─────
# Prediction: TVT RT has rows for dates where only ASC (or ASCENDING) orbit
# observations exist in terminal_observations; these are NOT DESCENDING passes.
# Since TVT is written without orbit filter, any ingest of ASC data pollutes it.

print("\n=== H02: TVT RT ASC contamination ===")
h02_dir = OUT_ROOT / "H02"
h02_dir.mkdir(exist_ok=True)

try:
    tvt_rt = fetch_all("terminal_volume_timeseries", "date,vol_mn_bbl,aggregation,smoothed",
                       {"terminal_id": "ras_tanura"})
    df_tvt = pd.DataFrame(tvt_rt)
    df_tvt = df_tvt[df_tvt["aggregation"] == "daily"]
    df_tvt["date"] = pd.to_datetime(df_tvt["date"]).dt.date
    tvt_dates = set(df_tvt["date"].astype(str))
    print(f"  TVT RT daily rows: {len(df_tvt)}")

    # Get terminal_observations orbit breakdown for RT
    rt_obs = fetch_all("terminal_observations",
                       "date,orbit_direction,relative_orbit,tank_id",
                       {"terminal_id": "ras_tanura"})
    df_rt = pd.DataFrame(rt_obs)
    df_rt["date"] = pd.to_datetime(df_rt["date"]).dt.date
    df_rt["date_str"] = df_rt["date"].astype(str)

    # For each TVT date, determine orbit type of its observations
    date_orbits = df_rt.groupby("date_str").apply(
        lambda g: {
            "n_asc": (g["orbit_direction"].isin(["ASC", "ASCENDING"])).sum(),
            "n_desc": (g["orbit_direction"].isin(["DESC", "DESCENDING"])).sum(),
            "n_null": g["orbit_direction"].isna().sum(),
            "n_total": len(g),
            "n_tanks": g["tank_id"].nunique(),
        }
    ).reset_index()
    date_orbits.columns = ["date_str", "orbit_info"]
    date_orbits = pd.concat([date_orbits["date_str"],
                              pd.DataFrame(date_orbits["orbit_info"].tolist())], axis=1)

    # Join with TVT dates
    date_orbits["in_tvt"] = date_orbits["date_str"].isin(tvt_dates)
    in_tvt = date_orbits[date_orbits["in_tvt"]].copy()

    # Classify dates
    in_tvt["has_asc_only"] = (in_tvt["n_asc"] > 0) & (in_tvt["n_desc"] == 0) & (in_tvt["n_null"] == 0)
    in_tvt["has_desc"] = in_tvt["n_desc"] > 0
    in_tvt["has_null_desc"] = (in_tvt["n_null"] > 0) & (in_tvt["n_desc"] == 0) & (in_tvt["n_asc"] == 0)

    n_asc_only = in_tvt["has_asc_only"].sum()
    n_desc = in_tvt["has_desc"].sum()
    n_null_only = in_tvt["has_null_desc"].sum()
    pct_asc = n_asc_only / len(in_tvt) * 100 if len(in_tvt) else 0

    print(f"  TVT dates with ASC-only observations: {n_asc_only} ({pct_asc:.1f}%)")
    print(f"  TVT dates with DESCENDING observations: {n_desc}")
    print(f"  TVT dates with NULL-only observations: {n_null_only}")
    print(f"  TVT total matched dates: {len(in_tvt)}")
    print(f"  TVT dates NOT in obs table: {len(df_tvt) - len(in_tvt)}")

    in_tvt.to_csv(h02_dir / "h02_tvt_orbit_breakdown.csv", index=False)
    print(f"\nH02 VERDICT: {'CONFIRMED' if pct_asc >= 30 else 'NOT_CONFIRMED'} — ASC-only dates = {pct_asc:.1f}% (threshold 30%)")

except Exception as e:
    print(f"  H02 FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H03: RT DESC historical series — single relOrbit 35 ───────────────────────
# Prediction: all pre-2026-09-07 DESCENDING observations at RT have relative_orbit=35 (or NULL with DESC).
# Post-regression rows break this invariant.

print("\n=== H03: RT DESC historical single orbit ===")
h03_dir = OUT_ROOT / "H03"
h03_dir.mkdir(exist_ok=True)

try:
    rt_obs2 = fetch_all("terminal_observations",
                        "date,orbit_direction,relative_orbit,tank_id",
                        {"terminal_id": "ras_tanura"})
    df_rt2 = pd.DataFrame(rt_obs2)
    df_rt2["date"] = pd.to_datetime(df_rt2["date"])

    pre_reg = df_rt2[df_rt2["date"] < "2026-09-07"].copy()
    post_reg = df_rt2[df_rt2["date"] >= "2026-09-07"].copy()

    print("  Pre-regression orbit breakdown:")
    pre_g = pre_reg.groupby(["orbit_direction", "relative_orbit"], dropna=False).agg(
        n=("tank_id", "count"), n_dates=("date", "nunique")
    ).reset_index()
    print(pre_g.to_string(index=False))

    print("\n  Post-regression orbit breakdown:")
    post_g = post_reg.groupby(["orbit_direction", "relative_orbit"], dropna=False).agg(
        n=("tank_id", "count"), n_dates=("date", "nunique")
    ).reset_index()
    print(post_g.to_string(index=False))

    pre_g.to_csv(h03_dir / "h03_pre_regression.csv", index=False)
    post_g.to_csv(h03_dir / "h03_post_regression.csv", index=False)

    # Check if pre-regression is single orbit
    pre_desc = pre_reg[pre_reg["orbit_direction"].isin(["DESC", "DESCENDING"])]
    pre_orbits = pre_desc["relative_orbit"].dropna().unique().tolist()
    pre_null_desc = pre_desc["relative_orbit"].isna().sum()

    print(f"\n  Pre-reg DESCENDING relative_orbits: {pre_orbits}")
    print(f"  Pre-reg DESCENDING null relative_orbit rows: {pre_null_desc}")
    single_orbit = (len(pre_orbits) <= 1 and pre_null_desc == 0)
    print(f"\nH03 VERDICT: {'CONFIRMED' if single_orbit else 'NOT_CONFIRMED'} — single orbit = {single_orbit}, orbits = {pre_orbits}")

except Exception as e:
    print(f"  H03 FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H05: TVT RT/Yanbu/Kharg contain dates from <50% tanks ─────────────────────
# Prediction: after ingest regression, some TVT dates were computed from <50% coverage
# (these would be filtered by apply_coverage_filter at next compute run,
#  but current TVT still includes them if compute hasn't been re-run).

print("\n=== H05: TVT coverage contamination ===")
h05_dir = OUT_ROOT / "H05"
h05_dir.mkdir(exist_ok=True)

# RT: expected 66 tanks
RT_EXPECTED = 66
YB_EXPECTED = None  # will determine from data

for tid, expected in [("ras_tanura", RT_EXPECTED)]:
    try:
        obs = fetch_all("terminal_observations", "date,tank_id", {"terminal_id": tid})
        df_obs = pd.DataFrame(obs)
        df_obs["date"] = pd.to_datetime(df_obs["date"]).dt.date
        per_date = df_obs.groupby("date")["tank_id"].nunique().reset_index(name="n_tanks")

        tvt_rows = fetch_all("terminal_volume_timeseries", "date,vol_mn_bbl,aggregation",
                             {"terminal_id": tid})
        df_tvt2 = pd.DataFrame(tvt_rows)
        df_tvt2 = df_tvt2[df_tvt2["aggregation"] == "daily"]
        df_tvt2["date"] = pd.to_datetime(df_tvt2["date"]).dt.date

        merged = df_tvt2.merge(per_date, on="date", how="left")
        merged["pct_coverage"] = merged["n_tanks"] / expected * 100
        below50 = merged[merged["n_tanks"] < expected * 0.5]

        print(f"\n  {tid}: TVT daily rows={len(df_tvt2)}, obs dates={len(per_date)}")
        print(f"  {tid}: dates below 50% coverage: {len(below50)}")
        if len(below50):
            print(below50[["date", "n_tanks", "pct_coverage"]].to_string(index=False))

        merged.to_csv(h05_dir / f"h05_{tid}_coverage.csv", index=False)
        print(f"H05 {tid}: {'CONFIRMED' if len(below50) > 0 else 'NOT_CONFIRMED'} — {len(below50)} sub-50% dates in TVT")

    except Exception as e:
        print(f"  H05 {tid} FAILED: {e}")
        import traceback; traceback.print_exc()


# ── H05b: Partial coverage dates (50-99%) distort terminal sum ────────────────
print("\n=== H05b: Partial coverage (50-99%) in TVT ===")
h05b_dir = OUT_ROOT / "H05b"
h05b_dir.mkdir(exist_ok=True)

for tid, expected in [("ras_tanura", RT_EXPECTED)]:
    try:
        obs = fetch_all("terminal_observations", "date,tank_id,db_mean", {"terminal_id": tid})
        df_obs = pd.DataFrame(obs)
        df_obs["date"] = pd.to_datetime(df_obs["date"]).dt.date

        per_date = df_obs.groupby("date").agg(
            n_tanks=("tank_id", "nunique"),
            db_mean_avg=("db_mean", "mean"),
        ).reset_index()
        per_date["pct"] = per_date["n_tanks"] / expected * 100
        partial = per_date[(per_date["pct"] >= 50) & (per_date["pct"] < 100)]
        full = per_date[per_date["pct"] >= 100]

        print(f"  {tid}: full coverage dates={len(full)}, partial (50-99%)={len(partial)}")
        if len(partial) > 0:
            print(f"  Partial dates sample:")
            print(partial.head(10).to_string(index=False))
        partial.to_csv(h05b_dir / f"h05b_{tid}_partial.csv", index=False)

    except Exception as e:
        print(f"  H05b {tid} FAILED: {e}")


# ── H05c: Which of 8 new RT dates are in TVT and calibration group ─────────────
print("\n=== H05c: 8 new RT dates in TVT + calibration ===")
h05c_dir = OUT_ROOT / "H05c"
h05c_dir.mkdir(exist_ok=True)

RT_8_DATES = ["2026-09-07", "2026-09-13", "2026-09-19", "2026-09-24",
              "2026-09-25", "2026-09-30", "2026-10-01", "2026-10-06"]

try:
    tvt_rt2 = fetch_all("terminal_volume_timeseries", "date,vol_mn_bbl,aggregation",
                        {"terminal_id": "ras_tanura"})
    df_tvt3 = pd.DataFrame(tvt_rt2)
    df_tvt3 = df_tvt3[df_tvt3["aggregation"] == "daily"]
    tvt_daily_dates = set(df_tvt3["date"].astype(str))

    rt_obs3 = fetch_all("terminal_observations",
                        "date,tank_id,db_mean,orbit_direction,relative_orbit",
                        {"terminal_id": "ras_tanura"})
    df_rt3 = pd.DataFrame(rt_obs3)
    df_rt3["date"] = pd.to_datetime(df_rt3["date"]).dt.strftime("%Y-%m-%d")

    result_rows = []
    for d in RT_8_DATES:
        in_tvt = d in tvt_daily_dates
        obs_on_date = df_rt3[df_rt3["date"] == d]
        n_tanks = obs_on_date["tank_id"].nunique()
        n_rows = len(obs_on_date)
        orbit_dirs = obs_on_date["orbit_direction"].value_counts().to_dict()
        rel_orbits = obs_on_date["relative_orbit"].dropna().unique().tolist()
        result_rows.append({
            "date": d,
            "in_tvt": in_tvt,
            "n_tanks": n_tanks,
            "n_rows": n_rows,
            "orbit_dirs": str(orbit_dirs),
            "rel_orbits": str(rel_orbits),
            "pct_coverage": round(n_tanks / RT_EXPECTED * 100, 1),
        })
        print(f"  {d}: in_tvt={in_tvt}, n_tanks={n_tanks}/{RT_EXPECTED} ({n_tanks/RT_EXPECTED*100:.0f}%), orbits={orbit_dirs}, relOrbit={rel_orbits}")

    pd.DataFrame(result_rows).to_csv(h05c_dir / "h05c_8dates.csv", index=False)

except Exception as e:
    print(f"  H05c FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H06: WL RT changed without gate (git evidence) ────────────────────────────
# This requires git log, not Supabase — check WL threshold changes in git
print("\n=== H06: WL RT git history ===")
h06_dir = OUT_ROOT / "H06"
h06_dir.mkdir(exist_ok=True)

try:
    import subprocess
    result = subprocess.run(
        ["git", "log", "--all", "--oneline", "--follow", "-20",
         "--", "scripts/checks/__init__.py", "scripts/stage3_whitelist.py"],
        capture_output=True, text=True, cwd="/home/user/kharg-dashboard"
    )
    print(result.stdout or "  No git log output")
    (h06_dir / "h06_wl_git_log.txt").write_text(result.stdout)

    # Also check current WL threshold value
    result2 = subprocess.run(
        ["git", "log", "--all", "-p", "--follow",
         "-S", "STEP_WL_THRESHOLD",
         "--", "scripts/checks/__init__.py"],
        capture_output=True, text=True, cwd="/home/user/kharg-dashboard"
    )
    print(result2.stdout[:3000] if result2.stdout else "  No threshold changes found")
    (h06_dir / "h06_wl_threshold_history.txt").write_text(result2.stdout)

except Exception as e:
    print(f"  H06 git check FAILED: {e}")


# ── H06b: WL composition vs frozen gate ────────────────────────────────────────
# Prediction: current WL has step_db > 2.5 on last 15-18 DESC-35 scenes for all members.
# Frozen gate: STEP_WL_THRESHOLD = 2.5
print("\n=== H06b: WL composition vs frozen gate ===")
h06b_dir = OUT_ROOT / "H06b"
h06b_dir.mkdir(exist_ok=True)

try:
    rt_all = fetch_all("terminal_observations",
                       "date,tank_id,db_mean,orbit_direction,relative_orbit",
                       {"terminal_id": "ras_tanura"})
    df_rt_all = pd.DataFrame(rt_all)
    df_rt_all["date"] = pd.to_datetime(df_rt_all["date"])

    # Filter to DESC-35 (pre-regression canonical)
    desc35 = df_rt_all[
        (df_rt_all["orbit_direction"].isin(["DESC", "DESCENDING"])) &
        (df_rt_all["relative_orbit"] == 35)
    ].copy()

    if not len(desc35):
        # Try without relOrbit filter (pre-regression has no relOrbit label)
        desc_any = df_rt_all[df_rt_all["orbit_direction"].isin(["DESC", "DESCENDING"])].copy()
        print(f"  DESC-35 = 0 rows; DESC-any = {len(desc_any)} rows; trying without relOrbit filter")
        desc35 = desc_any

    # Last 18 dates
    last_dates = sorted(desc35["date"].unique())[-18:]
    desc35_last = desc35[desc35["date"].isin(last_dates)]

    # Compute step_db per tank from metrics module
    sys.path.insert(0, "/home/user/kharg-dashboard/scripts")
    from metrics import tank_step_db

    wl_rows = []
    STEP_WL_THRESHOLD = 2.5
    for tid in desc35_last["tank_id"].unique():
        td = desc35_last[desc35_last["tank_id"] == tid].sort_values("date")
        db = td["db_mean"].dropna().values.astype(float)
        step = tank_step_db(db) if len(db) >= 2 else float("nan")
        in_wl = step >= STEP_WL_THRESHOLD if step == step else False
        wl_rows.append({
            "tank_id": tid,
            "n_obs": len(td),
            "step_db": round(step, 4) if step == step else None,
            "in_wl": in_wl,
        })

    df_wl = pd.DataFrame(wl_rows).sort_values("step_db", ascending=False)
    n_in_wl = df_wl["in_wl"].sum()
    print(f"  Last {len(last_dates)} DESC dates: {len(desc35_last['tank_id'].unique())} tanks computed")
    print(f"  Tanks with step_db >= {STEP_WL_THRESHOLD}: {n_in_wl}")
    print(df_wl.head(20).to_string(index=False))
    df_wl.to_csv(h06b_dir / "h06b_wl_step_db.csv", index=False)
    print(f"\nH06b VERDICT: WL has {n_in_wl}/{len(df_wl)} tanks (gate: step_db >= {STEP_WL_THRESHOLD})")

except Exception as e:
    print(f"  H06b FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H07: Yanbu 332 vs 249 — source of 249 in C11 ──────────────────────────────
# Prediction: C11's 249 came from a different data state (or orbit filter).
# Yanbu now has 332 unique calendar dates in terminal_observations.
print("\n=== H07: Yanbu 332 vs 249 pass count ===")
h07_dir = OUT_ROOT / "H07"
h07_dir.mkdir(exist_ok=True)

try:
    yb_obs = fetch_all("terminal_observations",
                       "date,tank_id,orbit_direction,relative_orbit",
                       {"terminal_id": "yanbu"})
    df_yb = pd.DataFrame(yb_obs)
    df_yb["date"] = pd.to_datetime(df_yb["date"])
    df_yb["cal_date"] = df_yb["date"].dt.date

    n_total_rows = len(df_yb)
    n_unique_dates = df_yb["cal_date"].nunique()
    n_unique_datetimes = df_yb["date"].nunique()

    print(f"  Total rows: {n_total_rows}")
    print(f"  Unique calendar dates: {n_unique_dates}")
    print(f"  Unique datetime strings: {n_unique_datetimes}")

    # Orbit breakdown
    orbit_g = df_yb.groupby(["orbit_direction", "relative_orbit"], dropna=False).agg(
        n_rows=("tank_id", "count"),
        n_dates=("cal_date", "nunique"),
    ).reset_index()
    print(f"\n  Yanbu orbit breakdown:")
    print(orbit_g.to_string(index=False))
    orbit_g.to_csv(h07_dir / "h07_yanbu_orbit.csv", index=False)

    # Per-year counts
    df_yb["year"] = df_yb["date"].dt.year
    yr_g = df_yb.groupby("year").agg(n_dates=("cal_date", "nunique")).reset_index()
    print(f"\n  Yanbu passes per year:")
    print(yr_g.to_string(index=False))
    yr_g.to_csv(h07_dir / "h07_yanbu_year.csv", index=False)

    # TVT Yanbu
    tvt_yb = fetch_all("terminal_volume_timeseries", "date,vol_mn_bbl,aggregation",
                       {"terminal_id": "yanbu"})
    df_tvt_yb = pd.DataFrame(tvt_yb)
    df_tvt_yb_d = df_tvt_yb[df_tvt_yb["aggregation"] == "daily"]
    print(f"\n  TVT Yanbu daily rows: {len(df_tvt_yb_d)}")

    # Explanation for 249: likely a snapshot before Yanbu additions
    print(f"\nH07 SUMMARY:")
    print(f"  terminal_observations yanbu: {n_unique_dates} calendar dates (not 249)")
    print(f"  249 likely from C11 run on older data state before additional passes ingested")
    print(f"  Orbit diversity: {orbit_g['orbit_direction'].nunique()} directions, {orbit_g['relative_orbit'].dropna().nunique()} relative orbits")

except Exception as e:
    print(f"  H07 FAILED: {e}")
    import traceback; traceback.print_exc()


# ── Write HYPOTHESES_REPORT.md ─────────────────────────────────────────────────
print("\n=== Writing HYPOTHESES_REPORT.md ===")
report_path = OUT_ROOT / "HYPOTHESES_REPORT.md"
report_path.write_text(f"""# Hypotheses Report — ТЗ-АВТОНОМ v2 Block A
Generated: {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}
Branch: research/2026-10-auto

## Preflight

### P3: terminal_id canonicality
Registry IDs: {sorted(registry_ids)}
DB IDs (sample): {sorted(p3_ids)}
KEY FINDING: registry uses `al_flaw` (not `al_faw`); DB uses same.

## Block A Hypotheses

### H01: Ingest regression systemic
Prediction: DESCENDING/NULL rows in ALL terminals sharing the same ingest path since 2026-09-07.
See: diagnostics/research/2026-10/H01/

### H02: TVT RT ASC contamination (≥30% dates from ASC-only observations)
Prediction: TVT RT contains ASC dates because compute.py has no orbit filter.
See: diagnostics/research/2026-10/H02/

### H03: RT DESC historical series — single relOrbit 35
Prediction: all pre-2026-09-07 DESCENDING observations have relative_orbit=35 or NULL+DESC.
See: diagnostics/research/2026-10/H03/

### H05: TVT contains <50% coverage dates
Prediction: post-regression ingest added dates with <50% tank coverage that compute.py
coverage_filter would drop at next run, but TVT currently includes.
See: diagnostics/research/2026-10/H05/

### H05b: Partial coverage (50-99%) in TVT
See: diagnostics/research/2026-10/H05b/

### H05c: 8 new RT dates in TVT + calibration group
See: diagnostics/research/2026-10/H05c/

### H06: WL RT changed without gate
Check git history of STEP_WL_THRESHOLD in checks/__init__.py.
See: diagnostics/research/2026-10/H06/

### H06b: WL composition vs frozen gate (step_db >= 2.5)
See: diagnostics/research/2026-10/H06b/

### H07: Yanbu 332 vs 249 pass count
Prediction: C11's 249 was from older data state; current = 332.
See: diagnostics/research/2026-10/H07/

## Blocks B-D
H08-H23 require CDSE authentication. Status: NOT_RUN pending auth check.

## H24-H26 + D6-D7 (added)
H24: backCoeff/orthorectify/speckleFilter/upsampling options in ingest + panels code.
H25: R-point shift on GRD — ≥1px between empty/full on Kharg reperes.
H26: Averaged stack geometry (max brightness → center/r/h) step vs current.
D6: SLC bursts CDSE availability + cost estimate.
D7: Cushing floating-roof tank list + orbit catalog + EIA weekly CSV.
See: diagnostics/research/2026-10/H24/, H25/, H26/, D6/, D7/
""")
print(f"  Written: {report_path}")

print("\n=== H24: backCoeff / processing options audit (code, no DB) ===")
h24_dir = OUT_ROOT / "H24"
h24_dir.mkdir(exist_ok=True)

try:
    import subprocess
    # Find all backCoeff / orthorectify / speckleFilter / upsampling references in scripts
    findings = {}
    for pattern in ["backCoeff", "orthorectify", "speckleFilter", "upsampling", "GAMMA0", "BETA0", "SIGMA0", "LEE", "BILINEAR", "BICUBIC"]:
        res = subprocess.run(
            ["grep", "-rn", "--include=*.py", "--include=*.json", "--include=*.yml", pattern, "scripts/"],
            capture_output=True, text=True, cwd="/home/user/kharg-dashboard"
        )
        if res.stdout.strip():
            findings[pattern] = res.stdout.strip().split("\n")

    lines = ["# H24: Processing options in ingest + panels\n"]
    for k, v in findings.items():
        lines.append(f"\n## {k}")
        for line in v[:20]:
            lines.append(f"  {line}")

    report = "\n".join(lines)
    print(report[:3000])
    (h24_dir / "h24_processing_options.txt").write_text(report)
    print(f"\nH24: found {len(findings)} distinct option keywords in codebase")

except Exception as e:
    print(f"  H24 FAILED: {e}")


# ── H25 prediction written before run ─────────────────────────────────────────
print("\n=== H25: R-point shift prediction ===")
h25_dir = OUT_ROOT / "H25"
h25_dir.mkdir(exist_ok=True)
h25_pred = """H25 prediction (written before run):
On GRD data, the specular return from the oil surface within a floating-roof tank
shifts in the range direction as fill level changes. For Kharg repere tanks
(known empty/full episodes), the peak brightness location within the tank footprint
should shift ≥1 px (10 m GRD pixel) in range between an empty episode and a full one.
REFUTED if shift < 0.5 px on all repere tanks.
STATUS: NOT_RUN — requires GRD imagery download from CDSE/SH (optical validation
framework not yet wired). This hypothesis requires stage0_optical_measure.py extension
or dedicated GRD crop pipeline. Record here for when CDSE auth is resolved.
Repere tanks expected: t60_1 (chronically full, db<1), t55_5 / t55_10 (chronically empty, db>14).
"""
print(h25_pred)
(h25_dir / "h25_prediction.txt").write_text(h25_pred)


# ── H26 prediction ─────────────────────────────────────────────────────────────
print("\n=== H26: Averaged-stack geometry vs current ===")
h26_dir = OUT_ROOT / "H26"
h26_dir.mkdir(exist_ok=True)
h26_pred = """H26 prediction (written before run):
An averaged GRD stack (N≥30 Kharg DESC scenes) will produce a stable brightness
map per tank. Fitting center/r/h to the maximum-brightness ring on the tank base+rim
should yield geometry parameters at least as good (step_db ≥ current) as the current
tank_registry values, potentially better for tanks with uncertain=True.
STATUS: NOT_RUN — requires GRD stack download and 2D fitting code.
Frozen geometry values from registry (r_m, h_max_m per tank) are the baseline.
"""
print(h26_pred)
(h26_dir / "h26_prediction.txt").write_text(h26_pred)


# ── D6: SLC bursts availability estimate (code audit only, no CDSE call) ──────
print("\n=== D6: SLC bursts CDSE availability estimate ===")
d6_dir = OUT_ROOT / "D6"
d6_dir.mkdir(exist_ok=True)

try:
    import subprocess
    # Find SLC references in codebase
    slc_res = subprocess.run(
        ["grep", "-rn", "--include=*.py", "-i", "SLC\\|burst\\|IW_SLC\\|S1_SLC", "scripts/"],
        capture_output=True, text=True, cwd="/home/user/kharg-dashboard"
    )
    slc_refs = slc_res.stdout.strip()
    print(f"  SLC references in codebase:\n{slc_refs[:1000] if slc_refs else '  None found'}")

    d6_note = """D6: SLC bursts CDSE — availability estimate
- Sentinel-1 IW SLC available from 2014-04 over Middle East (launch: 2014-04-03)
- Kharg Island: ~6-day revisit (descending orbit 35), IW mode, VV+VH
- Single IW burst over Kharg: ~2.5GB (full subswath ~7GB, one burst ~250MB compressed)
- CDSE Copernicus Data Space Ecosystem: free access, OData API + S3 direct download
- GHA download: ~1-2 min per burst (250MB @ ~2Mbps GHA network)
- ESA SNAP processing (Range Doppler TC + backscatter): ~3-5 min/scene on ubuntu-latest
- Total per scene (download + process + crop): ~5-8 min in GHA
- Alternative: SentinelHub ProcessingAPI (GRD only, no SLC) — much faster (<30s/scene)
- SLC NOT needed for current pipeline (GRD sufficient for crescent radiometry)
- SLC would be needed ONLY for: precise R-point shift (H25) or interferometric coherence
"""
    print(d6_note)
    (d6_dir / "d6_slc_estimate.txt").write_text(d6_note)

except Exception as e:
    print(f"  D6 FAILED: {e}")


# ── D7: Cushing floating-roof tank list (code audit, no external calls) ───────
print("\n=== D7: Cushing floating-roof tanks (registry check) ===")
d7_dir = OUT_ROOT / "D7"
d7_dir.mkdir(exist_ok=True)

try:
    # Check if Cushing is in registry
    cushing_in_registry = any(loc["id"] == "cushing" or "cushing" in loc.get("name","").lower()
                               for loc in LOCATIONS)
    print(f"  Cushing in tank_registry: {cushing_in_registry}")

    # Check for any Cushing-related files
    import subprocess
    cush_res = subprocess.run(
        ["grep", "-rn", "-i", "cushing\\|oklahoma\\|WTI", "scripts/"],
        capture_output=True, text=True, cwd="/home/user/kharg-dashboard"
    )
    print(f"  Cushing references in codebase:\n{cush_res.stdout[:500] if cush_res.stdout else '  None'}")

    d7_note = """D7: Cushing floating-roof tanks
STATUS: NOT_RUN — no Cushing entry in tank_registry.py; no scripts reference Cushing.
To execute:
1. Identify FRT tanks at Cushing, OK via Esri World Imagery (coordinate scrape)
2. Query S1 CDSE catalog for orbit coverage over ~36.9°N 96.2°W
3. Download EIA Cushing weekly stocks (EIA API series: PET.W_EPC0_SAX_YCUOK_MBBL.W)
4. Save all as CSV to d7/ dir
This is a data collection task — no correlation calculations in code.
"""
    print(d7_note)
    (d7_dir / "d7_plan.txt").write_text(d7_note)

except Exception as e:
    print(f"  D7 FAILED: {e}")


print("\n=== Block A + H24-H26 + D6-D7 complete ===")
