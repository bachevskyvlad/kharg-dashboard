# ASC Validation Report

Generated: 2026-10-01

---

## 1. Parameter Diff Table (Pilot vs Prod — closed)

| Parameter | Pilot | Prod (backfill) | Status |
|---|---|---|---|
| az (LOS azimuth) | 98° (ASC_LOS_AZ) | 98° (ASC_AZ=98) | ✓ |
| width_m (annulus) | 15m (WIDTH_WIDE) | 15m hardcoded | ✓ |
| buffer_m (bbox pad) | 500m (BUFFER=500) | 300m (BUFFER_M=300) | diff (minor) |
| resolution (m/px) | 5 (RESOLUTION) | 5 (RESOLUTION=5) | ✓ |
| DB_FLOOR | −5.0 (config.py) | −5.0 (clipped on read) | ✓ fixed |
| step metric | median\|diff\| | median\|diff\| (metrics.py) | ✓ fixed |
| orbit dataFilter | ASCENDING+101 | ASCENDING+101 | ✓ |

All critical parameters now aligned. buffer_m 500→300 may shift pixel counts
for r=25m tanks but does not materially affect step for r≥40m tanks.

---

## 2. Task 3 — Gate Verification

Pilot window: 2026-08-05 → 2026-09-28 (10 scenes, orbit 101)

| Tank | n | step_prod | pilot_ref | diff | status |
|---|---|---|---|---|---|
| rt_5 | 10 | 0.420 | 0.420 | +0.000 | PASS |
| rt_6 | 10 | 0.488 | 0.488 | +0.000 | PASS |
| rt_8 | 10 | 1.718 | 1.718 | +0.000 | PASS |

Gate result: **PASS** — prod path matches pilot ±0.1 dB.

---

## 3. Screen Table — All 66 Tanks

Threshold: step < 2.5 dB (canonical median|diff|, DB_FLOOR=−5.0 on read).
step_full = full history; step_2026 = 2026-01-01 onward.
gap_inflated = step_full≥2.5 but step_2026<2.5 (enters ASC-WL via 2026 value).

| tank_id | n_asc | step_full | step_2026 | ASC-WL | DESC-WL | gap_inf | category |
|---|---|---|---|---|---|---|---|
| rt_0 | 243 | 2.840 | 2.013 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_1 | 243 | 2.633 | 2.242 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_10 | 243 | 1.734 | 1.704 | ✓ | ✓ |  | BOTH |
| rt_11 | 243 | 1.890 | 0.983 | ✓ | ✓ |  | BOTH |
| rt_12 | 243 | 2.613 | 1.670 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_13 | 243 | 1.282 | 0.860 | ✓ | ✓ |  | BOTH |
| rt_14 | 243 | 1.404 | 1.181 | ✓ | ✓ |  | BOTH |
| rt_15 | 243 | 3.248 | 1.258 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_16 | 243 | 3.005 | 0.917 | ✓ | ✓ | ✓ | BOTH |
| rt_17 | 243 | 1.526 | 0.928 | ✓ | ✗ |  | ASC_ONLY |
| rt_18 | 243 | 2.514 | 1.050 | ✓ | ✓ | ✓ | BOTH |
| rt_19 | 243 | 2.386 | 1.005 | ✓ | ✓ |  | BOTH |
| rt_2 | 243 | 1.723 | 0.607 | ✓ | ✗ |  | ASC_ONLY |
| rt_20 | 243 | 2.154 | 0.809 | ✓ | ✓ |  | BOTH |
| rt_21 | 243 | 1.391 | 0.807 | ✓ | ✓ |  | BOTH |
| rt_22 | 243 | 1.412 | 1.369 | ✓ | ✓ |  | BOTH |
| rt_23 | 243 | 1.739 | 0.977 | ✓ | ✓ |  | BOTH |
| rt_24 | 243 | 3.934 | 1.322 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_25 | 243 | 1.478 | 1.138 | ✓ | ✓ |  | BOTH |
| rt_26 | 243 | 3.084 | 0.710 | ✓ | ✓ | ✓ | BOTH |
| rt_27 | 243 | 2.223 | 1.132 | ✓ | ✓ |  | BOTH |
| rt_28 | 243 | 2.492 | 1.548 | ✓ | ✗ |  | ASC_ONLY |
| rt_29 | 243 | 2.118 | 0.957 | ✓ | ✓ |  | BOTH |
| rt_3 | 243 | 3.011 | 1.590 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_30 | 243 | 2.469 | 1.834 | ✓ | ✗ |  | ASC_ONLY |
| rt_31 | 243 | 2.731 | 0.706 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_32 | 243 | 2.506 | 1.321 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_33 | 243 | 2.902 | 1.623 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_34 | 243 | 1.440 | 0.516 | ✓ | ✓ |  | BOTH |
| rt_35 | 243 | 2.661 | 0.967 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_36 | 243 | 2.953 | 1.514 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_37 | 243 | 1.894 | 1.446 | ✓ | ✓ |  | BOTH |
| rt_38 | 243 | 1.348 | 1.087 | ✓ | ✓ |  | BOTH |
| rt_39 | 243 | 3.500 | 2.543 | ✗ | ✗ |  | NEITHER |
| rt_4 | 243 | 2.167 | 1.350 | ✓ | ✓ |  | BOTH |
| rt_40 | 243 | 5.625 | 2.708 | ✗ | ✗ |  | NEITHER |
| rt_41 | 243 | 1.601 | 2.263 | ✓ | ✓ |  | BOTH |
| rt_42 | 243 | 0.939 | 0.594 | ✓ | ✓ |  | BOTH |
| rt_43 | 243 | 3.694 | 3.187 | ✗ | ✗ |  | NEITHER |
| rt_44 | 243 | 3.295 | 4.189 | ✗ | ✗ |  | NEITHER |
| rt_45 | 243 | 2.005 | 1.854 | ✓ | ✓ |  | BOTH |
| rt_46 | 243 | 2.284 | 1.223 | ✓ | ✓ |  | BOTH |
| rt_47 | 243 | 6.112 | 5.939 | ✗ | ✗ |  | NEITHER |
| rt_48 | 243 | 2.456 | 4.766 | ✓ | ✗ |  | ASC_ONLY |
| rt_49 | 243 | 2.075 | 7.297 | ✓ | ✗ |  | ASC_ONLY |
| rt_5 | 243 | 2.782 | 1.064 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_50 | 243 | 3.042 | 1.977 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_51 | 243 | 3.022 | 3.225 | ✗ | ✗ |  | NEITHER |
| rt_52 | 243 | 1.285 | 7.163 | ✓ | ✗ |  | ASC_ONLY |
| rt_53 | 243 | 3.094 | 3.920 | ✗ | ✗ |  | NEITHER |
| rt_54 | 243 | 2.885 | 2.280 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_55 | 243 | 3.772 | 4.696 | ✗ | ✗ |  | NEITHER |
| rt_56 | 243 | 2.534 | 2.761 | ✗ | ✗ |  | NEITHER |
| rt_57 | 243 | 3.667 | 4.707 | ✗ | ✗ |  | NEITHER |
| rt_58 | 243 | 2.246 | 2.448 | ✓ | ✗ |  | ASC_ONLY |
| rt_59 | 243 | 0.245 | 0.241 | ✓ | ✓ |  | BOTH |
| rt_6 | 243 | 1.617 | 0.506 | ✓ | ✓ |  | BOTH |
| rt_60 | 243 | 1.857 | 0.671 | ✓ | ✓ |  | BOTH |
| rt_61 | 243 | 1.488 | 0.663 | ✓ | ✗ |  | ASC_ONLY |
| rt_62 | 243 | 1.401 | 1.123 | ✓ | ✓ |  | BOTH |
| rt_63 | 243 | 2.539 | 0.989 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_64 | 243 | 1.714 | 1.338 | ✓ | ✓ |  | BOTH |
| rt_65 | 243 | 3.046 | 2.311 | ✓ | ✓ | ✓ | BOTH |
| rt_7 | 243 | 2.664 | 1.642 | ✓ | ✗ | ✓ | ASC_ONLY |
| rt_8 | 243 | 1.479 | 0.766 | ✓ | ✓ |  | BOTH |
| rt_9 | 243 | 4.430 | 3.268 | ✗ | ✗ |  | NEITHER |

