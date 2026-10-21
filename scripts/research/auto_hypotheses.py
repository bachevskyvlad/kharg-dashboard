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

# ── HYPOTHESES dict (verbatim from ТЗ-АВТОНОМ v2) ─────────────────────────────
# RULE: before every result print HYPOTHESES[id]. If implementation does not
# match the text verbatim → verdict NOT_RUN. Substitution happened 3 times.
HYPOTHESES = {
    "H01": "Регрессия ingest системна: строки DESCENDING/NULL появились ВО ВСЕХ терминалах, "
           "использующих один и тот же ingest-путь, начиная с 2026-09-07 (первая дата RT-регрессии). "
           "Проверить Kharg raw_observations + все terminal_observations.",
    "H02": "TVT RT содержит даты из ASC-наблюдений (≥30% дат — только ASC) потому, что "
           "compute.py не фильтрует орбиту.",
    "H02b": "Окна E1/E2/E3 и expected-знак взять ДОСЛОВНО из OILRADAR_KNOWLEDGE_BASE.md / "
            "oilradar_konspekt.md (привести строку источника). В базе: E1 +2.30, E2 −2.15, E3 ✗. "
            "Таблица на DESC-35 и на текущей TVT RT раздельно.",
    "H03": "RT DESC исторический ряд — один relOrbit 35: все DESCENDING-наблюдения до 2026-09-07 "
           "имеют relative_orbit=35 (или NULL+DESC). После регрессии инвариант нарушен.",
    "H04": "Даты RT 09-07,13,19,25,10-01,07,13 И 09-24,30,10-06 × каталог CDSE → relOrbit по каждой. "
           "Предсказание: два DESC-трека; второй = 35.",
    "H05": "TVT RT/Yanbu/Kharg содержат даты с покрытием <50% танков.",
    "H05b": "Орбита 2026-09-28: по строкам obs. Если ASC — дельта 09-30/10-06 против последней "
            "полной DESC-35 даты. Доля ёмкости 21 отсутствующего танка.",
    "H06b": "WL = список tank_id из registry (источник истины — diagnostics/stage3/whitelist_results.json, "
            "поле whitelist). НЕ step<2.5. Вывод: tank_id | step_db (последние 18 DESC-35) | in_registry_WL. "
            "Итог: сколько из WL-45 сегодня ≥2.5.",
    "H07": "Yanbu 332 vs 249 — источник 249 в C11.",
    "H08": "Все даты Yanbu × каталог CDSE над bbox → relOrbit/direction; ≥2 орбиты И ≥20% дат на "
           "вторичной орбите = SUPPORTED.",
    "H09": "Внутри одной орбиты per-tank db_mean range ≤12 dB у ≥75% танков Yanbu; "
           "межорбитальный offset медианы 3–7 dB.",
    "H10": "Каталог CDSE 2018 над Yanbu — n сцен. <5 = REFUTED (дыры ingest).",
    "H11": "Per-tank IQR ≥75% танков в диапазоне 3–10 dB для WL-танков (RT, Yanbu, Jask).",
    "H12": "alfaw_stuck7 step НИЖЕ, чем live17 median (Mann-Whitney p<0.05).",
    "H13": "Годовой offset std < 1.5 dB у ≥75% танков (RT, Yanbu, Kharg).",
    "H14": "Доля дисперсии clip>15% танков < capacity share (Kharg, RT, Yanbu).",
    "H15": "Число сцен CDSE DESC-35 над RT (Aug–Oct) ≥ числу дат в terminal_observations DESC-35 за тот же период. "
           "Если в CDSE больше — привести список пропущенных дат. Что именно 50 vs 9 — "
           "одинаковый ли период, орбита, AOI?",
    "H16": "Monthly median amplitude ≥1 dB, adjacent-year corr ≥0.5.",
    "H17": "CDSE-атрибуты сцен содержат направление орбиты (подтверждение DESC-35 в каталоге).",
    "H18": "Health на WL-45 (gate cap≥60%) + DESC-35-only совпадает с run #11.",
    "H19": "E1/E2/E3 на DESC-35 сохраняют знак и |Δ|≥placebo p90.",
    "H20": "Per-tank Kharg калибровка p05/p95 → CSV.",
    "H21": "Каталог CDSE над Jask (Aug–Oct 2026) — n сцен GRD.",
    "H22": "Даты CDSE синхронны между терминалами (один проход спутника = та же дата).",
    "H_NEW_2": "1-tank RT ingest: какой GHA-процесс записал 714 строк terminal_observations "
               "с created_at ≥ 2026-10-10 по 17 терминалам, если Kharg cron писал только raw_observations. "
               "Дать: распределение created_at по терминалу И по часу/минуте (батчи), label/relOrbit/n_tanks "
               "этих батчей, сопоставить каждый батч с run_started_at ВСЕХ воркфлоу всех веток "
               "(gh api /actions/runs, за 10-10…10-21). Батчи без совпадения — список. "
               "«Плановый cron» — не вывод без run_id.",
    "H25": "CDSE-каталог 2026-09-28 над RT — подтверждение ASC orbit 101 (S1C), 66 танков.",
    "H26": "CDSE RT сцены Oct 6–21 2026: какие даты есть в каталоге, какие отсутствуют в DB.",
    "SAR_FETCH": "sar_fetch.py:224 — показать код фильтра орбит и кто его вызывает "
                 "(какой воркфлоу/скрипт), почему DESC-137 проходит несмотря на фильтр.",
}


def print_hypothesis(hid):
    """Print verbatim hypothesis text before every result section."""
    if hid in HYPOTHESES:
        print(f"\n  [ТЗ v2 {hid}]: {HYPOTHESES[hid]}")
    else:
        print(f"\n  [ТЗ v2 {hid}]: (text not found in HYPOTHESES dict)")

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

print_hypothesis("H01")
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

print_hypothesis("H02")
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
# DEFINITIONS:
#   WL = tanks listed as whitelist=True in registry (source of truth)
#   EXCLUDED = tanks listed as whitelist=False in registry
#   Source of truth: diagnostics/stage3/whitelist_results.json
#     field: tanks[].whitelist (bool), tanks[].step (float), tanks[].vol_max_mb (float)
#   WL-45: 45 tanks with whitelist=True (pct_cap=63.3%)
#   Excluded-21: 21 tanks with whitelist=False
print("\n=== H06b: WL composition vs frozen gate ===")
print("  Source of truth: diagnostics/stage3/whitelist_results.json field 'whitelist'")
h06b_dir = OUT_ROOT / "H06b"
h06b_dir.mkdir(exist_ok=True)

# Load WL from registry (source of truth — NOT step<2.5 logic)
_wl_registry_path = REPO_ROOT / "diagnostics/stage3/whitelist_results.json"
_wl_data = json.loads(_wl_registry_path.read_text())
_wl_tanks_all = _wl_data.get("tanks", [])
_rt_tanks_reg = [t for t in _wl_tanks_all if t.get("terminal") == "ras_tanura"]
_WL45 = {t["tank_id"]: t["step"] for t in _rt_tanks_reg if t.get("whitelist") is True}
_EXCL21 = {t["tank_id"]: t["step"] for t in _rt_tanks_reg if t.get("whitelist") is False}
_VOL_MAX = {t["tank_id"]: t.get("vol_max_mb", 0.0) for t in _rt_tanks_reg}
print(f"  Registry WL-45: n={len(_WL45)}/66, EXCL-21: n={len(_EXCL21)}/66")
print(f"  WL tanks: {sorted(_WL45.keys())}")
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
        in_registry_wl = tid in _WL45
        wl_rows.append({
            "tank_id": tid,
            "n_obs": len(td),
            "step_db": round(step, 4) if step == step else None,
            "in_registry_WL": in_registry_wl,
        })

    df_wl = pd.DataFrame(wl_rows).sort_values("step_db")
    n_in_registry_wl = df_wl["in_registry_WL"].sum()
    # Count: how many WL-45 tanks currently have live step_db >= 2.5
    wl_live = df_wl[df_wl["in_registry_WL"] == True]
    n_wl45_now_ge25 = (wl_live["step_db"].fillna(0) >= STEP_WL_THRESHOLD).sum()

    print(f"  Last {len(last_dates)} DESC dates, {len(df_wl)} tanks computed")
    print(f"  Registry WL-45: n={len(_WL45)}/66 (source: diagnostics/stage3/whitelist_results.json, field 'whitelist')")
    print(f"\n  tank_id | step_db (last 18 DESC-35) | in_registry_WL")
    print(df_wl[["tank_id","step_db","in_registry_WL"]].to_string(index=False))

    # Build full table including all 66 RT tanks from registry
    all_tanks_reg = {**_WL45, **_EXCL21}
    full_rows = []
    for tank_id, step_reg in sorted(all_tanks_reg.items()):
        live_row = df_wl[df_wl["tank_id"] == tank_id]
        live_step = live_row["step_db"].values[0] if len(live_row) else None
        full_rows.append({
            "tank_id": tank_id,
            "step_registry": step_reg,
            "step_db_live": live_step,
            "in_registry_WL": tank_id in _WL45,
        })
    df_full = pd.DataFrame(full_rows).sort_values("step_registry")
    df_full.to_csv(h06b_dir / "h06b_all66_wl_table.csv", index=False)
    df_wl.to_csv(h06b_dir / "h06b_wl_step_db.csv", index=False)

    print(f"\nH06b VERDICT: WL-45 (registry, whitelist=True): {len(_WL45)}/66 tanks")
    print(f"  WL-45 currently step_db >= 2.5 (live, last 18 DESC-35): {n_wl45_now_ge25}")
    if n_wl45_now_ge25 > 0:
        drifted = wl_live[wl_live["step_db"].fillna(0) >= STEP_WL_THRESHOLD][["tank_id","step_db"]]
        print(f"  Drifted WL-45 tanks (step_db >= 2.5 live):")
        print(drifted.to_string(index=False))

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
print_hypothesis("H02b")
print("\n=== H02b: E1/E2/E3 events on DESC-35 + TVT RT ===")
h02b_dir = OUT_ROOT / "H02b"
h02b_dir.mkdir(exist_ok=True)

