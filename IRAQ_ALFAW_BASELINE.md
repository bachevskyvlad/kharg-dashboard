# IRAQ_ALFAW_BASELINE

Generated: 2026-10-02 12:47 UTC  
Terminal: `al_flaw` (Al-Faw, Iraq, IQ)  
Tanks: 24 (all radius=40m, h_max=14m)  
Pipeline: stage7_alfaw_baseline.py  

---

## Stage 1 — DB Inventory

| Field | Value |
|---|---|
| Rows in terminal_observations | 1000 |
| Unique tanks in DB | 24 |
| Unique passes (dates) | 42 |
| Date range | 2026-02-18 → 2026-09-29 |
| Backfill rows removed (≤-19.0) | 0 |
| Rows clipped to DB_FLOOR=-5.0 | 0 |
| Tanks in registry, absent from DB | [] |
| Tanks in DB, absent from registry | [] |

---

## Stage 2 — S1 Orbit Catalog

| Field | Value |
|---|---|
| Total S1 passes | 42 |
| Date range | 2026-02-18 → 2026-09-29 |
| Cadence median | 6.0 d |
| Cadence min / max | 1 / 7 d |
| Near-duplicate pairs (gap≤1d) | 4 |
| Terminal dB range (median across dates) | [0.16, 2.99] |

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
| WL tanks (step_db≥2.5) | 23 |
| Total capacity | 10.623 Mb |
| WL capacity | 10.18 Mb |
| WL cap fraction | 95.8% |
| Terminal WL gate (≥60%) | **PASS** |

### Per-tank table

| tank_id | n_passes | db_mean | db_std | step_full | step_fresh | WL |
|---|---|---|---|---|---|---|
| af_0 | 42 | 1.41 | 3.32 | 0.59 | 0.60 | ✓ |
| af_1 | 42 | 2.14 | 4.30 | 0.87 | 0.70 | ✓ |
| af_2 | 42 | 0.95 | 2.51 | 0.61 | 0.70 | ✓ |
| af_3 | 42 | 0.07 | 0.63 | 0.44 | 0.32 | ✓ |
| af_4 | 42 | 2.76 | 1.55 | 0.69 | 1.18 | ✓ |
| af_5 | 42 | 4.68 | 1.32 | 0.89 | 0.43 | ✓ |
| af_6 | 42 | 0.37 | 2.00 | 2.51 | 1.17 | ✓ |
| af_7 | 42 | -1.85 | 0.87 | 0.71 | 0.68 | ✓ |
| af_8 | 42 | 0.44 | 0.88 | 0.80 | 0.79 | ✓ |
| af_9 | 42 | -2.92 | 0.72 | 0.60 | 0.56 | ✓ |
| af_10 | 42 | 1.58 | 0.93 | 0.75 | 0.58 | ✓ |
| af_11 | 42 | -0.94 | 0.57 | 0.40 | 0.56 | ✓ |
| af_12 | 42 | 3.52 | 1.06 | 0.57 | 0.64 | ✓ |
| af_13 | 42 | 2.64 | 0.95 | 0.54 | 0.44 | ✓ |
| af_14 | 42 | 4.40 | 0.82 | 0.43 | 0.30 | ✓ |
| af_15 | 42 | 8.00 | 0.29 | 0.25 | 0.20 | — |
| af_16 | 41 | -3.96 | 0.97 | 0.38 | 0.53 | ✓ |
| af_17 | 41 | -1.21 | 0.81 | 0.53 | 0.50 | ✓ |
| af_18 | 41 | 2.44 | 0.45 | 0.25 | 0.35 | ✓ |
| af_19 | 41 | 3.54 | 1.44 | 0.59 | 0.43 | ✓ |
| af_20 | 41 | 5.32 | 1.94 | 0.88 | 0.68 | ✓ |
| af_21 | 41 | 6.12 | 1.12 | 0.69 | 0.95 | ✓ |
| af_22 | 41 | -0.48 | 0.62 | 0.34 | 0.20 | ✓ |
| af_23 | 41 | -1.45 | 0.90 | 0.70 | 0.48 | ✓ |

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
| WL passes in series | 42 |
| Volume range (WL tanks) | [3.647, 6.355] Mb |
| dB p5/p95 (WL calibration) | -3.796 / 6.446 dB |
| Placebo p90 change | 0.549 Mb |

**Iraq event sanity (E1 estimator — no export correlation, user-side):**

| Event | Date | E1 vol change Mb | Above placebo p90 | Passes in window |
|---|---|---|---|---|
| Basra Shutdown Jul-22 | 2022-07-20 | — | — | 0 |
| Pipeline Maint Jan-23 | 2023-01-15 | — | — | 0 |
| Exports Disruption Oct-23 | 2023-10-01 | — | — | 0 |

---

## Stage 7 — CSV Export

- `diagnostics/al_faw/iraq_alfaw_daily.csv` — terminal daily vol (WL tanks, Mb)
- `diagnostics/al_faw/iraq_alfaw_pertank.csv` — per-tank quality + step_db

**Correlation: user-side only. PortWatch/JODI/SOMO quarantined.**

---

## Terminal Assessment

WL gate: PASS (95.8% ≥ 60% required).  
Visual review of panels required before any series publication.  
No export verdicts in this pipeline run.
