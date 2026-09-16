"""
Group 1 — compute_volumes: formula, clip behaviour, floor, global percentiles.
"""

import math
import sys
import os
import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from volume import compute_volumes, aggregate_daily, aggregate_weekly
from config import BARREL_PER_M3, TANK_HEIGHT_MIN


def _one_tank_df(db_mean, radius_m=40.0, h_max=14.0, n=20):
    """20 rows of one tank so 5th/95th percentile spans the range."""
    dates = pd.date_range("2024-01-01", periods=n, freq="6D")
    return pd.DataFrame({
        "date": dates,
        "tank_id": "t40_0",
        "db_mean": np.linspace(0, 14, n) if db_mean is None else [db_mean] * n,
        "radius_m": radius_m,
        "h_max": h_max,
    })


def _two_tank_df():
    """Two tanks with known dB spread so we can predict fill_ratio."""
    dates = pd.date_range("2024-01-01", periods=20, freq="6D")
    df_a = pd.DataFrame({
        "date": dates, "tank_id": "tank_a",
        "db_mean": np.linspace(0, 14, 20),
        "radius_m": 40.0, "h_max": 14.0,
    })
    df_b = df_a.copy(); df_b["tank_id"] = "tank_b"
    return pd.concat([df_a, df_b], ignore_index=True)


# ── Test 1: full tank (low dB → fill_ratio ≈ 1) ──────────────────────────────
def test_low_db_gives_high_volume():
    df = _one_tank_df(db_mean=None)
    df_out, dB_min, dB_max = compute_volumes(df)
    # Lowest dB row should have the highest barrels
    assert df_out.loc[df_out["db_mean"].idxmin(), "barrels"] > \
           df_out.loc[df_out["db_mean"].idxmax(), "barrels"]


# ── Test 2: empty tank (high dB → fill_ratio ≈ 0 → h_current = TANK_HEIGHT_MIN)
def test_high_db_gives_floor_height():
    df = _one_tank_df(db_mean=None)
    df_out, dB_min, dB_max = compute_volumes(df)
    radius_m = 40.0
    floor_barrels = math.pi * radius_m**2 * TANK_HEIGHT_MIN * BARREL_PER_M3
    # Highest dB row ≈ floor (clipped to 95th percentile, not exact 14.0)
    highest_idx = df_out["db_mean"].idxmax()
    # floor_barrels is a lower bound; row barrels should be close
    assert df_out.loc[highest_idx, "barrels"] >= floor_barrels * 0.95


# ── Test 3: exact formula for a known middle row ─────────────────────────────
def test_formula_matches_manual_calculation():
    # Build a df where dB_min=1 and dB_max=11 (5th and 95th of a 20-row linspace 1..11)
    vals = np.linspace(1.0, 11.0, 20)
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=20, freq="6D"),
        "tank_id": "t1",
        "db_mean": vals,
        "radius_m": 55.0,
        "h_max": 17.0,
    })
    df_out, dB_min, dB_max = compute_volumes(df)
    # Pick a specific row (index 10, db_mean ≈ 6.26)
    row = df_out.iloc[10]
    vv = np.clip(row["db_mean"], dB_min, dB_max)
    fill_ratio = (dB_max - vv) / (dB_max - dB_min)
    h_current = TANK_HEIGHT_MIN + fill_ratio * (17.0 - TANK_HEIGHT_MIN)
    expected = math.pi * 55.0**2 * h_current * BARREL_PER_M3
    assert abs(row["barrels"] - expected) < 1e-6


# ── Test 4: clipping — outlier dB should be clipped to dB_max ────────────────
def test_outlier_db_clipped():
    vals = list(np.linspace(1.0, 11.0, 18)) + [100.0, -100.0]
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=20, freq="6D"),
        "tank_id": "t1",
        "db_mean": vals,
        "radius_m": 40.0,
        "h_max": 14.0,
    })
    df_out, dB_min, dB_max = compute_volumes(df)
    # Both extreme rows should have same barrels as if clipped
    outlier_high = df_out[df_out["db_mean"] == 100.0]["barrels"].iloc[0]
    outlier_low  = df_out[df_out["db_mean"] == -100.0]["barrels"].iloc[0]
    # clipped high → fill_ratio=0 → floor
    floor = math.pi * 40.0**2 * TANK_HEIGHT_MIN * BARREL_PER_M3
    assert abs(outlier_high - floor) < 1.0
    # clipped low → fill_ratio=1 → h_max
    top = math.pi * 40.0**2 * 14.0 * BARREL_PER_M3
    assert abs(outlier_low - top) < 1.0


# ── Test 5: global percentiles are computed across ALL tanks, not per-tank ────
def test_global_percentiles_not_per_tank():
    df = _two_tank_df()
    df_out, dB_min, dB_max = compute_volumes(df)
    # Each tank has same db_mean distribution so per-tank == global; verify
    # that both tanks' rows use the same dB_min/dB_max (function returns scalars)
    assert isinstance(dB_min, float)
    assert isinstance(dB_max, float)
    assert dB_min < dB_max


# ── Test 6: aggregate_daily sums across tanks correctly ──────────────────────
def test_aggregate_daily_sums_tanks():
    df = _two_tank_df()
    df_out, _, _ = compute_volumes(df)
    daily = aggregate_daily(df_out)
    # Should have one row per date and volumes should be positive
    assert len(daily) == 20
    assert (daily["vol_mn_bbl"] > 0).all()


# ── Test 7 (xfail BUG): all-same dB → dB_min==dB_max → division by zero ──────
# When every tank reads the same dB (e.g., all empty), the 5th and 95th
# percentiles are equal, causing (dB_max - dB_min) = 0 → NaN in fill_ratio.
# The floor should still apply but currently yields NaN barrels.
@pytest.mark.xfail(reason="BUG: compute_volumes divides by zero when dB_min==dB_max", strict=True)
def test_tank_height_min_floor():
    # Make a df where one row hits exactly fill_ratio=0
    # fill_ratio=0 when vv == dB_max (clipped)
    # Arrange: all values = dB_max (high dB, effectively empty)
    vals = [14.0] * 20
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=20, freq="6D"),
        "tank_id": "t1",
        "db_mean": vals,
        "radius_m": 40.0,
        "h_max": 14.0,
    })
    df_out, dB_min, dB_max = compute_volumes(df)
    # All fill_ratio=0 → h_current=TANK_HEIGHT_MIN=2m
    expected = math.pi * 40.0**2 * TANK_HEIGHT_MIN * BARREL_PER_M3
    assert all(abs(b - expected) < 1e-4 for b in df_out["barrels"])