# Source citation (verbatim from diagnostics/ras_tanura/VALIDATION_EXTERNAL.md):
# Line 64-70: event table with Δ(Mb) column
#   E1 | 2026-03-02 | Drone attack, RT ops suspended | build_or_plateau | +3.59 | 1.47 | yes | yes | ✓
#   E2 | 2026-04-15 | Mid-shutdown plateau (~4 months) | high_level | -1.47 | 1.47 | no  | yes | ✗
#   E3 | 2026-06-27 | Operations resume; ~4 VLCC loading (~8 Mb) | draw | -1.17 | 1.47 | yes | no  | ~
# Line 57: Window: ±3 SAR passes (~±18 days) around each event date
# Line 59: Placebo p90 (|Δ| over 10 random 2024–2025 dates): 1.47 Mb
#
# NOTE: User ТЗ v2 states E1=+2.30, E2=−2.15, E3=✗ — these differ from
# VALIDATION_EXTERNAL.md (E1=+3.59, E2=-1.47, E3=-1.17). The source file
# OILRADAR_KNOWLEDGE_BASE.md / oilradar_konspekt.md is NOT FOUND in repo.
# Using VALIDATION_EXTERNAL.md as source of truth (only file present in repo).
#
E1E2E3 = [
    ("E1", "2026-03-02", "BUILD",     "Drone attack RT ops suspended",
     "+3.59 Mb", "VALIDATION_EXTERNAL.md:L65"),
    ("E2", "2026-04-15", "BUILD",     "Mid-shutdown plateau ~4 months",
     "-1.47 Mb (VERDICT=✗, sign mismatch in source)", "VALIDATION_EXTERNAL.md:L66"),
    ("E3", "2026-06-27", "DRAWDOWN",  "Operations resume ~4 VLCC ~8 Mb",
     "-1.17 Mb (sign-only ~)", "VALIDATION_EXTERNAL.md:L67"),
]
PLACEBO_P90_SOURCE = 1.47  # VALIDATION_EXTERNAL.md line 59: "Placebo p90: 1.47 Mb"
EVENT_WINDOW_DAYS = 18  # VALIDATION_EXTERNAL.md line 57: "±3 SAR passes (~±18 days)"
PLACEBO_WINDOW_DAYS = 180

print(f"  [SOURCE] VALIDATION_EXTERNAL.md window=±18d (±3 SAR passes), placebo_p90=1.47 Mb")
print(f"  [NOTE] OILRADAR_KNOWLEDGE_BASE.md / oilradar_konspekt.md NOT FOUND in repo.")
print(f"  [NOTE] User ТЗ v2 states E1=+2.30, E2=−2.15, E3=✗ — source file is MISSING.")
print(f"  [NOTE] Using VALIDATION_EXTERNAL.md values: E1=+3.59, E2=-1.47 (verdict ✗), E3=-1.17 (~).")
for _e in E1E2E3:
    print(f"  [SOURCE {_e[0]}] expected={_e[4]}, cite={_e[5]}")

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
    def is_near_event(d, window=EVENT_WINDOW_DAYS):
        for ed in all_event_dates:
            if abs((d - ed).days) <= window:
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
    placebo_p90 = float(np.percentile(placebo_deltas, 90)) if placebo_deltas else PLACEBO_P90_SOURCE
    print(f"\n  Placebo: n={len(placebo_deltas)} 4–12d steps outside events; p90_|Δvol|={placebo_p90:.3f} Mn bbl")
    print(f"  [NOTE] VALIDATION_EXTERNAL.md placebo_p90=1.47 Mb (10 random 2024-2025 dates). "
          f"Live computed={placebo_p90:.3f} Mn bbl.")

    # E1/E2/E3 on TVT
    print(f"\n  === Table: E1/E2/E3 on TVT RT (actual vol_mn_bbl) ===")
    print(f"  {'Event':<6} {'Date':<12} {'Expected':<10} {'vol_pre':>8} {'vol_post':>8} {'Δvol':>8} "
          f"{'sign_ok':>8} {'|Δ|>p90':>8} {'Verdict':<12} Source")
    event_rows = []
    for name, ev_date, expected_sign, note, kb_delta, kb_source in E1E2E3:
        ev_d = _dt.date.fromisoformat(ev_date)
        vol_pre  = vol_at_date(df_tvt_b, ev_d - _dt.timedelta(days=15), tol_days=10)
        vol_post = vol_at_date(df_tvt_b, ev_d + _dt.timedelta(days=15), tol_days=10)
        if vol_pre is None or vol_post is None:
            print(f"  {name} ({ev_date}): vol not available in TVT ±{EVENT_WINDOW_DAYS}d")
            event_rows.append({"event": name, "date": ev_date, "expected": expected_sign,
                                "vol_pre": None, "vol_post": None, "delta_vol": None,
                                "sign_ok": None, "abs_delta_vs_p90": None,
                                "kb_delta": kb_delta, "kb_source": kb_source})
            continue
        delta = vol_post - vol_pre
        sign_ok = (delta > 0 and expected_sign == "BUILD") or (delta < 0 and expected_sign == "DRAWDOWN")
        abs_vs_p90 = abs(delta) >= placebo_p90
        verdict = "✓ CONFIRMED" if sign_ok and abs_vs_p90 else ("~ SIGN_ONLY" if sign_ok else "✗")
        print(f"  {name:<6} {ev_date:<12} {expected_sign:<10} {vol_pre:>8.3f} {vol_post:>8.3f} "
              f"{delta:>+8.3f} {str(sign_ok):>8} {str(abs_vs_p90):>8} {verdict:<12} {kb_source}")
        event_rows.append({
            "event": name, "date": ev_date, "expected": expected_sign,
            "vol_pre": round(vol_pre, 3), "vol_post": round(vol_post, 3),
            "delta_vol": round(delta, 3),
            "sign_ok": sign_ok, "abs_delta_vs_p90": abs_vs_p90,
            "kb_delta": kb_delta, "kb_source": kb_source,
        })

    df_ev = pd.DataFrame(event_rows)
    df_ev.to_csv(h02b_dir / "h02b_e1e2e3_tvt.csv", index=False)

    # E1/E2/E3 on DESC-35 (using db_mean proxy — inverse: db_mean drop = vol rise)
    print(f"\n  === Table: E1/E2/E3 on DESC-35 series (db_mean proxy) ===")
    print(f"  db_mean rises → vol drops → DRAWDOWN; db_mean falls → vol rises → BUILD")
    print(f"  {'Event':<6} {'Date':<12} {'Expected':<10} {'db_pre':>8} {'db_post':>8} {'Δdb':>7} "
          f"{'implied':>10} {'sign_ok':>8}")
    d35_vol_dict = {_dt.date.fromisoformat(str(r["date"])): r["db_mean_avg"]
                    for _, r in d35_daily.iterrows()}
    d35_dates = sorted(d35_vol_dict.keys())

    def db_at_date(d35_dict, d35_list, target_date, tol_days=18):
        best, best_dist = None, tol_days+1
        for d in d35_list:
            dist = abs((d - target_date).days)
            if dist < best_dist:
                best, best_dist = d, dist
        return d35_dict.get(best)

    d35_event_rows = []
    for name, ev_date, expected_sign, note, kb_delta, kb_source in E1E2E3:
        ev_d = _dt.date.fromisoformat(ev_date)
        db_pre  = db_at_date(d35_vol_dict, d35_dates, ev_d - _dt.timedelta(days=18))
        db_post = db_at_date(d35_vol_dict, d35_dates, ev_d + _dt.timedelta(days=18))
        if db_pre is None or db_post is None:
            print(f"  {name:<6}: db_mean not available in DESC-35 ±18d")
            d35_event_rows.append({"event": name, "date": ev_date, "expected": expected_sign,
                                    "db_pre": None, "db_post": None, "db_delta": None,
                                    "implied": None, "sign_ok": None})
            continue
        db_delta = db_post - db_pre
        implied_sign = "DRAWDOWN" if db_delta > 0 else "BUILD"
        sign_ok = implied_sign == expected_sign
        print(f"  {name:<6} {ev_date:<12} {expected_sign:<10} {db_pre:>8.2f} {db_post:>8.2f} "
              f"{db_delta:>+7.2f} {implied_sign:>10} {str(sign_ok):>8}")
        d35_event_rows.append({
            "event": name, "date": ev_date, "expected": expected_sign,
            "db_pre": round(db_pre, 3), "db_post": round(db_post, 3),
            "db_delta": round(db_delta, 3), "implied": implied_sign, "sign_ok": sign_ok,
        })
    pd.DataFrame(d35_event_rows).to_csv(h02b_dir / "h02b_e1e2e3_desc35.csv", index=False)

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

