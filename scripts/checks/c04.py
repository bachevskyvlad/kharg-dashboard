"""
C04 — Per-tank liveness (LOW-STUCK, Al-Faw method).

Per tank: n, p05/p50/p95, step (p95-p05), frac_at_floor, flat_frac, unimodality proxy.
Flag candidate = frac_at_floor >= STUCK_FLOOR_THRESH (0.50) — same criterion that
identified the 7 stuck tanks at Al-Faw (stuck_passport.py, ONSET_THRESH=0.50).

unimodality_proxy: std / (step + 1e-9) — small value = narrow, unimodal distribution;
large value relative to step = multi-modal or bimodal. Not a formal dip-test; raw number.

flat_frac: fraction of consecutive |Δdb| < 0.3 dB (FLAT_DELTA_THRESH).

verdict column: "STUCK_CANDIDATE" if frac_at_floor >= 0.50, else empty string.
"""

import numpy as np
import pandas as pd
from . import DB_FLOOR, FLAT_DELTA_THRESH, RUN_DATE, SHA, STUCK_FLOOR_THRESH


def run(terminal_id: str, df: pd.DataFrame, registry_tanks: list) -> pd.DataFrame:
    rows = []
    tank_ids = [t["id"] for t in registry_tanks]

    for tid in tank_ids:
        td = df[df["tank_id"] == tid].dropna(subset=["db_mean"]).sort_values("date")
        if len(td) == 0:
            rows.append({
                "terminal_id": terminal_id, "tank_id": tid,
                "n": 0, "p05": None, "p50": None, "p95": None,
                "step": None, "frac_at_floor": None, "flat_frac": None,
                "unimodality_proxy": None,
                "check_id": "C04", "run_date": RUN_DATE, "git_sha": SHA,
                "verdict": "NO_DATA",
            })
            continue

        db = td["db_mean"].values.astype(float)
        p05 = float(np.percentile(db, 5))
        p50 = float(np.percentile(db, 50))
        p95 = float(np.percentile(db, 95))
        step = p95 - p05
        frac_at_floor = float(np.mean(db <= DB_FLOOR))

        # flat_frac: fraction of |Δdb| < FLAT_DELTA_THRESH
        deltas = np.abs(np.diff(db))
        flat_frac = float(np.mean(deltas < FLAT_DELTA_THRESH)) if len(deltas) > 0 else float("nan")

        unimodality_proxy = float(np.std(db) / (step + 1e-9)) if step > 0 else float("nan")

        verdict = "STUCK_CANDIDATE" if frac_at_floor >= STUCK_FLOOR_THRESH else ""

        rows.append({
            "terminal_id": terminal_id, "tank_id": tid,
            "n": len(td),
            "p05": round(p05, 4), "p50": round(p50, 4), "p95": round(p95, 4),
            "step": round(step, 4),
            "frac_at_floor": round(frac_at_floor, 4),
            "flat_frac": round(flat_frac, 4),
            "unimodality_proxy": round(unimodality_proxy, 4),
            "check_id": "C04", "run_date": RUN_DATE, "git_sha": SHA,
            "verdict": verdict,
        })

    return pd.DataFrame(rows)
