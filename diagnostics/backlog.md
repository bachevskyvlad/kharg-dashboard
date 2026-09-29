# OilRadar Diagnostics Backlog

## Recurring

- **Whitelist re-audit quarterly**: recompute step for all Saudi tanks on
  15+ fresh scenes; tanks may have been in maintenance/static during the
  original 18-scene window. Threshold: step < 2.5 dB. Update `enabled`,
  `wl_step`, `wl_date`, `wl_n_scenes` in registry; re-run compute for
  affected terminals.

## Open Hypotheses

- **JW jw_1 / jw_17 — high step = active loading**: jw_1 step=12.7 dB,
  jw_17 step=6.9 dB. These are the highest-variance JW tanks and coincide
  with known loading-arm positions. Hypothesis: step tracks tanker loading
  cycles, not signal noise. Candidate for separate analysis:
  cross-correlate step with AIS VLCC departures at Juwaymah.

## Stage Gates Completed

- **Stage 2 (2026-09-29)**: v2 optical geometry gate — INCONCLUSIVE.
  jw_9 center off-tank (-557% step), all main tanks degraded. v2 rejected.
- **Stage 3 (2026-09-29)**: empirical whitelist (step<2.5, old geometry,
  18 scenes). RT 45/66 ✓, YB 8/12 ✓, JW 9/19 ✗.