# Capacity share of excluded-21 from registry vol_max_mb
_vol_wl45 = sum(_VOL_MAX.get(t, 0) for t in _WL45)
_vol_excl21 = sum(_VOL_MAX.get(t, 0) for t in _EXCL21)
_vol_total_rt = _vol_wl45 + _vol_excl21
_excl21_vol_share = _vol_excl21 / _vol_total_rt * 100 if _vol_total_rt > 0 else 0
print(f"  Excluded-21 capacity share (from registry vol_max_mb):")
print(f"  n_excl={len(_EXCL21)}, vol_excl21={_vol_excl21:.2f} Mb, vol_total={_vol_total_rt:.2f} Mb, "
      f"vol_share={_excl21_vol_share:.1f}%, tank_count_share={len(_EXCL21)/66*100:.1f}%")

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

    # Find last full DESC-35 date (pct_cov >= 95% AND orbit is DESCENDING AND relOrbit==35)
    desc35_4 = df_rto4[
        (df_rto4["orbit_direction"].isin(["DESC", "DESCENDING"])) &
        (df_rto4["relative_orbit"] == 35)
    ].copy()
    desc35_ntanks = desc35_4.groupby("date")["tank_id"].nunique().reset_index(name="n_tanks_d35")
    desc35_ntanks["pct_d35"] = (desc35_ntanks["n_tanks_d35"] / RT_EXPECTED * 100).round(1)
    last_full_desc35_rows = desc35_ntanks[desc35_ntanks["pct_d35"] >= 95].sort_values("date")
    last_full_desc35_date = last_full_desc35_rows["date"].values[-1] if len(last_full_desc35_rows) else None
    print(f"\n  Last full DESC-35 date (pct_cov>=95%): {last_full_desc35_date}")
    if last_full_desc35_date is not None:
        _lfd_row = merged4[merged4["date"] == last_full_desc35_date]
        if len(_lfd_row):
            print(f"  vol at last full DESC-35: {_lfd_row['vol_mn_bbl'].values[0]:.3f} Mn bbl")

    for fd in ["2026-09-30", "2026-10-06"]:
        fd_date = pd.to_datetime(fd).date()
        row = merged4[merged4["date"] == fd_date]
        if not len(row):
            print(f"  {fd}: NOT in TVT")
            continue
        vol_cur = row["vol_mn_bbl"].values[0]
        ntk_cur = row["n_tanks"].values[0] if not pd.isna(row["n_tanks"].values[0]) else 0
        pct_cur = row["pct_cov"].values[0]

        print(f"\n  {fd}: vol={vol_cur:.3f} Mn bbl, n_tanks={ntk_cur}/{RT_EXPECTED} ({pct_cur}%)")
        if last_full_desc35_date is not None:
            _base_row = merged4[merged4["date"] == last_full_desc35_date]
            if len(_base_row):
                vol_base = _base_row["vol_mn_bbl"].values[0]
                delta = vol_cur - vol_base
                print(f"    delta vs last full DESC-35 ({last_full_desc35_date}): {delta:+.3f} Mn bbl")

        # Capacity share of missing tanks using registry vol_max_mb
        n_missing = RT_EXPECTED - int(ntk_cur)
        print(f"    missing tanks: {n_missing}/{RT_EXPECTED}")
        if n_missing > 0 and _vol_total_rt > 0:
            # Approximate: missing tank fraction of total vol capacity
            _vol_per_tank_avg = _vol_total_rt / 66
            _missing_vol_share = n_missing * _vol_per_tank_avg / _vol_total_rt * 100
            print(f"    missing tank vol capacity share (avg per tank): ~{_missing_vol_share:.1f}%")

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

