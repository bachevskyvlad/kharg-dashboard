"""
coverage_filter.py — Partial-coverage filter for terminal observations.

Drops dates where fewer than min_share of the terminal's observed tanks
contributed data.  Prevents artificially low volume sums when a SAR pass
covers only one of Ras Tanura's two geographically separated clusters.
"""

import pandas as pd


def apply_coverage_filter(df: pd.DataFrame, min_share: float = 0.5):
    """
    Filter out partial-coverage dates.

    Parameters
    ----------
    df : DataFrame with columns ``date`` (datetime) and ``tank_id`` (str).
    min_share : minimum fraction of max_tanks a date must have to be kept.

    Returns
    -------
    filtered_df : DataFrame with partial-coverage dates removed.
    n_dropped   : number of dates that were dropped.
    max_tanks   : maximum unique tanks observed across any single date.
    """
    tanks_per_date = df.groupby("date")["tank_id"].nunique()
    max_tanks = int(tanks_per_date.max())
    threshold = max_tanks * min_share
    valid_dates = tanks_per_date[tanks_per_date >= threshold].index
    n_dropped = int((tanks_per_date < threshold).sum())
    return df[df["date"].isin(valid_dates)].copy(), n_dropped, max_tanks
