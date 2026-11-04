# EQUIVALENCE_7_6.md
Generated: 2026-09-27 UTC
Run: GHA round-7.6 on `claude/oilradar-dashboard-iran-zchdva`

---

## Prod import

```
from sar_fetch import build_crescent_masks  # OK
from sar_fetch import lonlat_to_pix          # OK
```

---

## Task 1 — Pixel-level mask equivalence

Scene: 2026-09-24 (Kharg DESC-35)

| tank | prod_px | diag_px | diff_px | equal |
|------|---------|---------|---------|-------|
| t60_1 | 99 | 99 | 0 | YES |
| t40_33 | 63 | 63 | 0 | YES |

> **Task 1: PASS — prod mask ≡ diag mask (np.array_equal)**

---

## Task 2 — Sweep prod mask: synthetic unit test

| plant_az | found_az | min_db | Δ dB | result |
|----------|----------|--------|------|--------|
| 30 | 30 | -23.0 | -17.0 | PASS |
| 98 | 105 | -16.2 | -10.2 | PASS |
| 282 | 285 | -18.8 | -12.9 | PASS |
| 350 | 345 | -17.3 | -11.4 | PASS |

> **Task 2: PASS** — sweep_prod_mask finds planted shadow within ±30°

---

## Task 3 — Kharg Gate #1 (t60_1 + t40_33)

| tank | date | min_az | Δ dB | pass |
|------|------|--------|------|------|
| t60_1 | 2026-09-24 | 177 | -1.65 | NO |
| t40_33 | 2026-09-24 | 192 | -1.83 | NO |
| t60_1 | 2026-09-18 | 132 | -1.45 | NO |
| t40_33 | 2026-09-18 | 177 | -2.33 | NO |
| t60_1 | 2026-09-12 | 117 | -2.67 | NO |
| t40_33 | 2026-09-12 | 177 | -2.06 | NO |
| t60_1 | 2026-09-06 | 177 | -0.54 | NO |
| t40_33 | 2026-09-06 | 192 | -2.34 | NO |

> t60_1: 0/4  t40_33: 0/4
> **Gate #1: FAIL**

**FINDING: prod crescent on Kharg etalon does not reproduce sweep at 282°.**
Per-sector dB table for t60_1 (first scene) — inspect where shadow falls:

### t60_1 sector table

| sector_az | dB |
|-----------|-----|
| 282 | 1.52 |
| 297 | 1.55 |
| 312 | 1.51 |
| 327 | 1.34 |
| 342 | 1.39 |
| 357 | 1.70 |
| 12 | 1.12 |
| 27 | -0.28 |
| 42 | -0.61 |
| 57 | -0.58 |
| 72 | -0.53 |
| 87 | -0.60 |
| 102 | -0.70 |
| 117 | -0.75 |
| 132 | -0.67 |
| 147 | -0.44 |
| 162 | -0.52 |
| 177 | -1.14 |
| 192 | -0.11 |
| 207 | 1.26 |
| 222 | 1.49 |
| 237 | 1.46 |
| 252 | 1.40 |
| 267 | 1.44 |

> Round 7.6 stops here. Tasks 4 and 5 not executed (gate chain broken).

---

## Task 4 — RT Gate

> Skipped: Gate #1 (Task 3) FAIL.

---

## Task 5 — Verdicts

Вердикты rounds 6–7.5 по Juaymah/Yanbu аннулированы. Действующие факты: Kharg r=0.7 (внешняя валидация), RT — event-валидация 2026. Juaymah/Yanbu — статус неизвестен до прогона эквивалентным инструментом.

---
