"""
Group 2 — apply_coverage_filter: both clusters present, north-only, xfail bugs.
"""

import sys
import os
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from coverage_filter import apply_coverage_filter


def _make_df(date_tank_pairs):
    """Build DataFrame from [(date_str, tank_id), ...] pairs."""
    rows = [{"date": pd.Timestamp(d), "tank_id": t} for d, t in date_tank_pairs]
    return pd.DataFrame(rows)


# ── Test 1: both clusters → full coverage dates kept ─────────────────────────
def test_full_coverage_dates_kept():
    # 3 dates with 66 tanks each (simulating Ras Tanura full pass)
    pairs = [(f"2024-01-{d:02d}", f"rt_{i}") for d in [1, 7, 13] for i in range(66)]
    df = _make_df(pairs)
    filtered, n_dropped, max_tanks = apply_coverage_filter(df, min_share=0.5)
    assert n_dropped == 0
    assert max_tanks == 66
    assert filtered["date"].nunique() == 3


# ── Test 2: north-only dates (6/66 tanks) are dropped ────────────────────────
def test_north_only_dates_dropped():
    # 2 good dates (66 tanks) + 1 partial date (6 tanks, north cluster only)
    good = [(f"2024-01-{d:02d}", f"rt_{i}") for d in [1, 7] for i in range(66)]
    partial = [("2024-01-14", f"rt_{i}") for i in [5, 6, 7, 8, 9, 10]]
    df = _make_df(good + partial)
    filtered, n_dropped, max_tanks = apply_coverage_filter(df, min_share=0.5)
    assert n_dropped == 1
    assert max_tanks == 66
    assert filtered["date"].nunique() == 2


# ── Test 3: single date with partial tanks — passes its own filter ────────────
# When there is only one date and max_tanks = n, threshold = n*0.5 < n, so the
# date is kept. "All partial" only makes sense when there is a reference date
# with more tanks (i.e., a later full-coverage date raises max_tanks).
def test_all_partial_returns_empty():
    # 1 full date (10 tanks) + 1 partial date (4 tanks) → partial dropped
    full = [("2024-01-01", f"rt_{i}") for i in range(10)]
    partial = [("2024-01-07", f"rt_{i}") for i in range(4)]  # 4/10 = 40% < 50%
    df = _make_df(full + partial)
    filtered, n_dropped, max_tanks = apply_coverage_filter(df, min_share=0.5)
    assert max_tanks == 10
    assert n_dropped == 1
    assert filtered["date"].nunique() == 1


# ── Test 4: single-tank df passes its own filter (max_tanks=1) ───────────────
def test_single_tank_always_passes():
    pairs = [(f"2024-01-{d:02d}", "t1") for d in range(1, 6)]
    df = _make_df(pairs)
    filtered, n_dropped, max_tanks = apply_coverage_filter(df)
    assert n_dropped == 0
    assert max_tanks == 1
    assert len(filtered) == 5


# ── Test 5: custom min_share threshold ───────────────────────────────────────
def test_custom_min_share():
    good = [("2024-01-01", f"t{i}") for i in range(10)]  # 10 tanks
    partial = [("2024-01-07", f"t{i}") for i in range(6)]  # 6/10 = 60%
    df = _make_df(good + partial)
    # At 70% threshold, 6-tank date should be dropped
    filtered_70, n_dropped_70, _ = apply_coverage_filter(df, min_share=0.7)
    assert n_dropped_70 == 1
    # At 50% threshold, it should be kept
    filtered_50, n_dropped_50, _ = apply_coverage_filter(df, min_share=0.5)
    assert n_dropped_50 == 0


# ── Test 6: same-calendar-day partial is filtered correctly ───────────────────
# Two timestamps on the same calendar day: one full (66 tanks), one partial (6).
# max_tanks=66, threshold=33; 6 < 33 → partial timestamp is dropped.
# The filter uses raw timestamps as group keys, which is correct because each
# SAR pass has a distinct acquisition time.
def test_same_day_partial_filtered():
    full_pass = [("2024-01-01 06:00", f"rt_{i}") for i in range(66)]
    partial_pass = [("2024-01-01 18:00", f"rt_{i}") for i in range(6)]
    df = _make_df(full_pass + partial_pass)
    filtered, n_dropped, max_tanks = apply_coverage_filter(df)
    assert max_tanks == 66
    assert n_dropped == 1
    assert filtered["date"].nunique() == 1


# ── Test 7: early north-only dates are dropped when later full dates arrive ───
# max_tanks is the global max across all dates, so early north-only dates (6 tanks)
# are correctly dropped once a later full-coverage date (66 tanks) exists.
def test_early_north_only_dates_dropped():
    early_north = [("2024-01-01", f"rt_{i}") for i in range(6)]   # north only
    later_full  = [("2024-01-14", f"rt_{i}") for i in range(66)]  # both clusters
    df = _make_df(early_north + later_full)
    filtered, n_dropped, max_tanks = apply_coverage_filter(df, min_share=0.5)
    assert max_tanks == 66
    # The 6-tank date should be dropped because 6 < 66*0.5=33
    assert n_dropped == 1
    assert filtered["date"].nunique() == 1
