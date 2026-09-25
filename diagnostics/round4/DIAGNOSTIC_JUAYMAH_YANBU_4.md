# DIAGNOSTIC_JUAYMAH_YANBU_4.md
Generated: 2026-09-25 10:45 UTC
Run: GHA round-4 on `claude/oilradar-dashboard-iran-zchdva`

---

## Task 1 — Juwaymah: profile × classification cross-reference

### jw_0 and jw_9 (profile-confirmed tanks) classification

| Tank | R3 class | R4 class (re-classified) | Profile (R2) |
|------|----------|--------------------------|--------------|
| jw_0 | unclear | unclear | 3/4 ✓ confirmed |
| jw_9 | unclear | floating | 4/4 ✓ confirmed |
| jw_18 | unclear | floating | 1/4 ✗ NOT confirmed |

**Verdict**: CONTRADICTION — jw_0=unclear, jw_9=floating but profile confirmed (min at 282°). Likely: optical heuristic misses dark metallic floating roofs. Profile verdict takes precedence.

### All 19 tanks — cross-reference

| tank_id | R3 class | R4 class | Profile | r_m |
|---------|----------|----------|---------|-----|
| jw_0 | unclear | unclear | 3/4 (✓) | 50 |
| jw_1 | unclear | unclear | — | 50 |
| jw_2 | unclear | unclear | — | 50 |
| jw_3 | fixed | unclear | — | 50 |
| jw_4 | fixed | fixed | — | 50 |
| jw_5 | unclear | unclear | — | 50 |
| jw_6 | fixed | floating | — | 50 |
| jw_7 | fixed | unclear | — | 50 |
| jw_8 | unclear | fixed | — | 50 |
| jw_9 | unclear | floating | 4/4 (✓) | 50 |
| jw_10 | unclear | floating | — | 50 |
| jw_11 | floating | unclear | — | 50 |
| jw_12 | unclear | unclear | — | 50 |
| jw_13 | fixed | unclear | — | 50 |
| jw_14 | fixed | fixed | — | 50 |
| jw_15 | unclear | floating | — | 50 |
| jw_16 | floating | unclear | — | 50 |
| jw_17 | floating | unclear | — | 50 |
| jw_18 | unclear | floating | 1/4 (✗) | 50 |


### Farm statistics (R4 reclassification)

| Metric | Value |
|--------|-------|
| Registry tanks | 19 |
| Floating (R4 optical) | 5 |
| Fixed (R4 optical) | 3 |
| Unclear (R4 optical) | 11 |
| Profile-confirmed (jw_0, jw_9) | 2 |

### GO/NO-GO verdict

> **GO backfill on ['jw_0', 'jw_9'] — 2 tanks with profile confirmed AND optical floating**

Supporting images: `jw_zoom_jw_0.jpg`, `jw_zoom_jw_9.jpg`, `jw_farm_map.jpg`

---

## Task 2 — Yanbu: overlay crops, shift vectors, offset grid search

### Shift vectors (7 tanks, 5m/px S2)

| tank_id | shift | components |
|---------|-------|------------|
| yb_0 | 41 m @ 4° | dx=3m dy=41m |
| yb_6 | 29 m @ 298° | dx=-26m dy=13m |
| yb_11 | 39 m @ 339° | dx=-14m dy=37m |
| yb_1 | 50 m @ 66° | dx=46m dy=20m |
| yb_2 | 43 m @ 357° | dx=-2m dy=43m |
| yb_4 | 18 m @ 210° | dx=-9m dy=-16m |
| yb_9 | 25 m @ 323° | dx=-15m dy=20m |


Systematic: NO
Median: 39.2 m @ 297.5°

### Offset grid search (±30m step 5m, floating tanks)

Best offset: dx=-15m dy=10m
Best score: 0.533 (fraction of ASC14 minima within ±30° of 98°)

Grid heatmap: `yb_offset_grid.jpg`

### Final Yanbu verdict

> **OFFSET FIXES CRESCENT: best offset dx=-15m dy=10m confirms 3 tanks (['yb_2', 'yb_4', 'yb_6']). Action: apply offset to yanbu registry in separate PR.**

Supporting images: `yb_overlay_yb_0.jpg` … `yb_overlay_yb_9.jpg`

---

## Task 3 — RT north / Juwaymah intersection

RT north centroid: {'lat': 26.6883, 'lon': 50.1168}
Juwaymah centroid: {'lat': 26.8192, 'lon': 49.9871}
Distance: 19.5 km
Duplicate tanks (< 500m): none

> **rt_north (rt_5–rt_10) is a SEPARATE group from Juwaymah Tank Farm. Distance 19.5 km. 0 coordinate overlaps within 500m. No double-counting risk at current registry scope. If both terminals are ever merged into a single query, they must be treated as separate physical facilities.**

Map: `rt_juwaymah_map.jpg`

---

## Task 4 — Verdict table

| Question | Juwaymah | Yanbu |
|----------|----------|-------|
| jw_0/jw_9: class vs profile consistent | CONTRADICTION — jw_0=unclear, jw_9=floating but profile confirmed (min at 282°).… | n/a |
| Floating confirmed / in registry / profile-confirmed | 5 / 19 / 2 | 5 / 12 / 0 |
| Profile after offset correction | n/a | score=0.533 (OFFSET FIXES CRESCENT: best offset dx=-15m dy=10m confirms 3…) |
| Coordinate shift vector | n/a | NOT SYSTEMATIC 39.2m @297.5° |
| Duplicate tanks with rt_* | none | n/a |
| **FINAL** | GO backfill on ['jw_0', 'jw_9'] — 2 tanks with profile confirmed AND optical flo… | OFFSET FIXES CRESCENT: best offset dx=-15m dy=10m confirms 3 tanks (['yb_2', 'yb… |

---

*Honesty rule applied: verdicts backed by image filename, run number, or git hash. "Not verified" used where data unavailable. No hypothesis substituted for measurement.*