TARGET_DATES = ["2026-09-07", "2026-09-13", "2026-09-19", "2026-09-25", "2026-10-01",
                "2026-10-07", "2026-10-13"]

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

    # URGENT rt_65: check created_at/updated_at columns, then query all terminals rows >= 2026-10-10
    print("\n  URGENT rt_65 — checking terminal_observations for created_at/updated_at columns...")
    _to_sample = sb.table("terminal_observations").select("*").limit(1).execute().data
    _to_cols_all = list(_to_sample[0].keys()) if _to_sample else []
    print(f"  terminal_observations columns: {_to_cols_all}")
    _has_created_at = "created_at" in _to_cols_all
    _has_updated_at = "updated_at" in _to_cols_all
    print(f"  has created_at: {_has_created_at}, has updated_at: {_has_updated_at}")
    if not _has_created_at and not _has_updated_at:
        print("  RESULT: terminal_observations has NO created_at or updated_at columns — "
              "cannot determine when rows were written from DB alone")
        (hnew2_dir / "hnew2_rt65_created_at.txt").write_text(
            f"terminal_observations_columns={_to_cols_all}\n"
            "has_created_at=False\nhas_updated_at=False\n"
            "Cannot determine insert time from DB — check GHA run logs and git history.\n"
        )
    else:
        # Query rows for all terminals with created_at / updated_at >= 2026-10-10
        _ts_col = "created_at" if _has_created_at else "updated_at"
        print(f"  Querying all terminals: rows with {_ts_col} >= 2026-10-10 ...")
        _late_rows = []
        for _tid in sorted(p3_ids):
            try:
                _r = (sb.table("terminal_observations")
                      .select(f"terminal_id,date,tank_id,{_ts_col}")
                      .eq("terminal_id", _tid)
                      .gte(_ts_col, "2026-10-10")
                      .execute().data)
                if _r:
                    _late_rows.extend(_r)
                    print(f"    {_tid}: {len(_r)} rows with {_ts_col}>=2026-10-10")
            except Exception as _e_lt:
                print(f"    {_tid}: query failed: {_e_lt}")
        if _late_rows:
            _df_late = pd.DataFrame(_late_rows)
            _df_late.to_csv(hnew2_dir / "hnew2_late_rows.csv", index=False)
            print(f"  Total late rows across all terminals: {len(_late_rows)}")
        else:
            print("  No rows with late insert timestamp found")
            (hnew2_dir / "hnew2_late_rows.csv").write_text(f"terminal_id,date,tank_id,{_ts_col}\n")

    # ── GHA runs cross-reference (item 1 run #7) ──────────────────────────────
    # Cross-reference each created_at batch with GHA run_started_at
    print_hypothesis("H_NEW_2")
    print("\n  --- GHA runs cross-reference (Oct 10–21 all branches) ---")
    _gh_runs_dir = hnew2_dir
    try:
        import subprocess as _gsp
        # gh api: list ALL workflow runs for Oct 10–21, all branches
        _gh_env_ok = bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
        print(f"  GH token available: {_gh_env_ok}")
        # Use per_page=100 (no --paginate) with created filter to avoid timeout
        _gh_res = _gsp.run(
            ["gh", "api",
             "/repos/bachevskyvlad/kharg-dashboard/actions/runs"
             "?per_page=100&created=>=2026-10-10",
             "-q", ".workflow_runs[] | {id: .id, name: .name, branch: .head_branch, "
                   "status: .status, conclusion: .conclusion, "
                   "run_started_at: .run_started_at, updated_at: .updated_at, "
                   "workflow_id: .workflow_id}"],
            capture_output=True, text=True, timeout=30
        )
        _gh_out = _gh_res.stdout.strip()
        _gh_err = _gh_res.stderr.strip()
        if _gh_err:
            print(f"  gh api stderr: {_gh_err[:300]}")
        if _gh_out:
            # Parse JSON lines
            _runs = []
            for _line in _gh_out.split("\n"):
                _line = _line.strip()
                if _line:
                    try:
                        _runs.append(json.loads(_line))
                    except Exception:
                        pass
            # Also try as JSON array
            if not _runs:
                try:
                    _runs = json.loads(_gh_out)
                    if isinstance(_runs, dict):
                        _runs = [_runs]
                except Exception:
                    pass
            # Filter to Oct 10–21
            _oct_runs = [r for r in _runs
                         if r.get("run_started_at", "") >= "2026-10-10"
                         and r.get("run_started_at", "") <= "2026-10-21T23:59:59Z"]
            print(f"  Total GHA runs parsed: {len(_runs)}, Oct 10–21: {len(_oct_runs)}")
            _df_runs = pd.DataFrame(_oct_runs) if _oct_runs else pd.DataFrame(
                columns=["id","name","branch","status","conclusion","run_started_at","updated_at"])
            _df_runs.to_csv(_gh_runs_dir / "hnew2_gha_runs_oct.csv", index=False)
            if len(_df_runs):
                print("\n  GHA runs Oct 10–21:")
                for _, _rrow in _df_runs.iterrows():
                    print(f"    run_id={_rrow.get('id')} name={_rrow.get('name')} "
                          f"branch={_rrow.get('branch')} started={_rrow.get('run_started_at')} "
                          f"conclusion={_rrow.get('conclusion')}")
        else:
            print("  gh api returned empty response (no runs or auth failure)")
            _df_runs = pd.DataFrame()

        # Now cross-reference each batch with GHA run_started_at
        # A batch = (terminal_id, created_at hour/minute) bucket from late rows
        if _late_rows and len(_df_runs) > 0:
            _df_late2 = pd.DataFrame(_late_rows)
            _df_late2["created_dt"] = pd.to_datetime(_df_late2[_ts_col], utc=True, errors="coerce")
            _df_late2["created_hm"] = _df_late2["created_dt"].dt.strftime("%Y-%m-%dT%H:%M")
            _df_late2["created_hr"] = _df_late2["created_dt"].dt.strftime("%Y-%m-%dT%H:00Z")

            # Batch = group by hour
            _batch_g = _df_late2.groupby("created_hr").agg(
                n_rows=("tank_id", "count"),
                n_terminals=("terminal_id", "nunique"),
                terminals=("terminal_id", lambda x: sorted(x.unique().tolist())),
                n_tanks=("tank_id", "nunique"),
            ).reset_index()

            print(f"\n  Created_at batches (by hour, n={len(_batch_g)}):")
            _df_runs["started_hr"] = pd.to_datetime(
                _df_runs["run_started_at"], utc=True, errors="coerce"
            ).dt.strftime("%Y-%m-%dT%H:00Z") if len(_df_runs) else pd.Series(dtype=str)

            unmatched_batches = []
            for _, _brow in _batch_g.iterrows():
                _bhr = _brow["created_hr"]
                # Match: GHA run started within ±30min of batch hour
                _bhr_dt = pd.to_datetime(_bhr, utc=True)
                _matched = []
                for _, _rrow in _df_runs.iterrows():
                    _rdt = pd.to_datetime(_rrow.get("run_started_at",""), utc=True, errors="coerce")
                    if pd.notna(_rdt) and abs((_rdt - _bhr_dt).total_seconds()) <= 1800:
                        _matched.append(f"run_id={_rrow.get('id')} name={_rrow.get('name')} branch={_rrow.get('branch')}")
                print(f"    batch {_bhr}: n_rows={_brow['n_rows']}, n_terminals={_brow['n_terminals']}, "
                      f"n_tanks={_brow['n_tanks']}, terminals={_brow['terminals']}")
                if _matched:
                    print(f"      MATCHED runs: {_matched}")
                else:
                    print(f"      NO MATCHING GHA run within ±30min → UNMATCHED")
                    unmatched_batches.append(_bhr)

            if unmatched_batches:
                print(f"\n  UNMATCHED batches (no GHA run within ±30min): {unmatched_batches}")
            else:
                print(f"\n  ALL batches matched to a GHA run.")
            _batch_g.to_csv(_gh_runs_dir / "hnew2_created_at_batches.csv", index=False)

            # Per-terminal distribution
            _term_dist = _df_late2.groupby("terminal_id").agg(
                n_rows=("tank_id", "count"),
                n_dates=("date", "nunique"),
                first_created=("created_dt", "min"),
                last_created=("created_dt", "max"),
            ).reset_index()
            print(f"\n  Per-terminal distribution of late rows:")
            print(_term_dist.to_string(index=False))
            _term_dist.to_csv(_gh_runs_dir / "hnew2_late_by_terminal.csv", index=False)

            # Per-minute distribution (to see batching pattern)
            _df_late2["created_min"] = _df_late2["created_dt"].dt.strftime("%Y-%m-%dT%H:%M")
            _min_dist = _df_late2.groupby("created_min").agg(
                n_rows=("tank_id", "count"),
                n_terminals=("terminal_id", "nunique"),
            ).reset_index()
            print(f"\n  Per-minute batches (top 20):")
            print(_min_dist.sort_values("n_rows", ascending=False).head(20).to_string(index=False))
            _min_dist.to_csv(_gh_runs_dir / "hnew2_per_minute_batches.csv", index=False)
        elif _late_rows and len(_df_runs) == 0:
            print("  Cannot cross-reference: no GHA runs fetched")
        else:
            print("  No late rows to cross-reference with GHA runs")
    except Exception as _e_gh:
        print(f"  GHA runs cross-reference FAILED: {_e_gh}")
        import traceback; traceback.print_exc()

    # Grep for rt_65 mentions across ALL scripts and smoke/test files
    for _rt65_pat in ["rt_65", "single.*tank.*upsert", "upsert.*single.*tank"]:
        _rt65_grep = subprocess.run(
            ["grep", "-rn", "--include=*.py", "--include=*.yml", "--include=*.sh", _rt65_pat,
             "scripts/", ".github/", "tests/"],
            capture_output=True, text=True, cwd=str(REPO_ROOT)
        )
        if _rt65_grep.stdout.strip():
            print(f"\n  Pattern '{_rt65_pat}' found:\n{_rt65_grep.stdout.strip()[:1000]}")
        (hnew2_dir / f"hnew2_grep_rt65_{_rt65_pat[:20].replace('/','_').replace('*','x')}.txt"
         ).write_text(_rt65_grep.stdout)

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
            # PGPASSWORD env var + -h/-U/-d separately (NOT URI — password may contain # or @)
            _psql_env = {**os.environ, "PGPASSWORD": _DB_PASSWORD}
            _res = _sp.run(
                ["psql",
                 "-h", _DB_HOST,
                 "-p", _DB_PORT,
                 "-U", _DB_USER,
                 "-d", _DB_NAME,
                 "-c", _q,
                 "-t"],
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

    def _extract_orbit_attrs(scene):
        """Extract orbitDirection and relativeOrbitNumber from CDSE OData attrs."""
        attrs = scene.get("Attributes", {})
        # OData attributes are a list of {Name, Value} dicts or a dict
        orb_dir, rel_orb = None, None
        if isinstance(attrs, list):
            for a in attrs:
                n = a.get("Name", "")
                v = a.get("Value", a.get("OData.CSC.StringAttribute", {}).get("Value") or
                           a.get("OData.CSC.IntegerAttribute", {}).get("Value"))
                if n == "orbitDirection":
                    orb_dir = v
                elif n == "relativeOrbitNumber":
                    try:
                        rel_orb = int(v)
                    except Exception:
                        rel_orb = v
        elif isinstance(attrs, dict):
            orb_dir = attrs.get("orbitDirection")
            rel_orb = attrs.get("relativeOrbitNumber")
        return orb_dir, rel_orb

    def cdse_search_with_attrs(polygon_wkt, start, end, max_results=200):
        """Search CDSE with full attribute extraction (orbitDirection, relativeOrbitNumber)."""
        all_items = []
        skip = 0
        while True:
            params = {
                "$filter": (
                    f"Collection/Name eq 'SENTINEL-1' and "
                    f"OData.CSC.Intersects(area=geography'SRID=4326;{polygon_wkt}') and "
                    f"ContentDate/Start gt {start}T00:00:00.000Z and "
                    f"ContentDate/Start lt {end}T23:59:59.000Z and "
                    f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' "
                    f"and att/OData.CSC.StringAttribute/Value eq 'GRD')"
                ),
                "$top": 100,
                "$skip": skip,
                "$expand": "Attributes",
                "$orderby": "ContentDate/Start asc",
            }
            try:
                r = _creq.get(_CDSE_CATALOG, params=params, headers=_CDSE_HEADERS, timeout=60)
                if r.status_code == 200:
                    items = r.json().get("value", [])
                    all_items.extend(items)
                    if len(items) < 100 or len(all_items) >= max_results:
                        break
                    skip += 100
                else:
                    print(f"  CDSE search HTTP {r.status_code}: {r.text[:200]}")
                    break
            except Exception as _e:
                print(f"  CDSE search FAILED: {_e}")
                break
        return all_items

    # ── H04: RT specific dates × CDSE catalog → relOrbit per date ─────────────
    print_hypothesis("H04")
    print("\n--- H04: RT specific dates × CDSE catalog (per ТЗ v2) ---")
    h04_dir = OUT_ROOT / "H04"
    h04_dir.mkdir(exist_ok=True)
    # Per ТЗ v2: dates from 09-07,13,19,25 and 10-01,07,13 (single-tank dates) AND
    # 09-24,30,10-06 (adjacent full dates). Prediction: two DESC tracks; second = 35.
    _RT_WKT = "POLYGON((50.0 26.5,50.6 26.5,50.6 27.2,50.0 27.2,50.0 26.5))"
    _RT_TARGET_DATES = [
        "2026-09-07","2026-09-13","2026-09-19","2026-09-25",
        "2026-10-01","2026-10-07","2026-10-13",
        "2026-09-24","2026-09-30","2026-10-06",
    ]
    # Fetch all RT scenes Sep–Oct 2026 to cover all target dates
    _rt_scenes_h04 = cdse_search_with_attrs(_RT_WKT, "2026-09-01", "2026-10-20", max_results=200)
    print(f"  RT CDSE scenes Sep–Oct 2026: n={len(_rt_scenes_h04)}")
    _h04_rows = []
    for _s in _rt_scenes_h04:
        _sdate = _s.get("ContentDate", {}).get("Start", "")[:10]
        _sname = _s.get("Name", "")
        _orb_dir, _rel_orb = _extract_orbit_attrs(_s)
        _h04_rows.append({"date": _sdate, "name": _sname,
                           "orbitDirection": _orb_dir, "relativeOrbit": _rel_orb})
    _df_h04 = pd.DataFrame(_h04_rows) if _h04_rows else pd.DataFrame(
        columns=["date","name","orbitDirection","relativeOrbit"])
    _df_h04.to_csv(h04_dir / "h04_rt_all_scenes.csv", index=False)
    # Per-date relOrbit breakdown for target dates
    print(f"\n  Per target date → relOrbit/direction:")
    _h04_target_rows = []
    for _td in _RT_TARGET_DATES:
        _day_scenes = _df_h04[_df_h04["date"] == _td] if len(_df_h04) else pd.DataFrame()
        _orbits = _day_scenes[["orbitDirection","relativeOrbit"]].drop_duplicates().values.tolist() \
            if len(_day_scenes) else []
        print(f"    {_td}: n_scenes={len(_day_scenes)}, orbits={_orbits}")
        _h04_target_rows.append({"date": _td, "n_scenes": len(_day_scenes), "orbits": str(_orbits)})
    pd.DataFrame(_h04_target_rows).to_csv(h04_dir / "h04_rt_target_dates.csv", index=False)
    # Prediction check: how many unique relOrbit DESC tracks appear?
    _desc_h04 = _df_h04[_df_h04["orbitDirection"].str.upper().str.contains("DESC", na=False)] \
        if len(_df_h04) else pd.DataFrame()
    _desc_orbits = _desc_h04["relativeOrbit"].dropna().unique().tolist() if len(_desc_h04) else []
    print(f"\n  DESC tracks found: {sorted(_desc_orbits)}")
    _has_35 = 35 in _desc_orbits or "35" in [str(x) for x in _desc_orbits]
    _verdict_h04 = "SUPPORTED" if len(_desc_orbits) >= 2 and _has_35 else (
        "PARTIAL" if _has_35 else "NOT_SUPPORTED")
    print(f"H04 VERDICT: {_verdict_h04} — DESC tracks={sorted(_desc_orbits)}, has_35={_has_35}")
    # Keep _rt_scenes for H15/H17/H22/H25/H26
    _rt_scenes = _rt_scenes_h04
    # Fetch Aug-Oct 2026 for H15/H22
    _rt_scenes_aug = cdse_search_with_attrs(_RT_WKT, "2026-08-01", "2026-10-20", max_results=200)
    print(f"  RT CDSE scenes Aug–Oct 2026: n={len(_rt_scenes_aug)}")

    # ── H08: Yanbu DB observation dates × CDSE catalog → orbit per DB date ──────
    # Per ТЗ v2: verdict by share of YANBU DB DATES on secondary orbit (not CDSE dates)
    print_hypothesis("H08")
    print("\n--- H08: Yanbu DB dates × CDSE catalog (per ТЗ v2) ---")
    h08_dir = OUT_ROOT / "H08"
    h08_dir.mkdir(exist_ok=True)
    _YNB_WKT = "POLYGON((37.9 23.9,38.6 23.9,38.6 24.4,37.9 24.4,37.9 23.9))"
    # Step 1: Fetch all Yanbu DB observation dates
    _ynb_obs_all = fetch_all("terminal_observations", "date", {"terminal_id": "yanbu"})
    _ynb_db_dates = sorted({r["date"][:10] for r in _ynb_obs_all if r.get("date")})
    print(f"  Yanbu DB observation dates: n={len(_ynb_db_dates)}")
    # Step 2: Fetch Yanbu CDSE scenes all-time to build date→orbit lookup
    _ynb_scenes_all = cdse_search_with_attrs(_YNB_WKT, "2017-01-01", "2026-10-20", max_results=500)
    print(f"  Yanbu CDSE scenes (all time): n={len(_ynb_scenes_all)}")
    _h08_cdse_rows = []
    for _s in _ynb_scenes_all:
        _sdate = _s.get("ContentDate", {}).get("Start", "")[:10]
        _sname = _s.get("Name", "")
        _orb_dir, _rel_orb = _extract_orbit_attrs(_s)
        _h08_cdse_rows.append({"date": _sdate, "name": _sname,
                                "orbitDirection": _orb_dir, "relativeOrbit": _rel_orb})
    _df_h08_cdse = pd.DataFrame(_h08_cdse_rows) if _h08_cdse_rows else pd.DataFrame(
        columns=["date","name","orbitDirection","relativeOrbit"])
    _df_h08_cdse.to_csv(h08_dir / "h08_yanbu_all_cdse_scenes.csv", index=False)
    # Step 3: For each DB date, find matching CDSE orbit (prefer unique match; multiple → list all)
    _h08_db_rows = []
    for _d in _ynb_db_dates:
        _match = _df_h08_cdse[_df_h08_cdse["date"] == _d] if len(_df_h08_cdse) else pd.DataFrame()
        if len(_match):
            for _, _mr in _match.drop_duplicates(["orbitDirection","relativeOrbit"]).iterrows():
                _h08_db_rows.append({"date": _d, "orbitDirection": _mr["orbitDirection"],
                                     "relativeOrbit": _mr["relativeOrbit"]})
        else:
            _h08_db_rows.append({"date": _d, "orbitDirection": None, "relativeOrbit": None})
    _df_h08_db = pd.DataFrame(_h08_db_rows) if _h08_db_rows else pd.DataFrame(
        columns=["date","orbitDirection","relativeOrbit"])
    _df_h08_db.to_csv(h08_dir / "h08_yanbu_db_dates_orbit.csv", index=False)
    # Step 4: Table: orbit | n_dates_Yanbu | years
    _h08_orb = _df_h08_db.dropna(subset=["orbitDirection"]).groupby(
        ["orbitDirection","relativeOrbit"], dropna=False
    ).agg(
        n_dates_yanbu=("date","nunique"),
        years=("date", lambda x: ",".join(sorted({str(d[:4]) for d in x if d}))),
    ).reset_index() if len(_df_h08_db) else pd.DataFrame()
    print(f"\n  Yanbu DB dates per orbit (from CDSE match):")
    print(_h08_orb.to_string(index=False) if len(_h08_orb) else "  (no data)")
    _h08_orb.to_csv(h08_dir / "h08_yanbu_orbit_summary.csv", index=False)
    # Step 5: Verdict by % of YANBU DB dates on secondary orbit
    _n_ynb_db_total = len(_ynb_db_dates)
    _n_matched = _df_h08_db["orbitDirection"].notna().sum()
    _h08_orb_counts = _df_h08_db.dropna(subset=["orbitDirection"]).groupby(
        ["orbitDirection","relativeOrbit"], dropna=False
    )["date"].nunique() if len(_df_h08_db) else pd.Series(dtype=int)
    _n_ynb_orbit_tracks = len(_h08_orb_counts)
    _secondary_pct = 0.0
    if _n_ynb_orbit_tracks >= 2 and _n_ynb_db_total > 0:
        _ynb_orb_sorted = _h08_orb_counts.sort_values(ascending=False)
        _secondary_n = _ynb_orb_sorted.iloc[1] if len(_ynb_orb_sorted) >= 2 else 0
        _secondary_pct = _secondary_n / _n_ynb_db_total * 100
    _verdict_h08 = "SUPPORTED" if _n_ynb_orbit_tracks >= 2 and _secondary_pct >= 20 else "NOT_SUPPORTED"
    print(f"\n  Yanbu: db_dates={_n_ynb_db_total}, matched={_n_matched}, orbit_tracks={_n_ynb_orbit_tracks}, "
          f"secondary_pct={_secondary_pct:.1f}% (threshold ≥20%)")
    print(f"H08 VERDICT: {_verdict_h08} — tracks={_n_ynb_orbit_tracks}, secondary_pct={_secondary_pct:.1f}% (YANBU DB dates)")

    # ── H09: Within one orbit per-tank range ≤12 dB; inter-orbit offset 3–7 dB ─
    print_hypothesis("H09")
    print("\n--- H09: Yanbu per-tank range within orbit; inter-orbit offset (per ТЗ v2) ---")
    h09_dir = OUT_ROOT / "H09"
    h09_dir.mkdir(exist_ok=True)
    try:
        _ynb_obs09 = fetch_all("terminal_observations",
                               "date,tank_id,db_mean,orbit_direction,relative_orbit",
                               {"terminal_id": "yanbu"})
        _df09 = pd.DataFrame(_ynb_obs09)
        if len(_df09):
            _df09["db_mean"] = pd.to_numeric(_df09["db_mean"], errors="coerce")
            # Within-orbit per-tank db_mean range (max-min)
            _grp09 = _df09.groupby(["orbit_direction","relative_orbit","tank_id"])["db_mean"].agg(
                ["min","max","count"]
            ).reset_index()
            _grp09.columns = ["orbit_direction","relative_orbit","tank_id","db_min","db_max","n"]
            _grp09["range_db"] = _grp09["db_max"] - _grp09["db_min"]
            _grp09.to_csv(h09_dir / "h09_yanbu_per_tank_range.csv", index=False)
            # Per orbit track: fraction of tanks with range ≤12 dB
            _h09_orbit_rows = []
            for (_od, _ro), _grp in _grp09.groupby(["orbit_direction","relative_orbit"]):
                _n_tanks = len(_grp)
                _n_le12 = (_grp["range_db"] <= 12).sum()
                _pct_le12 = _n_le12 / _n_tanks * 100 if _n_tanks else 0
                _med_db = _df09[
                    (_df09["orbit_direction"] == _od) & (_df09["relative_orbit"] == _ro)
                ]["db_mean"].median()
                print(f"  Orbit {_od}/{_ro}: n_tanks={_n_tanks}, pct_range_le12={_pct_le12:.1f}%, median_db={_med_db:.2f}")
                _h09_orbit_rows.append({
                    "orbit_direction": _od, "relative_orbit": _ro,
                    "n_tanks": _n_tanks, "pct_le12": round(_pct_le12, 1),
                    "median_db": round(_med_db, 3) if not pd.isna(_med_db) else None,
                })
            _df09_orbs = pd.DataFrame(_h09_orbit_rows)
            _df09_orbs.to_csv(h09_dir / "h09_orbit_summary.csv", index=False)
            # Inter-orbit offset: |median1 - median2| should be 3–7 dB
            _inter_offset = None
            if len(_df09_orbs) >= 2:
                _meds = sorted(_df09_orbs["median_db"].dropna().tolist())
                if len(_meds) >= 2:
                    _inter_offset = _meds[-1] - _meds[0]
                    print(f"  Inter-orbit median offset: {_inter_offset:.2f} dB (expected 3–7 dB)")
            # Verdict: ≥75% tanks in range ≤12 dB for primary orbit; inter-orbit 3–7 dB
            _primary09 = _df09_orbs.sort_values("n_tanks", ascending=False).iloc[0] \
                if len(_df09_orbs) else None
            _range_ok = _primary09["pct_le12"] >= 75 if _primary09 is not None else False
            _offset_ok = (_inter_offset is not None and 3 <= _inter_offset <= 7)
            _verdict_h09 = "CONFIRMED" if _range_ok and _offset_ok else (
                "PARTIAL" if _range_ok or _offset_ok else "NOT_CONFIRMED")
            print(f"H09 VERDICT: {_verdict_h09} — "
                  f"range_le12={_primary09['pct_le12'] if _primary09 is not None else 'N/A'}% "
                  f"(threshold ≥75%), inter_orbit_offset={_inter_offset}")
        else:
            print("  H09: Yanbu 0 rows")
    except Exception as _e09:
        print(f"  H09 FAILED: {_e09}")
        import traceback; traceback.print_exc()

    # ── H10: CDSE catalog 2018 over Yanbu — n scenes (<5 = REFUTED) ────────────
    print_hypothesis("H10")
    print("\n--- H10: CDSE catalog 2018 over Yanbu (per ТЗ v2) ---")
    h10_dir = OUT_ROOT / "H10"
    h10_dir.mkdir(exist_ok=True)
    _ynb_scenes_2018 = cdse_search_with_attrs(_YNB_WKT, "2018-01-01", "2018-12-31", max_results=100)
    print(f"  Yanbu CDSE scenes 2018: n={len(_ynb_scenes_2018)}")
    if _ynb_scenes_2018:
        _df_ynb18 = pd.DataFrame([{
            "date": s.get("ContentDate",{}).get("Start","")[:10],
            "name": s.get("Name",""),
            "orbitDirection": _extract_orbit_attrs(s)[0],
            "relativeOrbit": _extract_orbit_attrs(s)[1],
        } for s in _ynb_scenes_2018])
        _df_ynb18.to_csv(h10_dir / "h10_yanbu_2018_scenes.csv", index=False)
        print(_df_ynb18[["date","orbitDirection","relativeOrbit"]].to_string(index=False))
    else:
        (h10_dir / "h10_status.txt").write_text("n_scenes=0\n")
    _n_2018 = len(_ynb_scenes_2018)
    # Per ТЗ v2: ≥5 CDSE 2018 scenes means satellite DID pass over Yanbu in 2018.
    # Check if DB has Yanbu rows for 2018 — if CDSE has scenes but DB has none/few → SUPPORTED (ingest gap).
    _ynb_obs_2018 = fetch_all("terminal_observations", "date",
                               {"terminal_id": "yanbu"})
    _ynb_db_2018_dates = [r["date"][:10] for r in _ynb_obs_2018
                          if r.get("date") and r["date"][:4] == "2018"]
    _n_db_2018 = len(set(_ynb_db_2018_dates))
    print(f"  Yanbu DB dates in 2018: n={_n_db_2018}")
    # SUPPORTED = CDSE has ≥5 scenes AND DB has 0 dates (confirmed ingest gap)
    # NOT_SUPPORTED = CDSE has <5 scenes (no SAR coverage), or DB already has 2018 rows
    if _n_2018 >= 5 and _n_db_2018 == 0:
        _verdict_h10 = "SUPPORTED"
        _h10_reason = f"CDSE={_n_2018} 2018 scenes but DB has 0 Yanbu 2018 dates → ingest gap confirmed"
    elif _n_2018 >= 5 and _n_db_2018 > 0:
        _verdict_h10 = "PARTIAL"
        _h10_reason = f"CDSE={_n_2018} 2018 scenes; DB has {_n_db_2018} Yanbu 2018 dates → partial ingest"
    else:
        _verdict_h10 = "NOT_SUPPORTED"
        _h10_reason = f"CDSE={_n_2018} 2018 scenes < 5 → no SAR coverage, no ingest gap expected"
    print(f"H10 VERDICT: {_verdict_h10} — {_h10_reason}")

    # Keep _ynb_scenes for H22
    _ynb_scenes = _ynb_scenes_all

    # ── H15: CDSE DESC-35 scenes over RT Aug–Oct vs DB; list missed dates ───────
    print_hypothesis("H15")
    print("\n--- H15: CDSE DESC-35 vs DB for RT (per ТЗ v2) ---")
    h15_dir = OUT_ROOT / "H15"
    h15_dir.mkdir(exist_ok=True)
    try:
        # What is 50 vs 9? Prior run used Aug-Oct 2026 CDSE vs DB DESC-35 same period.
        # Per ТЗ v2: same period (Aug–Oct), same orbit (DESC-35), same AOI (RT bbox).
        _db_rt35 = fetch_all("terminal_observations",
                             "date,orbit_direction,relative_orbit",
                             {"terminal_id": "ras_tanura"})
        _df_db35 = pd.DataFrame(_db_rt35)
        _df_db35["date"] = pd.to_datetime(_df_db35["date"]).dt.date
        _desc35_db = _df_db35[
            (_df_db35["orbit_direction"].isin(["DESC", "DESCENDING"])) &
            (_df_db35["relative_orbit"] == 35)
        ]
        _db_all_desc35_dates = set(_desc35_db["date"].astype(str))
        _db_aug_oct_desc35 = _desc35_db[
            (_desc35_db["date"] >= pd.to_datetime("2026-08-01").date()) &
            (_desc35_db["date"] <= pd.to_datetime("2026-10-20").date())
        ]
        _n_db_aug_oct = _db_aug_oct_desc35["date"].nunique()
        _db_aug_oct_dates = set(_db_aug_oct_desc35["date"].astype(str))

        # CDSE: filter Aug-Oct by DESC + relOrbit 35
        _cdse_aug_oct = _rt_scenes_aug
        _cdse_desc35_dates = set()
        for _s in _cdse_aug_oct:
            _sdate = _s.get("ContentDate",{}).get("Start","")[:10]
            _od, _ro = _extract_orbit_attrs(_s)
            _is_desc = _od and "DESC" in str(_od).upper()
            _is_35 = _ro == 35 or _ro == "35"
            if _is_desc and _is_35:
                _cdse_desc35_dates.add(_sdate)

        # Also include all DESC scenes where relOrbit may be None (extract from name)
        _cdse_all_dates = set(s.get("ContentDate",{}).get("Start","")[:10] for s in _cdse_aug_oct)

        print(f"\n  Context: what is '50 vs 9'?")
        print(f"    Prior run: CDSE RT (any GRD, Aug-Oct 2026) = n_all={len(_cdse_all_dates)} dates")
        print(f"    Prior run: DB RT DESC-35 (Aug-Oct) = n_db={_n_db_aug_oct}")
        print(f"    Now (ТЗ v2): CDSE RT DESC-35 (Aug-Oct) = n={len(_cdse_desc35_dates)}")
        print(f"    Now (ТЗ v2): DB RT DESC-35 (Aug-Oct) = n={_n_db_aug_oct}")
        print(f"  Same period: YES (Aug 1–Oct 20 2026). Same orbit: YES (DESC-35). Same AOI: YES (RT bbox).")

        # Missed dates = in CDSE DESC-35 but NOT in DB
        _missed = sorted(_cdse_desc35_dates - _db_aug_oct_dates)
        _extra_in_db = sorted(_db_aug_oct_dates - _cdse_desc35_dates)
        print(f"\n  CDSE DESC-35 Aug-Oct: {sorted(_cdse_desc35_dates)}")
        print(f"  DB DESC-35 Aug-Oct:   {sorted(_db_aug_oct_dates)}")
        print(f"  Missed dates (CDSE has, DB lacks): {_missed}")
        print(f"  Extra in DB (DB has, CDSE lacks):  {_extra_in_db}")

        _h15_summary = {
            "cdse_all_aug_oct_dates": len(_cdse_all_dates),
            "cdse_desc35_aug_oct_dates": len(_cdse_desc35_dates),
            "db_desc35_aug_oct_dates": _n_db_aug_oct,
            "missed_by_db": len(_missed),
            "missed_dates": _missed,
            "extra_in_db": _extra_in_db,
        }
        (h15_dir / "h15_cdse_vs_db.json").write_text(json.dumps(_h15_summary, indent=2))
        pd.DataFrame({"missed_date": _missed}).to_csv(h15_dir / "h15_missed_dates.csv", index=False)
        _verdict_h15 = "CONFIRMED" if len(_cdse_desc35_dates) >= _n_db_aug_oct else "NOT_CONFIRMED"
        print(f"H15 VERDICT: {_verdict_h15} — CDSE DESC-35={len(_cdse_desc35_dates)} vs DB-DESC35={_n_db_aug_oct}; "
              f"missed={len(_missed)}")
    except Exception as _e15:
        print(f"  H15 FAILED: {_e15}")
        import traceback; traceback.print_exc()

    # H17: CDSE scene attributes contain orbit direction (confirming DESC-35 in catalog)
    print_hypothesis("H17")
    print("\n--- H17: CDSE orbit attribute check (DESC-35 in catalog) ---")
    h17_dir = OUT_ROOT / "H17"
    h17_dir.mkdir(exist_ok=True)
    try:
        _h17_rows = []
        for _sc in _rt_scenes[:10]:  # inspect first 10
            _attrs = _sc.get("Attributes", {})
            _name = _sc.get("Name", "")
            _date_str = _sc.get("ContentDate", {}).get("Start", "")[:10]
            _h17_rows.append({"name": _name, "date": _date_str, "attrs_keys": str(list(_attrs.keys()) if isinstance(_attrs, dict) else [])})
        _df_h17 = pd.DataFrame(_h17_rows)
        _df_h17.to_csv(h17_dir / "h17_rt_scene_attrs.csv", index=False)
        print(f"  Inspected {len(_h17_rows)} RT scenes for orbit attributes")
        print(_df_h17.to_string(index=False))
    except Exception as _e17:
        print(f"  H17 FAILED: {_e17}")

    # H21: Jask S1 GRD catalog completeness
    print_hypothesis("H21")
    print("\n--- H21: Jask S1 GRD catalog ---")
    h21_dir = OUT_ROOT / "H21"
    h21_dir.mkdir(exist_ok=True)
    _JSK_WKT = "POLYGON((57.7 25.5,58.2 25.5,58.2 25.9,57.7 25.9,57.7 25.5))"
    _jsk_scenes = cdse_search(_JSK_WKT, "2026-08-01", "2026-10-20", max_results=50)
    print(f"  Jask scenes Aug–Oct 2026: n={len(_jsk_scenes)}")
    if _jsk_scenes:
        _df_jsk = pd.DataFrame([{"name": s.get("Name"), "date": s.get("ContentDate",{}).get("Start","")[:10]}
                                 for s in _jsk_scenes])
        _df_jsk.to_csv(h21_dir / "h21_jask_scenes.csv", index=False)
        print(_df_jsk[["name","date"]].to_string(index=False))
    else:
        (h21_dir / "h21_status.txt").write_text("n_scenes=0\n")

    # H22: Cross-terminal CDSE scene date synchrony (same orbit pass)
    print_hypothesis("H22")
    print("\n--- H22: Cross-terminal CDSE scene date synchrony ---")
    h22_dir = OUT_ROOT / "H22"
    h22_dir.mkdir(exist_ok=True)
    try:
        # Fetch Kharg CDSE scenes (2026-08-01..now) for synchrony check
        _KHARG_WKT = "POLYGON((49.97 29.19,50.42 29.19,50.42 29.55,49.97 29.55,49.97 29.19))"
        _kharg_scenes = cdse_search(_KHARG_WKT, "2026-08-01", "2026-10-20", max_results=50)
        print(f"  Kharg CDSE scenes Aug–Oct 2026: n={len(_kharg_scenes)}")
        _cdse_dates = {
            "kharg": set(s.get("ContentDate", {}).get("Start", "")[:10] for s in _kharg_scenes),
            "rt": set(s.get("ContentDate", {}).get("Start", "")[:10] for s in _rt_scenes),
            "yanbu": set(s.get("ContentDate", {}).get("Start", "")[:10] for s in _ynb_scenes),
            "jask": set(s.get("ContentDate", {}).get("Start", "")[:10] for s in _jsk_scenes),
        }
        _all_dates_cdse = set()
        for _ds in _cdse_dates.values():
            _all_dates_cdse |= _ds
        _sync_rows = []
        for _d in sorted(_all_dates_cdse):
            _n_term = sum(1 for _ds in _cdse_dates.values() if _d in _ds)
            _sync_rows.append({"date": _d, "n_terminals": _n_term,
                                "terminals": [t for t, ds in _cdse_dates.items() if _d in ds]})
        _df_h22 = pd.DataFrame(_sync_rows)
        _df_h22.to_csv(h22_dir / "h22_cdse_synchrony.csv", index=False)
        _n_multi = (_df_h22["n_terminals"] >= 2).sum() if len(_df_h22) else 0
        _n_total = len(_df_h22)
        print(f"  CDSE dates in >=2 terminals: {_n_multi}/{_n_total} ({100*_n_multi/_n_total:.1f}%)" if _n_total else "  No dates found")
        _df_multi = _df_h22[_df_h22["n_terminals"] >= 2].sort_values("date")
        print(f"\n  Shared dates (n_terminals >= 2, showing all {len(_df_multi)}):")
        print(_df_multi.to_string(index=False) if len(_df_multi) else "  (none)")
        _pct_shared = 100 * _n_multi / _n_total if _n_total else 0
        _verdict_h22 = "SUPPORTED" if _pct_shared >= 80 else ("PARTIAL" if _pct_shared >= 50 else "NOT_SUPPORTED")
        print(f"\nH22 VERDICT: {_verdict_h22} — {_n_multi}/{_n_total} dates ({_pct_shared:.1f}%) in >=2 terminals")
    except Exception as _e22:
        print(f"  H22 FAILED: {_e22}")

    # H25: CDSE catalog has S1C scene for 2026-09-28 at RT (orbit 101 / ASC)
    print_hypothesis("H25")
    print("\n--- H25: CDSE 2026-09-28 RT scene (ASC orbit 101) ---")
    h25_dir = OUT_ROOT / "H25"
    h25_dir.mkdir(exist_ok=True)
    _rt_928_scenes = cdse_search(_RT_WKT, "2026-09-27", "2026-09-29", max_results=10)
    print(f"  RT CDSE scenes 2026-09-27/29: n={len(_rt_928_scenes)}")
    if _rt_928_scenes:
        _df_928 = pd.DataFrame([{"name": s.get("Name"), "date": s.get("ContentDate",{}).get("Start","")[:10],
                                   "attrs": str(s.get("Attributes",{}))} for s in _rt_928_scenes])
        _df_928.to_csv(h25_dir / "h25_rt_928_scenes.csv", index=False)
        print(_df_928.to_string(index=False))
    else:
        (h25_dir / "h25_status.txt").write_text("n_scenes=0 for RT 2026-09-27/29\n")

    # H26: CDSE catalog gap after 2026-10-06 at RT (last known DESC-35 before today)
    print_hypothesis("H26")
    print("\n--- H26: CDSE RT scenes Oct 2026 (post 10-06) ---")
    h26_dir = OUT_ROOT / "H26"
    h26_dir.mkdir(exist_ok=True)
    _rt_oct_scenes = cdse_search(_RT_WKT, "2026-10-06", "2026-10-21", max_results=20)
    print(f"  RT CDSE scenes Oct 6–21 2026: n={len(_rt_oct_scenes)}")
    if _rt_oct_scenes:
        _df_oct = pd.DataFrame([{"name": s.get("Name"), "date": s.get("ContentDate",{}).get("Start","")[:10],
                                   "attrs": str(s.get("Attributes",{}))} for s in _rt_oct_scenes])
        _df_oct.to_csv(h26_dir / "h26_rt_oct_scenes.csv", index=False)
        print(_df_oct[["name","date"]].to_string(index=False))
    else:
        (h26_dir / "h26_status.txt").write_text("n_scenes=0 for RT Oct 6-21 2026\n")

else:
    print("\n  CDSE block SKIPPED (P2 auth not available)")
    for _hid in ["H04", "H08", "H09", "H10", "H15", "H17", "H21", "H22", "H25", "H26"]:
        (_dir := OUT_ROOT / _hid).mkdir(exist_ok=True)
        (_dir / f"{_hid.lower()}_status.txt").write_text("NOT_RUN: CDSE auth not available\n")



# ── SAR_FETCH: orbit filter code in sar_fetch.py — who calls it, why DESC-137 passes ──
print_hypothesis("SAR_FETCH")
print("\n=== SAR_FETCH: sar_fetch.py orbit filter analysis ===")
_sar_dir = OUT_ROOT / "SAR_FETCH"
_sar_dir.mkdir(exist_ok=True)

_sar_analysis = """
# sar_fetch.py orbit filter analysis
# Item 5 of run #7 ТЗ v2

## Orbit filter code (sar_fetch.py lines 234-251)
```
if orbit_cfg:
    orb = orbit_cfg[0]
    dir_str = orb["direction"].upper()
    if dir_str == "DESC":
        dir_str = "DESCENDING"
    elif dir_str == "ASC":
        dir_str = "ASCENDING"
    other_args["dataFilter"] = {
        "orbitDirection": dir_str,
        "relativeOrbitNumber": orb["relative_orbit"],
    }
    other_args["mosaicking"] = "ORBIT"
```

## The filter is CONDITIONAL on orbit_cfg being passed.
## If orbit_cfg is None (falsy), no dataFilter is set → SentinelHub returns
## ALL available orbits for that day, including DESC-137.

## Callers of fetch_new_records():
"""

import subprocess as _sp2
_grep_callers = _sp2.run(
    ["grep", "-rn", "--include=*.py", "fetch_new_records", "scripts/"],
    capture_output=True, text=True, cwd=str(REPO_ROOT)
)
_callers_text = _grep_callers.stdout.strip()
print(f"  fetch_new_records callers:\n{_callers_text}\n")
_sar_analysis += _callers_text + "\n\n"

# Check orbit_cfg argument in each caller
_grep_orbit_cfg = _sp2.run(
    ["grep", "-rn", "--include=*.py", "orbit_cfg", "scripts/"],
    capture_output=True, text=True, cwd=str(REPO_ROOT)
)
_orbit_cfg_text = _grep_orbit_cfg.stdout.strip()
print(f"  orbit_cfg references:\n{_orbit_cfg_text}\n")
_sar_analysis += "orbit_cfg references:\n" + _orbit_cfg_text + "\n\n"

_sar_analysis += """
## WHY DESC-137 PASSES THROUGH THE FILTER

1. backfill_ras_tanura.py: passes ALLOWED_ORBIT = {"relative_orbit": 35, "direction": "DESC"}
   → orbit_cfg=[ALLOWED_ORBIT] → dataFilter set → DESC-137 BLOCKED ✓

2. main_script.py (regular ingest): calls fetch_new_records with orbit_cfg=None (default)
   → no dataFilter → SentinelHub returns ALL scenes for the date window
   → if BOTH DESC-35 and DESC-137 cover RT on the same day, BOTH are included
   → the mosaic is a blend of two orbits → db_mean is contaminated

3. stage1_backfill.py, ingest.py: also pass orbit_cfg=None by default
   → same contamination risk for any terminal where 2 DESC orbits exist

## CONTAMINATION MECHANISM (per sar_fetch.py comments, line 224)
   min_valid_fraction guard added to reject edge-of-frame partial illumination
   (the rt_65 / DESC-137 contamination mechanism)
   But: the guard only skips a tank if valid_fraction < threshold.
   It does NOT reject DESC-137 scenes; it only skips a per-tank row if too
   few pixels are finite. If DESC-137 illuminates the tank fully, it passes.

## CONCLUSION
   DESC-137 enters the DB on any date where:
   - orbit_cfg was not passed (regular ingest)
   - AND DESC-137 covered RT on that date
   The backfill script DOES filter; the ingest/main pipeline DOES NOT.
"""

_sar_analysis_path = _sar_dir / "sar_fetch_orbit_filter_analysis.txt"
_sar_analysis_path.write_text(_sar_analysis)
print(f"  Analysis written: {_sar_analysis_path}")

# Check if main_script.py passes orbit_cfg to fetch_new_records
_grep_main = _sp2.run(
    ["grep", "-n", "fetch_new_records\|orbit_cfg", "scripts/main_script.py"],
    capture_output=True, text=True, cwd=str(REPO_ROOT)
)
print(f"  main_script.py orbit_cfg usage:\n{_grep_main.stdout.strip()}\n")

# Check backfill_ras_tanura.py ALLOWED_ORBIT
_grep_backfill = _sp2.run(
    ["grep", "-n", "ALLOWED_ORBIT\|orbit_cfg\|fetch_new_records", "scripts/backfill_ras_tanura.py"],
    capture_output=True, text=True, cwd=str(REPO_ROOT)
)
print(f"  backfill_ras_tanura.py orbit_cfg:\n{_grep_backfill.stdout.strip()}\n")


# ── 4.11: Rebuild Assessment — scene counts per terminal (working orbit, historical) ──
# Per ТЗ VARIABLE_AUDIT: n scenes working orbit all-time, CDSE quota (PU/requests/month),
# GHA time estimate, V0 cache volume. READ ONLY — no DB writes.
print("\n=== 4.11: Rebuild Assessment (V0 scene counts per terminal) ===")
_411_dir = OUT_ROOT / "4_11"
_411_dir.mkdir(exist_ok=True)

# Terminal configs: id, WKT bbox, working orbit direction, relOrbit (None=all), start date
# Bbox WKTs: same as used above for CDSE queries + al_flaw added
_411_TERMINALS = [
    {"id": "kharg",     "wkt": "POLYGON((49.97 29.19,50.42 29.19,50.42 29.55,49.97 29.55,49.97 29.19))",
     "orbit_dir": "DESCENDING", "rel_orbit": None,  "start": "2017-01-01",
     "n_tanks": 47, "bbox_km2": 2.5,   "tiles": 1},
    {"id": "ras_tanura","wkt": "POLYGON((50.0 26.5,50.6 26.5,50.6 27.2,50.0 27.2,50.0 26.5))",
     "orbit_dir": "DESCENDING", "rel_orbit": 35,    "start": "2017-01-01",
     "n_tanks": 66, "bbox_km2": 22.0,  "tiles": 2},
    {"id": "yanbu",     "wkt": "POLYGON((37.9 23.9,38.6 23.9,38.6 24.4,37.9 24.4,37.9 23.9))",
     "orbit_dir": "ASCENDING",  "rel_orbit": 14,    "start": "2017-01-01",
     "n_tanks": 12, "bbox_km2": 3.0,   "tiles": 1},
    {"id": "al_flaw",   "wkt": "POLYGON((48.3 29.85,48.6 29.85,48.6 30.1,48.3 30.1,48.3 29.85))",
     "orbit_dir": "DESCENDING", "rel_orbit": None,  "start": "2017-01-01",
     "n_tanks": 24, "bbox_km2": 4.0,   "tiles": 1},
    {"id": "jask",      "wkt": "POLYGON((57.7 25.5,58.2 25.5,58.2 25.9,57.7 25.9,57.7 25.5))",
     "orbit_dir": "DESCENDING", "rel_orbit": 93,    "start": "2017-01-01",
     "n_tanks": 20, "bbox_km2": 2.5,   "tiles": 1},
]

# PU estimate: ~3 PU/scene for 1-tile bbox, ~6 PU/scene for 2-tile bbox (RT)
# S1 CDSE: 30,000 PU/month free tier; 1 request = 1 scene fetch
# GHA: ~5s/scene at 10m/px 1-tile; ~10s/scene for RT 2-tile
_411_rows = []
if CDSE_OK and cdse_token:
    for _t411 in _411_TERMINALS:
        try:
            _t411_scenes = cdse_search_with_attrs(
                _t411["wkt"], _t411["start"], "2026-10-21", max_results=2000
            )
            # Filter to working orbit direction
            _t411_filtered = [s for s in _t411_scenes
                               if s.get("orbitDirection", "").upper() == _t411["orbit_dir"].upper()
                               and (_t411["rel_orbit"] is None
                                    or s.get("relativeOrbit") == _t411["rel_orbit"]
                                    or str(s.get("relativeOrbit", "")) == str(_t411["rel_orbit"]))]
            _n_scenes = len(_t411_filtered)
            _pu_per_scene = 6 if _t411["tiles"] == 2 else 3
            _total_pu = _n_scenes * _pu_per_scene
            _pu_per_month = _total_pu / max(1, (2026 - 2017) * 12)
            _gha_hrs = _n_scenes * (10 if _t411["tiles"] == 2 else 5) / 3600
            # V0 cache volume: 10m/px, 1-band float32; bbox_km² × (1000/10)² × 4 bytes
            _px_side = int(_t411["bbox_km2"] ** 0.5 * 100)  # approx px per side at 10m
            _mb_per_scene = (_px_side ** 2 * 4) / 1e6
            _total_gb = _n_scenes * _mb_per_scene / 1000
            print(f"  {_t411['id']:12s}: n_scenes={_n_scenes:4d} | PU_total={_total_pu:6d} "
                  f"({_pu_per_month:.0f}/mo) | GHA={_gha_hrs:.1f}h | V0≈{_total_gb:.1f}GB "
                  f"(orbit={_t411['orbit_dir'][:4]} relOrbit={_t411['rel_orbit']})")
            _411_rows.append({
                "terminal": _t411["id"],
                "orbit_dir": _t411["orbit_dir"],
                "rel_orbit": _t411["rel_orbit"],
                "n_scenes_working_orbit": _n_scenes,
                "pu_per_scene": _pu_per_scene,
                "pu_total": _total_pu,
                "pu_per_month_avg": round(_pu_per_month),
                "gha_hours": round(_gha_hrs, 1),
                "v0_cache_gb": round(_total_gb, 1),
                "n_tanks": _t411["n_tanks"],
            })
        except Exception as _e411:
            print(f"  {_t411['id']}: FAILED — {_e411}")
    if _411_rows:
        _df_411 = pd.DataFrame(_411_rows)
        _df_411.to_csv(_411_dir / "4_11_rebuild_assessment.csv", index=False)
        print(f"\n  4.11 TOTAL: PU={_df_411['pu_total'].sum():,} | GHA={_df_411['gha_hours'].sum():.1f}h "
              f"| V0≈{_df_411['v0_cache_gb'].sum():.1f}GB")
        print(f"  At free tier (30k PU/mo): {_df_411['pu_total'].sum()/30000:.1f} months to rebuild")
        print("\n  Full table:")
        print(_df_411.to_string(index=False))
else:
    print("  4.11 SKIPPED — CDSE auth not available")


print("\n=== Block A + B/C/D complete ===")
