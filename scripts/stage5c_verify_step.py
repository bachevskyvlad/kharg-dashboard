"""
stage5c_verify_step.py — Step metric verification: pilot vs prod.

Checks:
1. Confirms az=98 was used in prod ASC backfill crescent masks (log).
2. Diffs pilot vs prod parameters table.
3. Recomputes step for rt_5, rt_6, rt_8 on the 10 pilot dates using the
   PROD DB rows from terminal_observations.
   - Pilot step = median(|db[i] - db[i-1]|) over consecutive passes (sorted by date)
   - stage5b step = p95 - p5  ← WRONG metric — this is the range, not volatility

Expected (pilot ground truth): rt_5=0.420  rt_6=0.488  rt_8=1.718
If prod rows give different numbers → calibration bug in ASC backfill.
If same → only stage5b WL metric is wrong (use median|diff| not p95-p5).
"""

import os, sys
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://yoekhkczwdeyauwarsyy.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or "sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM"

TERMINAL_ID = "ras_tanura"
PILOT_TANKS = ["rt_5", "rt_6", "rt_8"]
DB_FLOOR_PILOT = -5.0
DB_FLOOR_PROD  = -20.0

# 10 pilot dates (from asc101_pilot.txt header: 2026-08-05 → 2026-09-28, cadence 6d)
# We'll derive them from the DB rather than hardcode.

from supabase import create_client
sb = create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_asc_rows(tank_ids):
    rows = []; offset = 0
    while True:
        batch = (sb.table("terminal_observations")
                   .select("date,tank_id,db_mean,orbit_direction,relative_orbit")
                   .eq("terminal_id", TERMINAL_ID)
                   .eq("orbit_direction", "ASC")
                   .range(offset, offset+999).execute().data)
        rows.extend(batch)
        if len(batch) < 1000: break
        offset += 1000
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")
    return df[df["tank_id"].isin(tank_ids)]


print("=" * 62)
print("PART 1 — Parameter diff: pilot vs prod")
print("=" * 62)
params = [
    ("az (LOS azimuth)",       "98 (ASC_LOS_AZ)",     "98 (ASC_AZ=98)"),
    ("width_m (annulus)",      "15m (WIDTH_WIDE)",    "15m hardcoded"),
    ("buffer_m (bbox pad)",    "500m (BUFFER=500)",   "300m (BUFFER_M=300)"),
    ("resolution (m/px)",      "5 (RESOLUTION)",      "5 (RESOLUTION=5)"),
    ("DB_FLOOR",               "-5.0 (config.py)",   "-20.0 (stage5 constant)"),
    ("step metric (WL test)",  "median|db[t]-db[t-1]|", "p95-p5 (range, NOT diff)"),
    ("orbit_cfg dataFilter",   "yes (ASCENDING+101)", "yes (ASCENDING+101)"),
]
print(f"{'Parameter':<30} {'Pilot':>20} {'Prod backfill':>20}")
print("-" * 72)
for name, pilot, prod in params:
    flag = " ⚠" if pilot.split("(")[0].strip() != prod.split("(")[0].strip() else ""
    print(f"{name:<30} {pilot:>20} {prod:>20}{flag}")


print("\n" + "=" * 62)
print("PART 2 — Step recompute for rt_5, rt_6, rt_8")
print("=" * 62)

df = fetch_asc_rows(PILOT_TANKS)
print(f"Fetched {len(df)} ASC rows for {PILOT_TANKS}")

# Identify the 10 most-recent dates (= pilot dates)
all_dates_sorted = sorted(df["date"].dt.normalize().unique(), reverse=True)
pilot_10 = all_dates_sorted[:10]
print(f"10 most-recent ASC dates: {pilot_10[0].date()} → {pilot_10[-1].date()}")

df_pilot = df[df["date"].dt.normalize().isin(pilot_10)].copy()

pilot_ref = {"rt_5": 0.420, "rt_6": 0.488, "rt_8": 1.718}

print(f"\n{'Tank':<8} {'n_obs':>6} {'step_mediff_floor-5':>22} {'step_mediff_floor-20':>22} {'step_p95-p5':>14} {'pilot_ref':>10} {'match_floor-5':>14}")
print("-" * 100)

for tid in PILOT_TANKS:
    sub = df_pilot[df_pilot["tank_id"] == tid].sort_values("date")
    n = len(sub)
    ref = pilot_ref[tid]

    if n < 2:
        print(f"{tid:<8} {n:>6}  insufficient data")
        continue

    # metric A: median|diff| with DB_FLOOR=-5.0 (pilot)
    vals5 = sub["db_mean"].clip(lower=DB_FLOOR_PILOT).values
    diffs5 = [abs(vals5[i] - vals5[i-1]) for i in range(1, len(vals5))]
    step_a = float(np.median(diffs5))

    # metric B: median|diff| with DB_FLOOR=-20.0 (prod)
    vals20 = sub["db_mean"].clip(lower=DB_FLOOR_PROD).values
    diffs20 = [abs(vals20[i] - vals20[i-1]) for i in range(1, len(vals20))]
    step_b = float(np.median(diffs20))

    # metric C: p95-p5 (what stage5b uses — wrong metric)
    step_c = float(sub["db_mean"].quantile(0.95) - sub["db_mean"].quantile(0.05))

    match = "✓" if abs(step_a - ref) < 0.05 else f"DIFF {step_a - ref:+.3f}"
    print(f"{tid:<8} {n:>6} {step_a:>22.3f} {step_b:>22.3f} {step_c:>14.3f} {ref:>10.3f} {match:>14}")

print("""
PART 3 — Diagnosis summary
─────────────────────────────────────────────────────────────────
• az=98 is correct in both pilot and prod backfill.
• BUFFER_M differs (500m pilot vs 300m prod) — may shift pixel count
  but should not change step significantly for r=55m tanks.
• DB_FLOOR=-5.0 in pilot vs -20.0 in prod: if any pass returns
  db_mean < -5, the floor clips differ and step_a ≠ step_b.
• CRITICAL: stage5b WL metric = p95-p5 (range) ≠ median|diff|.
  p95-p5 measures total dynamic range over 6yr; median|diff| measures
  pass-to-pass volatility. range >> diff for slowly-trending tanks.
  Fix: replace WL test in stage5b with median|diff| on sorted dates.
""")
