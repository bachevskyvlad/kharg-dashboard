"""
diag_closure.py — Closure audit items 1–5.

1.  terminal_volume_timeseries RT: n_rows, min_date, max_date, last_updated
2.  Ingest regression: orbit_direction raw labels across ALL terminals + min/max dates
3.  Date-match RT 8 DESCENDING dates against SH catalog (relOrbit from scene metadata)
    Yanbu all dates → relOrbit from SH catalog
4.  161 DESCENDING rows per date: n_tanks per date (invariant check)
5.  Yanbu yb_0: n rows, n unique date strings, n unique datetime strings

SH catalog queries use CLIENT_ID / CLIENT_SECRET (already in terminal_checks.yml env).
"""

import os
import sys
import json
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from supabase import create_client as _create_client

_sb_url = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
_sb_key = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
sb = _create_client(_sb_url, _sb_key)

OUT = Path("diagnostics/closure")
OUT.mkdir(parents=True, exist_ok=True)

TERMINALS = ["ras_tanura", "yanbu", "al_faw", "jask", "juwaymah"]


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


# ── Item 1: terminal_volume_timeseries RT ──────────────────────────────────────
print("=== Item 1: terminal_volume_timeseries RT ===")
try:
    tvt = fetch_all("terminal_volume_timeseries", "date,vol_mn_bbl,aggregation,smoothed",
                    {"terminal_id": "ras_tanura"})
    df_tvt = pd.DataFrame(tvt)
    if len(df_tvt):
        df_tvt["date"] = pd.to_datetime(df_tvt["date"])
        summary = df_tvt.groupby("aggregation").agg(
            n=("date", "count"),
            min_date=("date", "min"),
            max_date=("date", "max"),
        ).reset_index()
        summary["min_date"] = summary["min_date"].dt.strftime("%Y-%m-%d")
        summary["max_date"] = summary["max_date"].dt.strftime("%Y-%m-%d")
        print(summary.to_string(index=False))
        summary.to_csv(OUT / "tvt_rt.csv", index=False)
        print(f"  last row date: {df_tvt.date.max().strftime('%Y-%m-%d')}")
    else:
        print("  RT: 0 rows in terminal_volume_timeseries")
        pd.DataFrame().to_csv(OUT / "tvt_rt.csv", index=False)
except Exception as e:
    print(f"  FAILED: {e}")
    pd.DataFrame([{"error": str(e)}]).to_csv(OUT / "tvt_rt.csv", index=False)


# ── Item 2: Ingest regression — orbit labels across all terminals ──────────────
print("\n=== Item 2: Orbit label breakdown all terminals ===")
all_labels = []
for tid in TERMINALS:
    try:
        rows = fetch_all("terminal_observations",
                         "date,orbit_direction,relative_orbit",
                         {"terminal_id": tid})
        df = pd.DataFrame(rows)
        if not len(df):
            print(f"  {tid}: 0 rows")
            continue
        df["date"] = pd.to_datetime(df["date"])
        g = (df.groupby(["orbit_direction", "relative_orbit"], dropna=False)
               .agg(n=("date", "count"),
                    min_date=("date", "min"),
                    max_date=("date", "max"))
               .reset_index())
        g.insert(0, "terminal_id", tid)
        g["min_date"] = g["min_date"].dt.strftime("%Y-%m-%d")
        g["max_date"] = g["max_date"].dt.strftime("%Y-%m-%d")
        all_labels.append(g)
        print(f"  {tid}:")
        print(g.to_string(index=False))
    except Exception as e:
        print(f"  {tid} FAILED: {e}")

if all_labels:
    df_labels = pd.concat(all_labels, ignore_index=True)
    df_labels.to_csv(OUT / "orbit_labels_all.csv", index=False)


