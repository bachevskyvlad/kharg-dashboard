"""
Per-tank inflow/outflow analysis for Feb-Jul 2026.
Outputs data/tank_flows_2026.json
"""
import os, json
import pandas as pd
import numpy as np
from supabase import create_client

BARREL_PER_M3 = 6.2898
TANK_HEIGHT_MIN = 2

sb = create_client(
    "https://yoekhkczwdeyauwarsyy.supabase.co",
    "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"
)

# ── 1. Pull raw_observations 2025-11 onwards (need prior context for percentiles)
rows = []
offset = 0
while True:
    res = sb.table("raw_observations")\
        .select("date,tank_id,radius_m,h_max,db_mean")\
        .gte("date", "2025-11-01")\
        .lte("date", "2026-07-31")\
        .range(offset, offset+999)\
        .execute().data
    rows.extend(res)
    if len(res) < 1000:
        break
    offset += 1000

print(f"Loaded {len(rows)} rows")
df = pd.DataFrame(rows)
df["date"] = pd.to_datetime(df["date"])

# ── 2. Global percentile calibration (use full history for stable percentiles)
rows_all = []
offset = 0
while True:
    res = sb.table("raw_observations")\
        .select("tank_id,db_mean")\
        .range(offset, offset+999)\
        .execute().data
    rows_all.extend(res)
    if len(res) < 1000:
        break
    offset += 1000

df_all = pd.DataFrame(rows_all)
DB_MIN = df_all["db_mean"].quantile(0.05)
DB_MAX = df_all["db_mean"].quantile(0.95)
print(f"Global dB range: [{DB_MIN:.2f}, {DB_MAX:.2f}]")

# ── 3. Per-tank volume per date
def vol_bbl(row):
    db_clipped = np.clip(row["db_mean"], DB_MIN, DB_MAX)
    fill_ratio = (DB_MAX - db_clipped) / (DB_MAX - DB_MIN)
    h = TANK_HEIGHT_MIN + fill_ratio * (row["h_max"] - TANK_HEIGHT_MIN)
    return np.pi * row["radius_m"]**2 * h * BARREL_PER_M3 / 1e6  # Mb

df["vol_mb"] = df.apply(vol_bbl, axis=1)

# ── 4. Focus on Feb 2026+, compute per-tank Δvol between consecutive dates
focus = df[df["date"] >= "2026-02-01"].copy()
dates_sorted = sorted(focus["date"].unique())

records = []
for i in range(1, len(dates_sorted)):
    d0, d1 = dates_sorted[i-1], dates_sorted[i]
    gap_days = (d1 - d0).days

    s0 = focus[focus["date"] == d0].set_index("tank_id")["vol_mb"]
    s1 = focus[focus["date"] == d1].set_index("tank_id")["vol_mb"]
    common = s0.index.intersection(s1.index)

    delta = (s1[common] - s0[common])
    inflow_tanks  = delta[delta > 0.01].sort_values(ascending=False)
    outflow_tanks = delta[delta < -0.01].sort_values()

    total_delta = delta.sum()
    rate_per_day = total_delta / gap_days

    records.append({
        "period": f"{d0.strftime('%Y-%m-%d')} → {d1.strftime('%Y-%m-%d')}",
        "gap_days": gap_days,
        "total_delta_mb": round(total_delta, 3),
        "rate_mb_per_day": round(rate_per_day, 4),
        "vol_start_mb": round(s0[common].sum(), 2),
        "vol_end_mb": round(s1[common].sum(), 2),
        "inflow_tanks": {t: round(v, 3) for t, v in inflow_tanks.items()},
        "outflow_tanks": {t: round(v, 3) for t, v in outflow_tanks.items()},
        "tanks_filling": len(inflow_tanks),
        "tanks_draining": len(outflow_tanks),
    })

# ── 5. Save
out = {"periods": records}
os.makedirs("data", exist_ok=True)
with open("data/tank_flows_2026.json", "w") as f:
    json.dump(out, f, indent=2)
print(f"Saved {len(records)} periods to data/tank_flows_2026.json")

# ── 6. Print summary to stdout
for r in records:
    sign = "▲" if r["total_delta_mb"] > 0 else "▼"
    print(f"{r['period']} ({r['gap_days']}d): {sign}{abs(r['total_delta_mb']):.2f} Mb  "
          f"[{r['vol_start_mb']}→{r['vol_end_mb']}]  "
          f"filling:{r['tanks_filling']} draining:{r['tanks_draining']}")
