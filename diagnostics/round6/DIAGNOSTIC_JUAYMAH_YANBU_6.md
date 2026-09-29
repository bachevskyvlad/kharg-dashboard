# DIAGNOSTIC_JUAYMAH_YANBU_6.md
Generated: 2026-09-26 15:06 UTC
Run: GHA round-6 on `claude/oilradar-dashboard-iran-zchdva`

---

## Task 0 — Coord-transform unit test

| tank_id | px | py | err_px | pass |
|---------|----|----|--------|------|
| t60_1 | 445.85 | 408.67 | 0.0 | YES |
| rt_0 | 428.0 | 364.05 | 0.0 | YES |
| jw_9 | 648.05 | 358.54 | 0.0 | YES |

> **Task 0: PASS — all errors < 1px**


---

## Task 1 — Kharg etalon (Gate #1)

| tank_id | scenes_pass | gate |
|---------|-------------|------|
| t60_1 | 4/4 | PASS |
| t40_33 | 4/4 | PASS |

> **Gate #1: PASS**


---

## Task 2 — RT south gate #2

| date | pass | min_az | min_db |
|------|------|--------|--------|
| 2026-07-26 | NO | 315.0 | -1.12 |
| 2026-08-13 | YES | 285.0 | -0.97 |
| 2026-08-01 | YES | 285.0 | 0.68 |
| 2026-07-14 | YES | 285.0 | 1.4 |

> **Gate #2: PASS (3/4 scenes confirmed)**


---

## Task 3 — Juwaymah final verdict

SAR scenes: ['2024-01-13', '2024-12-26', '2025-09-28', '2026-03-21']

| tank_id | shift_m | corr_score | n_pass | tank_pass |
|---------|---------|------------|--------|-----------|
| jw_0 | 21.8 | 0.282 | 0/4 | NO |
| jw_1 | 22.2 | 0.36 | 0/4 | NO |
| jw_2 | 33.8 | 0.214 | 0/4 | NO |
| jw_3 | 52.3 | 0.195 | 1/4 | NO |
| jw_4 | 60.1 | 0.197 | 1/4 | NO |
| jw_5 | 47.4 | 0.198 | 0/4 | NO |
| jw_6 | 32.3 | 0.296 | 4/4 | YES |
| jw_7 | 34.1 | 0.2 | 2/4 | NO |
| jw_8 | 45.3 | 0.192 | 2/4 | NO |
| jw_9 | 5.3 | 0.287 | 0/4 | NO |
| jw_10 | 17.9 | 0.217 | 0/4 | NO |
| jw_11 | 60.9 | 0.206 | 0/4 | NO |
| jw_12 | 36.7 | 0.238 | 0/4 | NO |
| jw_13 | 50.5 | 0.215 | 0/4 | NO |
| jw_14 | 55.3 | 0.245 | 2/4 | NO |
| jw_15 | 44.7 | 0.25 | 0/4 | NO |
| jw_16 | 47.8 | 0.208 | 0/4 | NO |
| jw_17 | 21.7 | 0.214 | 3/4 | YES |
| jw_18 | 32.1 | 0.239 | 0/4 | NO |

> **Juwaymah: PARTIAL — 2/19 tanks confirmed**


---

## Task 4 — Yanbu final verdict

SAR scenes: ['2024-01-11', '2024-12-24', '2025-11-07']

| tank_id | shift_m | corr_score | n_pass | tank_pass |
|---------|---------|------------|--------|-----------|
| yb_0 | 34.1 | 0.223 | 0/3 | NO |
| yb_1 | 63.4 | 0.228 | 0/3 | NO |
| yb_2 | 63.7 | 0.203 | 0/3 | NO |
| yb_4 | 18.6 | 0.249 | 0/3 | NO |
| yb_5 | 27.0 | 0.216 | 0/3 | NO |
| yb_6 | 22.6 | 0.217 | 0/3 | NO |
| yb_7 | 9.5 | 0.295 | 0/3 | NO |
| yb_8 | 13.2 | 0.266 | 0/3 | NO |
| yb_9 | 22.8 | 0.252 | 0/3 | NO |
| yb_10 | 12.3 | 0.216 | 2/3 | YES |
| yb_11 | 19.0 | 0.261 | 0/3 | NO |

> **Yanbu: DROP — 1/11 tanks confirmed**


---

## Task 5 — Retrospective: root cause rounds 2–5

### Root cause of broken sweep (rounds 2–5)

- **Scene selection was arbitrary**: rounds 2–5 chose SAR dates from a fixed
  calendar list without checking whether the reference tank (rt_0) was actually
  full on those dates. Crescent signal requires a floating roof in raised position
  (tank >50% full). An empty tank produces no radar shadow → min_az is noise.
- **Round 5 specific**: rt_0 returned min_az=352° vs 282°±30° (ΔAZ=70°) on the
  etalon scene used. SAR etalon failed but verdicts were reported anyway — rule
  violation. The gate logic introduced in round 6 prevents this.
- **Hough circle detection** in round 5 gave 213.8m median shift on Juwaymah.
  At 5m/px that is 42px — impossible for a correctly identified tank at known GPS
  coordinates. The Hough radii parameters (minRadius/maxRadius) were not tuned
  to the actual tank radii, causing matches to unrelated circular features.
  Template matching (ring kernel, ±10px) is used from round 6 onward.

### Which past verdicts survive?

- **RT Kharg inventory cycle (rounds 3–4)**: uses DESC-35 Kharg tanks (t60_1,
  t55_*, etc.) on their own bbox with the prod-identical crescent geometry.
  Round 3 confirmed 6/6 Kharg tanks with 0px TM shift, etalon min_az=281.3°.
  These verdicts are **valid** — etalon was on the same orbit and bbox.
- **Juwaymah rounds 2–4**: etalon was RT south (DESC-35) but scene was not
  data-selected. Round 4 gave 3/4 confirmed from 3 tanks only (jw_0, jw_9,
  jw_18). Inconclusive sample size. Round 6 re-runs all 19 tanks. **Annulled
  pending round 6 result.**
- **Yanbu rounds 3–5**: consistently NOT CONFIRMED across all rounds.
  Round 5 TM-corrected: 0/11 pass. Round 6 gives final one-chance verdict.


---

## Verdict summary

| Question | Result |
|----------|--------|
| Coord transform (Task 0) | PASS |
| Kharg etalon Gate #1 | PASS |
| RT south Gate #2 | PASS |
| Juwaymah final | PARTIAL (2/19 confirmed) |
| Yanbu final | DROP (1/11 confirmed) |

*Honesty rule applied: no hypothesis substituted for measurement.*
