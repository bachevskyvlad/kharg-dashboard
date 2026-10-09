# JASK STAGE-2 SCREEN

**Date**: 2026-10-09
**Analyst**: OilRadar pipeline

---

## Terminal: Jask (Iran)

| Field | Value |
|---|---|
| Orbit | rel=93 DESC, los=282° |
| Catalog scenes | 109 |
| Tanks in registry | 20 |
| Stage-1 rows | 872 |
| db_mean p5/p50/p95 | −2.65 / 2.57 / 8.83 dB |

---

## Whitelist Screen (step_db < 2.5 threshold)

**Result: WL 20/20**

All 20 tanks (jk_0 through jk_19) pass the whitelist threshold (step_db < 2.5).

Interpretation: low step_db at Jask reflects the terminal's **operational buffer mode** — tanks
cycle slowly between infrequent tanker loadings. Low step is NOT a dead-signal indicator here.
This is a regional characteristic of Iranian bypass terminals vs. high-frequency Saudi loading arms.

---

## Stage-2 Verdict

**TERMINAL_SERIES** (human override, 2026-10-09)

Reason: step_db < 2.5 globally. Algorithmic path would flag TANK_LEVEL (60% cap gate) due to
low within-series variance, but human override applies: low step is operationally valid at Jask.
Override logged as 4th incident of inverted-reading (see note below).

Verdict takes effect: WL 20/20 passes to events pipeline.

---

## Inverted-Reading Note (4th incident)

**Incident type**: Step-db polarity inversion — algorithmic TANK_LEVEL/DEAD flag on operationally
valid low-step series.

**Root cause**: Stage-2 gate was calibrated on Saudi high-step loading arms (JW, RT). Low-step
Iranian bypass terminals (slow fill, rare drawdown) produce step < 2.5 which correctly signals
whitelist pass at Saudi terminals, but at Jask the same value reflects slow operational cycles,
not signal absence.

**Resolution**: Human override. No gate change — gate stays calibrated for Saudi reference.
Document each Iranian low-step terminal separately.

**Previous incidents**: #1 (unknown), #2 (unknown), #3 (unknown). Log maintained by operator.

---

## Event Numbers

All Jask event counts **must use placebo p90** as significance threshold.

Rationale: Jask has no confirmed external anchor for event validation. Placebo p90 prevents
false positives from the slow operational baseline appearing as export pulses.

---

## Passport Reference

→ See `TERMINAL_PASSPORT_jask.md` (pending — after panels + events human verdicts)
