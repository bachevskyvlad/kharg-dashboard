"""
Group 3 — aggregate_daily / aggregate_weekly: missing tank, duplicate averaging.
"""

import sys
import os
import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from volume import aggregate_daily, aggregate_weekly, compute_volumes


def _base_df():
    """3 tanks × 5 dates, no missing data."""
    rows = []
    for i in range(5):
        dt = pd.Timestamp("2024-01-01") + pd.Timedelta(days=i * 6)
        for j in range(3):
            rows.append({
                "date": dt, "tank_id": f"t{j}",
                "barrels": 100_000.0 * (j + 1),  # t0=100k, t1=200k, t2=300k
            })
    return pd.DataFrame(rows)


# ── Test 1: daily totals = sum across tanks ───────────────────────────────────
def test_daily_total_sums_tanks():
    df = _base_df()
    daily = aggregate_daily(df)
    # All dates have same total: 100k + 200k + 300k = 600k barrels = 0.6 Mn bbl
    assert (daily["vol_mn_bbl"].round(3) == 0.600).all()


# ── Test 2: missing tank on one date lowers that day's sum ───────────────────
def test_missing_tank_lowers_daily_sum():
    df = _base_df()
    # Remove t2 (300k) from date index 2
    target_date = df["date"].unique()[2]
    df = df[~((df["date"] == target_date) & (df["tank_id"] == "t2"))]
    daily = aggregate_daily(df).sort_values("day").reset_index(drop=True)
    # All dates except index 2 should have 0.600; index 2 should have 0.300
    for i, row in daily.iterrows():
        if row["day"] == target_date:
            assert abs(row["vol_mn_bbl"] - 0.300) < 1e-9
        else:
            assert abs(row["vol_mn_bbl"] - 0.600) < 1e-9


# ── Test 3: duplicate observations for (date, tank_id) are averaged ──────────
def test_duplicate_obs_are_averaged():
    df = _base_df()
    # Add a second observation for t0 on date 0 with double the barrels
    dup_row = df[(df["tank_id"] == "t0") & (df["date"] == df["date"].min())].copy()
    dup_row["barrels"] = 300_000.0  # original was 100k, dup is 300k → mean = 200k
    df = pd.concat([df, dup_row], ignore_index=True)
    daily = aggregate_daily(df).sort_values("day").reset_index(drop=True)
    # First date: t0 averaged (100k+300k)/2=200k, t1=200k, t2=300k → 700k = 0.7 Mn bbl
    first = daily.iloc[0]["vol_mn_bbl"]
    assert abs(first - 0.700) < 1e-9


# ── Test 4: weekly alignment — same-week dates collapse to Monday ─────────────
def test_weekly_aligns_to_monday():
    # Wednesday + Friday of same week → both map to same Monday bucket
    rows = []
    for day_offset, barrels in [(2, 100_000.0), (4, 200_000.0)]:  # Wed=2, Fri=4
        dt = pd.Timestamp("2024-01-01") + pd.Timedelta(days=day_offset)
        rows.append({"date": dt, "tank_id": "t0", "barrels": barrels})
    # Add extra week so EWMA can compute (needs >= 1 prior point)
    for day_offset in [7, 13]:
        dt = pd.Timestamp("2024-01-01") + pd.Timedelta(days=day_offset)
        rows.append({"date": dt, "tank_id": "t0", "barrels": 150_000.0})
    df = pd.DataFrame(rows)
    weekly = aggregate_weekly(df)
    first_week = weekly.iloc[0]
    # Monday of the week containing Jan 3 (Wed) is Jan 1
    assert first_week["week_start"] == pd.Timestamp("2024-01-01")
    # Both Wed and Fri contribute to the same week; mean per tank then sum
    # t0: mean(100k, 200k) = 150k → 0.15 Mn bbl raw
    assert abs(first_week["vol_mn_bbl"] - 0.150) < 1e-9


# ── Test 5: aggregate_daily returns vol_mn_bbl column ────────────────────────
def test_aggregate_daily_columns():
    df = _base_df()
    daily = aggregate_daily(df)
    assert "vol_mn_bbl" in daily.columns
    assert "day" in daily.columns


# ── Test 6: aggregate_weekly returns smoothed_vol column ─────────────────────
def test_aggregate_weekly_columns():
    df = _base_df()
    weekly = aggregate_weekly(df)
    assert "smoothed_vol" in weekly.columns
    assert "week_start" in weekly.columns
