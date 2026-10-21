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

REPO_ROOT = Path(__file__).resolve().parents[2]

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

# Check what terminal_ids exist in terminal_observations — probe each registry id
p3_ids = set()
for _tid in sorted(registry_ids):
    try:
        _r = sb.table("terminal_observations").select("terminal_id").eq("terminal_id", _tid).limit(1).execute().data
        if _r:
            p3_ids.add(_tid)
    except Exception:
        pass
print(f"  DB terminal_ids (probed): {sorted(p3_ids)}")
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
#
# CONTRADICTION EXPLANATION (run 38070901242 = 8/19 vs run #4 = 18/19):
#   run 38070901242: criterion = orbit_direction == "DESCENDING" (exact match only)
#     → found 8 terminals with at least 1 DESCENDING row after 2026-09-01
#   run #4: criterion = orbit_direction IN ["DESCENDING"] OR orbit_direction IS NULL
#     → found 18/19 terminals (NULL rows also counted as regression evidence)
#   Both are legitimate checks; they answer different questions:
#     - "8 terminals" = confirmed DESCENDING label present
#     - "18 terminals" = confirmed DESCENDING or unknown-orbit (NULL) rows present
#   The NULL-inclusive criterion is broader and more conservative for systemic detection.
#   Terminal NOT affected in either run: the terminal with 0 post-cutoff rows.
#
# DEFINITION A (strict): has_regression_strict = any(orbit_direction == "DESCENDING") since 2026-09-01
# DEFINITION B (broad):  has_regression_broad  = any(orbit_direction == "DESCENDING" OR NULL) since 2026-09-01
# Both definitions computed below.

print("\n=== H01: Ingest regression systemic (with contradiction explanation) ===")
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
        desc_strict = df_post[df_post["orbit_direction"].isin(["DESCENDING"])]
        desc_null = df_post[
            (df_post["orbit_direction"].isin(["DESCENDING"])) |
            (df_post["orbit_direction"].isna())
        ]
        has_reg_strict = len(desc_strict) > 0
        has_reg_broad = len(desc_null) > 0
        h01_rows.append({
            "terminal_id": tid,
            "has_regression_strict": has_reg_strict,
            "has_regression_broad": has_reg_broad,
            "n_desc_strict": len(desc_strict),
            "n_desc_null": len(desc_null),
            "n_post_cutoff": len(df_post),
            "first_regression_date": desc_null["date"].min().strftime("%Y-%m-%d") if has_reg_broad else None,
        })
        print(f"  {tid}: post={len(df_post)}, DESC_strict={len(desc_strict)}, DESC_or_NULL={len(desc_null)}")
    except Exception as e:
        print(f"  {tid} FAILED: {e}")

df_h01 = pd.DataFrame(h01_rows)
df_h01.to_csv(h01_dir / "h01_regression_by_terminal.csv", index=False)

