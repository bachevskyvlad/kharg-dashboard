"""
correlations.py — Pearson correlations between Kharg and Gulf terminals.

Different terminals have different Sentinel-1 pass dates (different orbital
swaths), so we resample all series to weekly means before correlating.
"""

import pandas as pd
import numpy as np
from config import sb


def load_kharg():
    rows, offset = [], 0
    while True:
        batch = (sb.table("volume_timeseries")
                   .select("date,vol_mn_bbl")
                   .eq("aggregation", "daily")
                   .eq("smoothed", False)
                   .range(offset, offset + 999)
                   .execute().data)
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["vol_mn_bbl"].sort_index()


def load_terminals():
    rows, offset = [], 0
    while True:
        batch = (sb.table("terminal_volume_timeseries")
                   .select("terminal_id,date,vol_mn_bbl")
                   .eq("aggregation", "daily")
                   .eq("smoothed", False)
                   .range(offset, offset + 999)
                   .execute().data)
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    result = {}
    for tid, grp in df.groupby("terminal_id"):
        result[tid] = grp.set_index("date")["vol_mn_bbl"].sort_index()
    return result


print("Loading data...")
kharg_daily = load_kharg()
terminals = load_terminals()

print(f"  Kharg: {len(kharg_daily)} dates ({kharg_daily.index.min().date()} — {kharg_daily.index.max().date()})")
for tid, s in terminals.items():
    print(f"  {tid}: {len(s)} dates ({s.index.min().date()} — {s.index.max().date()})")

# Resample everything to weekly means (Monday anchor)
kharg_w = kharg_daily.resample("W-MON").mean()
term_w = {tid: s.resample("W-MON").mean() for tid, s in terminals.items()}

# Build aligned weekly DataFrame
df = pd.DataFrame({"kharg": kharg_w})
for tid, s in term_w.items():
    df[tid] = s
df = df.dropna()
print(f"\nCommon weekly dates (all series present): {len(df)}")
if len(df) == 0:
    # Fall back: allow pairwise NaN, don't require all
    df = pd.DataFrame({"kharg": kharg_w})
    for tid, s in term_w.items():
        df[tid] = s
    print("(Using pairwise complete obs)")

# ── Pearson correlations vs Kharg ─────────────────────────────────────────────
print("\n" + "="*55)
print("PEARSON r vs KHARG (weekly volumes, contemporaneous)")
print("="*55)
for tid in terminals:
    sub = df[["kharg", tid]].dropna()
    if len(sub) < 10:
        print(f"  {tid:20s}  insufficient data (n={len(sub)})")
        continue
    r = sub["kharg"].corr(sub[tid])
    print(f"  {tid:20s}  r = {r:+.4f}  (n={len(sub)} weeks)")

# ── Pairwise between terminals ──────────────────────────────────────────────
print("\n" + "="*55)
print("PAIRWISE CORRELATIONS BETWEEN TERMINALS")
print("="*55)
cols = list(terminals.keys())
for i, a in enumerate(cols):
    for b in cols[i+1:]:
        sub = df[[a, b]].dropna()
        if len(sub) < 10:
            print(f"  {a:20s} vs {b:20s}  n={len(sub)} (skip)")
            continue
        r = sub[a].corr(sub[b])
        print(f"  {a:20s} vs {b:20s}  r = {r:+.4f}  (n={len(sub)})")

# ── Lag analysis: Kharg vs each terminal (±8 weeks) ────────────────────────
print("\n" + "="*55)
print("LAG ANALYSIS (±8 weeks): who leads whom?")
print("  Positive lag = terminal LEADS Kharg")
print("="*55)
for tid in terminals:
    sub = df[["kharg", tid]].dropna()
    if len(sub) < 20:
        continue
    best_r, best_lag = 0, 0
    for lag in range(-8, 9):
        if lag == 0:
            r = sub["kharg"].corr(sub[tid])
        elif lag > 0:
            r = sub["kharg"].iloc[lag:].corr(sub[tid].iloc[:-lag])
        else:
            r = sub["kharg"].iloc[:lag].corr(sub[tid].iloc[-lag:])
        if abs(r) > abs(best_r):
            best_r, best_lag = r, lag
    direction = "terminal LEADS" if best_lag > 0 else ("Kharg LEADS" if best_lag < 0 else "simultaneous")
    print(f"  {tid:20s}  best r={best_r:+.4f} at lag={best_lag:+2d}w  ({direction})")

# ── Rolling 52-week correlation ──────────────────────────────────────────────
print("\n" + "="*55)
print("ROLLING 52-WEEK CORRELATION vs KHARG")
print("="*55)
for tid in terminals:
    sub = df[["kharg", tid]].dropna()
    if len(sub) < 52:
        print(f"  {tid}: insufficient data for rolling")
        continue
    rolling_r = sub["kharg"].rolling(52).corr(sub[tid]).dropna()
    print(f"  {tid}:")
    print(f"    overall mean r : {rolling_r.mean():+.4f}")
    print(f"    range          : [{rolling_r.min():+.4f}, {rolling_r.max():+.4f}]")
    for year in sorted(rolling_r.index.year.unique())[-4:]:
        yr = rolling_r[rolling_r.index.year == year]
        print(f"    {year}: mean r = {yr.mean():+.4f}")

print("\nDone.")
