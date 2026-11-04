"""
C15 — Optical label independence.

Documents the source of EMPTY/FULL labels used in the optical Gate 1 (Kharg-30).
No DB fetch required. Output is a text description per label class.

Gate 1 labels:
  EMPTY: t55_5, t55_10 — selected because their mean dB (>14 dB over 18 months)
    places them as persistently idle/offline tanks, confirmed in raw_observations.
    Source: dB time-series analysis in optical_kharg_gate.py (empirical criterion,
    not a ground-truth label from an independent measurement).
  FULL: t60_1, t40_33, t55_31 — selected because their mean dB (<1 dB chronically)
    and shape of distribution indicate persistent near-full state.
    Source: same dB time-series analysis; t55_31 additionally has architectural notes.

Link to SAR: EMPTY/FULL labels are DERIVED from SAR dB directly. They are NOT
independent of the SAR signal. This means Gate 1 tests internal SAR consistency
(shadow fraction anti-correlates with known-high-dB vs known-low-dB tanks), NOT
an independent ground truth. Gate 2 (cross-sensor correlation) provides a
degree of independence by using S2 optical shadow as a separate measurement.

For non-Kharg terminals: no optical gate labels exist (no Gate 1 was run for
yanbu, ras_tanura, etc.). Status = N_A for these terminals.
"""

import pandas as pd
from . import RUN_DATE, SHA

KHARG_LABELS = {
    "EMPTY": {
        "tanks": ["t55_5", "t55_10"],
        "source": "SAR dB time-series (mean_db > 14 over 18mo; raw_observations)",
        "independent_of_sar": False,
        "note": "Labels derived from same SAR signal; not independent ground truth",
    },
    "FULL": {
        "tanks": ["t60_1", "t40_33", "t55_31"],
        "source": "SAR dB time-series (mean_db < 1 chronically; raw_observations)",
        "independent_of_sar": False,
        "note": "Labels derived from same SAR signal; Gate 2 S2 shadow adds independence",
    },
}


def run(terminal_id: str, _df: pd.DataFrame, _registry_tanks: list) -> pd.DataFrame:
    if terminal_id == "kharg":
        rows = []
        for label, info in KHARG_LABELS.items():
            rows.append({
                "terminal_id": terminal_id,
                "label_class": label,
                "tanks": str(info["tanks"]),
                "source": info["source"],
                "independent_of_sar": info["independent_of_sar"],
                "note": info["note"],
                "check_id": "C15",
                "run_date": RUN_DATE,
                "git_sha": SHA,
                "verdict": "LABELS_FROM_SAR",
            })
        return pd.DataFrame(rows)

    return pd.DataFrame([{
        "terminal_id": terminal_id,
        "label_class": None,
        "tanks": None,
        "source": "no optical gate for this terminal",
        "independent_of_sar": None,
        "note": "",
        "check_id": "C15",
        "run_date": RUN_DATE,
        "git_sha": SHA,
        "verdict": "N_A",
    }])
