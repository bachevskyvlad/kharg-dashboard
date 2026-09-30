"""
volume.py — Volume calculation and daily/weekly aggregation.

Physics model (inverse crescent radiometry):
  1. Clip each tank's db_mean to the global [5th, 95th] percentile range.
     This handles outlier tanks (chronically full or idle) without excluding
     them from the dataset entirely. Outliers at the tails compress toward
     the clip boundary, contributing some volume rather than distorting it.

  2. fill_ratio = (dB_max - db_mean) / (dB_max - dB_min)
     Inverted because lower dB = more oil. fill_ratio=1 means the tank reads
     as full as the fullest 5th-percentile tank ever observed historically.

  3. h_current = TANK_HEIGHT_MIN + fill_ratio × (h_max - TANK_HEIGHT_MIN)
     Converts ratio to physical height. TANK_HEIGHT_MIN=2m is the physical
     floor below which the floating roof cannot descend.

  4. vol_bbl = π × radius² × h_current × BARREL_PER_M3
     Standard cylindrical volume formula.

Aggregation:
  - Daily: mean barrels per (day, tank_id), then sum across tanks.
    'mean' handles the rare case where a day has multiple raw records
    for the same tank (shouldn't happen with dedup, but defensive).
  - Weekly: align to Monday of each week, then EWMA-smooth.
    Weekly smoothing is only for the chart/marketing view (data.json).
    Analysts use data_daily.json for raw signal.

The dB_min/dB_max are computed globally across the entire history every run.
This means adding new passes slightly shifts percentiles, which marginally
changes historical volume estimates. This is intentional and correct —
new data improves the calibration. If you need frozen historical values,
snapshot the percentiles at a fixed date.
"""

import numpy as np
import pandas as pd
from config import BARREL_PER_M3, TANK_HEIGHT_MIN, EWMA_ALPHA


def compute_volumes(df, db_min_override=None, db_max_override=None,
                    per_orbit_calibration=False):
    """
    Adds a 'barrels' column to df using the inverse crescent radiometry model.

    df must have columns: db_mean, radius_m, h_max.
    Percentile clipping is global (all tanks, all dates) — do not filter
    before calling this or the calibration will be wrong.

    db_min_override / db_max_override: optional frozen calibration values.
    When set, skip the percentile computation and use the supplied constants.
    This keeps volume time-series comparable when updating historical data.
    For Kharg: never pass overrides (golden test must pass bit-for-bit).

    per_orbit_calibration: when True and df has an 'orbit_direction' column,
    compute separate dB_min/dB_max per orbit_direction group. ASC and DESC
    differ by ~4.7 dB offset — mixing them into one global percentile produces
    a systematic bias. Returns calibration_map = {direction: (dB_min, dB_max)}.
    When False (default), uses global percentiles (legacy behavior).
    """
    if per_orbit_calibration and "orbit_direction" in df.columns:
        calibration_map = {}
        df = df.copy()
        df["barrels"] = np.nan

        for direction, grp in df.groupby("orbit_direction"):
            if db_min_override is not None and db_max_override is not None:
                dB_min_d = db_min_override
                dB_max_d = db_max_override
            else:
                dB_min_d = grp["db_mean"].quantile(0.05)
                dB_max_d = grp["db_mean"].quantile(0.95)
            calibration_map[direction] = (dB_min_d, dB_max_d)

            def _row_vol(row, lo=dB_min_d, hi=dB_max_d):
                vv = np.clip(row["db_mean"], lo, hi)
                fr = (hi - vv) / (hi - lo)
                h  = TANK_HEIGHT_MIN + fr * (row["h_max"] - TANK_HEIGHT_MIN)
                return np.pi * row["radius_m"]**2 * h * BARREL_PER_M3

            df.loc[grp.index, "barrels"] = grp.apply(_row_vol, axis=1)

        # Return first direction's calibration as (dB_min, dB_max) for compatibility,
        # plus full map as third return value when per_orbit_calibration=True.
        first = next(iter(calibration_map.values()))
        return df, first[0], first[1], calibration_map

    # Legacy global-percentile path
    if db_min_override is not None and db_max_override is not None:
        dB_min = db_min_override
        dB_max = db_max_override
    else:
        dB_min = df['db_mean'].quantile(0.05)
        dB_max = df['db_mean'].quantile(0.95)

    def _row_volume(row):
        vv = np.clip(row["db_mean"], dB_min, dB_max)
        fill_ratio = (dB_max - vv) / (dB_max - dB_min)
        h_current  = TANK_HEIGHT_MIN + fill_ratio * (row["h_max"] - TANK_HEIGHT_MIN)
        vol_m3     = np.pi * row["radius_m"]**2 * h_current
        return vol_m3 * BARREL_PER_M3

    df["barrels"] = df.apply(_row_volume, axis=1)
    return df, dB_min, dB_max


def aggregate_daily(df):
    """
    Aggregates per-tank barrel estimates to a daily total volume series.

    Returns a DataFrame with columns: day (date), vol_mn_bbl.
    Units: Mn bbl (millions of barrels) — all frontend/email code expects Mb.
    """
    df["day"] = df["date"].dt.normalize()
    daily_tank  = df.groupby(["day", "tank_id"])["barrels"].mean().reset_index()
    daily_total = daily_tank.groupby("day")["barrels"].sum().reset_index()
    daily_total["vol_mn_bbl"] = daily_total["barrels"] / 1_000_000
    return daily_total


def aggregate_weekly(df):
    """
    Aggregates to weekly totals aligned to Monday, then applies EWMA smoothing.

    Week alignment: each pass date is mapped to its week's Monday via
    date - dayofweek_offset. This means a Wednesday pass and a Friday pass
    in the same calendar week both contribute to the same weekly bucket.

    EWMA alpha=0.1 (~10-week memory) is applied to the smoothed_vol column.
    Raw weekly values are also retained for the background scatter in the chart.

    Returns a DataFrame with columns: week_start, vol_mn_bbl, smoothed_vol.
    """
    df["week_start"] = (df["date"] - pd.to_timedelta(df["date"].dt.dayofweek, unit="D")).dt.normalize()
    weekly_tank  = df.groupby(["week_start", "tank_id"])["barrels"].mean().reset_index()
    weekly_total = weekly_tank.groupby("week_start")["barrels"].sum().reset_index()
    weekly_total["vol_mn_bbl"]   = weekly_total["barrels"] / 1_000_000
    weekly_total["smoothed_vol"] = weekly_total["vol_mn_bbl"].ewm(alpha=EWMA_ALPHA, adjust=False).mean()
    weekly_total = weekly_total.dropna(subset=["smoothed_vol"])
    return weekly_total
