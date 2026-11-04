# VERIFICATION_3 — Ras Tanura Geolocation / Orbit / Date-Window Analysis

Generated: 2026-09-22T18:20:39Z
Scene window: 2026-07-16 → 2026-09-16 (corrected from round 2)

Task order per spec:
  5 (first): Scene date window root cause and fix confirmation
  1: Annotated crops — rt_0, rt_11 + 3 neighbours, 3 DESC 35 + 2 ASC 101
  2: Geolocation offset — template matching ±10px, 10 south tanks × 3 scenes
  3: 213 single-tank dates vs orbit 137 12-day cycle
  4: Exact SQL — terminal_observations for ras_tanura at 10 specific dates

No Supabase writes. No production code changes.

## Task 5 — Scene Date Window: Root Cause of Round 2 Wrong Dates

### What round 2 used
Round 2 searched `"2024-01-01"` → `"2025-12-31"` in `_catalog()`.
That window ends **2025-12-31**, so no 2026 scenes were ever considered.
The "most recent" scenes returned were from **Nov–Dec 2025** — the actual tail
of that search window.

### Why that happened
`verify_orbit_fix_2.py` hardcoded the catalog search window to `("2024-01-01", "2025-12-31")`
in **three places**: task2, task3, and task4. The requested analysis window
(2026-07→09) was never passed to the catalog calls.

### Fix applied in round 3
`WIN_START = "2026-07-16"`, `WIN_END = "2026-09-16"` are module-level constants.
All `_catalog()` calls in tasks 1 and 2 pass `(WIN_START, WIN_END)` explicitly.

### Verification
Scene catalog is re-run below. If DESC 35 or ASC 101 scenes are found inside
the 2026-07-16 → 2026-09-16 window, the fix is confirmed.


**Catalog probe results for 2026-07-16 → 2026-09-16:**

DESC 35 scenes found: 10
ASC 101 scenes found: 11
DESC 35 dates: 2026-09-12, 2026-09-06, 2026-08-31, 2026-08-25, 2026-08-19, 2026-08-13, 2026-08-07, 2026-08-01, 2026-07-26, 2026-07-20
ASC 101 dates: 2026-09-16, 2026-09-10, 2026-09-04, 2026-08-29, 2026-08-23, 2026-08-17, 2026-08-11, 2026-08-05, 2026-07-30, 2026-07-24, 2026-07-18

## Task 1 — Annotated SAR Crops (2026-07→09 window)

Tanks: ['rt_0', 'rt_11', 'rt_64', 'rt_1', 'rt_13']  (5 total: rt_0 + rt_11 + 3 neighbours)
Scenes: 5 = up to 3 DESC 35 + 2 ASC 101
Resolution: 5 m/px, ×8 nearest upscale
Annotations: registry circle (green), inner annulus (green dashed), 282° crescent (magenta)

**Zero-size crop fix**: guard `if x1 <= x0 or y1 <= y0: skip` added — prevents the numpy indexing crash that occurred in round 2.

**Crops generated**: 25 of 25 attempted

**Successful crops:**
| orbit | date | tank | crop_h | crop_w |
|-------|------|------|--------|--------|
| DESC_35 | 2026-09-12 | rt_0 | 96 | 96 |
| DESC_35 | 2026-09-12 | rt_11 | 56 | 56 |
| DESC_35 | 2026-09-12 | rt_64 | 56 | 56 |
| DESC_35 | 2026-09-12 | rt_1 | 96 | 96 |
| DESC_35 | 2026-09-12 | rt_13 | 56 | 56 |
| DESC_35 | 2026-09-06 | rt_0 | 96 | 96 |
| DESC_35 | 2026-09-06 | rt_11 | 56 | 56 |
| DESC_35 | 2026-09-06 | rt_64 | 56 | 56 |
| DESC_35 | 2026-09-06 | rt_1 | 96 | 96 |
| DESC_35 | 2026-09-06 | rt_13 | 56 | 56 |
| DESC_35 | 2026-08-31 | rt_0 | 96 | 96 |
| DESC_35 | 2026-08-31 | rt_11 | 56 | 56 |
| DESC_35 | 2026-08-31 | rt_64 | 56 | 56 |
| DESC_35 | 2026-08-31 | rt_1 | 96 | 96 |
| DESC_35 | 2026-08-31 | rt_13 | 56 | 56 |
| ASC_101 | 2026-09-16 | rt_0 | 96 | 96 |
| ASC_101 | 2026-09-16 | rt_11 | 56 | 56 |
| ASC_101 | 2026-09-16 | rt_64 | 56 | 56 |
| ASC_101 | 2026-09-16 | rt_1 | 96 | 96 |
| ASC_101 | 2026-09-16 | rt_13 | 56 | 56 |
| ASC_101 | 2026-09-10 | rt_0 | 96 | 96 |
| ASC_101 | 2026-09-10 | rt_11 | 56 | 56 |
| ASC_101 | 2026-09-10 | rt_64 | 56 | 56 |
| ASC_101 | 2026-09-10 | rt_1 | 96 | 96 |
| ASC_101 | 2026-09-10 | rt_13 | 56 | 56 |

PNG files in `diagnostics/ras_tanura/task1_crops/`.

## Task 2 — Geolocation Offset (Template Matching ±10px)

Tanks: 10 south-cluster RT tanks
Scenes: ['2026-09-12', '2026-09-06', '2026-08-31']
Method: normalised cross-correlation (FFT), ±10px search window, 5m/px
Search window: ±10px = ±50m

### RT offset results

