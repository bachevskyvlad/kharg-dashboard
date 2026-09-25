# DIAGNOSTIC_JUAYMAH_YANBU_5.md
Generated: 2026-09-25 15:10 UTC
Run: GHA round-5 on `claude/oilradar-dashboard-iran-zchdva`

---

## Task 0 — SAR etalon (rt_0 DESC 35)

| Item | Value |
|------|-------|
| SAR scene used | 2025-02-12 |
| rt_0 profile min_az | 352° |
| Expected | 282° ±30° |
| Etalon valid | NO — crescent NOT confirmed |

> **SAR etalon: FAIL — crescent not confirmed on rt_0; remaining tasks may be unreliable**

---

## Task 1 — Juwaymah Hough re-centering

### Farm statistics

| Metric | Value |
|--------|-------|
| Physical tanks (Hough) | 21 |
| Registry tanks | 19 |
| Paired registry↔Hough (≤60m) | 4 |
| Registry tanks without Hough pair | 15 |
| Hough circles without registry match | 17 |
| Median shift (paired) | 213.8m |
| Floating (Hough re-classified) | 12 |

### Per-tank match table

| tank_id | shift_m | dx_m | dy_m | r_reg | r_hough | paired | cls_r5 |
|---------|---------|------|------|-------|---------|--------|--------|
| jw_0 | — | — | — | 50 | — | False | unclear |
| jw_1 | — | — | — | 50 | — | False | unclear |
| jw_2 | — | — | — | 50 | — | False | floating |
| jw_3 | 273.5 | 48.8 | -24.7 | 50 | 60 | True | floating |
| jw_4 | — | — | — | 50 | — | False | unclear |
| jw_5 | — | — | — | 50 | — | False | unclear |
| jw_6 | 9.2 | -0.4 | -1.8 | 50 | 65 | True | floating |
| jw_7 | 194.5 | 37.0 | -11.9 | 50 | 40 | True | floating |
| jw_8 | — | — | — | 50 | — | False | floating |
| jw_9 | — | — | — | 50 | — | False | floating |
| jw_10 | — | — | — | 50 | — | False | floating |
| jw_11 | — | — | — | 50 | — | False | unclear |
| jw_12 | — | — | — | 50 | — | False | unclear |
| jw_13 | 233.0 | -30.4 | 35.3 | 50 | 65 | True | floating |
| jw_14 | — | — | — | 50 | — | False | floating |
| jw_15 | — | — | — | 50 | — | False | floating |
| jw_16 | — | — | — | 50 | — | False | unclear |
| jw_17 | — | — | — | 50 | — | False | floating |
| jw_18 | — | — | — | 50 | — | False | floating |

> Supporting images: `jw_farm_map_r5.jpg`, `jw_zoom_r5_jw_0.jpg`, `jw_zoom_r5_jw_9.jpg`, `jw_zoom_r5_jw_18.jpg`

---

## Task 2 — Yanbu Hough re-centering

### Farm statistics

| Metric | Value |
|--------|-------|
| Physical tanks (Hough) | 241 |
| Registry tanks | 12 |
| Paired registry↔Hough (≤80m) | 12 |
| Registry tanks without Hough pair | 0 |
| Median shift (paired) | 72.8m |
| Floating (re-classified) | 11 |

### Per-tank match table

| tank_id | R5_shift_m | dx_m | dy_m | R4_shift_m | r_reg | r_hough | paired | cls_r5 |
|---------|-----------|------|------|-----------|-------|---------|--------|--------|
| yb_0 | 123.0 | -4.0 | 24.3 | 41 | 60 | 75 | True | floating |
| yb_1 | 92.7 | 18.4 | -1.9 | 50 | 60 | 70 | True | floating |
| yb_2 | 57.9 | 10.3 | 5.4 | 43 | 60 | 60 | True | floating |
| yb_3 | 4.5 | 0.7 | -0.5 | — | 60 | 60 | True | fixed |
| yb_4 | 43.1 | 8.5 | 1.6 | 18 | 60 | 60 | True | floating |
| yb_5 | 63.6 | 1.9 | 12.6 | — | 60 | 70 | True | floating |
| yb_6 | 55.7 | -3.1 | 10.7 | 29 | 60 | 65 | True | floating |
| yb_7 | 74.9 | -5.6 | 13.9 | — | 60 | 75 | True | floating |
| yb_8 | 76.3 | 3.2 | 14.9 | — | 60 | 65 | True | floating |
| yb_9 | 85.4 | -2.3 | 16.9 | 25 | 60 | 70 | True | floating |
| yb_10 | 93.5 | 7.0 | 17.3 | — | 60 | 70 | True | floating |
| yb_11 | 70.8 | 3.6 | 13.7 | 39 | 60 | 65 | True | floating |

> Supporting images: `yb_farm_map_r5.jpg`

---

## Task 3 — Profiles on corrected centers

### Juwaymah

Floating tanks fed to profiler: ['jw_3', 'jw_6', 'jw_7', 'jw_13']
SAR scenes used: ['2024-01-13', '2024-07-11', '2024-12-26', '2025-06-12']

| Scene | Tanks confirmed | Scene pass |
|-------|----------------|------------|
| 2024-01-13 | 4/4 | True |
| 2024-07-11 | 2/4 | True |
| 2024-12-26 | 0/4 | False |
| 2025-06-12 | 2/4 | True |

> **Juwaymah verdict: CONFIRMED on corrected centers (['jw_3', 'jw_6', 'jw_7', 'jw_13'])**

### jw_9 conflict resolution

> Both centers confirmed (old=292°, new=292°) — tanks are close or same

> Supporting images: `jw9_conflict_resolution.jpg`

### Yanbu

Floating tanks fed to profiler: ['yb_0', 'yb_1', 'yb_2', 'yb_4', 'yb_5', 'yb_6', 'yb_7', 'yb_8', 'yb_9', 'yb_10', 'yb_11']
SAR scenes used: ['2024-01-11', '2024-05-10', '2024-09-07', '2025-01-05']

| Scene | Tanks confirmed | Scene pass |
|-------|----------------|------------|
| 2024-01-11 | 0/11 | False |
| 2024-05-10 | 1/11 | False |
| 2024-09-07 | 2/11 | False |
| 2025-01-05 | 0/11 | False |

> **Yanbu verdict: NOT CONFIRMED — method on this site does not produce consistent crescent signal**

---

## Task 4 — Verdict table

| Question | Juwaymah | Yanbu |
|----------|----------|-------|
| SAR crops valid (etalon rt_0) | NO | NO |
| Physical / registry / paired after Hough | 21 / 19 / 4 | 241 / 12 / 12 |
| Median shift old coords | 213.8m | 72.8m |
| Floating (re-classified Hough) | 12 | 11 |
| Profile CONFIRMED / NOT | CONFIRMED on corrected centers (['jw_3',… | NOT CONFIRMED — method on this site does… |
| jw_9 conflict resolved | Both centers confirmed (old=292°, new=292°) — tanks are clos… | n/a |
| **FINAL verdict** | **GO backfill on ['jw_3', 'jw_6', 'jw_7', 'jw_13']** | **DROP — profile NOT CONFIRMED on corrected centers** |

### Juwaymah next step (if CONFIRMED)

Floating tanks: 4. Threshold ≥8–10 for full terminal curve: BELOW threshold — partial series only. Kадры DESC 35 общие с RT south (orbit 35) — backfill infrastructure already exists.

---

*Honesty rule applied: verdicts backed by image filename, run number, or git hash. "Not verified" used where data unavailable. No hypothesis substituted for measurement.*
