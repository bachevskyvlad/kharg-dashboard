"""
C12 — Polarity anchor: inventory of documented loading/export events.

This check does NOT count SAR δdB episodes automatically.
It enumerates documented events from the known record (cross-referenced
against published reports, AIS confirms, or internal session notes)
and records: event label, date range, source, expected SAR sign, and
whether the sign is unambiguous.

For terminals without documented events in the known record, outputs a
single NOT_DOCUMENTED row rather than auto-generating noise from δdB.

Polarity sign convention:
  POSITIVE db_mean change (+Δ) = radar brighter = less liquid = drawdown / export
  NEGATIVE db_mean change (−Δ) = radar darker  = more liquid = buildup / import

Documented events per terminal — extend this dict as events are confirmed:
"""

import pandas as pd
from . import RUN_DATE, SHA

# Keys are terminal_id values. Each entry is a list of dicts:
#   event_label, date_start, date_end, source, expected_sign, unambiguous (bool), note
KNOWN_EVENTS: dict[str, list[dict]] = {
    "kharg": [
        {
            "event_label": "2022-02 buildup peak",
            "date_start": "2022-01-15",
            "date_end": "2022-02-28",
            "source": "kharg_gate_run / Gate1 repère corr",
            "expected_sign": "NEGATIVE",
            "unambiguous": True,
            "note": "Gate 1 repère tanks t60_1/t40_33/t55_31 at db_mean < 1 dB",
        },
        {
            "event_label": "t55_5 / t55_10 persistent idle",
            "date_start": "2020-01-01",
            "date_end": "2026-12-31",
            "source": "repere_analysis / Gate1",
            "expected_sign": "POSITIVE",
            "unambiguous": True,
            "note": "db_mean > 14 dB across 18 months; EMPTY structural state",
        },
    ],
    # ras_tanura, yanbu, jask, al_faw, juwaymah: no confirmed events in record yet
}


def run(terminal_id: str, df: pd.DataFrame, _registry_tanks: list) -> pd.DataFrame:
    events = KNOWN_EVENTS.get(terminal_id, [])

    if not events:
        return pd.DataFrame([{
            "terminal_id": terminal_id,
            "event_label": "NOT_DOCUMENTED",
            "date_start": None,
            "date_end": None,
            "source": None,
            "expected_sign": None,
            "unambiguous": None,
            "note": "No confirmed anchor events in known record for this terminal.",
            "check_id": "C12",
            "run_date": RUN_DATE,
            "git_sha": SHA,
            "verdict": "",
        }])

    rows = []
    for ev in events:
        rows.append({
            "terminal_id": terminal_id,
            "event_label": ev["event_label"],
            "date_start": ev["date_start"],
            "date_end": ev["date_end"],
            "source": ev["source"],
            "expected_sign": ev["expected_sign"],
            "unambiguous": ev["unambiguous"],
            "note": ev.get("note", ""),
            "check_id": "C12",
            "run_date": RUN_DATE,
            "git_sha": SHA,
            "verdict": "",
        })

    return pd.DataFrame(rows)