**Summary**: BOTH=30  ASC_ONLY=25  DESC_ONLY=0  NEITHER=11  gap_inflated=20

---

## 4. Per-Orbit Calibration

| Orbit | p5 (dB) | p95 (dB) | n_obs |
|---|---|---|---|
| ASC  | -0.923 | 16.944 | 7290 |
| DESC | -1.171 | 12.523 | 9450 |

Mean ASC−DESC volume offset: +nan Mb

---

## 5. M1 — Orbit Correlation

Method: corr(Δ_ASC, Δ_DESC) on pass pairs ≤4 days apart.

- Tank set: BOTH-WL = 30 tanks (ASC-WL ∩ DESC-WL)
- **Level corr (nearest-pair ≤4d, n=238): 0.2001**
- Δ-M1 raw (paired ≤4d, n=238): 0.0647
- Monthly Δ-M1 (n=80 months): 0.2118
- M1_war  (2026-02→09, n=41  pairs): 0.1775
- M1_peace (pre-war,   n=197 pairs): 0.0527

### Noise Model

| | ASC | DESC |
|---|---|---|
| WL tanks | 55 | 30 |
| med step (dB) | 1.737 | 1.963 |
| σ_terminal (dB-step) | 0.3322 | 0.3755 |

Expected M1 from noise model: **0.7459**  (measured/expected = 0.09)

Interpretation: if measured M1 ≈ expected M1, raw corr(Δ,Δ) was suppressed by noise
by construction and is not a valid validation metric for this cadence/tank-count.

### E1 / E2 Events (point estimator: val[+3 passes] − val[−3 passes])

| Event | Date | Expected | ASC Δ (Mb) | Status | DESC ref (Mb) |
|---|---|---|---|---|---|
| E1 | 2026-03-02 | BUILD (+) | -0.02 | ✗ WRONG SIGN | +2.30 |
| E2 | 2026-06-26 | DRAW  (−) | -0.62 | ✓ | −2.15 |

Series CSV: `diagnostics/rt_decomp/asc_desc_series_2024.csv`

---

## 6. Verdict

**STOP — level_corr=0.200 < 0.4; ASC orbit not validated**

Grid (fixed 2026-10-01) — primary criterion: level_corr on BOTH-set, nearest-pair ≤4d:
- level_corr ≥ 0.7 → GO (Δ-M1 deprecated: cadence mismatch suppresses pass-to-pass Δ by construction)
- level_corr 0.4–0.7 → HUMAN REVIEW
- level_corr < 0.4 or NaN → STOP настоящий