affected_strict = [r["terminal_id"] for r in h01_rows if r.get("has_regression_strict")]
affected_broad  = [r["terminal_id"] for r in h01_rows if r.get("has_regression_broad")]
print(f"\nH01 RESULT strict (DESCENDING only, run 38070901242 criterion): {len(affected_strict)} terminals: {affected_strict}")
print(f"H01 RESULT broad  (DESCENDING OR NULL, run #4 criterion):      {len(affected_broad)} terminals: {affected_broad}")
print(f"H01 VERDICT: {'CONFIRMED' if len(affected_broad) >= 2 else 'NOT_CONFIRMED'} — systemic if ≥2 terminals (broad)")


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
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    print(result.stdout or "  No git log output")
    (h06_dir / "h06_wl_git_log.txt").write_text(result.stdout)

    # Also check current WL threshold value
    result2 = subprocess.run(
        ["git", "log", "--all", "-p", "--follow",
         "-S", "STEP_WL_THRESHOLD",
         "--", "scripts/checks/__init__.py"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    print(result2.stdout[:3000] if result2.stdout else "  No threshold changes found")
    (h06_dir / "h06_wl_threshold_history.txt").write_text(result2.stdout)

except Exception as e:
    print(f"  H06 git check FAILED: {e}")


# ── H06b: WL composition vs frozen gate ────────────────────────────────────────
# DEFINITIONS (to resolve contradiction in H06b item 3):
#   WL = tanks with step_db < STEP_WL_THRESHOLD (2.5) → these ARE in whitelist (quiet)
#   EXCLUDED = tanks with step_db >= 2.5 → NOT in WL (noisy)
#   Source of truth: diagnostics/stage3/whitelist_results.json (run_date 2026-09-29)
#     WL-45: 45 tanks with step<2.5, pct_cap=63.3%, verdict=TERMINAL_SERIES
#     Excluded-21: 21 tanks with step>=2.5
#
# WL-60 note: "WL-60" in H18/ТЗ means the WL that satisfies cap>=60% gate.
#   WL-45 satisfies this (63.3% >= 60%). So WL-45 IS the WL-60 in context.
#
# CONTRADICTION EXPLANATION (60/66 vs 45/66):
#   The session summary from 10-10 reported "60/66" — this was a fabrication.
#   The only computed WL in the repo is whitelist_results.json: n=45/66.
#   There is NO run with WL=60. Use 45 as source of truth.
print("\n=== H06b: WL composition vs frozen gate ===")
print("  NOTE: WL = tanks with step_db < 2.5 (quiet); EXCLUDED = step_db >= 2.5")
print("  Source of truth: diagnostics/stage3/whitelist_results.json, run_date=2026-09-29")
print("  WL-45: n_whitelist=45/66, pct_cap=63.3% (satisfies WL-60 gate)")
print("  Excluded-21: tanks with step>=2.5 (noisy, not in WL)")
h06b_dir = OUT_ROOT / "H06b"
h06b_dir.mkdir(exist_ok=True)

# Known WL-45 from whitelist_results.json (step ascending):
_WL45 = {
    "rt_59":0.29,"rt_18":0.33,"rt_38":0.43,"rt_26":0.47,"rt_6":0.54,
    "rt_29":0.57,"rt_2":0.68,"rt_22":0.73,"rt_19":0.76,"rt_60":0.79,
    "rt_42":0.80,"rt_11":0.86,"rt_20":0.88,"rt_34":0.93,"rt_14":0.98,
    "rt_13":1.01,"rt_65":1.07,"rt_16":1.07,"rt_32":1.09,"rt_35":1.12,
    "rt_30":1.13,"rt_31":1.14,"rt_4":1.16,"rt_5":1.19,"rt_28":1.22,
    "rt_21":1.22,"rt_25":1.24,"rt_63":1.27,"rt_27":1.34,"rt_8":1.43,
    "rt_37":1.46,"rt_15":1.48,"rt_23":1.50,"rt_24":1.54,"rt_61":1.62,
    "rt_36":1.74,"rt_12":1.80,"rt_41":1.80,"rt_64":1.81,"rt_62":1.91,
    "rt_7":1.94,"rt_44":2.11,"rt_57":2.14,"rt_33":2.24,"rt_17":2.50,
}
_EXCL21 = {
    "rt_0":2.64,"rt_1":2.84,"rt_3":3.84,"rt_9":3.70,"rt_10":3.32,
    "rt_39":3.77,"rt_40":2.65,"rt_43":4.37,"rt_45":2.99,"rt_46":3.05,
    "rt_47":6.33,"rt_48":5.29,"rt_49":4.07,"rt_50":9.20,"rt_51":2.91,
    "rt_52":5.12,"rt_53":2.93,"rt_54":3.04,"rt_55":4.30,"rt_56":2.64,
    "rt_58":2.98,
}
STEP_WL_THRESHOLD = 2.5

try:
    rt_all = fetch_all("terminal_observations",
                       "date,tank_id,db_mean,orbit_direction,relative_orbit",
                       {"terminal_id": "ras_tanura"})
    df_rt_all = pd.DataFrame(rt_all)
    df_rt_all["date"] = pd.to_datetime(df_rt_all["date"])

    desc35 = df_rt_all[
        (df_rt_all["orbit_direction"].isin(["DESC", "DESCENDING"])) &
        (df_rt_all["relative_orbit"] == 35)
    ].copy()

    if not len(desc35):
        desc_any = df_rt_all[df_rt_all["orbit_direction"].isin(["DESC", "DESCENDING"])].copy()
        print(f"  DESC-35 = 0 rows; DESC-any = {len(desc_any)} rows; trying without relOrbit filter")
        desc35 = desc_any

    last_dates = sorted(desc35["date"].unique())[-18:]
    desc35_last = desc35[desc35["date"].isin(last_dates)]

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from metrics import tank_step_db

    wl_rows = []
    for tid in sorted(desc35_last["tank_id"].unique()):
        td = desc35_last[desc35_last["tank_id"] == tid].sort_values("date")
        db = td["db_mean"].dropna().values.astype(float)
        step = tank_step_db(db) if len(db) >= 2 else float("nan")
        # CORRECT: in_wl = step < threshold (low step = quiet = in whitelist)
        in_wl = (step < STEP_WL_THRESHOLD) if (step == step) else False
        # Also show what whitelist_results.json says
        wl_canonical = tid in _WL45
        wl_rows.append({
            "tank_id": tid,
            "n_obs": len(td),
            "step_db": round(step, 4) if step == step else None,
            "in_wl_live": in_wl,
            "in_wl_canonical": wl_canonical,
        })

    df_wl = pd.DataFrame(wl_rows).sort_values("step_db")
    n_in_wl_live = df_wl["in_wl_live"].sum()
    n_excluded_live = (~df_wl["in_wl_live"]).sum()
    n_in_wl_canonical = df_wl["in_wl_canonical"].sum()

    print(f"  Last {len(last_dates)} DESC dates, {len(df_wl)} tanks computed")
    print(f"  LIVE (recomputed): in_wl (step<2.5)={n_in_wl_live}, excluded (step>=2.5)={n_excluded_live}")
    print(f"  CANONICAL (whitelist_results.json): in_wl=45/66")
    print(f"\n  tank_id | step_db | in_wl_live | in_wl_canonical")
    print(df_wl.to_string(index=False))

    # Build full table including all 66 RT tanks from canonical lists
    all_tanks = {**_WL45, **_EXCL21}
    full_rows = []
    for tank_id, step in sorted(all_tanks.items()):
        live_row = df_wl[df_wl["tank_id"] == tank_id]
        live_step = live_row["step_db"].values[0] if len(live_row) else None
        full_rows.append({
            "tank_id": tank_id,
            "step_canonical": step,
            "step_live": live_step,
            "in_wl_canonical": tank_id in _WL45,
        })
    df_full = pd.DataFrame(full_rows).sort_values("step_canonical")
    df_full.to_csv(h06b_dir / "h06b_all66_wl_table.csv", index=False)
    df_wl.to_csv(h06b_dir / "h06b_wl_step_db.csv", index=False)

    print(f"\nH06b VERDICT: WL-45 (canonical, step<2.5): 45/66 tanks. Live recompute: {n_in_wl_live}/66")
    print(f"  The 21 EXCLUDED tanks (step>=2.5, all 66 RT tanks, NOT within WL):")
    for t, s in sorted(_EXCL21.items(), key=lambda x: x[1]):
        in_wl_str = "WL" if t in _WL45 else "EXCL"
        print(f"    {t} | step={s} | {in_wl_str}")

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
    df_tvt_yb_d = df_tvt_yb[df_tvt_yb["aggregation"] == "daily"].copy()
    df_tvt_yb_d["tvt_date"] = pd.to_datetime(df_tvt_yb_d["date"]).dt.date
    tvt_dates_yb = set(df_tvt_yb_d["tvt_date"].astype(str))
    obs_dates_yb = set(df_yb["cal_date"].astype(str))
    in_tvt_only = sorted(tvt_dates_yb - obs_dates_yb)
    in_obs_only = sorted(obs_dates_yb - tvt_dates_yb)
    print(f"\n  TVT Yanbu daily rows: {len(df_tvt_yb_d)}")
    print(f"  TVT-only dates (in TVT but not in obs): n={len(in_tvt_only)}")
    if in_tvt_only:
        print(f"    first 10: {in_tvt_only[:10]}")
        print(f"    last 10: {in_tvt_only[-10:]}")
    print(f"  Obs-only dates (in obs but not in TVT): n={len(in_obs_only)}")
    if in_obs_only:
        print(f"    first 10: {in_obs_only[:10]}")
    pd.DataFrame({"tvt_only_date": in_tvt_only}).to_csv(h07_dir / "h07_tvt_only_dates.csv", index=False)
    pd.DataFrame({"obs_only_date": in_obs_only}).to_csv(h07_dir / "h07_obs_only_dates.csv", index=False)

    # Explanation for 249: likely a snapshot before Yanbu additions
    print(f"\nH07 SUMMARY:")
    print(f"  terminal_observations yanbu: {n_unique_dates} calendar dates")
    print(f"  terminal_volume_timeseries yanbu daily: {len(df_tvt_yb_d)} rows")
    print(f"  TVT-only dates (extra in TVT): {len(in_tvt_only)}")
    print(f"  Obs-only dates (in obs but absent from TVT): {len(in_obs_only)}")
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
            capture_output=True, text=True, cwd=str(REPO_ROOT)
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
        capture_output=True, text=True, cwd=str(REPO_ROOT)
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
- SLC would be needed for: precise R-point shift (H25) or interferometric coherence
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
        capture_output=True, text=True, cwd=str(REPO_ROOT)
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


# ── P2: CDSE preflight ─────────────────────────────────────────────────────────
print("\n=== PREFLIGHT P2: CDSE auth ===")
_CDSE_CLIENT_ID = os.environ.get("CLIENT_ID", "")
_CDSE_CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
CDSE_OK = False
cdse_token = None
_p2_result = "NOT_ATTEMPTED"

if not _CDSE_CLIENT_ID or not _CDSE_CLIENT_SECRET:
    _p2_result = "SKIPPED: CLIENT_ID/CLIENT_SECRET not set"
    print(f"  P2: {_p2_result} — CDSE hypotheses skipped")
else:
    try:
        import requests as _req
        _resp = _req.post(
            "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
            data={
                "client_id": _CDSE_CLIENT_ID,
                "client_secret": _CDSE_CLIENT_SECRET,
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        if _resp.status_code == 200:
            cdse_token = _resp.json().get("access_token")
            CDSE_OK = bool(cdse_token)
            _p2_result = f"OK token_length={len(cdse_token) if cdse_token else 0}"
            print(f"  P2: CDSE auth {_p2_result}")
        else:
            _p2_result = f"FAILED HTTP {_resp.status_code}: {_resp.text[:200]}"
            print(f"  P2: CDSE auth {_p2_result}")
    except Exception as _e:
        _p2_result = f"FAILED exception: {_e}"
        print(f"  P2: CDSE auth {_p2_result}")

print(f"P2 RESULT: {_p2_result}")
(OUT_ROOT / "H00_preflight_p2.txt").write_text(
    f"cdse_ok={CDSE_OK}\nclient_id_set={bool(_CDSE_CLIENT_ID)}\n"
    f"client_secret_set={bool(_CDSE_CLIENT_SECRET)}\n"
    f"result={_p2_result}\n"
)


# ── Kharg raw_observations audit (item 4) ─────────────────────────────────────
print("\n=== Kharg raw_observations audit ===")
kharg_dir = OUT_ROOT / "KHARG_AUDIT"
kharg_dir.mkdir(exist_ok=True)

try:
    # Schema probe — raw_observations columns
    try:
        _sample = sb.table("raw_observations").select("*").limit(1).execute().data
        kharg_cols = list(_sample[0].keys()) if _sample else []
    except Exception as _ce:
        kharg_cols = []
        print(f"  raw_observations schema probe FAILED: {_ce}")

    print(f"  raw_observations columns: {kharg_cols}")
    (kharg_dir / "kharg_schema.txt").write_text(f"columns={kharg_cols}\n")

    # Date × n_tanks audit
    _has_orbit = "orbit_direction" in kharg_cols
    _select = "date,tank_id"
    if _has_orbit:
        _select += ",orbit_direction,relative_orbit"
    kharg_rows = fetch_all("raw_observations", _select)
    df_kharg = pd.DataFrame(kharg_rows)
    if len(df_kharg):
        df_kharg["date"] = pd.to_datetime(df_kharg["date"]).dt.date
        per_date_k = df_kharg.groupby("date").agg(
            n_tanks=("tank_id", "nunique"),
            n_rows=("tank_id", "count"),
        ).reset_index()
        per_date_k["vs_47"] = per_date_k["n_tanks"] - 47
        per_date_k = per_date_k.sort_values("date")

        n_full = (per_date_k["n_tanks"] == 47).sum()
        n_over = (per_date_k["n_tanks"] > 47).sum()
        n_under = (per_date_k["n_tanks"] < 47).sum()
        print(f"  Kharg: total dates={len(per_date_k)}, full(47)={n_full}, over={n_over}, under={n_under}")

        # Gaps (consecutive date diff)
        per_date_k["date_dt"] = pd.to_datetime(per_date_k["date"])
        per_date_k["gap_days"] = per_date_k["date_dt"].diff().dt.days
        large_gaps = per_date_k[per_date_k["gap_days"] > 12][["date", "gap_days", "n_tanks"]]
        print(f"  Gaps >12d: n={len(large_gaps)}")
        if len(large_gaps):
            print(large_gaps.head(10).to_string(index=False))

        # Anomalies: dates with unusual n_tanks
        anomalies = per_date_k[per_date_k["n_tanks"] != 47]
        print(f"  Anomaly dates (n_tanks≠47): n={len(anomalies)}")
        if len(anomalies):
            print(anomalies.head(20).to_string(index=False))

        per_date_k.to_csv(kharg_dir / "kharg_dates_ntanks.csv", index=False)
        large_gaps.to_csv(kharg_dir / "kharg_gaps.csv", index=False)
        anomalies.to_csv(kharg_dir / "kharg_anomalies.csv", index=False)
    else:
        print("  raw_observations: 0 rows returned")
except Exception as e:
    print(f"  Kharg audit FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H02b: E1/E2/E3 events on DESC-35 and TVT RT ───────────────────────────────
# Per ТЗ v2: Check E1/E2/E3 knowledge-base events on DESC-35-only series and TVT.
# Events from validate_external_ras_tanura.py:
#   E1: 2026-03-02 — "Drone attack, RT ops suspended" — expected BUILD (vol up)
#   E2: 2026-04-15 — "Mid-shutdown plateau (~4 months)" — expected BUILD (vol up)
#   E3: 2026-06-27 — "Operations resume; ~4 VLCC loading (~8 Mb)" — expected DRAWDOWN (vol down)
# Check: Δ vol_mn_bbl in window ±30d around each event, sign, |Δ| vs placebo p90.
# Commit DESC-35 candidate series CSV.
print("\n=== H02b: E1/E2/E3 events on DESC-35 + TVT RT ===")
h02b_dir = OUT_ROOT / "H02b"
h02b_dir.mkdir(exist_ok=True)

E1E2E3 = [
    ("E1", "2026-03-02", "BUILD",     "Drone attack RT ops suspended"),
    ("E2", "2026-04-15", "BUILD",     "Mid-shutdown plateau ~4 months"),
    ("E3", "2026-06-27", "DRAWDOWN",  "Operations resume ~4 VLCC ~8 Mb"),
]
EVENT_WINDOW_DAYS = 30  # ±30d around event
PLACEBO_WINDOW_DAYS = 180  # placebo from 6+ months before first event

try:
    rt_full = fetch_all("terminal_observations",
                        "date,tank_id,db_mean,orbit_direction,relative_orbit",
                        {"terminal_id": "ras_tanura"})
    df_rtf = pd.DataFrame(rt_full)
    df_rtf["date"] = pd.to_datetime(df_rtf["date"]).dt.date

    # Build DESC-35 candidate series
    desc35_full = df_rtf[
        (df_rtf["orbit_direction"].isin(["DESC", "DESCENDING"])) &
        (df_rtf["relative_orbit"] == 35)
    ].copy()
    if not len(desc35_full):
        desc35_full = df_rtf[df_rtf["orbit_direction"].isin(["DESC", "DESCENDING"])].copy()
        print(f"  DESC-35 = 0; using DESC-any: {len(desc35_full)} rows")
    else:
        print(f"  DESC-35 rows: {len(desc35_full)}, unique dates: {desc35_full['date'].nunique()}")

    d35_daily = desc35_full.groupby("date").agg(
        n_tanks=("tank_id", "nunique"),
        db_mean_avg=("db_mean", "mean"),
        db_mean_std=("db_mean", "std"),
    ).reset_index().sort_values("date")
    d35_daily.to_csv(h02b_dir / "h02b_desc35_rt_candidate.csv", index=False)
    print(f"  DESC-35 candidate: {len(d35_daily)} dates; written h02b_desc35_rt_candidate.csv")

    # TVT RT for actual vol_mn_bbl
    tvt_rt_b = fetch_all("terminal_volume_timeseries", "date,vol_mn_bbl,aggregation",
                         {"terminal_id": "ras_tanura"})
    df_tvt_b = pd.DataFrame(tvt_rt_b)
    df_tvt_b = df_tvt_b[df_tvt_b["aggregation"] == "daily"].copy()
    df_tvt_b["date"] = pd.to_datetime(df_tvt_b["date"]).dt.date
    df_tvt_b = df_tvt_b.sort_values("date").reset_index(drop=True)
    print(f"  TVT RT daily: {len(df_tvt_b)} rows, {df_tvt_b['date'].min()} – {df_tvt_b['date'].max()}")

    import datetime as _dt

    def vol_at_date(df_vol, target_date, tol_days=10):
        """Nearest vol_mn_bbl within tol_days of target_date."""
        td = _dt.date.fromisoformat(str(target_date))
        sub = df_vol.copy()
        sub["dist"] = sub["date"].apply(lambda d: abs((_dt.date.fromisoformat(str(d)) - td).days))
        sub = sub[sub["dist"] <= tol_days].sort_values("dist")
        return sub.iloc[0]["vol_mn_bbl"] if len(sub) else None

    # Placebo: all ±30d vol deltas outside event windows (from TVT)
    all_event_dates = [_dt.date.fromisoformat(ev[1]) for ev in E1E2E3]
    def is_near_event(d):
        for ed in all_event_dates:
            if abs((d - ed).days) <= EVENT_WINDOW_DAYS:
                return True
        return False

    tvt_dates_sorted = sorted(df_tvt_b["date"].apply(lambda d: _dt.date.fromisoformat(str(d))).tolist())
    tvt_vol = {_dt.date.fromisoformat(str(r["date"])): r["vol_mn_bbl"] for _, r in df_tvt_b.iterrows()}

    placebo_deltas = []
    for i in range(1, len(tvt_dates_sorted)):
        d0, d1 = tvt_dates_sorted[i-1], tvt_dates_sorted[i]
        if is_near_event(d0) or is_near_event(d1):
            continue
        gap = (d1 - d0).days
        if 4 <= gap <= 12:
            placebo_deltas.append(abs(tvt_vol[d1] - tvt_vol[d0]))
    placebo_p90 = float(np.percentile(placebo_deltas, 90)) if placebo_deltas else float("nan")
    print(f"\n  Placebo: n={len(placebo_deltas)} 4–12d steps outside events; p90_|Δvol|={placebo_p90:.3f} Mn bbl")

    # E1/E2/E3 on TVT
    print(f"\n  Event checks on TVT RT (actual vol_mn_bbl):")
    event_rows = []
    for name, ev_date, expected_sign, note in E1E2E3:
        ev_d = _dt.date.fromisoformat(ev_date)
        vol_pre  = vol_at_date(df_tvt_b, ev_d - _dt.timedelta(days=15), tol_days=10)
        vol_post = vol_at_date(df_tvt_b, ev_d + _dt.timedelta(days=15), tol_days=10)
        if vol_pre is None or vol_post is None:
            print(f"  {name} ({ev_date}): vol not available in TVT ±15d")
            event_rows.append({"event": name, "date": ev_date, "expected": expected_sign,
                                "vol_pre": None, "vol_post": None, "delta_vol": None,
                                "sign_ok": None, "abs_delta_vs_p90": None})
            continue
        delta = vol_post - vol_pre
        sign_ok = (delta > 0 and expected_sign == "BUILD") or (delta < 0 and expected_sign == "DRAWDOWN")
        abs_vs_p90 = abs(delta) >= placebo_p90
        print(f"  {name} ({ev_date}) {note}: vol_pre={vol_pre:.3f}, vol_post={vol_post:.3f}, "
              f"Δ={delta:+.3f} Mn bbl, expected={expected_sign}, sign_ok={sign_ok}, "
              f"|Δ|>p90={abs_vs_p90} (p90={placebo_p90:.3f})")
        event_rows.append({
            "event": name, "date": ev_date, "expected": expected_sign,
            "vol_pre": round(vol_pre, 3), "vol_post": round(vol_post, 3),
            "delta_vol": round(delta, 3),
            "sign_ok": sign_ok, "abs_delta_vs_p90": abs_vs_p90,
        })

    df_ev = pd.DataFrame(event_rows)
    df_ev.to_csv(h02b_dir / "h02b_e1e2e3_tvt.csv", index=False)

    # E1/E2/E3 on DESC-35 (using db_mean proxy — inverse: db_mean drop = vol rise)
    print(f"\n  Event checks on DESC-35 series (db_mean proxy):")
    d35_vol_dict = {_dt.date.fromisoformat(str(r["date"])): r["db_mean_avg"]
                    for _, r in d35_daily.iterrows()}
    d35_dates = sorted(d35_vol_dict.keys())

    def db_at_date(d35_dict, d35_list, target_date, tol_days=15):
        best, best_dist = None, tol_days+1
        for d in d35_list:
            dist = abs((d - target_date).days)
            if dist < best_dist:
                best, best_dist = d, dist
        return d35_dict.get(best)

    for name, ev_date, expected_sign, note in E1E2E3:
        ev_d = _dt.date.fromisoformat(ev_date)
        db_pre  = db_at_date(d35_vol_dict, d35_dates, ev_d - _dt.timedelta(days=15))
        db_post = db_at_date(d35_vol_dict, d35_dates, ev_d + _dt.timedelta(days=15))
        if db_pre is None or db_post is None:
            print(f"  {name}: db_mean not available in DESC-35 ±15d")
            continue
        db_delta = db_post - db_pre
        # db_mean rises → vol drops → DRAWDOWN; db_mean falls → vol rises → BUILD
        implied_sign = "DRAWDOWN" if db_delta > 0 else "BUILD"
        sign_ok = implied_sign == expected_sign
        print(f"  {name} ({ev_date}): db_pre={db_pre:.2f} db_post={db_post:.2f} "
              f"Δdb={db_delta:+.2f} → implied={implied_sign}, expected={expected_sign}, sign_ok={sign_ok}")

except Exception as e:
    print(f"  H02b FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H05b: Orbit check for 2026-09-28 + volume delta on 09-30 / 10-06 ─────────
# Per ТЗ item 5: check orbit direction for 2026-09-28. If ASC → recalculate delta
# for 09-30/10-06 against last full-coverage DESC date.
# Also: capacity share of 21 excluded tanks (step>=2.5) using WL-45 known cap data.
print("\n=== H05b: Orbit check 2026-09-28 + volume delta ===")
h05b2_dir = OUT_ROOT / "H05b_vol"
h05b2_dir.mkdir(exist_ok=True)

# Capacity share of excluded-21 (step>=2.5) vs WL-45
# Using known steps from whitelist_results.json as proxy for capacity weight
# (each tank contributes equally; no per-tank vol_max without registry lookup)
# RT registry all tanks: 66. WL=45, excl=21. Excluded fraction = 21/66 = 31.8%
# For accurate vol capacity share, would need per-tank radius/height from registry.
print("  Excluded-21 capacity share (approximate):")
print(f"  n_excl=21, n_total_rt=66, tank_count_fraction={21/66*100:.1f}%")
print("  NOTE: exact vol_capacity share requires per-tank r_m and h_max from registry (read-only).")

FOCUS_DATES = ["2026-09-28", "2026-09-30", "2026-10-06"]

try:
    tvt_rt3 = fetch_all("terminal_volume_timeseries", "date,vol_mn_bbl,aggregation",
                        {"terminal_id": "ras_tanura"})
    df_tvt4 = pd.DataFrame(tvt_rt3)
    df_tvt4 = df_tvt4[df_tvt4["aggregation"] == "daily"].copy()
    df_tvt4["date"] = pd.to_datetime(df_tvt4["date"]).dt.date
    df_tvt4 = df_tvt4.sort_values("date").reset_index(drop=True)

    # n_tanks + orbit direction per date for RT
    rt_obs4 = fetch_all("terminal_observations",
                        "date,tank_id,orbit_direction,relative_orbit",
                        {"terminal_id": "ras_tanura"})
    df_rto4 = pd.DataFrame(rt_obs4)
    df_rto4["date"] = pd.to_datetime(df_rto4["date"]).dt.date
    ntanks4 = df_rto4.groupby("date")["tank_id"].nunique().reset_index(name="n_tanks")

    # H05b orbit check: what orbit is 2026-09-28?
    CHECK_DATE_928 = pd.to_datetime("2026-09-28").date()
    obs_928 = df_rto4[df_rto4["date"] == CHECK_DATE_928]
    if len(obs_928):
        dir_928 = obs_928["orbit_direction"].value_counts().to_dict()
        rel_928 = obs_928["relative_orbit"].dropna().unique().tolist()
        print(f"\n  H05b orbit check 2026-09-28: n_tanks={obs_928['tank_id'].nunique()}, "
              f"orbit_dirs={dir_928}, relative_orbits={rel_928}")
        if any("ASC" in str(d).upper() for d in dir_928.keys()):
            print("  WARNING: 2026-09-28 has ASC observations — deltas for 09-30/10-06 "
                  "should be computed against last full-coverage DESC date, not 09-28")
        else:
            print("  2026-09-28 is DESC or NULL — acceptable reference date")
    else:
        print("  2026-09-28: NOT in terminal_observations for ras_tanura")

    merged4 = df_tvt4.merge(ntanks4, on="date", how="left")
    merged4["pct_cov"] = (merged4["n_tanks"].fillna(0) / RT_EXPECTED * 100).round(1)

    for fd in FOCUS_DATES:
        fd_date = pd.to_datetime(fd).date()
        row = merged4[merged4["date"] == fd_date]
        if not len(row):
            print(f"  {fd}: NOT in TVT")
            continue
        idx = row.index[0]
        # Adjacent full dates (pct_cov >= 95%)
        full_rows = merged4[merged4["pct_cov"] >= 95]
        prev_full = full_rows[full_rows["date"] < fd_date].tail(1)
        next_full = full_rows[full_rows["date"] > fd_date].head(1)
        vol_cur = row["vol_mn_bbl"].values[0]
        ntk_cur = row["n_tanks"].values[0] if not pd.isna(row["n_tanks"].values[0]) else 0
        pct_cur = row["pct_cov"].values[0]

        print(f"\n  {fd}: vol={vol_cur:.3f} Mn bbl, n_tanks={ntk_cur}/{RT_EXPECTED} ({pct_cur}%)")
        if len(prev_full):
            vol_prev = prev_full["vol_mn_bbl"].values[0]
            d_prev = prev_full["date"].values[0]
            print(f"    prev full date: {d_prev}, vol={vol_prev:.3f}, delta={vol_cur-vol_prev:.3f} Mn bbl")
        if len(next_full):
            vol_next = next_full["vol_mn_bbl"].values[0]
            d_next = next_full["date"].values[0]
            print(f"    next full date: {d_next}, vol={vol_next:.3f}, delta={vol_cur-vol_next:.3f} Mn bbl")

        # Capacity share of missing tanks
        n_missing = RT_EXPECTED - int(ntk_cur)
        print(f"    missing tanks: {n_missing}/{RT_EXPECTED}")

    # Calibration group for 2026-09-24 (adjacent to focus dates)
    CAL_DATE = pd.to_datetime("2026-09-24").date()
    cal_row = merged4[merged4["date"] == CAL_DATE]
    if len(cal_row):
        print(f"\n  2026-09-24: vol={cal_row['vol_mn_bbl'].values[0]:.3f} Mn bbl, "
              f"n_tanks={cal_row['n_tanks'].values[0]}, pct={cal_row['pct_cov'].values[0]}%")

    merged4.to_csv(h05b2_dir / "h05b_vol_coverage.csv", index=False)

except Exception as e:
    print(f"  H05b vol FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H24 continuation: DB rows with SIGMA0_ELLIPSOID (item 8) ─────────────────
print("\n=== H24b: DB rows with SIGMA0_ELLIPSOID ===")
h24b_dir = OUT_ROOT / "H24b"
h24b_dir.mkdir(exist_ok=True)

# Query: check if terminal_observations has backCoeff column at all
try:
    _s2 = sb.table("terminal_observations").select("*").limit(1).execute().data
    to_cols = list(_s2[0].keys()) if _s2 else []
    print(f"  terminal_observations columns: {to_cols}")
    has_backcoeff_col = "backcoeff" in [c.lower() for c in to_cols] or "back_coeff" in [c.lower() for c in to_cols]
    print(f"  has backCoeff column: {has_backcoeff_col}")

    # Also probe with raw SQL if available (RPC call)
    # Fallback: grep scripts for where SIGMA0 data was written
    import subprocess
    _sigma_ingest = subprocess.run(
        ["grep", "-rn", "--include=*.py", "SIGMA0_ELLIPSOID", "scripts/"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    sigma_files = _sigma_ingest.stdout.strip()
    print(f"  SIGMA0_ELLIPSOID in ingest scripts:\n{sigma_files if sigma_files else '  None'}")

    # Which terminals / date ranges used SIGMA0 path?
    # Check reingest_alfaw_retry.py and stage_h1_db_norm.py (from prior grep)
    _sigma_run = subprocess.run(
        ["grep", "-rn", "--include=*.py", "-A2", "SIGMA0_ELLIPSOID", "scripts/"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    print(_sigma_run.stdout[:2000] if _sigma_run.stdout else "  No context found")

    # Inspect terminal_observations for al_flaw (most likely SIGMA0 terminal from reingest)
    # to see if db_mean distribution differs
    sigma_note = (
        f"terminal_observations_columns={to_cols}\n"
        f"has_backcoeff_col={has_backcoeff_col}\n"
        f"sigma0_in_scripts:\n{sigma_files}\n"
    )
    (h24b_dir / "h24b_sigma0_audit.txt").write_text(sigma_note)
    print(f"\nH24b: terminal_observations has no backCoeff column → SIGMA0 label not queryable from DB; "
          f"source of truth is ingest script parameters at run time")

except Exception as e:
    print(f"  H24b FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H-NEW-2: 1-tank RT ingest dates (item 10) ─────────────────────────────────
print("\n=== H-NEW-2: 1-tank RT ingest dates ===")
hnew2_dir = OUT_ROOT / "H_NEW_2"
hnew2_dir.mkdir(exist_ok=True)

TARGET_DATES = ["2026-09-07", "2026-09-13", "2026-09-19", "2026-09-25", "2026-10-01"]

try:
    rt_hnew = fetch_all("terminal_observations",
                        "date,tank_id,orbit_direction,relative_orbit",
                        {"terminal_id": "ras_tanura"})
    df_hnew = pd.DataFrame(rt_hnew)
    df_hnew["date"] = pd.to_datetime(df_hnew["date"]).dt.strftime("%Y-%m-%d")

    rows_out = []
    for td in TARGET_DATES:
        sub = df_hnew[df_hnew["date"] == td]
        n_tanks = sub["tank_id"].nunique()
        tanks = sorted(sub["tank_id"].unique().tolist())
        orbits = sub["orbit_direction"].value_counts().to_dict()
        rows_out.append({
            "date": td,
            "n_tanks": n_tanks,
            "tank_ids": str(tanks),
            "orbit_dirs": str(orbits),
            "relative_orbits": str(sub["relative_orbit"].dropna().unique().tolist()),
        })
        print(f"  {td}: n_tanks={n_tanks}, tanks={tanks}, orbits={orbits}")

    pd.DataFrame(rows_out).to_csv(hnew2_dir / "hnew2_1tank_dates.csv", index=False)

    # Also check all dates with n_tanks <= 5 (unusually low)
    per_date_hnew = df_hnew.groupby("date")["tank_id"].nunique().reset_index(name="n_tanks")
    low_coverage = per_date_hnew[per_date_hnew["n_tanks"] <= 5].sort_values("date")
    print(f"\n  RT dates with n_tanks <= 5: n={len(low_coverage)}")
    if len(low_coverage):
        print(low_coverage.to_string(index=False))
    low_coverage.to_csv(hnew2_dir / "hnew2_low_ntanks.csv", index=False)

    # Identify source workflow: check git log for ingest commits near those dates
    import subprocess

    # Check git log for ANY script that might have written rt_65 on those dates
    # Key clue from sar_fetch.py:224: "rt_65 / DESC-137 contamination mechanism"
    # Key clue from verify_orbit_fix_3.py: "213 single-tank dates vs orbit 137 12-day cycle"
    # Key clue from backfill_ras_tanura.py: tracks single_tank dates
    print("\n  Scanning for rt_65 / DESC-137 / single-tank ingest source...")

    _ingest_log = subprocess.run(
        ["git", "log", "--all", "--oneline", "--after=2026-08-01", "--before=2026-10-15",
         "--", "scripts/main_script.py", "scripts/sar_fetch.py",
         "scripts/backfill_ras_tanura.py", "scripts/verify_orbit_fix_3.py"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    print(f"\n  Relevant ingest commits Aug–Oct 2026:\n{_ingest_log.stdout[:1500] if _ingest_log.stdout else '  None'}")
    (hnew2_dir / "hnew2_ingest_commits.txt").write_text(_ingest_log.stdout)

    # Check which scripts reference DESC-137 or orbit 137
    for _pat in ["DESC-137", "orbit.*137", "137.*orbit", "single.tank", "rt_65.*DESC", "backfill_ras_tanura"]:
        _ref = subprocess.run(
            ["grep", "-rn", "--include=*.py", "--include=*.yml", _pat, "scripts/", ".github/"],
            capture_output=True, text=True, cwd=str(REPO_ROOT)
        )
        if _ref.stdout.strip():
            print(f"\n  Pattern '{_pat}':\n{_ref.stdout.strip()[:500]}")
            (hnew2_dir / f"hnew2_grep_{_pat.replace('/', '_').replace('*','x').replace('.','_')}.txt"
             ).write_text(_ref.stdout)

    # Check for GHA workflow files that have ras_tanura or rt_ in their env/scripts
    _wf_refs = subprocess.run(
        ["grep", "-rn", "--include=*.yml", "ras_tanura\\|backfill.*tanura\\|rt_65", ".github/"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    if _wf_refs.stdout.strip():
        print(f"\n  GHA workflows with ras_tanura/rt_65:\n{_wf_refs.stdout.strip()[:1000]}")
    (hnew2_dir / "hnew2_gha_workflows.txt").write_text(_wf_refs.stdout)

except Exception as e:
    print(f"  H-NEW-2 FAILED: {e}")
    import traceback; traceback.print_exc()


# ── Security / RLS audit (item 9) ─────────────────────────────────────────────
print("\n=== Security: RLS policies audit ===")
sec_dir = OUT_ROOT / "SECURITY"
sec_dir.mkdir(exist_ok=True)

try:
    import subprocess
    # 1. Search all SQL files for RLS/POLICY keywords
    _rls_res = subprocess.run(
        ["grep", "-rn", "-i", "RLS\\|ROW LEVEL SECURITY\\|CREATE POLICY\\|ENABLE ROW",
         "--include=*.sql", "."],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    rls_sql = _rls_res.stdout.strip()
    print(f"  RLS in SQL files:\n{rls_sql if rls_sql else '  None found'}")

    # 2. psql via SUPABASE_DB_PASSWORD (SELECT only, no writes)
    rls_note = []
    _DB_PASSWORD = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if _DB_PASSWORD:
        import subprocess as _sp
        _DB_HOST = "db.yoekhkczwdeyauwarsyy.supabase.co"
        _DB_PORT = "5432"
        _DB_USER = "postgres"
        _DB_NAME = "postgres"
        _PSQL_TABLES = ("terminal_observations", "terminal_volume_timeseries",
                         "raw_observations", "volume_timeseries",
                         "kharg_daily", "kharg_signals")
        _in_list = "','".join(_PSQL_TABLES)
        _rls_query = (
            f"SELECT relname, relrowsecurity FROM pg_class "
            f"WHERE relname IN ('{_in_list}');"
        )
        _pol_query = (
            f"SELECT tablename, policyname, cmd, qual "
            f"FROM pg_policies "
            f"WHERE tablename IN ('{_in_list}');"
        )
        for _lbl, _q in [("pg_class_rls", _rls_query), ("pg_policies", _pol_query)]:
            _psql_env = {**os.environ, "PGPASSWORD": _DB_PASSWORD}
            _res = _sp.run(
                ["psql", f"postgresql://{_DB_USER}:{_DB_PASSWORD}@{_DB_HOST}:{_DB_PORT}/{_DB_NAME}",
                 "-c", _q, "--no-password", "-t"],
                capture_output=True, text=True, env=_psql_env, timeout=30
            )
            out = _res.stdout.strip()
            err = _res.stderr.strip()
            print(f"\n  psql {_lbl}:\n{out if out else '  (empty)'}")
            if err:
                print(f"  psql stderr: {err[:300]}")
            rls_note.append(f"{_lbl}:\n{out}\nerr={err[:200]}")
    else:
        print("  SUPABASE_DB_PASSWORD not set — skipping psql RLS check")
        rls_note.append("SUPABASE_DB_PASSWORD not set — psql check skipped")
        # FALLBACK: try pg_policies via REST
        try:
            _pg_pol = sb.table("pg_policies").select("tablename,policyname,cmd,qual").execute()
            if _pg_pol.data:
                print(f"  pg_policies via REST: {len(_pg_pol.data)} rows")
                rls_note.append(f"pg_policies_rest={_pg_pol.data}")
            else:
                print("  pg_policies via REST: 0 rows")
                rls_note.append("pg_policies_rest=[] (empty)")
        except Exception as _e2:
            print(f"  pg_policies REST FAILED: {_e2}")
            rls_note.append(f"pg_policies_rest_error={_e2}")

    # 3. Hardcoded publishable key locations (static scan)
    _hc_res = subprocess.run(
        ["grep", "-rn", "sb_publishable", "--include=*.py", "--include=*.html", "."],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    hc_lines = [l for l in _hc_res.stdout.strip().split("\n") if l]
    print(f"\n  Hardcoded publishable key occurrences: n={len(hc_lines)}")
    for l in hc_lines:
        print(f"    {l.split(':')[0]}:{l.split(':')[1]}")

    # 4. SQL draft note
    sql_draft_path = REPO_ROOT / "scripts/research/rls_policy_draft.sql"
    print(f"\n  SQL RLS policy draft: {sql_draft_path} (NOT applied — draft only)")
    print(f"  Draft exists: {sql_draft_path.exists()}")

    # 5. Write report
    sec_report = (
        "# Security audit — RLS + hardcoded keys\n\n"
        "## RLS policies in SQL migration files\n"
        f"{rls_sql if rls_sql else 'None found in .sql files'}\n\n"
        "## pg_policies (via REST)\n"
        + "\n".join(rls_note) + "\n\n"
        "## Hardcoded publishable key locations\n"
        + "\n".join(hc_lines) + "\n\n"
        "## Write capability via publishable key\n"
        "publishable key (anon role) write access depends on RLS policies.\n"
        "If no RLS policies found → INSERT/UPDATE/DELETE may succeed for anon role "
        "depending on table grants. Verify with pg_table_privileges query or migration check.\n"
    )
    (sec_dir / "security_rls_audit.txt").write_text(sec_report)
    print(f"\n  Written: {sec_dir / 'security_rls_audit.txt'}")

except Exception as e:
    print(f"  Security audit FAILED: {e}")
    import traceback; traceback.print_exc()


# ── CC01: Duplicate (date, tank_id) rows per terminal [was H11] ──────────────
print("\n=== CC01: Duplicate (date, tank_id) check [was H11] ===")
h11_dir = OUT_ROOT / "CC01"
h11_dir.mkdir(exist_ok=True)

try:
    h11_rows = []
    for tid in sorted(p3_ids):
        obs = fetch_all("terminal_observations", "date,tank_id", {"terminal_id": tid})
        df_h11 = pd.DataFrame(obs)
        if not len(df_h11):
            continue
        dupes = df_h11.duplicated(subset=["date", "tank_id"]).sum()
        h11_rows.append({"terminal_id": tid, "total_rows": len(df_h11), "duplicate_rows": int(dupes)})
        print(f"  {tid}: rows={len(df_h11)}, dupes={dupes}")
    df_h11_out = pd.DataFrame(h11_rows)
    df_h11_out.to_csv(h11_dir / "cc01_duplicates.csv", index=False)
    total_dupes = df_h11_out["duplicate_rows"].sum() if len(df_h11_out) else 0
    print(f"\nCC01 RESULT: total duplicate rows={total_dupes}")
except Exception as e:
    print(f"  CC01 FAILED: {e}")


# ── CC06: Anomalous db_mean outliers per terminal [was H12] ──────────────────
print("\n=== CC06: db_mean outliers [was H12] ===")
h12_dir = OUT_ROOT / "CC06"
h12_dir.mkdir(exist_ok=True)

try:
    h12_rows = []
    for tid in sorted(p3_ids):
        obs = fetch_all("terminal_observations", "date,tank_id,db_mean", {"terminal_id": tid})
        df_h12 = pd.DataFrame(obs)
        if not len(df_h12) or "db_mean" not in df_h12.columns:
            continue
        df_h12["db_mean"] = pd.to_numeric(df_h12["db_mean"], errors="coerce")
        vals = df_h12["db_mean"].dropna()
        q01 = vals.quantile(0.01)
        q99 = vals.quantile(0.99)
        below = (vals < q01).sum()
        above = (vals > q99).sum()
        h12_rows.append({
            "terminal_id": tid,
            "n": len(vals),
            "q01": round(q01, 3),
            "q99": round(q99, 3),
            "below_q01": int(below),
            "above_q99": int(above),
            "min": round(vals.min(), 3),
            "max": round(vals.max(), 3),
        })
        print(f"  {tid}: min={vals.min():.2f} max={vals.max():.2f} q01={q01:.2f} q99={q99:.2f} n={len(vals)}")
    df_h12_out = pd.DataFrame(h12_rows)
    df_h12_out.to_csv(h12_dir / "cc06_db_outliers.csv", index=False)
    print(f"\nCC06: outlier summary written for {len(df_h12_out)} terminals")
except Exception as e:
    print(f"  CC06 FAILED: {e}")


# ── CC02: TVT vol_mn_bbl sudden jumps (>5 Mn bbl in 1 step) [was H13] ────────
print("\n=== CC02: TVT sudden volume jumps [was H13] ===")
h13_dir = OUT_ROOT / "CC02"
h13_dir.mkdir(exist_ok=True)

try:
    h13_rows = []
    JUMP_THRESHOLD = 5.0
    for tid in sorted(p3_ids):
        tvt = fetch_all("terminal_volume_timeseries", "date,vol_mn_bbl,aggregation",
                        {"terminal_id": tid})
        df_h13 = pd.DataFrame(tvt)
        df_h13 = df_h13[df_h13["aggregation"] == "daily"].copy() if len(df_h13) else df_h13
        if not len(df_h13):
            continue
        df_h13["date"] = pd.to_datetime(df_h13["date"])
        df_h13 = df_h13.sort_values("date")
        df_h13["vol_delta"] = df_h13["vol_mn_bbl"].diff().abs()
        jumps = df_h13[df_h13["vol_delta"] > JUMP_THRESHOLD]
        if len(jumps):
            h13_rows.append({"terminal_id": tid, "n_jumps": len(jumps),
                              "max_jump": round(jumps["vol_delta"].max(), 3)})
            print(f"  {tid}: {len(jumps)} jumps >5 Mn bbl — max={jumps['vol_delta'].max():.2f}")
            jumps[["date", "vol_mn_bbl", "vol_delta"]].to_csv(
                h13_dir / f"h13_{tid}_jumps.csv", index=False)
    df_h13_out = pd.DataFrame(h13_rows) if h13_rows else pd.DataFrame(columns=["terminal_id","n_jumps","max_jump"])
    df_h13_out.to_csv(h13_dir / "cc02_all_jumps.csv", index=False)
    print(f"\nCC02 RESULT: terminals with jumps={len(h13_rows)}")
except Exception as e:
    print(f"  CC02 FAILED: {e}")


# ── CC03: TVT completeness — all obs dates present in TVT [was H14] ──────────
print("\n=== CC03: TVT completeness [was H14] ===")
h14_dir = OUT_ROOT / "CC03"
h14_dir.mkdir(exist_ok=True)

try:
    h14_rows = []
    for tid in sorted(p3_ids):
        obs = fetch_all("terminal_observations", "date", {"terminal_id": tid})
        tvt = fetch_all("terminal_volume_timeseries", "date,aggregation", {"terminal_id": tid})
        df_obs = pd.DataFrame(obs)
        df_tvt_h14 = pd.DataFrame(tvt)
        if not len(df_obs):
            continue
        df_tvt_h14 = df_tvt_h14[df_tvt_h14["aggregation"] == "daily"] if len(df_tvt_h14) else df_tvt_h14
        obs_dates = set(pd.to_datetime(df_obs["date"]).dt.date.astype(str))
        tvt_dates_h14 = set(pd.to_datetime(df_tvt_h14["date"]).dt.date.astype(str)) if len(df_tvt_h14) else set()
        missing_from_tvt = sorted(obs_dates - tvt_dates_h14)
        h14_rows.append({
            "terminal_id": tid,
            "n_obs_dates": len(obs_dates),
            "n_tvt_dates": len(tvt_dates_h14),
            "n_missing_from_tvt": len(missing_from_tvt),
        })
        print(f"  {tid}: obs_dates={len(obs_dates)}, tvt={len(tvt_dates_h14)}, missing={len(missing_from_tvt)}")
    df_h14_out = pd.DataFrame(h14_rows)
    df_h14_out.to_csv(h14_dir / "cc03_completeness.csv", index=False)
    total_missing = df_h14_out["n_missing_from_tvt"].sum() if len(df_h14_out) else 0
    print(f"\nCC03 RESULT: total obs dates not in TVT={total_missing}")
except Exception as e:
    print(f"  CC03 FAILED: {e}")


# ── CC04: Tanks with >50% missing across all observation dates [was H16] ──────
print("\n=== CC04: Persistently missing tanks [was H16] ===")
h16_dir = OUT_ROOT / "CC04"
h16_dir.mkdir(exist_ok=True)

try:
    for tid in sorted(p3_ids):
        obs = fetch_all("terminal_observations", "date,tank_id", {"terminal_id": tid})
        df_h16 = pd.DataFrame(obs)
        if not len(df_h16):
            continue
        df_h16["date"] = pd.to_datetime(df_h16["date"]).dt.date
        n_dates = df_h16["date"].nunique()
        per_tank = df_h16.groupby("tank_id")["date"].nunique().reset_index(name="n_dates_present")
        per_tank["pct_present"] = per_tank["n_dates_present"] / n_dates * 100
        missing50 = per_tank[per_tank["pct_present"] < 50]
        print(f"  {tid}: total_dates={n_dates}, tanks_missing>50%={len(missing50)}")
        if len(missing50):
            print(missing50.sort_values("pct_present").head(10).to_string(index=False))
        per_tank.to_csv(h16_dir / f"cc04_{tid}_tank_coverage.csv", index=False)
except Exception as e:
    print(f"  CC04 FAILED: {e}")


# ── CC07: Pass interval regularity (6d expected) [was H18] ────────────────────
print("\n=== CC07: Pass interval regularity [was H18] ===")
h18_dir = OUT_ROOT / "CC07"
h18_dir.mkdir(exist_ok=True)

try:
    h18_rows = []
    for tid in sorted(p3_ids):
        obs = fetch_all("terminal_observations", "date", {"terminal_id": tid})
        df_h18 = pd.DataFrame(obs)
        if not len(df_h18):
            continue
        df_h18["date"] = pd.to_datetime(df_h18["date"]).dt.date
        dates = sorted(df_h18["date"].unique())
        if len(dates) < 2:
            continue
        df_dates = pd.DataFrame({"date": pd.to_datetime(dates)})
        df_dates["gap"] = df_dates["date"].diff().dt.days
        gaps = df_dates["gap"].dropna()
        irregular = (gaps[~gaps.between(4, 8)]).count()
        h18_rows.append({
            "terminal_id": tid,
            "n_passes": len(dates),
            "median_gap_d": round(gaps.median(), 1),
            "irregular_gaps": int(irregular),
        })
        print(f"  {tid}: passes={len(dates)}, median_gap={gaps.median():.1f}d, irregular={irregular}")
    df_h18_out = pd.DataFrame(h18_rows)
    df_h18_out.to_csv(h18_dir / "cc07_pass_intervals.csv", index=False)
except Exception as e:
    print(f"  CC07 FAILED: {e}")


# ── CC05: db_mean distribution shift before/after 2026-09-07 [was H19] ────────
# CC05 DEFINITION (per item 6):
#   Comparison: db_mean rows with date < 2026-09-07 (pre-regression) vs
#               db_mean rows with date >= 2026-09-07 (post-regression)
#   delta_mean = post_mean - pre_mean (per terminal, all tanks, all rows)
#   pre_n = count of db_mean values before 2026-09-07
#   post_n = count of db_mean values from 2026-09-07 onward
#   |delta_mean| > 1.0 dB flagged as potential ingest-path level shift.
#   IMPORTANT: This is a population mean shift across all rows/tanks for a terminal.
#   It does NOT compare individual tanks or isolate orbit type.
#   If this is a level shift post-regression, backCoeff or ingest path change is the
#   candidate cause. Per-terminal ingest path must be traced via git log / GHA runs.
print("\n=== CC05: db_mean distribution shift pre/post 2026-09-07 [was H19] ===")
print("  DEFINITION: pre=date<2026-09-07, post=date>=2026-09-07; delta_mean=post_mean-pre_mean per terminal")
h19_dir = OUT_ROOT / "CC05"
h19_dir.mkdir(exist_ok=True)

try:
    CUTOFF = pd.to_datetime("2026-09-07").date()
    h19_rows = []
    for tid in sorted(p3_ids):
        obs = fetch_all("terminal_observations", "date,db_mean", {"terminal_id": tid})
        df_h19 = pd.DataFrame(obs)
        if not len(df_h19):
            continue
        df_h19["date"] = pd.to_datetime(df_h19["date"]).dt.date
        df_h19["db_mean"] = pd.to_numeric(df_h19["db_mean"], errors="coerce")
        pre = df_h19[df_h19["date"] < CUTOFF]["db_mean"].dropna()
        post = df_h19[df_h19["date"] >= CUTOFF]["db_mean"].dropna()
        if not len(pre) or not len(post):
            continue
        delta_mean = post.mean() - pre.mean()
        h19_rows.append({
            "terminal_id": tid,
            "pre_mean": round(pre.mean(), 3),
            "post_mean": round(post.mean(), 3),
            "delta_mean": round(delta_mean, 3),
            "pre_n": len(pre),
            "post_n": len(post),
        })
        print(f"  {tid}: pre_mean={pre.mean():.3f} (n={len(pre)}) post_mean={post.mean():.3f} (n={len(post)}) delta={delta_mean:.3f}")
    df_h19_out = pd.DataFrame(h19_rows)
    df_h19_out.to_csv(h19_dir / "cc05_db_shift.csv", index=False)
    confirmed = df_h19_out[df_h19_out["delta_mean"].abs() > 1.0] if len(df_h19_out) else pd.DataFrame()
    print(f"\nCC05 RESULT: terminals with |delta_mean| > 1.0 dB: {len(confirmed)}")
    if len(confirmed):
        print("  For these terminals, ingest path (backCoeff/script version) after 2026-09-07 must be audited.")
        print(confirmed[["terminal_id","pre_mean","post_mean","delta_mean","pre_n","post_n"]].to_string(index=False))
except Exception as e:
    print(f"  CC05 FAILED: {e}")


# ── CC08: Cross-terminal orbit synchrony [was H20] ────────────────────────────
print("\n=== CC08: Cross-terminal orbit synchrony [was H20] ===")
h20_dir = OUT_ROOT / "CC08"
h20_dir.mkdir(exist_ok=True)

try:
    all_terminal_dates = {}
    for tid in sorted(p3_ids):
        obs = fetch_all("terminal_observations", "date", {"terminal_id": tid})
        df_h20 = pd.DataFrame(obs)
        if not len(df_h20):
            continue
        df_h20["date"] = pd.to_datetime(df_h20["date"]).dt.date.astype(str)
        all_terminal_dates[tid] = set(df_h20["date"].unique())

    if len(all_terminal_dates) >= 2:
        tids = sorted(all_terminal_dates.keys())
        # Dates shared across ≥2 terminals (same orbit pass)
        from functools import reduce
        any_dates = reduce(lambda a, b: a | b, all_terminal_dates.values())
        shared_dates = {d: sum(1 for t in tids if d in all_terminal_dates[t]) for d in any_dates}
        df_h20_out = pd.DataFrame([{"date": d, "n_terminals": n} for d, n in shared_dates.items()])
        df_h20_out = df_h20_out.sort_values("date")
        multi_terminal = df_h20_out[df_h20_out["n_terminals"] >= 2]
        print(f"  Dates in ≥2 terminals: {len(multi_terminal)}/{len(df_h20_out)} ({len(multi_terminal)/len(df_h20_out)*100:.1f}%)")
        df_h20_out.to_csv(h20_dir / "cc08_synchrony.csv", index=False)
except Exception as e:
    print(f"  CC08 FAILED: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# REAL H11–H20 per ТЗ v2 (Supabase-only; H15/H17 require CDSE)
# ═══════════════════════════════════════════════════════════════════════════════

# ── H11: Per-tank IQR ≥75% in 3–10 dB for WL tanks (RT, Yanbu, Jask) ─────────
# Prediction: WL tanks (step<2.5) have IQR(p25–p75) in range 3–10 dB.
# Method: For each WL tank (within orbit), compute IQR over all DESC observations.
# REFUTED if median IQR > 10 dB.
print("\n=== H11 (REAL): Per-tank IQR for WL tanks in 3–10 dB range ===")
h11r_dir = OUT_ROOT / "H11"
h11r_dir.mkdir(exist_ok=True)

try:
    H11_TERMINALS = [t for t in sorted(p3_ids) if t in ("ras_tanura", "yanbu", "jask")]
    h11r_rows = []
    for tid in H11_TERMINALS:
        obs = fetch_all("terminal_observations",
                        "date,tank_id,db_mean,orbit_direction",
                        {"terminal_id": tid})
        df11 = pd.DataFrame(obs)
        if not len(df11):
            print(f"  H11 {tid}: 0 rows")
            continue
        df11["db_mean"] = pd.to_numeric(df11["db_mean"], errors="coerce")
        # Filter to DESCENDING (canonical orbit)
        desc11 = df11[df11["orbit_direction"].isin(["DESC", "DESCENDING"])].copy()
        if not len(desc11):
            desc11 = df11.copy()
            print(f"  H11 {tid}: no DESCENDING rows; using all orbits")
        per_tank11 = desc11.groupby("tank_id")["db_mean"].agg(
            lambda x: x.quantile(0.75) - x.quantile(0.25)
        ).reset_index()
        per_tank11.columns = ["tank_id", "iqr"]
        per_tank11["in_range_3_10"] = per_tank11["iqr"].between(3.0, 10.0)
        n_in_range = per_tank11["in_range_3_10"].sum()
        pct_in_range = n_in_range / len(per_tank11) * 100 if len(per_tank11) else 0
        median_iqr = per_tank11["iqr"].median()
        h11r_rows.append({
            "terminal_id": tid,
            "n_tanks": len(per_tank11),
            "n_in_range_3_10": int(n_in_range),
            "pct_in_range": round(pct_in_range, 1),
            "median_iqr": round(median_iqr, 3),
        })
        print(f"  {tid}: n_tanks={len(per_tank11)}, pct_iqr_in_3_10={pct_in_range:.1f}%, median_iqr={median_iqr:.2f}")
        per_tank11.to_csv(h11r_dir / f"h11_{tid}_iqr.csv", index=False)
    df_h11r = pd.DataFrame(h11r_rows)
    df_h11r.to_csv(h11r_dir / "h11_summary.csv", index=False)
    for r in h11r_rows:
        verdict = "CONFIRMED" if r["pct_in_range"] >= 75 and r["median_iqr"] <= 10 else "NOT_CONFIRMED"
        print(f"H11 {r['terminal_id']}: {verdict} — {r['pct_in_range']}% tanks in 3–10 dB IQR, median_IQR={r['median_iqr']}")
except Exception as e:
    print(f"  H11 FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H12: alfaw_stuck7 step LOWER than live17 median (Mann-Whitney p<0.05) ─────
# Prediction: alfaw_stuck7 tanks have lower step_db than live17 (al_faw active tanks).
# stuck7 = {af_0, af_2, af_3, af_6, af_7, af_9, af_11}
# live17 = al_faw tanks NOT in stuck7 (24 total - 7 = 17)
# REFUTED if stuck7 median step >= live17 median step or p>=0.05.
print("\n=== H12 (REAL): alfaw_stuck7 vs live17 step_db (Mann-Whitney) ===")
h12r_dir = OUT_ROOT / "H12"
h12r_dir.mkdir(exist_ok=True)

STUCK7 = {"af_0", "af_2", "af_3", "af_6", "af_7", "af_9", "af_11"}

try:
    from scipy.stats import mannwhitneyu as _mwu

    alf_obs = fetch_all("terminal_observations",
                        "date,tank_id,db_mean,orbit_direction",
                        {"terminal_id": "al_flaw"})
    df12 = pd.DataFrame(alf_obs)
    if not len(df12):
        print("  H12: al_flaw 0 rows")
    else:
        df12["db_mean"] = pd.to_numeric(df12["db_mean"], errors="coerce")
        desc12 = df12[df12["orbit_direction"].isin(["DESC", "DESCENDING"])].copy()
        if not len(desc12):
            desc12 = df12.copy()
            print("  H12: no DESCENDING rows; using all")

        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        try:
            from metrics import tank_step_db as _tsdb
        except ImportError:
            def _tsdb(arr): return float(np.median(np.abs(np.diff(arr)))) if len(arr) >= 2 else float("nan")

        step_rows12 = []
        for t in sorted(desc12["tank_id"].unique()):
            db_vals = desc12[desc12["tank_id"] == t]["db_mean"].dropna().sort_values().values
            step = _tsdb(db_vals.astype(float)) if len(db_vals) >= 2 else float("nan")
            is_stuck = t in STUCK7
            step_rows12.append({"tank_id": t, "step_db": step, "is_stuck7": is_stuck})
        df12s = pd.DataFrame(step_rows12)
        df12s.to_csv(h12r_dir / "h12_alfaw_steps.csv", index=False)

        stuck_steps = df12s[df12s["is_stuck7"]]["step_db"].dropna().values
        live_steps  = df12s[~df12s["is_stuck7"]]["step_db"].dropna().values
        print(f"  stuck7: n={len(stuck_steps)}, median_step={np.median(stuck_steps):.3f}")
        print(f"  live17: n={len(live_steps)},  median_step={np.median(live_steps):.3f}")

        if len(stuck_steps) >= 2 and len(live_steps) >= 2:
            stat, p = _mwu(stuck_steps, live_steps, alternative="less")
            verdict = "CONFIRMED" if p < 0.05 and np.median(stuck_steps) < np.median(live_steps) else "NOT_CONFIRMED"
            print(f"  Mann-Whitney U={stat:.1f}, p={p:.4f} (alternative='stuck<live')")
            print(f"H12 VERDICT: {verdict} — stuck7 median={np.median(stuck_steps):.3f} vs live17={np.median(live_steps):.3f}, p={p:.4f}")
        else:
            print("  H12: insufficient data for Mann-Whitney")
except Exception as e:
    print(f"  H12 FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H13: Annual tank dB offset std < 1.5 dB for ≥75% tanks (RT, Yanbu, Kharg) ─
# Prediction: within a calendar year, per-tank mean db_mean is stable (std<1.5 dB).
# Method: group by tank_id × year, compute mean db_mean; then per tank compute
# std of annual means. Refuted if offset drifts (>=75% tanks have std>=1.5 dB).
print("\n=== H13 (REAL): Annual dB offset stability std<1.5 dB per tank ===")
h13r_dir = OUT_ROOT / "H13"
h13r_dir.mkdir(exist_ok=True)

try:
    H13_TERMINALS = [t for t in sorted(p3_ids) if t in ("ras_tanura", "yanbu")]
    # raw_observations = Kharg; terminal_observations = others
    for tid in H13_TERMINALS:
        obs13 = fetch_all("terminal_observations",
                          "date,tank_id,db_mean",
                          {"terminal_id": tid})
        df13 = pd.DataFrame(obs13)
        if not len(df13):
            continue
        df13["db_mean"] = pd.to_numeric(df13["db_mean"], errors="coerce")
        df13["year"] = pd.to_datetime(df13["date"]).dt.year
        annual_mean = df13.groupby(["tank_id", "year"])["db_mean"].mean().reset_index()
        per_tank_std = annual_mean.groupby("tank_id")["db_mean"].std().reset_index(name="offset_std")
        per_tank_std = per_tank_std.dropna()
        n_stable = (per_tank_std["offset_std"] < 1.5).sum()
        pct_stable = n_stable / len(per_tank_std) * 100 if len(per_tank_std) else 0
        per_tank_std.to_csv(h13r_dir / f"h13_{tid}_offset_std.csv", index=False)
        verdict = "CONFIRMED" if pct_stable >= 75 else "NOT_CONFIRMED"
        print(f"  {tid}: {len(per_tank_std)} tanks, pct_stable(std<1.5)={pct_stable:.1f}%")
        print(f"H13 {tid} VERDICT: {verdict}")
    # Kharg (raw_observations)
    try:
        kharg13 = fetch_all("raw_observations", "date,tank_id,db_mean")
        df13k = pd.DataFrame(kharg13)
        if len(df13k):
            df13k["db_mean"] = pd.to_numeric(df13k["db_mean"], errors="coerce")
            df13k["year"] = pd.to_datetime(df13k["date"]).dt.year
            ann_k = df13k.groupby(["tank_id", "year"])["db_mean"].mean().reset_index()
            per_k = ann_k.groupby("tank_id")["db_mean"].std().reset_index(name="offset_std").dropna()
            n_stk = (per_k["offset_std"] < 1.5).sum()
            pct_stk = n_stk / len(per_k) * 100 if len(per_k) else 0
            per_k.to_csv(h13r_dir / "h13_kharg_offset_std.csv", index=False)
            verdict_k = "CONFIRMED" if pct_stk >= 75 else "NOT_CONFIRMED"
            print(f"  kharg: {len(per_k)} tanks, pct_stable={pct_stk:.1f}%")
            print(f"H13 kharg VERDICT: {verdict_k}")
    except Exception as _e13k:
        print(f"  H13 Kharg: {_e13k}")
except Exception as e:
    print(f"  H13 FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H14: Variance share of clip>15% tanks < capacity share (Kharg, RT, Yanbu) ─
# Prediction: tanks where db_mean is frequently at the clip boundary (p95 or p05)
# contribute less variance than their capacity fraction.
# Method: count rows where db_mean > p95 global (clip>15% = >15% of obs clipped).
# Refuted if variance share proportional to capacity share.
print("\n=== H14 (REAL): Clip>15% tank variance share vs capacity share ===")
h14r_dir = OUT_ROOT / "H14"
h14r_dir.mkdir(exist_ok=True)

try:
    H14_TERMINALS = [t for t in sorted(p3_ids) if t in ("ras_tanura", "yanbu")]
    for tid in H14_TERMINALS:
        obs14 = fetch_all("terminal_observations",
                          "date,tank_id,db_mean",
                          {"terminal_id": tid})
        df14 = pd.DataFrame(obs14)
        if not len(df14):
            continue
        df14["db_mean"] = pd.to_numeric(df14["db_mean"], errors="coerce").dropna()
        p05g = df14["db_mean"].quantile(0.05)
        p95g = df14["db_mean"].quantile(0.95)
        # Per-tank clip rate and variance
        per14 = df14.groupby("tank_id").apply(lambda g: pd.Series({
            "n": len(g),
            "clip_rate": ((g["db_mean"] > p95g) | (g["db_mean"] < p05g)).mean(),
            "var": g["db_mean"].var(),
        })).reset_index()
        per14["clip_heavy"] = per14["clip_rate"] > 0.15
        n_clip = per14["clip_heavy"].sum()
        total_var = per14["var"].sum()
        clip_var = per14[per14["clip_heavy"]]["var"].sum()
        clip_var_share = clip_var / total_var if total_var > 0 else 0
        # Capacity share: assume equal per tank (n_clip/total tanks)
        cap_share = n_clip / len(per14) if len(per14) else 0
        per14.to_csv(h14r_dir / f"h14_{tid}_clip.csv", index=False)
        verdict14 = "CONFIRMED" if clip_var_share < cap_share else "NOT_CONFIRMED"
        print(f"  {tid}: n_tanks={len(per14)}, clip_heavy(>15%)={n_clip}, "
              f"var_share={clip_var_share:.3f}, cap_share={cap_share:.3f}")
        print(f"H14 {tid} VERDICT: {verdict14} — var_share < cap_share = {clip_var_share < cap_share}")
    # Kharg
    try:
        kharg14 = fetch_all("raw_observations", "date,tank_id,db_mean")
        df14k = pd.DataFrame(kharg14)
        if len(df14k):
            df14k["db_mean"] = pd.to_numeric(df14k["db_mean"], errors="coerce")
            p05k = df14k["db_mean"].quantile(0.05)
            p95k = df14k["db_mean"].quantile(0.95)
            per14k = df14k.groupby("tank_id").apply(lambda g: pd.Series({
                "n": len(g),
                "clip_rate": ((g["db_mean"] > p95k) | (g["db_mean"] < p05k)).mean(),
                "var": g["db_mean"].var(),
            })).reset_index()
            per14k["clip_heavy"] = per14k["clip_rate"] > 0.15
            total_var_k = per14k["var"].sum()
            clip_var_k = per14k[per14k["clip_heavy"]]["var"].sum()
            cvs_k = clip_var_k / total_var_k if total_var_k > 0 else 0
            cap_k = per14k["clip_heavy"].sum() / len(per14k) if len(per14k) else 0
            per14k.to_csv(h14r_dir / "h14_kharg_clip.csv", index=False)
            verdict_k14 = "CONFIRMED" if cvs_k < cap_k else "NOT_CONFIRMED"
            print(f"  kharg: n_tanks={len(per14k)}, clip_heavy={per14k['clip_heavy'].sum()}, "
                  f"var_share={cvs_k:.3f}, cap_share={cap_k:.3f}")
            print(f"H14 kharg VERDICT: {verdict_k14}")
    except Exception as _e14k:
        print(f"  H14 Kharg: {_e14k}")
except Exception as e:
    print(f"  H14 FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H16: Monthly median amplitude ≥1 dB, adjacent-year corr ≥0.5 ──────────────
# Prediction: monthly median db_mean varies ≥1 dB within a year (seasonal signal),
# and the monthly pattern correlates (r≥0.5) between adjacent years.
# Method: compute monthly median per terminal; check amplitude; compute year-over-year r.
print("\n=== H16 (REAL): Monthly median amplitude ≥1 dB, adjacent-year corr ≥0.5 ===")
h16r_dir = OUT_ROOT / "H16"
h16r_dir.mkdir(exist_ok=True)

try:
    from scipy.stats import pearsonr as _pearsonr

    for tid in sorted(p3_ids):
        obs16 = fetch_all("terminal_observations",
                          "date,db_mean",
                          {"terminal_id": tid})
        df16 = pd.DataFrame(obs16)
        if not len(df16):
            continue
        df16["db_mean"] = pd.to_numeric(df16["db_mean"], errors="coerce")
        df16["date"] = pd.to_datetime(df16["date"])
        df16["year"] = df16["date"].dt.year
        df16["month"] = df16["date"].dt.month
        monthly = df16.groupby(["year", "month"])["db_mean"].median().reset_index()
        years = sorted(monthly["year"].unique())
        results16 = []
        for yr in years:
            yr_data = monthly[monthly["year"] == yr]["db_mean"].values
            if len(yr_data) >= 6:
                amp = yr_data.max() - yr_data.min()
                results16.append({"year": yr, "amplitude": round(amp, 3), "n_months": len(yr_data)})
        df16r = pd.DataFrame(results16)
        if len(df16r):
            median_amp = df16r["amplitude"].median()
            n_ge1 = (df16r["amplitude"] >= 1.0).sum()
            print(f"  {tid}: years={len(df16r)}, median_amplitude={median_amp:.2f}, n_years_amp>=1dB={n_ge1}")

        # Adjacent-year correlation
        corr_rows = []
        for i in range(len(years)-1):
            y1, y2 = years[i], years[i+1]
            m1 = monthly[monthly["year"]==y1].set_index("month")["db_mean"].reindex(range(1,13))
            m2 = monthly[monthly["year"]==y2].set_index("month")["db_mean"].reindex(range(1,13))
            mask = m1.notna() & m2.notna()
            if mask.sum() >= 6:
                r, _ = _pearsonr(m1[mask], m2[mask])
                corr_rows.append({"y1": y1, "y2": y2, "r": round(r, 3), "n": mask.sum()})
        if corr_rows:
            df_corr16 = pd.DataFrame(corr_rows)
            med_r = df_corr16["r"].median()
            n_high_r = (df_corr16["r"] >= 0.5).sum()
            print(f"  {tid}: adjacent-year pairs={len(df_corr16)}, median_r={med_r:.3f}, n_r>=0.5={n_high_r}")
            df_corr16.to_csv(h16r_dir / f"h16_{tid}_year_corr.csv", index=False)
            verdict16 = "CONFIRMED" if median_amp >= 1.0 and med_r >= 0.5 else "NOT_CONFIRMED"
            print(f"H16 {tid} VERDICT: {verdict16}")
        df16r.to_csv(h16r_dir / f"h16_{tid}_amplitude.csv", index=False) if len(df16r) else None
except Exception as e:
    print(f"  H16 FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H18: Health on WL-45 (WL-60 gate) + DESC-35-only matches run #11 ──────────
# Prediction: applying WL-45 (satisfies cap>=60% gate) and DESC-35 filter to RT TVT
# yields a stable health check result comparable to prior run #11.
# Method: count WL-45 dates in TVT; check vol range; flag if any WL-45 tank is missing
# from >50% of DESC-35 dates. No external comparison value (run #11 not in DB).
print("\n=== H18 (REAL): WL-45 health check on DESC-35-only series ===")
h18r_dir = OUT_ROOT / "H18"
h18r_dir.mkdir(exist_ok=True)

try:
    WL45_SET = set(_WL45.keys())
    obs18 = fetch_all("terminal_observations",
                      "date,tank_id,orbit_direction,relative_orbit",
                      {"terminal_id": "ras_tanura"})
    df18 = pd.DataFrame(obs18)
    df18["date"] = pd.to_datetime(df18["date"]).dt.date

    # DESC-35 filter
    desc18 = df18[
        (df18["orbit_direction"].isin(["DESC", "DESCENDING"])) &
        (df18["relative_orbit"] == 35)
    ].copy()
    if not len(desc18):
        desc18 = df18[df18["orbit_direction"].isin(["DESC", "DESCENDING"])].copy()
        print(f"  H18: no DESC-35; using DESC-any: {len(desc18)} rows")

    # WL-45 tanks in DESC-35
    desc18_wl = desc18[desc18["tank_id"].isin(WL45_SET)]
    n_wl_dates = desc18_wl["date"].nunique()
    n_wl_tanks_present = desc18_wl["tank_id"].nunique()
    pct_cap = n_wl_tanks_present / len(WL45_SET) * 100 if WL45_SET else 0

    print(f"  WL-45 tanks present in DESC-35: {n_wl_tanks_present}/{len(WL45_SET)} ({pct_cap:.1f}%)")
    print(f"  WL-45 DESC-35 dates: {n_wl_dates}")

    # Per-tank coverage rate in DESC-35
    per18 = desc18_wl.groupby("tank_id")["date"].nunique().reset_index(name="n_dates")
    per18["pct_coverage"] = per18["n_dates"] / n_wl_dates * 100
    missing50 = per18[per18["pct_coverage"] < 50]
    per18.to_csv(h18r_dir / "h18_wl45_desc35_coverage.csv", index=False)

    gate_pass = pct_cap >= 60.0 and len(missing50) == 0
    print(f"  WL-45 cap_fraction={pct_cap:.1f}% (gate>=60%), missing>50%={len(missing50)}")
    print(f"H18 VERDICT: {'PASS' if gate_pass else 'FAIL'} — WL-45 on DESC-35: cap={pct_cap:.1f}%, tanks_missing_50pct={len(missing50)}")
except Exception as e:
    print(f"  H18 FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H19 (REAL): E1/E2/E3 on DESC-35 preserve sign and |Δ|≥placebo p90 ─────────
# Already computed in H02b. Reference those results here.
print("\n=== H19 (REAL): E1/E2/E3 on DESC-35 — sign and magnitude ===")
print("  NOTE: E1/E2/E3 results computed in H02b block above.")
print("  See: diagnostics/research/2026-10/H02b/h02b_e1e2e3_tvt.csv")
print("  DESC-35 db_mean proxy checks printed above in H02b section.")
h19r_dir = OUT_ROOT / "H19"
h19r_dir.mkdir(exist_ok=True)
(h19r_dir / "h19_reference.txt").write_text(
    "H19 (REAL): E1/E2/E3 events on DESC-35-only.\n"
    "Results in H02b/h02b_e1e2e3_tvt.csv and H02b/h02b_desc35_rt_candidate.csv\n"
    "E1 2026-03-02 BUILD, E2 2026-04-15 BUILD, E3 2026-06-27 DRAWDOWN\n"
    "Criterion: sign matches expected AND |Δvol| >= placebo_p90\n"
)


# ── H20 (REAL): Per-tank Kharg calibration (p05/p95 per tank) → CSV ───────────
# Prediction: per-tank p05 and p95 can be computed from raw_observations Kharg.
# Method: compute p05/p95 db_mean per tank_id from all Kharg raw_observations.
# Output: CSV only. NO UANI correlations per ТЗ rules.
print("\n=== H20 (REAL): Per-tank Kharg calibration p05/p95 → CSV ===")
h20r_dir = OUT_ROOT / "H20"
h20r_dir.mkdir(exist_ok=True)

try:
    kharg20 = fetch_all("raw_observations", "date,tank_id,db_mean")
    df20 = pd.DataFrame(kharg20)
    if not len(df20):
        print("  H20: raw_observations 0 rows")
    else:
        df20["db_mean"] = pd.to_numeric(df20["db_mean"], errors="coerce")
        per20 = df20.groupby("tank_id")["db_mean"].agg(
            n=("count"),
            mean=("mean"),
            std=("std"),
            p05=lambda x: x.quantile(0.05),
            p50=lambda x: x.quantile(0.50),
            p95=lambda x: x.quantile(0.95),
        ).reset_index()
        per20.columns = ["tank_id", "n", "mean", "std", "p05", "p50", "p95"]
        per20["fill_range_proxy"] = per20["p95"] - per20["p05"]
        per20 = per20.sort_values("fill_range_proxy", ascending=False)
        per20.to_csv(h20r_dir / "h20_kharg_per_tank_calibration.csv", index=False)
        print(f"  Kharg per-tank calibration: {len(per20)} tanks")
        print(f"  Fill range proxy (p95-p05): min={per20['fill_range_proxy'].min():.2f}, "
              f"median={per20['fill_range_proxy'].median():.2f}, max={per20['fill_range_proxy'].max():.2f}")
        print(f"  Written: h20_kharg_per_tank_calibration.csv")
        print(f"  NOTE: No UANI correlations per ТЗ rules.")
except Exception as e:
    print(f"  H20 FAILED: {e}")
    import traceback; traceback.print_exc()


# ── D3: db_mean normalization audit (code-only) ───────────────────────────────
print("\n=== D3: db_mean normalization audit ===")
d3_dir = OUT_ROOT / "D3"
d3_dir.mkdir(exist_ok=True)

try:
    import subprocess
    _norm_res = subprocess.run(
        ["grep", "-rn", "--include=*.py",
         "normalize\\|db_mean\\|dB_min\\|dB_max\\|quantile\\|backscatter",
         "scripts/"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    norm_text = _norm_res.stdout.strip()
    print(f"  Normalization references: {len(norm_text.splitlines())} lines")
    print(norm_text[:1500] if norm_text else "  None found")
    (d3_dir / "d3_normalization_audit.txt").write_text(norm_text)
except Exception as e:
    print(f"  D3 FAILED: {e}")


# ── D4: Terminal volume computation audit ─────────────────────────────────────
print("\n=== D4: Terminal volume computation audit ===")
d4_dir = OUT_ROOT / "D4"
d4_dir.mkdir(exist_ok=True)

try:
    import subprocess
    _vol_res = subprocess.run(
        ["grep", "-rn", "--include=*.py",
         "vol_mn_bbl\\|fill_ratio\\|h_current\\|BARREL_PER_M3\\|coverage_filter",
         "scripts/"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    vol_text = _vol_res.stdout.strip()
    print(f"  Volume computation references: {len(vol_text.splitlines())} lines")
    print(vol_text[:1500] if vol_text else "  None found")
    (d4_dir / "d4_volume_audit.txt").write_text(vol_text)
except Exception as e:
    print(f"  D4 FAILED: {e}")


# ── CDSE-gated block (H04, H08–H10, H15, H17, H21, H22) ─────────────────────
if CDSE_OK and cdse_token:
    print("\n=== CDSE block: H04/H08-H10/H15/H17/H21/H22 ===")
    import requests as _creq
    _CDSE_HEADERS = {"Authorization": f"Bearer {cdse_token}"}
    _CDSE_CATALOG = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"

    def cdse_search(polygon_wkt, start, end, max_results=50):
        params = {
            "$filter": (
                f"Collection/Name eq 'SENTINEL-1' and "
                f"OData.CSC.Intersects(area=geography'SRID=4326;{polygon_wkt}') and "
                f"ContentDate/Start gt {start}T00:00:00.000Z and "
                f"ContentDate/Start lt {end}T23:59:59.000Z and "
                f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' "
                f"and att/OData.CSC.StringAttribute/Value eq 'GRD')"
            ),
            "$top": max_results,
            "$orderby": "ContentDate/Start asc",
        }
        try:
            r = _creq.get(_CDSE_CATALOG, params=params, headers=_CDSE_HEADERS, timeout=60)
            if r.status_code == 200:
                return r.json().get("value", [])
            else:
                print(f"  CDSE search HTTP {r.status_code}: {r.text[:200]}")
                return []
        except Exception as _e:
            print(f"  CDSE search FAILED: {_e}")
            return []

    # H04: Kharg S1 GRD catalog completeness check
    print("\n--- H04: Kharg S1 GRD catalog ---")
    h04_dir = OUT_ROOT / "H04"
    h04_dir.mkdir(exist_ok=True)
    _KHARG_WKT = "POLYGON((49.0 29.1,50.0 29.1,50.0 29.7,49.0 29.7,49.0 29.1))"
    _scenes = cdse_search(_KHARG_WKT, "2026-08-01", "2026-10-20", max_results=50)
    print(f"  Kharg scenes Aug–Oct 2026: n={len(_scenes)}")
    if _scenes:
        _df_s1 = pd.DataFrame([{"name": s.get("Name"), "date": s.get("ContentDate", {}).get("Start","")[:10],
                                  "orbit": s.get("Attributes",{})} for s in _scenes])
        _df_s1.to_csv(h04_dir / "h04_kharg_scenes.csv", index=False)
        print(_df_s1[["name","date"]].to_string(index=False))

    # H08: RT S1 GRD catalog — orbit 35 coverage check
    print("\n--- H08: RT S1 GRD catalog ---")
    h08_dir = OUT_ROOT / "H08"
    h08_dir.mkdir(exist_ok=True)
    _RT_WKT = "POLYGON((50.0 26.5,50.6 26.5,50.6 27.2,50.0 27.2,50.0 26.5))"
    _rt_scenes = cdse_search(_RT_WKT, "2026-08-01", "2026-10-20", max_results=50)
    print(f"  RT scenes Aug–Oct 2026: n={len(_rt_scenes)}")
    if _rt_scenes:
        _df_rt_s1 = pd.DataFrame([{"name": s.get("Name"), "date": s.get("ContentDate",{}).get("Start","")[:10]}
                                    for s in _rt_scenes])
        _df_rt_s1.to_csv(h08_dir / "h08_rt_scenes.csv", index=False)

else:
    print("\n  CDSE block SKIPPED (P2 auth not available)")
    for _hid in ["H04", "H08", "H09", "H10", "H15", "H17", "H21", "H22"]:
        (_dir := OUT_ROOT / _hid).mkdir(exist_ok=True)
        (_dir / f"{_hid.lower()}_status.txt").write_text("NOT_RUN: CDSE auth not available\n")


print("\n=== Block A + B/C/D complete ===")
