"""
C06 — Zero level per-tank.

Metric: median(tank db_mean) − median(terminal db_mean) across all tanks.
Terminal median = median of all db_mean values for this terminal (all tanks, all dates).
A large spread (e.g. ~17 dB at Jask with 8–10 dB contrast) warrants further inspection.
No automatic verdict — output raw number.
"""

import numpy as np
import pandas as pd
from . import RUN_DATE, SHA


def run(terminal_id: str, df: pd.DataFrame, registry_tanks: list) -> pd.DataFrame:
    terminal_median = float(df["db_mean"].dropna().median()) if len(df) > 0 else float("nan")

    rows = []
    for t in registry_tanks:
        tid = t["id"]
        td = df[df["tank_id"] == tid].dropna(subset=["db_mean"])
        tank_median = float(td["db_mean"].median()) if len(td) > 0 else float("nan")
        offset = round(tank_median - terminal_median, 4) if not (
            np.isnan(tank_median) or np.isnan(terminal_median)
        ) else None

        rows.append({
            "terminal_id": terminal_id, "tank_id": tid,
            "n": len(td),
            "tank_median_db": round(tank_median, 4) if not np.isnan(tank_median) else None,
            "terminal_median_db": round(terminal_median, 4) if not np.isnan(terminal_median) else None,
            "offset_db": offset,
            "check_id": "C06", "run_date": RUN_DATE, "git_sha": SHA,
            "verdict": "",
        })

    return pd.DataFrame(rows)
