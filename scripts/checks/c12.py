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
    "ras_tanura": [
        {
            "event_label": "E1: Attack/Halt 2026-03-02",
            "date_start": "2026-03-02",
            "date_end": "2026-03-14",
            "source": "diagnostics/rt_decomp/event_decomp.csv; RT_DECOMPOSITION.md §Stage2",
            "expected_sign": "NEGATIVE",
            "unambiguous": True,
            "note": "Halt of export ops → inventory accumulation; TERMINAL point Δ=+2.298 Mb (BUILD confirmed)",
        },
        {
            "event_label": "E2: Ops resumed 2026-06-26",
            "date_start": "2026-06-26",
            "date_end": "2026-07-10",
            "source": "diagnostics/rt_decomp/event_decomp.csv; RT_DECOMPOSITION.md §Stage2",
            "expected_sign": "POSITIVE",
            "unambiguous": True,
            "note": "Resumption of loading → drawdown; TERMINAL point Δ=−1.724 Mb (DRAW confirmed)",
        },
        {
            "event_label": "E3: Ramp ~90% 2026-07-15",
            "date_start": "2026-07-15",
            "date_end": "2026-07-31",
            "source": "diagnostics/rt_decomp/event_decomp.csv; RT_DECOMPOSITION.md §Stage2",
            "expected_sign": "POSITIVE",
            "unambiguous": False,
            "note": "Partial ramp; mean estimator=−0.686 Mb vs point=+2.140 Mb — estimators conflict; sign uncertain",
        },
        {
            "event_label": "war_2026 drawdown pressure",
            "date_start": "2026-03-01",
            "date_end": "2026-09-30",
            "source": "diagnostics/rt_decomp/RT_DECOMPOSITION.md; conflict context",
            "expected_sign": "POSITIVE",
            "unambiguous": False,
            "note": "Regional conflict 2026; direction conditional on ops phase (E1=inverse, E2/E3=direct)",
        },
    ],
    "al_faw": [
        {
            "event_label": "basra_2022q2 DRAW",
            "date_start": "2022-05-01",
            "date_end": "2022-08-31",
            "source": "diagnostics/al_faw/iraq_alfaw_events_v3.csv; MOMR OPEC",
            "expected_sign": "POSITIVE",
            "unambiguous": True,
            "note": "Iraq production push post-Ukraine; raw24 SIGNAL (Δ=−0.45 Mb → dB rises = DRAW confirmed)",
        },
        {
            "event_label": "opec_cuts_2025h1 BUILD",
            "date_start": "2025-01-01",
            "date_end": "2025-06-30",
            "source": "diagnostics/al_faw/iraq_alfaw_events_v3.csv; OPEC press room",
            "expected_sign": "NEGATIVE",
            "unambiguous": True,
            "note": "Iraq voluntary OPEC+ makeup cuts H1 2025; raw24 SIGNAL (Δ=+0.92 Mb) — all 3 series SIGNAL",
        },
        {
            "event_label": "covid_2020 BUILD",
            "date_start": "2020-03-01",
            "date_end": "2020-06-30",
            "source": "diagnostics/al_faw/iraq_alfaw_events_v3.csv; WHO declaration 2020-03-11",
            "expected_sign": "NEGATIVE",
            "unambiguous": True,
            "note": "COVID demand collapse → exports fall → storage builds; raw24 SIGNAL (Δ=+0.92 Mb)",
        },
        {
            "event_label": "war_2026 DRAW",
            "date_start": "2026-03-01",
            "date_end": "2026-09-30",
            "source": "diagnostics/al_faw/iraq_alfaw_events_v3.csv",
            "expected_sign": "POSITIVE",
            "unambiguous": True,
            "note": "Regional conflict 2026; raw24 SIGNAL (Δ=−0.32 Mb), all 3 series SIGNAL",
        },
        {
            "event_label": "basra_2021q3 DRAW — WRONG",
            "date_start": "2021-07-01",
            "date_end": "2021-09-30",
            "source": "diagnostics/al_faw/iraq_alfaw_events_v3.csv; MOMR OPEC",
            "expected_sign": "POSITIVE",
            "unambiguous": False,
            "note": "Iraq post-OPEC+ ramp-up; all 3 series WRONG (Δ=+0.67 Mb = BUILD when DRAW expected). Score raw24: 4/5 SIGNAL on confirmed events; overall 5/10 including WRONG anchors.",
        },
    ],
    "jask": [
        {
            "event_label": "anchor_1 — inconclusive by construction",
            "date_start": None,
            "date_end": None,
            "source": "diagnostics/jask/JASK_POLARITY.md",
            "expected_sign": None,
            "unambiguous": False,
            "note": "All 3 Jask anchors inconclusive by construction; polarity assumed_direct per regional default (see JASK_POLARITY.md)",
        },
    ],
    "yanbu": [
        {
            "event_label": "NOT_DOCUMENTED",
            "date_start": None,
            "date_end": None,
            "source": None,
            "expected_sign": None,
            "unambiguous": None,
            "note": "No documented anchor events found. Sources searched: diagnostics/STAGE2_GATE.md, SERIES_HEALTH_STANDARD.md, DIAGNOSTIC_JUAYMAH_YANBU.md, INSTRUMENT_AUDIT_7.md. OILRADAR_KNOWLEDGE_BASE.md and oilradar_konspekt.md not present in repo.",
        },
    ],
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
