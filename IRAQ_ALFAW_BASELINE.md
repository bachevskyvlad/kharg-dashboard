# IRAQ_ALFAW_BASELINE

Generated: 2026-10-02 13:47 UTC  
Terminal: `al_flaw` (Al-Faw, Iraq, IQ)  
Tanks: 24 (all radius=40m, h_max=14m)  
Pipeline: stage7_alfaw_baseline.py  

---

## Stage 1 — DB Inventory

| Field | Value |
|---|---|
| Rows in terminal_observations | 7584 |
| Unique tanks in DB | 24 |
| Unique passes (dates) | 281 |
| Date range | 2015-04-11 → 2026-09-29 |
| Backfill rows removed (≤-19.0) | 0 |
| Rows clipped to DB_FLOOR=-5.0 | 370 |
| Tanks in registry, absent from DB | [] |
| Tanks in DB, absent from registry | [] |

---

## Stage 2 — S1 Orbit Catalog

| Field | Value |
|---|---|
| Total S1 passes | 281 |
| Date range | 2015-04-11 → 2026-09-29 |
| Cadence median | 12.0 d |
| Cadence min / max | 1 / 684 d |
| Near-duplicate pairs (gap≤1d) | 4 |
| Terminal dB range (median across dates) | [-2.15, 3.24] |

---

## Stage 3 — S2 Optical Geometry

| Field | Value |
|---|---|
| S2 scenes (cloud<20%) | 0 |
| Low shadow geometry (sun_elv>65°) | 0 |
| Usable shadow geometry | 0 |
| Tanks with ≥2 usable shadow pixels | 24/24 |

All tanks: radius=40m → 8px/ray at 5m/px, shadow pixels = 7px ≥ 2 — geometry OK.

---

## Stage 4 — Per-Tank Quality Screen

| Field | Value |
|---|---|
| Tanks total | 24 |
| WL tanks (step_db≥2.5) | 24 |
| Total capacity | 10.623 Mb |
| WL capacity | 10.622 Mb |
| WL cap fraction | 100.0% |
| Terminal WL gate (≥60%) | **PASS** |

### Per-tank table

| tank_id | n_passes | db_mean | db_std | step_full | step_fresh | WL |
|---|---|---|---|---|---|---|
| af_0 | 316 | 2.23 | 4.76 | 0.53 | 0.60 | ✓ |
| af_1 | 316 | 1.89 | 5.57 | 0.39 | 0.70 | ✓ |
| af_2 | 316 | -0.89 | 4.10 | 0.16 | 0.70 | ✓ |
| af_3 | 316 | 0.24 | 3.50 | 0.42 | 0.32 | ✓ |
| af_4 | 316 | 2.09 | 3.37 | 0.54 | 1.18 | ✓ |
| af_5 | 315 | 0.20 | 3.74 | 0.46 | 0.43 | ✓ |
| af_6 | 315 | 0.76 | 3.37 | 1.18 | 1.17 | ✓ |
| af_7 | 315 | -1.18 | 2.20 | 0.70 | 0.68 | ✓ |
| af_8 | 316 | -0.62 | 1.47 | 0.78 | 0.79 | ✓ |
| af_9 | 316 | -3.56 | 1.22 | 0.40 | 0.56 | ✓ |
| af_10 | 316 | 2.44 | 2.52 | 0.92 | 0.58 | ✓ |
| af_11 | 316 | -0.83 | 1.41 | 0.57 | 0.56 | ✓ |
| af_12 | 316 | 2.43 | 2.07 | 0.55 | 0.64 | ✓ |
| af_13 | 316 | 0.87 | 1.77 | 0.55 | 0.44 | ✓ |
| af_14 | 316 | 5.59 | 0.96 | 0.45 | 0.30 | ✓ |
| af_15 | 316 | 8.09 | 0.38 | 0.25 | 0.20 | ✓ |
| af_16 | 316 | -0.30 | 2.30 | 0.60 | 0.53 | ✓ |
| af_17 | 317 | -1.78 | 0.80 | 0.46 | 0.50 | ✓ |
| af_18 | 317 | 0.31 | 1.16 | 0.29 | 0.35 | ✓ |
| af_19 | 317 | 3.28 | 1.35 | 0.62 | 0.43 | ✓ |
| af_20 | 316 | 2.70 | 2.98 | 0.78 | 0.68 | ✓ |
| af_21 | 316 | 3.20 | 2.88 | 0.61 | 0.95 | ✓ |
| af_22 | 316 | -0.64 | 0.82 | 0.44 | 0.20 | ✓ |
| af_23 | 316 | -1.65 | 1.05 | 0.40 | 0.48 | ✓ |

---

## Stage 5 — Visual Panels

Panels written to `diagnostics/al_faw/panels/` (no verdicts — visual review by analyst).

- `diagnostics/al_faw/panels/alfaw_sar_panel_b01.png`
- `diagnostics/al_faw/panels/alfaw_sar_panel_b02.png`
- `diagnostics/al_faw/panels/alfaw_sar_panel_b03.png`
- `diagnostics/al_faw/panels/alfaw_sar_panel_b04.png`
- `diagnostics/al_faw/alfaw_tank_map.png`

---

## Stage 6 — Terminal Series + Iraq Event Sanity

| Field | Value |
|---|---|
| WL passes in series | 281 |
| Volume range (WL tanks) | [1.155, 13.795] Mb |
| dB p5/p95 (WL calibration) | -4.938 / 7.773 dB |
| Placebo p90 change | 1.011 Mb |

**Iraq event sanity (E1 estimator — no export correlation, user-side):**

| Event | Date | E1 vol change Mb | Above placebo p90 | Passes in window |
|---|---|---|---|---|
| Basra Shutdown Jul-22 | 2022-07-20 | — | — | 5 |
| Pipeline Maint Jan-23 | 2023-01-15 | — | — | 5 |
| Exports Disruption Oct-23 | 2023-10-01 | — | — | 0 |

---

## Stage 7 — CSV Export

- `diagnostics/al_faw/iraq_alfaw_daily.csv` — terminal daily vol (WL tanks, Mb)
- `diagnostics/al_faw/iraq_alfaw_pertank.csv` — per-tank quality + step_db

**Correlation: user-side only. PortWatch/JODI/SOMO quarantined.**

---

## Terminal Assessment

WL gate: PASS (100.0% ≥ 60% required).  
Visual review of panels required before any series publication.  
No export verdicts in this pipeline run.