# ── Item 4: 161 DESCENDING rows per date ──────────────────────────────────────
print("\n=== Item 4: RT DESCENDING rows per date ===")
try:
    rows = fetch_all("terminal_observations",
                     "date,tank_id,relative_orbit,orbit_direction",
                     {"terminal_id": "ras_tanura"})
    df_rt = pd.DataFrame(rows)
    df_rt["date"] = pd.to_datetime(df_rt["date"])
    mask = df_rt["orbit_direction"].isin(["DESCENDING"]) & df_rt["relative_orbit"].isna()
    df_desc = df_rt[mask].copy()
    per_date = (df_desc.groupby(df_desc["date"].dt.strftime("%Y-%m-%d"))
                       .agg(n_tanks=("tank_id", "nunique"),
                            n_rows=("tank_id", "count"))
                       .reset_index()
                       .rename(columns={"date": "date"}))
    per_date["expected"] = 66
    per_date["missing"] = per_date["expected"] - per_date["n_tanks"]
    print(per_date.to_string(index=False))
    per_date.to_csv(OUT / "descending_per_date.csv", index=False)
    print(f"\n  Total DESCENDING rows: {len(df_desc)}")
    print(f"  Unique tanks seen: {df_desc.tank_id.nunique()}")
    print(f"  All tank list: {sorted(df_desc.tank_id.unique().tolist())}")
except Exception as e:
    print(f"  FAILED: {e}")


# ── Item 5: Yanbu yb_0 — date vs datetime ─────────────────────────────────────
print("\n=== Item 5: Yanbu yb_0 date vs datetime ===")
try:
    rows = fetch_all("terminal_observations",
                     "date,tank_id,db_mean,relative_orbit,orbit_direction",
                     {"terminal_id": "yanbu"})
    df_yb = pd.DataFrame(rows)
    print(f"  All Yanbu rows: {len(df_yb)}")
    print(f"  Unique tank_ids: {df_yb.tank_id.nunique()}")

    # Raw date field type
    print(f"  date field sample (raw): {df_yb['date'].iloc[:3].tolist()}")

    yb0 = df_yb[df_yb["tank_id"] == "yb_0"].copy()
    print(f"\n  yb_0 n rows: {len(yb0)}")
    print(f"  yb_0 raw date sample: {yb0['date'].iloc[:5].tolist()}")

    # Check if dates contain time component
    has_time = yb0['date'].str.contains("T|:").any() if yb0['date'].dtype == object else False
    print(f"  yb_0 dates contain time component: {has_time}")

    # Unique as strings (raw)
    n_unique_raw = yb0['date'].nunique()
    print(f"  yb_0 unique raw date strings: {n_unique_raw}")

    # Parse and check calendar dates
    yb0["dt"] = pd.to_datetime(yb0["date"])
    yb0["cal_date"] = yb0["dt"].dt.date
    n_cal = yb0["cal_date"].nunique()
    print(f"  yb_0 unique calendar dates: {n_cal}")
    print(f"  yb_0 dates with >1 observation: {(yb0.groupby('cal_date').size() > 1).sum()}")

    # Show duplicate dates
    dup = yb0.groupby("cal_date").size().reset_index(name="n").query("n > 1")
    if len(dup):
        print(f"  yb_0 sample duplicate cal_dates (first 5):")
        print(dup.head(5).to_string(index=False))
        # Show raw datetime for one dup date
        dup_date = dup.iloc[0]["cal_date"]
        rows_on_dup = yb0[yb0["cal_date"] == dup_date][["date", "db_mean", "relative_orbit", "orbit_direction"]]
        print(f"  Raw rows on {dup_date}:")
        print(rows_on_dup.to_string(index=False))

    yb0.to_csv(OUT / "yanbu_yb0_all.csv", index=False)
    print(f"\n  n_all_yanbu_dates (nunique as datetime): {df_yb.drop_duplicates('date')['date'].nunique()}")
    df_yb["dt"] = pd.to_datetime(df_yb["date"])
    print(f"  n unique calendar dates (all yanbu): {df_yb['dt'].dt.date.nunique()}")

except Exception as e:
    print(f"  FAILED: {e}")