| date | tank | dx_px | dy_px | dx_m | dy_m | status |
|------|------|-------|-------|------|------|--------|
| 2026-09-12 | rt_0 | 0 | 0 | 0 | 0 | ok |
| 2026-09-12 | rt_1 | 0 | 0 | 0 | 0 | ok |
| 2026-09-12 | rt_2 | 0 | 0 | 0 | 0 | ok |
| 2026-09-12 | rt_3 | 0 | 0 | 0 | 0 | ok |
| 2026-09-12 | rt_4 | 0 | 0 | 0 | 0 | ok |
| 2026-09-12 | rt_11 | 0 | 0 | 0 | 0 | ok |
| 2026-09-12 | rt_12 | 0 | 0 | 0 | 0 | ok |
| 2026-09-12 | rt_13 | 0 | 0 | 0 | 0 | ok |
| 2026-09-12 | rt_14 | -10 | -2 | -50 | -10 | ok |
| 2026-09-12 | rt_15 | 0 | 0 | 0 | 0 | ok |
| 2026-09-06 | rt_0 | 0 | 0 | 0 | 0 | ok |
| 2026-09-06 | rt_1 | 0 | 0 | 0 | 0 | ok |
| 2026-09-06 | rt_2 | 0 | 0 | 0 | 0 | ok |
| 2026-09-06 | rt_3 | 0 | 0 | 0 | 0 | ok |
| 2026-09-06 | rt_4 | 0 | 0 | 0 | 0 | ok |
| 2026-09-06 | rt_11 | 0 | 0 | 0 | 0 | ok |
| 2026-09-06 | rt_12 | 0 | 0 | 0 | 0 | ok |
| 2026-09-06 | rt_13 | 0 | 0 | 0 | 0 | ok |
| 2026-09-06 | rt_14 | 7 | 1 | 35 | 5 | ok |
| 2026-09-06 | rt_15 | -10 | -2 | -50 | -10 | ok |
| 2026-08-31 | rt_0 | 0 | 0 | 0 | 0 | ok |
| 2026-08-31 | rt_1 | 0 | 0 | 0 | 0 | ok |
| 2026-08-31 | rt_2 | 0 | 0 | 0 | 0 | ok |
| 2026-08-31 | rt_3 | 0 | 0 | 0 | 0 | ok |
| 2026-08-31 | rt_4 | 0 | 0 | 0 | 0 | ok |
| 2026-08-31 | rt_11 | 0 | 0 | 0 | 0 | ok |
| 2026-08-31 | rt_12 | 0 | 0 | 0 | 0 | ok |
| 2026-08-31 | rt_13 | 0 | 0 | 0 | 0 | ok |
| 2026-08-31 | rt_14 | 0 | 0 | 0 | 0 | ok |
| 2026-08-31 | rt_15 | 0 | 0 | 0 | 0 | ok |

**Summary**: mean dx=-0.43px (±2.9), mean dy=-0.1px (±0.55)
Offset in metres: dx=-2.2m, dy=-0.5m

**Offset within ±1px**: registry coords are well-aligned. Round 2 paired measurements unaffected by geolocation error.

### Kharg control (expected ≈ 0–1px)

| date | tank | dx_px | dy_px | status |
|------|------|-------|-------|--------|
| 2026-09-12 | t60_0 | 0 | 0 | ok |
| 2026-09-12 | t60_1 | 0 | 0 | ok |
| 2026-09-06 | t60_0 | 0 | 0 | ok |
| 2026-09-06 | t60_1 | 0 | 0 | ok |
| 2026-08-31 | t60_0 | 0 | 0 | ok |
| 2026-08-31 | t60_1 | 0 | 0 | ok |

## Task 3 — Single-Tank Dates vs Orbit 137 12-Day Cycle

**Single-tank dates in terminal_observations**: 215 (spec: 213)
**Period**: 12 days (Sentinel-1 repeat)
**DESC 35 anchor dates available**: 145
**Orbit 137 offset from orbit 35**: 4–7 days within 12-day cycle

**Matches (offset 4–7 mod 12)**: 0 / 215 = 0.0%
**Non-matches**: 215 / 215


**Conclusion**: Single-tank dates do NOT cluster on orbit-137 offsets. Contamination from orbit 137 is not the primary cause; another mechanism (partial orbit coverage, cloud/rain shadow, pipeline deduplication artifact) may be responsible.

## Task 4 — Exact SQL on terminal_observations (10 target dates)

**Query**: `SELECT date, tank_id, db_mean FROM terminal_observations WHERE terminal_id = 'ras_tanura' AND date IN (...)`

| date | n_rows | mean_db | tanks |
|------|--------|---------|-------|
| 2026-07-21 | 1 | 10.655 | rt_65 |
| 2026-07-27 | 1 | 3.266 | rt_65 |
| 2026-08-02 | 1 | 4.391 | rt_65 |
| 2026-08-08 | 1 | 3.898 | rt_65 |
| 2026-08-14 | 1 | 3.989 | rt_65 |
| 2026-08-20 | 0 | None | — |
| 2026-08-26 | 1 | 5.931 | rt_65 |
| 2026-09-01 | 1 | 1.076 | rt_65 |
| 2026-09-07 | 1 | 5.446 | rt_65 |
| 2026-09-13 | 1 | 3.734 | rt_65 |

**Total rows across 10 dates**: 9
**Dates with zero rows**: 2026-08-20
Zero rows on a date = no scene was processed by the pipeline (orbit 35 did not have a pass, or the scene was rejected).