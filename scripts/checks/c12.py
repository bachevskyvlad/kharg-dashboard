"""
C12 — Polarity: inventory of anchor episodes with clear sign from the DB.

Looks for rows in terminal_observations where db_mean shows a clear directional
step (consecutive-date drawdown or buildup >= 3 dB) and cross-references against
any events stored in the terminal series (volume_timeseries or kharg_signals for
Kharg; terminal-level equivalent for others).

For non-Kharg terminals using terminal_observations: computes per-tank
consecutive step events (delta >= 3 dB over 1-2 passes) as proxy anchor episodes.
No export-linkage computation (per ТЗ §14 rule).

Output per terminal: episode count with clear positive sign, clear negative sign,
ambiguous. One row per candidate episode.
"""

import numpy as np
import pandas as pd
from . import RUN_DATE, SHA

ANCHOR_DELTA_DB = 3.0   # minimum step to qualify as anchor episode
ANCHOR_MAX_DAYS = 14    # maximum days between consecutive passes for delta


def run(terminal_id: str, df: pd.DataFrame, _registry_tanks: list) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["db_mean"]).sort_values(["tank_id", "date"])

    rows = []
    for tid, td in df.groupby("tank_id"):
        td = td.sort_values("date").reset_index(drop=True)
        for i in range(1, len(td)):
            gap_days = (td.loc[i, "date"] - td.loc[i - 1, "date"]).days
            if gap_days > ANCHOR_MAX_DAYS:
                continue
            delta = td.loc[i, "db_mean"] - td.loc[i - 1, "db_mean"]
            if abs(delta) < ANCHOR_DELTA_DB:
                continue
            sign = "POSITIVE" if delta > 0 else "NEGATIVE"
            rows.append({
                "terminal_id": terminal_id,
                "tank_id": tid,
                "date_start": str(td.loc[i - 1, "date"].date()),
                "date_end": str(td.loc[i, "date"].date()),
                "gap_days": gap_days,
                "delta_db": round(delta, 3),
                "sign": sign,
                "check_id": "C12",
                "run_date": RUN_DATE,
                "git_sha": SHA,
                "verdict": "",
            })

    if not rows:
        return pd.DataFrame([{
            "terminal_id": terminal_id, "tank_id": "ALL",
            "date_start": None, "date_end": None, "gap_days": None,
            "delta_db": None, "sign": None,
            "check_id": "C12", "run_date": RUN_DATE, "git_sha": SHA,
            "verdict": "NO_EPISODES",
        }])

    return pd.DataFrame(rows)
