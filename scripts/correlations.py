"""
correlations.py — Pearson correlations between Kharg and Gulf terminals.

Pulls daily (unsmoothed) volumes from:
  volume_timeseries      — Kharg Island
  terminal_volume_timeseries — Al Flaw, Lavan, Siri, Jask

Aligns on common dates and prints:
  1. Pearson r for each terminal vs Kharg
  2. Cross-correlation (lag analysis) ±30 days
  3. Rolling 365-day correlation over time
"""

import pandas as pd
import numpy as np
from config import sb

# ── Load Kharg daily volumes ───────────────────────────────────────────────────
print("Loading Kharg volumes...")
rows = []
offset = 0
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

kharg = pd.DataFrame(rows)
kharg["date"] = pd.to_datetime(kharg["date"])
kharg = kharg.set_index("date")["vol_mn_bbl"].rename("kharg").sort_index()
print(f"  Kharg: {len(kharg)} dates ({kharg.index.min().date()} — {kharg.index.max().date()})")

# ── Load terminal volumes ──────────────────────────────────────────────────────
print("Loading terminal volumes...")
rows = []
offset = 0
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

tdf = pd.DataFrame(rows)
tdf["date"] = pd.to_datetime(tdf["date"])

terminals = {}
for tid, grp in tdf.groupby("terminal_id"):
    s = grp.set_index("date")["vol_mn_bbl"].sort_index()
    terminals[tid] = s
    print(f"  {tid}: {len(s)} dates ({s.index.min().date()} — {s.index.max().date()})")

# ── Build aligned DataFrame ────────────────────────────────────────────────────
df = pd.DataFrame({"kharg": kharg})
for tid, s in terminals.items():
    df[tid] = s
df = df.dropna()
print(f"\nCommon dates with all series: {len(df)}")

# ── Pearson correlations ───────────────────────────────────────────────────────
print("\n" + "="*50)
print("PEARSON CORRELATION vs KHARG (contemporaneous)")
print("="*50)
for tid in terminals:
    if tid in df.columns:
        r = df["kharg"].corr(df[tid])
        n = df[["kharg", tid]].dropna().__len__()
        print(f"  {tid:20s}  r = {r:+.4f}  (n={n})")

# ── Pairwise between terminals ─────────────────────────────────────────────────
print("\n" + "="*50)
print("PAIRWISE CORRELATIONS BETWEEN TERMINALS")
print("="*50)
cols = list(terminals.keys())
for i, a in enumerate(cols):
    for b in cols[i+1:]:
        if a in df.columns and b in df.columns:
            sub = df[[a, b]].dropna()
            r = sub[a].corr(sub[b])
            print(f"  {a:20s} vs {b:20s}  r = {r:+.4f}  (n={len(sub)})")

# ── Lag analysis: Kharg vs each terminal ──────────────────────────────────────
print("\n" + "="*50)
print("LAG ANALYSIS (days): peak cross-correlation Kharg vs terminal")
print("  Positive lag = terminal LEADS Kharg")
print("="*50)
for tid in terminals:
    if tid not in df.columns:
        continue
    sub = df[["kharg", tid]].dropna()
    best_r, best_lag = 0, 0
    for lag in range(-30, 31):
        if lag == 0:
            r = sub["kharg"].corr(sub[tid])
        elif lag > 0:
            r = sub["kharg"].iloc[lag:].corr(sub[tid].iloc[:-lag])
        else:
            r = sub["kharg"].iloc[:lag].corr(sub[tid].iloc[-lag:])
        if abs(r) > abs(best_r):
            best_r, best_lag = r, lag
    direction = "terminal LEADS" if best_lag > 0 else ("KHARG LEADS" if best_lag < 0 else "simultaneous")
    print(f"  {tid:20s}  best r={best_r:+.4f} at lag={best_lag:+3d}d  ({direction})")

# ── Rolling 365-day correlation ────────────────────────────────────────────────
print("\n" + "="*50)
print("ROLLING 365-DAY CORRELATION vs KHARG (recent 3 years)")
print("="*50)
for tid in terminals:
    if tid not in df.columns:
        continue
    rolling_r = df["kharg"].rolling(365).corr(df[tid]).dropna()
    recent = rolling_r.last("3Y")
    if len(recent) == 0:
        continue
    print(f"  {tid}:")
    print(f"    mean r (3y): {recent.mean():+.4f}")
    print(f"    range:       [{recent.min():+.4f}, {recent.max():+.4f}]")
    # year-by-year
    for year in sorted(recent.index.year.unique())[-3:]:
        yr = recent[recent.index.year == year]
        print(f"    {year}: {yr.mean():+.4f}")

print("\nDone.")
