"""
diag_orbit_labels.py — Orbit label audit for RT and Yanbu (items 1–4 closure).

Outputs:
  diagnostics/rt_orbit/orbit_labels.csv    — RT: label | relOrbit | n (all 566 rows)
  diagnostics/rt_orbit/prod_counts.csv     — RT: prod series n by orbit (terminal_volume_timeseries)
  diagnostics/rt_orbit/descending8.csv     — RT: 8 NaN/DESCENDING 2026 dates + relOrbit from SH catalog
  diagnostics/yanbu_orbit/orbit_dates.csv  — Yanbu: 249 dates + relOrbit (SH catalog match)
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from supabase import create_client as _create_client

_sb_url = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
_sb_key = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
sb = _create_client(_sb_url, _sb_key)

OUT_RT = Path("diagnostics/rt_orbit")
OUT_YB = Path("diagnostics/yanbu_orbit")
OUT_RT.mkdir(parents=True, exist_ok=True)
OUT_YB.mkdir(parents=True, exist_ok=True)


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


# ── Item 1: RT orbit_direction raw label breakdown ─────────────────────────────
print("Item 1: RT orbit label breakdown…")
rows = fetch_all(
    "terminal_observations",
    "relative_orbit,orbit_direction",
    {"terminal_id": "ras_tanura"},
)
df_rt = pd.DataFrame(rows)
label_counts = (
    df_rt.groupby(["orbit_direction", "relative_orbit"], dropna=False)
    .size()
    .reset_index(name="n")
    .rename(columns={"orbit_direction": "label", "relative_orbit": "relOrbit"})
    .sort_values("n", ascending=False)
)
label_counts.to_csv(OUT_RT / "orbit_labels.csv", index=False)
print(f"  RT total rows: {len(df_rt)}")
print(label_counts.to_string(index=False))


# ── Item 1b: Prod series counts by orbit (terminal_volume_timeseries) ──────────
print("\nItem 1b: RT prod series counts…")
try:
    tvt_rows = fetch_all(
        "terminal_volume_timeseries",
        "date,relative_orbit,orbit_direction,n_tanks",
        {"terminal_id": "ras_tanura"},
    )
    df_tvt = pd.DataFrame(tvt_rows)
    if len(df_tvt):
        prod_counts = (
            df_tvt.groupby(["orbit_direction", "relative_orbit"], dropna=False)
            .size()
            .reset_index(name="n_rows")
        )
        prod_counts.to_csv(OUT_RT / "prod_counts.csv", index=False)
        print(prod_counts.to_string(index=False))
    else:
        print("  terminal_volume_timeseries: no RT rows")
        pd.DataFrame(columns=["orbit_direction", "relative_orbit", "n_rows"]).to_csv(
            OUT_RT / "prod_counts.csv", index=False
        )
except Exception as e:
    print(f"  terminal_volume_timeseries query failed: {e}")
    pd.DataFrame(columns=["orbit_direction", "relative_orbit", "n_rows", "error"]).assign(
        error=str(e)
    ).to_csv(OUT_RT / "prod_counts.csv", index=False)


# ── Item 2: RT 8 NaN/DESCENDING 2026 rows — fetch dates ───────────────────────
print("\nItem 2: RT DESCENDING/NaN rows…")
desc_rows = fetch_all(
    "terminal_observations",
    "date,tank_id,relative_orbit,orbit_direction",
    {"terminal_id": "ras_tanura"},
)
df_all = pd.DataFrame(desc_rows)
df_all["date"] = pd.to_datetime(df_all["date"])

mask = (
    df_all["orbit_direction"].isin(["DESCENDING"]) |
    df_all["orbit_direction"].isna()
) & (df_all["relative_orbit"].isna()) & (df_all["date"].dt.year == 2026)

df_desc8 = df_all[mask].copy()
dates_desc8 = df_desc8["date"].dt.strftime("%Y-%m-%d").unique().tolist()
print(f"  Matching rows: {len(df_desc8)}, unique dates: {len(dates_desc8)}")
print("  Dates:", sorted(dates_desc8))

# Event windows: E1=2026-03-02 ±14d, E2=2026-06-26 ±14d, E3=2026-07-15 ±14d
events = {
    "E1_BUILD": "2026-03-02",
    "E2_DRAW": "2026-06-26",
    "E3_DRAW": "2026-07-15",
}
WINDOW = 14
rows_out = []
for d in sorted(dates_desc8):
    dt = datetime.strptime(d, "%Y-%m-%d")
    in_events = []
    for ev, ev_date in events.items():
        ev_dt = datetime.strptime(ev_date, "%Y-%m-%d")
        if abs((dt - ev_dt).days) <= WINDOW:
            in_events.append(ev)
    rows_out.append({"date": d, "event_windows": ";".join(in_events) or "none"})

df_out = pd.DataFrame(rows_out)
df_out.to_csv(OUT_RT / "descending8.csv", index=False)
print(df_out.to_string(index=False))


# ── Item 4: Yanbu — all 249 distinct dates ─────────────────────────────────────
print("\nItem 4: Yanbu orbit date-match…")
yb_rows = fetch_all(
    "terminal_observations",
    "date,relative_orbit,orbit_direction",
    {"terminal_id": "yanbu"},
)
df_yb = pd.DataFrame(yb_rows)
df_yb["date"] = pd.to_datetime(df_yb["date"])
date_orbit = (
    df_yb.groupby("date", dropna=False)
    .agg(
        relative_orbit=("relative_orbit", lambda x: x.dropna().unique().tolist()),
        orbit_direction=("orbit_direction", lambda x: x.dropna().unique().tolist()),
        n_passes=("relative_orbit", "count"),
    )
    .reset_index()
    .sort_values("date")
)
date_orbit["date"] = date_orbit["date"].dt.strftime("%Y-%m-%d")
date_orbit["relative_orbit"] = date_orbit["relative_orbit"].apply(
    lambda x: ";".join(str(v) for v in x) if x else "NULL"
)
date_orbit["orbit_direction"] = date_orbit["orbit_direction"].apply(
    lambda x: ";".join(str(v) for v in x) if x else "NULL"
)
date_orbit.to_csv(OUT_YB / "orbit_dates.csv", index=False)
print(f"  Yanbu unique dates: {len(date_orbit)}")
print(f"  Orbits seen: {df_yb['relative_orbit'].dropna().unique().tolist()}")
print(f"  Directions seen: {df_yb['orbit_direction'].dropna().unique().tolist()}")
print("done.")
