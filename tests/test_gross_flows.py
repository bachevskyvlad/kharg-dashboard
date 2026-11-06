"""
gross_flows.compute_gross_flows — sign conventions, counts, consistency with
the main series (same calibration as volume.compute_volumes).
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from gross_flows import compute_gross_flows, COLS  # noqa: E402
from volume import compute_volumes, aggregate_daily  # noqa: E402


def _raw(n_dates=12, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n_dates, freq="6D")
    tanks = [("t60_0", 60, 18), ("t55_1", 55, 17), ("t40_2", 40, 14), ("t25_3", 25, 12)]
    rows = [{"date": d.strftime("%Y-%m-%d"), "tank_id": t, "radius_m": r, "h_max": h,
             "db_mean": float(rng.uniform(-2, 15))}
            for d in dates for t, r, h in tanks]
    return pd.DataFrame(rows)


def test_columns_and_pairs():
    raw = _raw()
    f = compute_gross_flows(raw)
    assert list(f.columns) == COLS
    assert len(f) == raw["date"].nunique() - 1
    assert (f["days"] == 6).all()
    assert (f["date_from"].iloc[1:].values == f["date_to"].iloc[:-1].values).all()


def test_signs_and_net():
    f = compute_gross_flows(_raw())
    assert (f["gross_out_mb"] >= 0).all() and (f["gross_in_mb"] >= 0).all()
    assert np.allclose(f["net_mb"], f["gross_in_mb"] - f["gross_out_mb"], atol=2e-4)
    assert ((f["tanks_draining"] + f["tanks_filling"]) <= 4).all()


def test_net_equals_main_series_delta():
    """Same calibration as volume_timeseries ⇒ Σ per-tank Δ == Δ daily total."""
    raw = _raw(n_dates=20, seed=1)
    f = compute_gross_flows(raw)
    df = raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df, _, _ = compute_volumes(df)
    daily = aggregate_daily(df).set_index("day")["vol_mn_bbl"]
    daily.index = daily.index.strftime("%Y-%m-%d")
    expected = f["date_to"].map(daily).values - f["date_from"].map(daily).values
    assert np.allclose(f["net_mb"].values, expected, atol=2e-4)


def test_missing_tank_excluded_from_pair():
    raw = _raw(n_dates=3)
    raw = raw[~((raw["date"] == "2025-01-07") & (raw["tank_id"] == "t25_3"))]
    f = compute_gross_flows(raw)
    # t25_3 absent on 2025-01-07 → contributes to neither adjacent pair
    assert (f["tanks_draining"] + f["tanks_filling"] <= 3).all()


def test_single_date_returns_empty():
    raw = _raw(n_dates=1)
    assert compute_gross_flows(raw).empty
