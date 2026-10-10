"""
C04 — Per-tank liveness: raw measurement table, no automatic threshold.

Per tank: n, p05/p25/p50/p75/p95, range (p95−p05), step_db (canonical median|Δdb|
from metrics.tank_step_db), frac_at_floor (db ≤ DB_FLOOR), flat_frac (fraction of
consecutive |Δdb| < FLAT_DELTA_THRESH), dip (Hartigan dip statistic via diptest;
falls back to scipy bimodality coefficient), unimodality_proxy (std / (range + ε)).

No STUCK threshold applied here. Verdicts are human-assigned.
"""

import sys
import os
import numpy as np
import pandas as pd
from . import DB_FLOOR, FLAT_DELTA_THRESH, RUN_DATE, SHA

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from metrics import tank_step_db

try:
    import diptest as _diptest
    def _dip_stat(x):
        return float(_diptest.dip(x)[0])
except ImportError:
    from scipy.stats import skew, kurtosis as _kurtosis
    def _dip_stat(x):
        if len(x) < 4:
            return float("nan")
        s = float(skew(x))
        k = float(_kurtosis(x, fisher=True)) + 3  # convert to Pearson kurtosis
        bc = (s ** 2 + 1) / (k + 3 * (len(x) - 1) ** 2 / ((len(x) - 2) * (len(x) - 3)))
        return round(bc, 6)


def run(terminal_id: str, df: pd.DataFrame, registry_tanks: list) -> pd.DataFrame:
    rows = []
    tank_ids = [t["id"] for t in registry_tanks]

    for tid in tank_ids:
        td = df[df["tank_id"] == tid].dropna(subset=["db_mean"]).sort_values("date")
        if len(td) == 0:
            rows.append({
                "terminal_id": terminal_id, "tank_id": tid,
                "n": 0, "p05": None, "p25": None, "p50": None, "p75": None, "p95": None,
                "range": None, "step_db": None, "frac_at_floor": None, "flat_frac": None,
                "dip": None, "unimodality_proxy": None,
                "check_id": "C04", "run_date": RUN_DATE, "git_sha": SHA,
                "verdict": "NO_DATA",
            })
            continue

        db = td["db_mean"].values.astype(float)
        p05 = float(np.percentile(db, 5))
        p25 = float(np.percentile(db, 25))
        p50 = float(np.percentile(db, 50))
        p75 = float(np.percentile(db, 75))
        p95 = float(np.percentile(db, 95))
        rng = p95 - p05
        step = tank_step_db(db)
        frac_at_floor = float(np.mean(db <= DB_FLOOR))

        deltas = np.abs(np.diff(db))
        flat_frac = float(np.mean(deltas < FLAT_DELTA_THRESH)) if len(deltas) > 0 else float("nan")
        dip = _dip_stat(db)
        unimodality_proxy = float(np.std(db) / (rng + 1e-9)) if rng > 0 else float("nan")

        rows.append({
            "terminal_id": terminal_id, "tank_id": tid,
            "n": len(td),
            "p05": round(p05, 4), "p25": round(p25, 4), "p50": round(p50, 4),
            "p75": round(p75, 4), "p95": round(p95, 4),
            "range": round(rng, 4),
            "step_db": round(step, 4) if step == step else float("nan"),
            "frac_at_floor": round(frac_at_floor, 4),
            "flat_frac": round(flat_frac, 4),
            "dip": round(dip, 6) if dip == dip else float("nan"),
            "unimodality_proxy": round(unimodality_proxy, 4),
            "check_id": "C04", "run_date": RUN_DATE, "git_sha": SHA,
            "verdict": "",
        })

    return pd.DataFrame(rows)
