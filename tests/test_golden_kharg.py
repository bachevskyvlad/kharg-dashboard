"""
Group 6 — Golden regression test: fixture-based bit-identical output check.

The fixture (tests/fixtures/kharg_sample.csv + kharg_golden.json) was generated
once from the current production logic. This test confirms that any refactor
produces the exact same numbers to 3 decimal places (the precision used in
Supabase writes). If these numbers change, it means the physics model changed.
"""

import sys
import os
import json
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from volume import compute_volumes, aggregate_daily

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
CSV_PATH    = os.path.join(FIXTURE_DIR, "kharg_sample.csv")
GOLDEN_PATH = os.path.join(FIXTURE_DIR, "kharg_golden.json")


@pytest.fixture(scope="module")
def golden():
    with open(GOLDEN_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def sample_df():
    df = pd.read_csv(CSV_PATH, parse_dates=["date"])
    return df


# ── Test 1: dB calibration range is stable ───────────────────────────────────
def test_db_range_stable(sample_df, golden):
    _, dB_min, dB_max = compute_volumes(sample_df.copy())
    assert round(float(dB_min), 3) == round(golden["dB_min"], 3), \
        f"dB_min changed: {dB_min:.6f} vs {golden['dB_min']}"
    assert round(float(dB_max), 3) == round(golden["dB_max"], 3), \
        f"dB_max changed: {dB_max:.6f} vs {golden['dB_max']}"


# ── Test 2: daily volumes are bit-identical to 3dp ───────────────────────────
def test_daily_volumes_stable(sample_df, golden):
    df_vols, _, _ = compute_volumes(sample_df.copy())
    daily = aggregate_daily(df_vols)
    daily = daily.sort_values("day").reset_index(drop=True)

    golden_rows = {r["date"]: r["vol_mn_bbl"] for r in golden["daily"]}

    for _, row in daily.iterrows():
        date_str = str(row["day"].date())
        assert date_str in golden_rows, f"Unexpected date in output: {date_str}"
        computed = round(float(row["vol_mn_bbl"]), 3)
        expected = round(golden_rows[date_str], 3)
        assert computed == expected, (
            f"Volume mismatch on {date_str}: got {computed}, expected {expected}"
        )


# ── Test 3: row count matches golden ─────────────────────────────────────────
def test_daily_row_count(sample_df, golden):
    df_vols, _, _ = compute_volumes(sample_df.copy())
    daily = aggregate_daily(df_vols)
    assert len(daily) == len(golden["daily"])


# ── Test 4: all output volumes are within physical bounds ─────────────────────
def test_volumes_within_bounds(sample_df):
    import math
    from config import BARREL_PER_M3, TANK_HEIGHT_MIN
    df_vols, _, _ = compute_volumes(sample_df.copy())
    daily = aggregate_daily(df_vols)
    # Max theoretical capacity from the fixture tanks
    # t60×5: π×60²×18 + t55×18: π×55²×17 + t40×12: π×40²×14 + t25×12: π×25²×12
    max_m3 = (
        5 * math.pi * 60**2 * 18 +
        18 * math.pi * 55**2 * 17 +
        12 * math.pi * 40**2 * 14 +
        12 * math.pi * 25**2 * 12
    )
    max_bbl = max_m3 * BARREL_PER_M3 / 1_000_000
    floor_bbl = (
        5 * math.pi * 60**2 * TANK_HEIGHT_MIN +
        18 * math.pi * 55**2 * TANK_HEIGHT_MIN +
        12 * math.pi * 40**2 * TANK_HEIGHT_MIN +
        12 * math.pi * 25**2 * TANK_HEIGHT_MIN
    ) * BARREL_PER_M3 / 1_000_000
    for _, row in daily.iterrows():
        assert row["vol_mn_bbl"] >= floor_bbl * 0.95, \
            f"Volume below floor on {row['day']}: {row['vol_mn_bbl']:.3f}"
        assert row["vol_mn_bbl"] <= max_bbl * 1.01, \
            f"Volume above max on {row['day']}: {row['vol_mn_bbl']:.3f}"
