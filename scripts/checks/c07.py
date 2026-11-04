"""
C07 — Mask contamination.

Requires per-pixel SAR crop data (not available in terminal_observations, which stores
only aggregate db_mean per tank per pass). For terminals where only db_mean is available
in the database (yanbu, ras_tanura, juwaymah, jask — all non-Kharg), this check is
NOT_RUN; pixel-level fetches via SentinelHub are out of scope for the initial run.

When pixel data IS available (future: jk_13 first per ТЗ), metrics are:
  - mask_p99_frac: fraction of mask pixels > p99 of the full scene (median across scenes)
  - neighbor_dist_m: distance to nearest other tank (from registry)
  - flag_candidate: True if mask_p99_frac > 0.10 (sidelobe contamination indicator)

This file outputs one NOT_RUN row per terminal for now.
"""

import pandas as pd
from . import RUN_DATE, SHA


def run(terminal_id: str, _df: pd.DataFrame, _registry_tanks: list) -> pd.DataFrame:
    return pd.DataFrame([{
        "terminal_id": terminal_id,
        "tank_id": "ALL",
        "note": "requires per-pixel SAR crops; not available from terminal_observations aggregate",
        "check_id": "C07",
        "run_date": RUN_DATE,
        "git_sha": SHA,
        "verdict": "NOT_RUN",
    }])
