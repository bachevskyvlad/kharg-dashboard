"""
C06b — Fill-clip fraction per current prod calibration.

For each terminal, compute per-orbit calibration (dB_min, dB_max) exactly as
volume.py does (5th/95th global percentile per orbit_direction group).
Then for each (tank, date) row compute fill_ratio = (dB_max - db_mean) / (dB_max - dB_min).

Report per tank:
  n_total: total rows with db_mean
  frac_clip_floor: fraction where fill_ratio clips at 0 (db_mean >= dB_max → empty edge)
  frac_clip_ceil:  fraction where fill_ratio clips at 1 (db_mean <= dB_min → full edge)
  n_clip_floor, n_clip_ceil: counts

Clipped rows contribute 0% or 100% fill, biasing the volume integral.
High clip fractions indicate a tank consistently outside the calibration range.
"""

import numpy as np
import pandas as pd
from . import RUN_DATE, SHA


def run(terminal_id: str, df: pd.DataFrame, registry_tanks: list) -> pd.DataFrame:
    df = df.copy()
    df = df.dropna(subset=["db_mean"])

    # Per-orbit calibration (mirrors volume.py per_orbit_calibration=True path)
    calib = {}
    if "orbit_direction" in df.columns and df["orbit_direction"].nunique() > 0:
        for dirn, grp in df.groupby("orbit_direction"):
            calib[dirn] = (float(grp["db_mean"].quantile(0.05)),
                           float(grp["db_mean"].quantile(0.95)))
    else:
        calib["ALL"] = (float(df["db_mean"].quantile(0.05)),
                        float(df["db_mean"].quantile(0.95)))
        df = df.copy()
        df["orbit_direction"] = "ALL"

    def fill_ratio(db, dB_min, dB_max):
        if dB_max == dB_min:
            return np.full_like(db, 0.5)
        return (dB_max - db) / (dB_max - dB_min)

    rows = []
    for t in registry_tanks:
        tid = t["id"]
        td = df[df["tank_id"] == tid]
        if len(td) == 0:
            rows.append({
                "terminal_id": terminal_id, "tank_id": tid,
                "n_total": 0,
                "frac_clip_floor": None, "n_clip_floor": None,
                "frac_clip_ceil": None, "n_clip_ceil": None,
                "calib_note": "NO_DATA",
                "check_id": "C06b", "run_date": RUN_DATE, "git_sha": SHA,
                "verdict": "",
            })
            continue

        clips_floor = 0
        clips_ceil = 0
        n_total = 0
        for dirn, grp in td.groupby("orbit_direction"):
            lo, hi = calib.get(dirn, calib.get("ALL", (np.nan, np.nan)))
            db = grp["db_mean"].values.astype(float)
            fr = fill_ratio(db, lo, hi)
            clips_floor += int(np.sum(fr <= 0.0))
            clips_ceil += int(np.sum(fr >= 1.0))
            n_total += len(db)

        calib_orbits = "; ".join(f"{k}:[{v[0]:.2f},{v[1]:.2f}]" for k, v in sorted(calib.items()))
        rows.append({
            "terminal_id": terminal_id, "tank_id": tid,
            "n_total": n_total,
            "frac_clip_floor": round(clips_floor / n_total, 4) if n_total else None,
            "n_clip_floor": clips_floor,
            "frac_clip_ceil": round(clips_ceil / n_total, 4) if n_total else None,
            "n_clip_ceil": clips_ceil,
            "calib_note": calib_orbits,
            "check_id": "C06b", "run_date": RUN_DATE, "git_sha": SHA,
            "verdict": "",
        })

    return pd.DataFrame(rows)
