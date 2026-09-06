"""
eda_terminals.py — Extended EDA for all terminal_observations + volume data.

Checks:
1. Date coverage per terminal (total passes, date range, gaps >10 days)
2. Volume sanity (vol > max_capacity, vol < 0, vol == 0 streaks)
3. db_mean sanity (extreme values, null counts)
4. Cross-terminal summary table
"""

import os, sys
import pandas as pd
import numpy as np
from config import sb

# Max capacity from tank geometry: π × r² × h_max × 6.2898 / 1e6 (Mn bbl)
TERMINAL_MAX_CAP = {
    "kharg":         33.2,
    "lavan":          5.2,
    "siri":           4.4,
    "jask":           6.3,
    "al_flaw":       10.6,
    "halul":          3.5,
    "sitra":          3.7,
    "kozmino":        1.8,
    "de_kastri":      1.2,
    "primorsk":       3.8,
    "novorossiysk":   3.2,
    "ras_laffan":    12.4,
    "ras_tanura":    31.1,
    "fujairah":      11.5,
    "mina_ahmadi":    8.8,
    "ruwais":        11.5,
    "das_island":     5.2,
    "zirku":          7.9,
    "juwaymah":      15.0,
    "yanbu":         15.4,
}

issues = []

def fetch_all(table, select, filters=None):
    rows = []; offset = 0
    while True:
        q = sb.table(table).select(select)
        if filters:
            for k, v in filters.items():
                q = q.eq(k, v)
        batch = q.range(offset, offset+999).execute().data
        rows.extend(batch)
        if len(batch) < 1000: break
        offset += 1000
    return rows

print("=" * 70)
print("EDA: terminal_observations (raw SAR)")
print("=" * 70)

# ── 1. Fetch all terminal_observations ────────────────────────────────────
print("\nFetching terminal_observations...")
raw = fetch_all("terminal_observations", "terminal_id,date,tank_id,db_mean")
df = pd.DataFrame(raw)
if df.empty:
    print("CRITICAL: terminal_observations is empty!")
    sys.exit(1)

df["date"] = pd.to_datetime(df["date"])
print(f"Total rows: {len(df):,}")
print(f"Terminals: {sorted(df['terminal_id'].unique())}")

print("\n── Per-terminal date coverage ──")
summary_rows = []
for tid, grp in df.groupby("terminal_id"):
    dates = grp["date"].drop_duplicates().sort_values()
    n = len(dates)
    first = dates.iloc[0].date()
    last  = dates.iloc[-1].date()
    gaps  = dates.diff().dt.days.dropna()
    max_gap = int(gaps.max()) if len(gaps) else 0
    gaps_big = int((gaps > 10).sum())
    null_db   = int(grp["db_mean"].isna().sum())
    db_min = round(grp["db_mean"].min(), 2)
    db_max = round(grp["db_mean"].max(), 2)

    flag = ""
    if gaps_big > 0:
        flag += f" ⚠ {gaps_big} gap(s)>{10}d (max {max_gap}d)"
    if null_db > 0:
        flag += f" ⚠ {null_db} null db_mean"
    if db_min < -10:
        flag += f" ⚠ db_min={db_min}"
    if db_max > 25:
        flag += f" ⚠ db_max={db_max}"

    tanks_per_date = grp.groupby("date")["tank_id"].count()
    t_min = int(tanks_per_date.min())
    t_max = int(tanks_per_date.max())
    t_mean = round(tanks_per_date.mean(), 1)
    t_sparse = int((tanks_per_date < t_max * 0.5).sum())

    tank_flag = ""
    if t_sparse > 0:
        tank_flag = f" ⚠ {t_sparse} dates<50%tanks (tanks/date: min={t_min} mean={t_mean} max={t_max})"

    print(f"  {tid:<20} passes={n:>4}  {first} → {last}  max_gap={max_gap:>3}d  db=[{db_min:>6},{db_max:>6}]{flag}{tank_flag}")
    summary_rows.append({"tid": tid, "passes": n, "first": first, "last": last,
                          "max_gap": max_gap, "gaps_big": gaps_big, "null_db": null_db,
                          "db_min": db_min, "db_max": db_max})
    if flag:
        issues.append(f"[obs] {tid}: {flag.strip()}")

# ── 2. Fetch Kharg raw_observations ───────────────────────────────────────
print("\n── Kharg (raw_observations) ──")
k_raw = fetch_all("raw_observations", "date,tank_id,db_mean")
kdf = pd.DataFrame(k_raw)
if not kdf.empty:
    kdf["date"] = pd.to_datetime(kdf["date"])
    dates_k = kdf["date"].drop_duplicates().sort_values()
    gaps_k = dates_k.diff().dt.days.dropna()
    max_gap_k = int(gaps_k.max())
    big_k = int((gaps_k > 10).sum())
    null_k = int(kdf["db_mean"].isna().sum())
    print(f"  kharg  passes={len(dates_k):>4}  {dates_k.iloc[0].date()} → {dates_k.iloc[-1].date()}  max_gap={max_gap_k:>3}d  db=[{kdf['db_mean'].min():.2f},{kdf['db_mean'].max():.2f}]")
    if big_k: issues.append(f"[obs] kharg: {big_k} gap(s)>10d (max {max_gap_k}d)")
    if null_k: issues.append(f"[obs] kharg: {null_k} null db_mean")

# ── 3. Volume sanity ──────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("EDA: terminal_volume_timeseries")
print("=" * 70)

