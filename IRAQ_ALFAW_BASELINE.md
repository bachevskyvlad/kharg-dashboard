# IRAQ_ALFAW_BASELINE

Generated: 2026-10-02 15:48 UTC  
Terminal: `al_flaw` (Al-Faw, Iraq, IQ)  
Tanks: 24 (all radius=40m, h_max=14m)  
Pipeline: stage7_alfaw_baseline.py  

---

## Stage 1 — DB Inventory

| Field | Value |
|---|---|
| Rows in terminal_observations | 6697 |
| Unique tanks in DB | 24 |
| Unique passes (dates) | 281 |
| Date range | 2015-04-11 → 2026-09-29 |
| Backfill rows removed (≤-19.0) | 0 |
| Rows clipped to DB_FLOOR=-5.0 | 311 |
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

**Year-by-year pass coverage:**

| Year | Passes |
|---|---|
| 2015 | 1 |
| 2017 | 25 |
| 2018 | 30 |
| 2019 | 26 |
| 2020 | 29 |
| 2021 | 29 |
| 2022 | 26 |
| 2023 | 17 |
| 2024 | 14 |
| 2025 | 35 |
| 2026 | 49 |

**Large gaps (>90d):**

| From | To | Days |
|---|---|---|
| 2015-04-11 | 2017-02-23 | 684 |
| 2023-08-27 | 2024-01-30 | 156 |
| 2024-07-04 | 2025-04-18 | 288 |

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
| af_0 | 280 | 1.74 | 4.84 | 0.62 | 0.60 | ✓ |
| af_1 | 280 | 2.17 | 5.60 | 0.48 | 0.70 | ✓ |
| af_2 | 280 | -0.68 | 3.99 | 0.37 | 0.70 | ✓ |
| af_3 | 280 | 0.18 | 3.71 | 0.51 | 0.32 | ✓ |
| af_4 | 280 | 1.78 | 3.40 | 0.69 | 1.18 | ✓ |
| af_5 | 279 | 0.65 | 3.74 | 0.52 | 0.43 | ✓ |
| af_6 | 279 | 0.65 | 3.37 | 1.45 | 1.17 | ✓ |
| af_7 | 279 | -1.09 | 2.18 | 0.93 | 0.68 | ✓ |
| af_8 | 279 | -0.66 | 1.53 | 0.90 | 0.79 | ✓ |
| af_9 | 279 | -3.57 | 1.17 | 0.49 | 0.56 | ✓ |
| af_10 | 279 | 2.39 | 2.58 | 1.11 | 0.58 | ✓ |
| af_11 | 279 | -0.67 | 1.34 | 0.71 | 0.56 | ✓ |
| af_12 | 279 | 2.28 | 2.09 | 0.63 | 0.64 | ✓ |
| af_13 | 279 | 0.89 | 1.79 | 0.65 | 0.44 | ✓ |
| af_14 | 279 | 5.55 | 0.99 | 0.52 | 0.30 | ✓ |
| af_15 | 279 | 8.07 | 0.38 | 0.29 | 0.20 | ✓ |
| af_16 | 279 | -0.47 | 2.36 | 0.74 | 0.53 | ✓ |
| af_17 | 279 | -1.74 | 0.81 | 0.55 | 0.50 | ✓ |
| af_18 | 279 | 0.42 | 1.19 | 0.33 | 0.35 | ✓ |
| af_19 | 279 | 3.27 | 1.38 | 0.74 | 0.43 | ✓ |
| af_20 | 278 | 2.39 | 2.95 | 0.91 | 0.68 | ✓ |
| af_21 | 278 | 3.32 | 2.97 | 0.70 | 0.95 | ✓ |
| af_22 | 278 | -0.61 | 0.86 | 0.52 | 0.20 | ✓ |
| af_23 | 278 | -1.56 | 1.04 | 0.52 | 0.48 | ✓ |

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
| Volume range (WL tanks) | [1.166, 7.809] Mb |
| dB p5/p95 (WL calibration) | -4.807 / 7.802 dB |
| Placebo p90 change | 0.959 Mb |

**Iraq event sanity (E1 estimator — no export correlation, user-side):**

| Event | Date | E1 vol change Mb | Above placebo p90 | Passes in window |
|---|---|---|---|---|
| Basra Shutdown Jul-22 | 2022-07-20 | 0.621 | False | 9 |
| Pipeline Maint Jan-23 | 2023-01-15 | -0.108 | False | 9 |
| Exports Disruption Oct-23 | 2023-10-01 | nan | — | 2 |

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
