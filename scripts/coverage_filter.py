"""
coverage_filter.py — Partial-coverage filter for terminal observations.

Drops dates where fewer than min_share of the terminal's observed tanks
contributed data.  Prevents artificially low volume sums when a SAR pass
covers only one of Ras Tanura's two geographically separated clusters.
"""

import pandas as pd


def apply_coverage_filter(df: pd.DataFrame, min_share: float = 0.5,
                          tank_capacity: dict = None):
    """
    Filter out partial-coverage dates.

    Parameters
    ----------
    df            : DataFrame with columns ``date`` (datetime) and ``tank_id`` (str).
    min_share     : minimum fraction a date must cover to be kept.
    tank_capacity : optional dict {tank_id: capacity_m3}. When provided, uses
                    capacity-weighted coverage instead of tank count — a date is
                    kept only if the sum of capacities of observed tanks is >=
                    min_share × total_capacity.  This prevents low-capacity
                    partial passes from passing the filter even when many tanks
                    are present (e.g. RT north cluster = 4 tanks but 26% capacity).

    Returns
    -------
    filtered_df   : DataFrame with partial-coverage dates removed.
    n_dropped     : number of dates that were dropped.
    ref_value     : total_capacity (capacity mode) or max_tanks (count mode).
    """
    if tank_capacity is not None:
        total_capacity = sum(tank_capacity.values())
        if total_capacity == 0:
            tanks_per_date = df.groupby("date")["tank_id"].nunique()
            max_tanks = int(tanks_per_date.max())
            threshold = max_tanks * min_share
            valid_dates = tanks_per_date[tanks_per_date >= threshold].index
            n_dropped = int((tanks_per_date < threshold).sum())
            return df[df["date"].isin(valid_dates)].copy(), n_dropped, max_tanks

        def _cap_for_date(grp):
            return sum(tank_capacity.get(tid, 0) for tid in grp["tank_id"].unique())

        cap_per_date = df.groupby("date").apply(_cap_for_date)
        threshold = total_capacity * min_share
        valid_dates = cap_per_date[cap_per_date >= threshold].index
        n_dropped = int((cap_per_date < threshold).sum())
        return df[df["date"].isin(valid_dates)].copy(), n_dropped, total_capacity

    # Legacy: tank-count threshold
    tanks_per_date = df.groupby("date")["tank_id"].nunique()
    max_tanks = int(tanks_per_date.max())
    threshold = max_tanks * min_share
    valid_dates = tanks_per_date[tanks_per_date >= threshold].index
    n_dropped = int((tanks_per_date < threshold).sum())
    return df[df["date"].isin(valid_dates)].copy(), n_dropped, max_tanks