# ── Item 3: Date-match RT 8 DESCENDING dates + Yanbu against SH catalog ───────
print("\n=== Item 3: SH catalog date-match ===")

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("  SKIP: CLIENT_ID / CLIENT_SECRET not set")
else:
    try:
        from sentinelhub import (SHConfig, SentinelHubCatalog, DataCollection,
                                  BBox, CRS, Geometry)
        cfg = SHConfig()
        cfg.sh_client_id = CLIENT_ID
        cfg.sh_client_secret = CLIENT_SECRET
        catalog = SentinelHubCatalog(config=cfg)

        # RT bbox (Ras Tanura terminal area)
        RT_BBOX = BBox(bbox=[49.97, 26.62, 50.05, 26.70], crs=CRS.WGS84)
        YB_BBOX = BBox(bbox=[37.10, 24.06, 37.20, 24.14], crs=CRS.WGS84)

        RT_DATES = ["2026-09-07", "2026-09-13", "2026-09-19", "2026-09-24",
                    "2026-09-25", "2026-09-30", "2026-10-01", "2026-10-06"]

        def sh_scenes(bbox, date_from, date_to):
            results = []
            search_iter = catalog.search(
                DataCollection.SENTINEL1_IW,
                bbox=bbox,
                time=(date_from, date_to),
                fields={"include": ["id", "properties.datetime", "properties.sat:relative_orbit",
                                    "properties.sat:orbit_state", "properties.platform"]},
            )
            for item in search_iter:
                p = item.get("properties", {})
                results.append({
                    "scene_id": item.get("id"),
                    "datetime": p.get("datetime"),
                    "relative_orbit": p.get("sat:relative_orbit"),
                    "orbit_state": p.get("sat:orbit_state"),
                    "platform": p.get("platform"),
                })
            return results

        print("  RT 8 DESCENDING dates:")
        rt_sh_rows = []
        for d in RT_DATES:
            d0 = d
            d1 = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            scenes = sh_scenes(RT_BBOX, d0, d1)
            for s in scenes:
                s["query_date"] = d
                rt_sh_rows.append(s)
                print(f"    {d}: relOrbit={s['relative_orbit']}  state={s['orbit_state']}  dt={s['datetime']}")
            if not scenes:
                rt_sh_rows.append({"query_date": d, "scene_id": None, "datetime": None,
                                   "relative_orbit": None, "orbit_state": None})
                print(f"    {d}: NO SCENE FOUND")

        pd.DataFrame(rt_sh_rows).to_csv(OUT / "rt_sh_catalog.csv", index=False)

        print("\n  Yanbu: sampling first/last 5 + checking all 249 cal dates...")
        # Get all yanbu calendar dates
        yb_dates_file = OUT / "yanbu_yb0_all.csv"
        if yb_dates_file.exists():
            df_tmp = pd.read_csv(yb_dates_file)
            df_tmp["cal_date"] = pd.to_datetime(df_tmp["dt"]).dt.strftime("%Y-%m-%d")
            yb_cal_dates = sorted(df_tmp["cal_date"].unique().tolist())
        else:
            yb_cal_dates = []

        yb_sh_rows = []
        for d in yb_cal_dates:
            d1 = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            scenes = sh_scenes(YB_BBOX, d, d1)
            for s in scenes:
                s["query_date"] = d
                yb_sh_rows.append(s)
            if not scenes:
                yb_sh_rows.append({"query_date": d, "scene_id": None, "datetime": None,
                                   "relative_orbit": None, "orbit_state": None})

        df_yb_sh = pd.DataFrame(yb_sh_rows)
        df_yb_sh.to_csv(OUT / "yanbu_sh_catalog.csv", index=False)
        found = df_yb_sh[df_yb_sh["scene_id"].notna()]
        print(f"  Yanbu: {len(yb_cal_dates)} cal dates queried, {len(found)} scenes found")
        orb_summary = found.groupby(["relative_orbit","orbit_state"]).size().reset_index(name="n")
        print(orb_summary.to_string(index=False))

    except Exception as e:
        print(f"  SH catalog FAILED: {e}")
        import traceback; traceback.print_exc()

print("\ndone.")
