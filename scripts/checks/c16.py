"""
C16 — Scene validity.

Metrics per tank:
  n_total: total rows in terminal_observations for this tank
  n_valid: rows with non-null db_mean
  valid_fraction: n_valid / n_total
  n_null: rows with null db_mean

For Kharg (raw_observations): same metrics from raw_observations.
Cloud/weather filtering is not tracked in the current DB schema — this check
documents what IS tracked (null db_mean = scene rejected or missing).
"""

import pandas as pd
from . import RUN_DATE, SHA


def run(terminal_id: str, df: pd.DataFrame, registry_tanks: list) -> pd.DataFrame:
    rows = []
    for t in registry_tanks:
        tid = t["id"]
        td = df[df["tank_id"] == tid]
        n_total = len(td)
        n_valid = int(td["db_mean"].notna().sum())
        n_null = n_total - n_valid
        valid_fraction = round(n_valid / n_total, 4) if n_total > 0 else None

        rows.append({
            "terminal_id": terminal_id, "tank_id": tid,
            "n_total": n_total,
            "n_valid": n_valid,
            "n_null": n_null,
            "valid_fraction": valid_fraction,
            "note": "null db_mean = scene rejected or missing; cloud tracking not stored separately",
            "check_id": "C16", "run_date": RUN_DATE, "git_sha": SHA,
            "verdict": "",
        })

    return pd.DataFrame(rows)
