# DIAGNOSTIC_JUAYMAH_YANBU_6.md
Generated: 2026-09-26 09:27 UTC
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
| t60_1 | 3/4 | PASS |
| t40_33 | 4/4 | PASS |

> **Gate #1: PASS**


---

## Task 2 — RT south gate #2

| date | pass | min_az | min_db |
|------|------|--------|--------|
| 2026-08-31 | NO | 195.0 | 5.36 |
| 2026-08-25 | YES | 270.0 | 3.58 |
| 2026-08-19 | NO | 315.0 | 2.52 |
| 2026-08-13 | YES | 285.0 | -0.97 |

> **Gate #2: FAIL (2/4 scenes confirmed)**


---

## Tasks 3–5 — SKIPPED (Gate #2 failed)

> All downstream tasks cancelled per gate logic.
