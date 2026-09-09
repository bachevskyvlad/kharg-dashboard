"""
Run once to generate fixtures/kharg_sample.csv and fixtures/kharg_golden.json.
Uses only numpy/pandas — no Supabase, no Sentinel Hub.

Usage:
    cd tests/fixtures
    python generate_kharg_fixture.py
"""

import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from volume import compute_volumes, aggregate_daily

# Deterministic seed
rng = np.random.default_rng(42)

# 47 tanks × 5 dates
TANK_SPECS = (
    [("t60", 60.0, 18.0)] * 5 +
    [("t55", 55.0, 17.0)] * 18 +
    [("t40", 40.0, 14.0)] * 12 +
    [("t25", 25.0, 12.0)] * 12
)
DATES = pd.date_range("2024-01-01", periods=5, freq="6D")

rows = []
for date in DATES:
    for i, (prefix, r, h) in enumerate(TANK_SPECS):
        rows.append({
            "date": date,
            "tank_id": f"{prefix}_{i}",
            "radius_m": r,
            "h_max": h,
            "db_mean": rng.uniform(0, 14),
        })

df = pd.DataFrame(rows)
df.to_csv("kharg_sample.csv", index=False)

df_vols, dB_min, dB_max = compute_volumes(df)
daily = aggregate_daily(df_vols)

golden = {
    "dB_min": round(float(dB_min), 6),
    "dB_max": round(float(dB_max), 6),
    "daily": [
        {"date": str(r["day"].date()), "vol_mn_bbl": round(float(r["vol_mn_bbl"]), 6)}
        for _, r in daily.iterrows()
    ]
}

with open("kharg_golden.json", "w") as f:
    json.dump(golden, f, indent=2)

print(f"Written kharg_sample.csv ({len(df)} rows) and kharg_golden.json ({len(daily)} daily rows)")
print(f"dB range: {dB_min:.4f} – {dB_max:.4f}")
print(daily[["day", "vol_mn_bbl"]].to_string(index=False))
