"""
C05 — Level range per-tank.

Metric: step = p95 − p05 of db_mean per tank.
Reference: Kharg repère contrast is 8–10 dB (EMPTY t55_5/t55_10 vs FULL t60_1/t40_33/t55_31).
Output is a raw number, no automatic verdict. Column `kharg_ref_contrast` = "8-10 dB" for reference.
"""

import numpy as np
import pandas as pd
from . import RUN_DATE, SHA

KHARG_REF_CONTRAST = "8-10"  # dB, from Gate 1 repère analysis


def run(terminal_id: str, df: pd.DataFrame, registry_tanks: list) -> pd.DataFrame:
    rows = []
    for t in registry_tanks:
        tid = t["id"]
        td = df[df["tank_id"] == tid].dropna(subset=["db_mean"])
        if len(td) == 0:
            step = None
        else:
            db = td["db_mean"].values.astype(float)
            step = round(float(np.percentile(db, 95) - np.percentile(db, 5)), 4)

        rows.append({
            "terminal_id": terminal_id, "tank_id": tid,
            "n": len(td),
            "step_p95_p05_db": step,
            "kharg_ref_contrast_db": KHARG_REF_CONTRAST,
            "check_id": "C05", "run_date": RUN_DATE, "git_sha": SHA,
            "verdict": "",
        })

    return pd.DataFrame(rows)
