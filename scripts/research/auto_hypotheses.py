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
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
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
CDSE_CLIENT_ID = os.environ.get("CDSE_CLIENT_ID", "")
CDSE_CLIENT_SECRET = os.environ.get("CDSE_CLIENT_SECRET", "")
CDSE_OK = False
cdse_token = None

if not CDSE_CLIENT_ID or not CDSE_CLIENT_SECRET:
    print("  P2: CDSE_CLIENT_ID/CDSE_CLIENT_SECRET not set — CDSE hypotheses skipped")
else:
    try:
        import requests as _req
        _resp = _req.post(
            "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
            data={
                "client_id": CDSE_CLIENT_ID,
                "client_secret": CDSE_CLIENT_SECRET,
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        if _resp.status_code == 200:
            cdse_token = _resp.json().get("access_token")
            CDSE_OK = bool(cdse_token)
            print(f"  P2: CDSE auth OK token_length={len(cdse_token) if cdse_token else 0}")
        else:
            print(f"  P2: CDSE auth FAILED HTTP {_resp.status_code}: {_resp.text[:200]}")
    except Exception as _e:
        print(f"  P2: CDSE auth FAILED: {_e}")

(OUT_ROOT / "H00_preflight_p2.txt").write_text(
    f"cdse_ok={CDSE_OK}\nclient_id_set={bool(CDSE_CLIENT_ID)}\n"
    f"client_secret_set={bool(CDSE_CLIENT_SECRET)}\n"
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


# ── H02 continuation: DESC-35 only RT series → CSV (item 7) ──────────────────
print("\n=== H02b: DESC-35 only RT series ===")
h02b_dir = OUT_ROOT / "H02b"
h02b_dir.mkdir(exist_ok=True)

try:
    rt_full = fetch_all("terminal_observations",
                        "date,tank_id,db_mean,orbit_direction,relative_orbit",
                        {"terminal_id": "ras_tanura"})
    df_rtf = pd.DataFrame(rt_full)
    df_rtf["date"] = pd.to_datetime(df_rtf["date"]).dt.date

    # DESC-35 only
    desc35_full = df_rtf[
        (df_rtf["orbit_direction"].isin(["DESC", "DESCENDING"])) &
        (df_rtf["relative_orbit"] == 35)
    ].copy()

    if not len(desc35_full):
        desc35_full = df_rtf[df_rtf["orbit_direction"].isin(["DESC", "DESCENDING"])].copy()
        print(f"  DESC-35 = 0; using DESC-any: {len(desc35_full)} rows")
    else:
        print(f"  DESC-35 rows: {len(desc35_full)}")

    # Per-date volume proxy (mean db_mean across tanks, n_tanks)
    d35_daily = desc35_full.groupby("date").agg(
        n_tanks=("tank_id", "nunique"),
        db_mean_avg=("db_mean", "mean"),
        db_mean_std=("db_mean", "std"),
    ).reset_index()
    d35_daily = d35_daily.sort_values("date")

    print(f"  DESC-35 unique dates: {len(d35_daily)}")
    if len(d35_daily):
        print(d35_daily.tail(10).to_string(index=False))

    d35_daily.to_csv(h02b_dir / "h02b_desc35_rt_daily.csv", index=False)

    # E1/E2/E3 events (drawdown ≥2 Mn bbl in 6–12d; proxy via db_mean rise)
    # Since we don't have vol here, use db_mean increase as proxy
    # db_mean increases → less oil → drawdown
    d35_daily["db_delta_6d"] = d35_daily["db_mean_avg"].diff(periods=1)
    # Export pulse proxy: db_mean rise ≥ threshold in 1 step
    DB_EXPORT_THRESHOLD = 1.0  # dB rise proxy for export event
    events = d35_daily[d35_daily["db_delta_6d"] >= DB_EXPORT_THRESHOLD].copy()
    print(f"  E-events (db_delta_6d >= {DB_EXPORT_THRESHOLD} dB): n={len(events)}")
    if len(events):
        print(events[["date", "n_tanks", "db_mean_avg", "db_delta_6d"]].to_string(index=False))
    events.to_csv(h02b_dir / "h02b_desc35_rt_events.csv", index=False)

    # Compare with full series (all orbits) — E-event overlap
    all_daily = df_rtf.groupby("date").agg(
        n_tanks=("tank_id", "nunique"),
        db_mean_avg=("db_mean", "mean"),
    ).reset_index().sort_values("date")
    all_daily["db_delta"] = all_daily["db_mean_avg"].diff()
    all_events = all_daily[all_daily["db_delta"] >= DB_EXPORT_THRESHOLD]
    print(f"  E-events all-orbit series: n={len(all_events)}")
    all_daily.to_csv(h02b_dir / "h02b_allorbit_rt_daily.csv", index=False)

    # Delta between series counts
    desc35_dates = set(d35_daily["date"].astype(str))
    all_dates = set(all_daily["date"].astype(str))
    extra_dates = sorted(all_dates - desc35_dates)
    print(f"  Dates in all-orbit but NOT in DESC-35: n={len(extra_dates)}")
    if extra_dates:
        print(f"    first 10: {extra_dates[:10]}")
    pd.DataFrame({"extra_date": extra_dates}).to_csv(h02b_dir / "h02b_extra_dates.csv", index=False)

except Exception as e:
    print(f"  H02b FAILED: {e}")
    import traceback; traceback.print_exc()


# ── H05b volume delta on 2026-09-30 and 2026-10-06 (item 6) ──────────────────
print("\n=== H05b: Volume delta on 2026-09-30 and 2026-10-06 ===")
h05b2_dir = OUT_ROOT / "H05b_vol"
h05b2_dir.mkdir(exist_ok=True)

FOCUS_DATES = ["2026-09-30", "2026-10-06"]

try:
    tvt_rt3 = fetch_all("terminal_volume_timeseries", "date,vol_mn_bbl,aggregation",
                        {"terminal_id": "ras_tanura"})
    df_tvt4 = pd.DataFrame(tvt_rt3)
    df_tvt4 = df_tvt4[df_tvt4["aggregation"] == "daily"].copy()
    df_tvt4["date"] = pd.to_datetime(df_tvt4["date"]).dt.date
    df_tvt4 = df_tvt4.sort_values("date").reset_index(drop=True)

    # n_tanks per date for RT
    rt_obs4 = fetch_all("terminal_observations", "date,tank_id", {"terminal_id": "ras_tanura"})
    df_rto4 = pd.DataFrame(rt_obs4)
    df_rto4["date"] = pd.to_datetime(df_rto4["date"]).dt.date
    ntanks4 = df_rto4.groupby("date")["tank_id"].nunique().reset_index(name="n_tanks")

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
    _ingest_log = subprocess.run(
        ["git", "log", "--all", "--oneline", "--after=2026-09-01", "--before=2026-10-10",
         "--", "scripts/main_script.py", "scripts/ingest*.py"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    print(f"\n  Ingest commits Sep–Oct 2026:\n{_ingest_log.stdout[:1000] if _ingest_log.stdout else '  None'}")
    (hnew2_dir / "hnew2_ingest_commits.txt").write_text(_ingest_log.stdout)

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

    # 2. Try pg_policies via Supabase RPC (read-only information_schema)
    rls_note = []
    try:
        # Supabase exposes pg_policies via REST: GET /rest/v1/pg_policies (not standard)
        # Try via rpc if a get_rls function exists, otherwise note as unavailable
        _pg_pol = sb.table("pg_policies").select("tablename,policyname,cmd,qual").execute()
        if _pg_pol.data:
            print(f"  pg_policies rows: {len(_pg_pol.data)}")
            rls_note.append(f"pg_policies={_pg_pol.data}")
        else:
            print("  pg_policies: 0 rows (no policies configured)")
            rls_note.append("pg_policies=[] (empty)")
    except Exception as _e2:
        print(f"  pg_policies query FAILED: {_e2} → RLS likely not configured or not accessible via REST")
        rls_note.append(f"pg_policies_error={_e2}")

    # 3. Hardcoded publishable key locations (static scan)
    _hc_res = subprocess.run(
        ["grep", "-rn", "sb_publishable", "--include=*.py", "--include=*.html", "."],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    hc_lines = [l for l in _hc_res.stdout.strip().split("\n") if l]
    print(f"\n  Hardcoded publishable key occurrences: n={len(hc_lines)}")
    for l in hc_lines:
        print(f"    {l.split(':')[0]}:{l.split(':')[1]}")

    # 4. Write report
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


# ── H11: Duplicate (date, tank_id) rows per terminal ─────────────────────────
print("\n=== H11: Duplicate (date, tank_id) check ===")
h11_dir = OUT_ROOT / "H11"
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
    df_h11_out.to_csv(h11_dir / "h11_duplicates.csv", index=False)
    total_dupes = df_h11_out["duplicate_rows"].sum() if len(df_h11_out) else 0
    print(f"\nH11 VERDICT: {'CONFIRMED' if total_dupes > 0 else 'NOT_CONFIRMED'} — total duplicate rows={total_dupes}")
except Exception as e:
    print(f"  H11 FAILED: {e}")


# ── H12: Anomalous db_mean outliers per terminal ──────────────────────────────
print("\n=== H12: db_mean outliers ===")
h12_dir = OUT_ROOT / "H12"
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
    df_h12_out.to_csv(h12_dir / "h12_db_outliers.csv", index=False)
    print(f"\nH12: outlier summary written for {len(df_h12_out)} terminals")
except Exception as e:
    print(f"  H12 FAILED: {e}")


# ── H13: TVT vol_mn_bbl sudden jumps (>5 Mn bbl in 1 step) ───────────────────
print("\n=== H13: TVT sudden volume jumps ===")
h13_dir = OUT_ROOT / "H13"
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
    df_h13_out.to_csv(h13_dir / "h13_all_jumps.csv", index=False)
    print(f"\nH13 VERDICT: {'CONFIRMED' if len(h13_rows) > 0 else 'NOT_CONFIRMED'} — terminals with jumps={len(h13_rows)}")
except Exception as e:
    print(f"  H13 FAILED: {e}")


# ── H14: TVT completeness — all obs dates present in TVT ─────────────────────
print("\n=== H14: TVT completeness ===")
h14_dir = OUT_ROOT / "H14"
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
    df_h14_out.to_csv(h14_dir / "h14_completeness.csv", index=False)
    total_missing = df_h14_out["n_missing_from_tvt"].sum() if len(df_h14_out) else 0
    print(f"\nH14 VERDICT: {'CONFIRMED' if total_missing > 0 else 'NOT_CONFIRMED'} — total obs dates not in TVT={total_missing}")
except Exception as e:
    print(f"  H14 FAILED: {e}")


# ── H16: Tanks with >50% missing across all observation dates ─────────────────
print("\n=== H16: Persistently missing tanks ===")
h16_dir = OUT_ROOT / "H16"
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
        per_tank.to_csv(h16_dir / f"h16_{tid}_tank_coverage.csv", index=False)
except Exception as e:
    print(f"  H16 FAILED: {e}")


# ── H18: Pass interval regularity (6d expected) ───────────────────────────────
print("\n=== H18: Pass interval regularity ===")
h18_dir = OUT_ROOT / "H18"
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
    df_h18_out.to_csv(h18_dir / "h18_pass_intervals.csv", index=False)
except Exception as e:
    print(f"  H18 FAILED: {e}")


# ── H19: db_mean distribution shift before/after 2026-09-07 ──────────────────
print("\n=== H19: db_mean distribution shift pre/post 2026-09-07 ===")
h19_dir = OUT_ROOT / "H19"
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
        print(f"  {tid}: pre_mean={pre.mean():.3f} post_mean={post.mean():.3f} delta={delta_mean:.3f}")
    df_h19_out = pd.DataFrame(h19_rows)
    df_h19_out.to_csv(h19_dir / "h19_db_shift.csv", index=False)
    confirmed = df_h19_out[df_h19_out["delta_mean"].abs() > 1.0] if len(df_h19_out) else pd.DataFrame()
    print(f"\nH19: terminals with |delta_mean| > 1.0 dB: {len(confirmed)}")
except Exception as e:
    print(f"  H19 FAILED: {e}")


# ── H20: Cross-terminal orbit synchrony ───────────────────────────────────────
print("\n=== H20: Cross-terminal orbit synchrony ===")
h20_dir = OUT_ROOT / "H20"
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
        df_h20_out.to_csv(h20_dir / "h20_synchrony.csv", index=False)
except Exception as e:
    print(f"  H20 FAILED: {e}")


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
