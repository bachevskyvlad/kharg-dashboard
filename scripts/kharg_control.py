"""
kharg_control.py — Kharg calibration control.

Computes smoothed_corr(Δ_all47, Δ_top30-by-step) using the same methodology
as stage5_decompose.py. Used to calibrate the smoothed_corr threshold:
if Kharg (ground-truth dataset) yields ~0.6-0.7, the 0.9 threshold was
unrealistic; if ~0.9, the threshold was appropriate.

step = median |db[i] - db[i-1]| across consecutive passes per tank.
Top-30 = 30 tanks with highest step (most radiometrically active).
Shared calibration: dB_min/dB_max from all 47 tanks.

Output: diagnostics/stage5/kharg_control.txt
"""

import os, sys, datetime
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd

from config import sb
from volume import compute_volumes, aggregate_daily
from coverage_filter import apply_coverage_filter

os.makedirs("diagnostics/stage5", exist_ok=True)


def _fetch_kharg():
    all_obs = []; offset = 0
    while True:
        batch = (sb.table("raw_observations")
                   .select("date,tank_id,db_mean,radius_m,h_max")
                   .range(offset, offset + 999).execute().data)
        all_obs.extend(batch)
        if len(batch) < 1000: break
        offset += 1000
    return all_obs


def _smooth3(s):
    return s.rolling(3, center=True, min_periods=1).median()


def _corr_delta(a, b):
    da = a.diff().dropna()
    db = b.diff().dropna()
    idx = da.index.intersection(db.index)
    if len(idx) < 5:
        return float("nan")
    return float(da.loc[idx].corr(db.loc[idx]))


def main():
    print("Fetching Kharg raw_observations...")
    rows = _fetch_kharg()
    print(f"  {len(rows)} rows")

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["radius_m"] = pd.to_numeric(df["radius_m"], errors="coerce")
    df["h_max"] = pd.to_numeric(df["h_max"], errors="coerce")
    df["db_mean"] = pd.to_numeric(df["db_mean"], errors="coerce")
    df = df.dropna(subset=["radius_m", "h_max", "db_mean"])

    # Compute step per tank
    steps = {}
    for tid, grp in df.groupby("tank_id"):
        s = grp.sort_values("date")["db_mean"]
        if len(s) < 3:
            steps[tid] = 0.0
        else:
            steps[tid] = float(s.diff().dropna().abs().median())

    all_tanks = list(steps.keys())
    print(f"  Total tanks: {len(all_tanks)}")

    top30 = sorted(steps, key=lambda t: -steps[t])[:30]
    print(f"  Top-30 step range: {steps[top30[0]]:.3f} – {steps[top30[29]]:.3f} dB")

    # Shared date gate from all tanks
    df_gate, _, _ = apply_coverage_filter(df.copy(), min_share=0.5)
    valid_dates = set(df_gate["date"].unique())
    print(f"  Valid dates (full coverage gate): {len(valid_dates)}")

    # Shared calibration from all tanks
    _, shared_db_min, shared_db_max = compute_volumes(df.copy())
    print(f"  Shared calibration: dB_min={shared_db_min:.3f}  dB_max={shared_db_max:.3f}")

    def _curve(tank_filter=None):
        d = df.copy()
        if tank_filter is not None:
            d = d[d["tank_id"].isin(tank_filter)]
        d = d[d["date"].isin(valid_dates)]
        d, _, _ = compute_volumes(d, db_min_override=shared_db_min, db_max_override=shared_db_max)
        return aggregate_daily(d)

    all47_daily = _curve(tank_filter=None)
    top30_daily = _curve(tank_filter=set(top30))

    merged = (all47_daily.rename(columns={"vol_mn_bbl": "all47", "day": "date"})
              .merge(top30_daily.rename(columns={"vol_mn_bbl": "top30", "day": "date"}),
                     on="date", how="inner"))

    all47_s = _smooth3(merged.set_index("date")["all47"])
    top30_s = _smooth3(merged.set_index("date")["top30"])

    raw_corr     = _corr_delta(merged.set_index("date")["all47"], merged.set_index("date")["top30"])
    smoothed_corr = _corr_delta(all47_s, top30_s)

    print(f"\n  raw_corr(Δ_all47, Δ_top30)     = {raw_corr:.4f}")
    print(f"  smoothed_corr(Δ_all47, Δ_top30) = {smoothed_corr:.4f}  ← KEY")

    out = [
        f"Kharg Control  {datetime.datetime.utcnow():%Y-%m-%d %H:%M} UTC",
        f"n_tanks_total={len(all_tanks)}  n_top30=30",
        f"n_dates_overlap={len(merged)}",
        f"top30_step_range: {steps[top30[0]]:.3f} – {steps[top30[29]]:.3f} dB",
        f"raw_corr(Δ_all47, Δ_top30)     = {raw_corr:.4f}",
        f"smoothed_corr(Δ_all47, Δ_top30) = {smoothed_corr:.4f}",
    ]
    txt = "\n".join(out)
    with open("diagnostics/stage5/kharg_control.txt", "w") as f:
        f.write(txt)
    print(f"\n{txt}")
    print("\nSaved: diagnostics/stage5/kharg_control.txt")


if __name__ == "__main__":
    main()