vol_raw = fetch_all("terminal_volume_timeseries", "terminal_id,date,vol_mn_bbl,aggregation")
vdf = pd.DataFrame(vol_raw)
if vdf.empty:
    print("WARNING: terminal_volume_timeseries is empty!")
else:
    vdf["date"] = pd.to_datetime(vdf["date"])
    vdf = vdf[vdf["aggregation"] == "daily"]
    print(f"\nDaily volume rows: {len(vdf):,}")

    print("\n── Per-terminal volume stats (SPI = vol/cap × 100) ──")
    print(f"  {'terminal':<20} {'n':>4}  {'min%':>5} {'p25%':>5} {'med%':>5} {'p75%':>5} {'p90%':>5} {'max%':>5}  {'mean%':>6} {'std%':>5}  {'cv':>5}  flags")
    print("  " + "-" * 110)

    spi_rows = []
    for tid, grp in vdf.groupby("terminal_id"):
        cap = TERMINAL_MAX_CAP.get(tid, None)
        if not cap:
            continue
        v = grp["vol_mn_bbl"].dropna()
        spi = v / cap * 100
        nulls    = int(grp["vol_mn_bbl"].isna().sum())
        over_cap = int((v > cap).sum())
        neg      = int((v < 0).sum())
        zeros    = int((v == 0).sum())

        p_min = round(spi.min(), 1)
        p25   = round(spi.quantile(0.25), 1)
        p50   = round(spi.median(), 1)
        p75   = round(spi.quantile(0.75), 1)
        p90   = round(spi.quantile(0.90), 1)
        p_max = round(spi.max(), 1)
        mean_ = round(spi.mean(), 1)
        std_  = round(spi.std(), 1)
        cv    = round(std_ / mean_ * 100, 1) if mean_ else 0  # coefficient of variation %

        flag = ""
        if over_cap:      flag += f" ⚠ {over_cap} over-cap"
        if neg:           flag += f" ⚠ {neg} negative"
        if zeros:         flag += f" ⚠ {zeros} zero"
        if nulls:         flag += f" ⚠ {nulls} null"
        if p90 > 85:      flag += f" ⚠ p90={p90}% CRITICAL"
        elif p50 > 85:    flag += f" ⚠ median={p50}% CRITICAL"
        elif p_max > 85:  flag += f" ℹ max>{85}%"

        print(f"  {tid:<20} {len(v):>4}  {p_min:>5} {p25:>5} {p50:>5} {p75:>5} {p90:>5} {p_max:>5}  {mean_:>6} {std_:>5}  {cv:>5}  {flag}")
        spi_rows.append({"tid": tid, "mean_spi": mean_, "std_spi": std_, "cv": cv, "p90": p90, "p_max": p_max})
        if flag:
            issues.append(f"[vol] {tid}: {flag.strip()}")

    # ── Cross-terminal comparison ─────────────────────────────────────────
    print("\n── Cross-terminal SPI comparison (sorted by mean SPI) ──")
    for r in sorted(spi_rows, key=lambda x: -x["mean_spi"]):
        bar = "█" * int(r["mean_spi"] / 5)
        print(f"  {r['tid']:<20} mean={r['mean_spi']:>5.1f}%  std={r['std_spi']:>4.1f}%  cv={r['cv']:>5.1f}%  {bar}")

    # ── db_mean std per terminal (pass-level activity) ────────────────────
    print("\n── db_mean std per terminal (proxy for tank activity) ──")
    print(f"  {'terminal':<20}  {'db_std_mean':>11}  {'db_std_p90':>10}  note")
    print("  " + "-" * 65)
    for tid, grp in df.groupby("terminal_id"):
        # std of db_mean across tanks per date (cross-tank dispersion)
        per_date_std = grp.groupby("date")["db_mean"].std().dropna()
        if per_date_std.empty:
            continue
        m = round(per_date_std.mean(), 2)
        p90 = round(per_date_std.quantile(0.90), 2)
        note = "active" if m > 4 else ("moderate" if m > 2 else "low/idle")
        print(f"  {tid:<20}  {m:>11}  {p90:>10}  {note}")

# ── 4. Kharg volume_timeseries ────────────────────────────────────────────
print("\n── Kharg (volume_timeseries, daily) ──")
k_vol = fetch_all("volume_timeseries", "date,vol_mn_bbl,aggregation", {"aggregation": "daily"})
kvdf = pd.DataFrame(k_vol)
if not kvdf.empty:
    kvdf["date"] = pd.to_datetime(kvdf["date"])
    cap = 33.2
    vmin = round(kvdf["vol_mn_bbl"].min(), 3)
    vmax = round(kvdf["vol_mn_bbl"].max(), 3)
    vmean = round(kvdf["vol_mn_bbl"].mean(), 3)
    over = int((kvdf["vol_mn_bbl"] > cap).sum())
    neg  = int((kvdf["vol_mn_bbl"] < 0).sum())
    print(f"  kharg  rows={len(kvdf):>4}  vol=[{vmin},{vmax}]  mean={vmean}  cap={cap}  max%={round(vmax/cap*100,1)}")
    if over: issues.append(f"[vol] kharg: {over} rows OVER CAPACITY")
    if neg:  issues.append(f"[vol] kharg: {neg} negative values")

# ── 5. Summary ────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print(f"ISSUES FOUND: {len(issues)}")
print("=" * 70)
if issues:
    for iss in issues:
        print(f"  {iss}")
else:
    print("  None — all checks passed.")
