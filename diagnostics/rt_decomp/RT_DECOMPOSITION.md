# RT Decomposition — Report

Run: 2026-09-30 17:02 UTC

## Registration
- Train: 2020-01 → 2023-12
- Holdout: 2024-01 → 2026-02 (2026-03+ conflict excluded)
- Sub-series: 5 (frozen before calculation)
- Combinations: 40  Bonferroni α = 0.05/40 = 0.00125

## Calibration
- dB_min (global 5th pct) = -1.556
- dB_max (global 95th pct) = 13.769
- Single terminal-level calibration, not per sub-group

## Sanity Check — Geo-split == Prod WL
PASS: south_pier + south_inland + north == prod WL curve (tolerance 0.5%)

## Sub-Series Composition

| Sub-series | n_tanks | Tank IDs |
|---|---|---|
| south_pier | 4 | rt_28, rt_32, rt_36, rt_44 |
| south_inland | 37 | rt_11, rt_12, rt_13, rt_14, rt_15, rt_16, rt_17, rt_18, rt_19, rt_2, rt_20, rt_21, rt_22, rt_23, rt_24, rt_25, rt_26, rt_27, rt_29, rt_30, rt_31, rt_33, rt_34, rt_35, rt_37, rt_38, rt_4, rt_41, rt_42, rt_57, rt_59, rt_60, rt_61, rt_62, rt_63, rt_64, rt_65 |
| north | 4 | rt_5, rt_6, rt_7, rt_8 |
| t60_class | 6 | rt_2, rt_4, rt_5, rt_6, rt_7, rt_8 |
| active10 | 10 | rt_12, rt_17, rt_2, rt_28, rt_30, rt_31, rt_35, rt_38, rt_5, rt_57 |

Pier reference: (26.648, 50.16) (≤600m → south_pier)

## Stage 2 — Event Decomposition (2026 conflict, diagnostic)

Two estimators compared. Mean-window: last−first in ±21d window.
Point: val[+3 SAR passes] − val[−3 SAR passes]. Sums of groups must match TERMINAL row.

| Event | Expected | Sub-series | Δ mean-window (Mb) | Δ point (Mb) |
|---|---|---|---|---|
| E1: Attack/Halt | BUILD | south_pier | -0.053 | +0.104 |
| E1: Attack/Halt | BUILD | south_inland | +0.889 | +1.121 |
| E1: Attack/Halt | BUILD | north | +1.230 | +1.073 |
| E1: Attack/Halt | BUILD | t60_class | +1.502 | +1.465 |
| E1: Attack/Halt | BUILD | active10 | +0.987 | +1.057 |
| E1: Attack/Halt | BUILD | TERMINAL | +2.065 | +2.298 |
| E1: Attack/Halt | BUILD | GROUP_SUM | +4.555 | +4.821 |
| E2: Ops resumed | DRAW | south_pier | -0.033 | -0.044 |
| E2: Ops resumed | DRAW | south_inland | -0.550 | -0.409 |
| E2: Ops resumed | DRAW | north | -1.394 | -1.271 |
| E2: Ops resumed | DRAW | t60_class | -1.429 | -1.351 |
| E2: Ops resumed | DRAW | active10 | -1.526 | -1.358 |
| E2: Ops resumed | DRAW | TERMINAL | -1.976 | -1.724 |
| E2: Ops resumed | DRAW | GROUP_SUM | -4.931 | -4.434 |
| E3: Ramp ~90% | DRAW | south_pier | +0.293 | +0.458 |
| E3: Ramp ~90% | DRAW | south_inland | +0.249 | +1.126 |
| E3: Ramp ~90% | DRAW | north | -1.228 | +0.556 |
| E3: Ramp ~90% | DRAW | t60_class | -1.019 | +0.827 |
| E3: Ramp ~90% | DRAW | active10 | -1.143 | +0.525 |
| E3: Ramp ~90% | DRAW | TERMINAL | -0.686 | +2.140 |
| E3: Ramp ~90% | DRAW | GROUP_SUM | -2.848 | +3.492 |

**Canonical estimator**: point estimator (val[+3]−val[−3]) — matches PROOF_OF_VALUE event study,
removes window-edge sensitivity. Mean-window retained for reference.

## Stage 3 — Correlation Grid (train 2020-01 → 2023-12)

Bonferroni α = 0.00125

| Sub-series | Anchor | Metric | Lag | r_train | p | n | Verdict |
|---|---|---|---|---|---|---|---|
| south_pier | jodi_saudi | delta | +0 | -0.0244 | 0.8706 | 47 | NULL |
| south_pier | jodi_saudi | delta | +1 | 0.1751 | 0.2444 | 46 | NULL |
| south_pier | jodi_saudi | level_anom | +0 | -0.2206 | 0.1552 | 43 | NULL |
| south_pier | jodi_saudi | level_anom | +1 | -0.1987 | 0.2070 | 42 | NULL |
| south_pier | rtju_mbpd | delta | +0 | n/a | n/a | 0 | NO DATA |
| south_pier | rtju_mbpd | delta | +1 | n/a | n/a | 0 | NO DATA |
| south_pier | rtju_mbpd | level_anom | +0 | n/a | n/a | 0 | NO DATA |
| south_pier | rtju_mbpd | level_anom | +1 | n/a | n/a | 0 | NO DATA |
| south_inland | jodi_saudi | delta | +0 | 0.0301 | 0.8407 | 47 | NULL |
| south_inland | jodi_saudi | delta | +1 | -0.0178 | 0.9066 | 46 | NULL |
| south_inland | jodi_saudi | level_anom | +0 | -0.2746 | 0.0748 | 43 | NULL |
| south_inland | jodi_saudi | level_anom | +1 | -0.3134 | 0.0433 | 42 | PROMISING |
| south_inland | rtju_mbpd | delta | +0 | n/a | n/a | 0 | NO DATA |
| south_inland | rtju_mbpd | delta | +1 | n/a | n/a | 0 | NO DATA |
| south_inland | rtju_mbpd | level_anom | +0 | n/a | n/a | 0 | NO DATA |
| south_inland | rtju_mbpd | level_anom | +1 | n/a | n/a | 0 | NO DATA |
| north | jodi_saudi | delta | +0 | -0.1065 | 0.4763 | 47 | NULL |
| north | jodi_saudi | delta | +1 | 0.1469 | 0.3300 | 46 | NULL |
| north | jodi_saudi | level_anom | +0 | 0.2772 | 0.0720 | 43 | NULL |
| north | jodi_saudi | level_anom | +1 | 0.3658 | 0.0172 | 42 | NULL |
| north | rtju_mbpd | delta | +0 | n/a | n/a | 0 | NO DATA |
| north | rtju_mbpd | delta | +1 | n/a | n/a | 0 | NO DATA |
| north | rtju_mbpd | level_anom | +0 | n/a | n/a | 0 | NO DATA |
| north | rtju_mbpd | level_anom | +1 | n/a | n/a | 0 | NO DATA |
| t60_class | jodi_saudi | delta | +0 | -0.1097 | 0.4630 | 47 | NULL |
| t60_class | jodi_saudi | delta | +1 | 0.1384 | 0.3588 | 46 | NULL |
| t60_class | jodi_saudi | level_anom | +0 | 0.0840 | 0.5924 | 43 | NULL |
| t60_class | jodi_saudi | level_anom | +1 | 0.1723 | 0.2752 | 42 | NULL |
| t60_class | rtju_mbpd | delta | +0 | n/a | n/a | 0 | NO DATA |
| t60_class | rtju_mbpd | delta | +1 | n/a | n/a | 0 | NO DATA |
| t60_class | rtju_mbpd | level_anom | +0 | n/a | n/a | 0 | NO DATA |
| t60_class | rtju_mbpd | level_anom | +1 | n/a | n/a | 0 | NO DATA |
| active10 | jodi_saudi | delta | +0 | 0.2781 | 0.0583 | 47 | NULL |
| active10 | jodi_saudi | delta | +1 | -0.0682 | 0.6525 | 46 | NULL |
| active10 | jodi_saudi | level_anom | +0 | 0.1246 | 0.4259 | 43 | NULL |
| active10 | jodi_saudi | level_anom | +1 | -0.0483 | 0.7614 | 42 | NULL |
| active10 | rtju_mbpd | delta | +0 | n/a | n/a | 0 | NO DATA |
| active10 | rtju_mbpd | delta | +1 | n/a | n/a | 0 | NO DATA |
| active10 | rtju_mbpd | level_anom | +0 | n/a | n/a | 0 | NO DATA |
| active10 | rtju_mbpd | level_anom | +1 | n/a | n/a | 0 | NO DATA |

## Stage 3 — Holdout (2024-01 → 2026-02)

Bonferroni PASS: 0  |  Promising (train p<0.10 AND holdout r<0): 3

| Sub-series | Anchor | Metric | Lag | r_train | r_holdout | p_holdout | Bonf | Sign |
|---|---|---|---|---|---|---|---|---|
| south_pier | jodi_saudi | delta | +0 | -0.0244 | 0.0714 | 0.7346 | FAIL | ✗ |
| south_pier | jodi_saudi | delta | +1 | 0.1751 | 0.2169 | 0.3086 | FAIL | ✓ |
| south_pier | jodi_saudi | level_anom | +0 | -0.2206 | 0.0744 | 0.7485 | FAIL | ✗ |
| south_pier | jodi_saudi | level_anom | +1 | -0.1987 | 0.1123 | 0.6373 | FAIL | ✗ |
| south_inland | jodi_saudi | delta | +0 | 0.0301 | 0.2326 | 0.2631 | FAIL | ✓ |
| south_inland | jodi_saudi | delta | +1 | -0.0178 | -0.1071 | 0.6184 | FAIL | ✓ |
| south_inland | jodi_saudi | level_anom | +0 | -0.2746 | -0.1636 | 0.4787 | FAIL | ✓ |
| south_inland | jodi_saudi | level_anom | +1 | -0.3134 | -0.3380 | 0.1450 | FAIL | ✓ |
| north | jodi_saudi | delta | +0 | -0.1065 | -0.0050 | 0.9810 | FAIL | ✓ |
| north | jodi_saudi | delta | +1 | 0.1469 | 0.0731 | 0.7344 | FAIL | ✓ | ⚠ sign violation (expected −, got +); exploratory note only
| north | jodi_saudi | level_anom | +0 | 0.2772 | 0.4286 | 0.0525 | FAIL | ✓ | ⚠ sign violation (expected −, got +); exploratory note only
| north | jodi_saudi | level_anom | +1 | 0.3658 | 0.1779 | 0.4530 | FAIL | ✓ | ⚠ sign violation (expected −, got +); exploratory note only
| t60_class | jodi_saudi | delta | +0 | -0.1097 | 0.1956 | 0.3487 | FAIL | ✗ |
| t60_class | jodi_saudi | delta | +1 | 0.1384 | 0.0546 | 0.8000 | FAIL | ✓ |
| t60_class | jodi_saudi | level_anom | +0 | 0.0840 | 0.3963 | 0.0753 | FAIL | ✓ |
| t60_class | jodi_saudi | level_anom | +1 | 0.1723 | 0.1134 | 0.6342 | FAIL | ✓ |
| active10 | jodi_saudi | delta | +0 | 0.2781 | -0.0740 | 0.7252 | FAIL | ✗ |
| active10 | jodi_saudi | delta | +1 | -0.0682 | 0.0478 | 0.8246 | FAIL | ✗ |
| active10 | jodi_saudi | level_anom | +0 | 0.1246 | 0.0070 | 0.9760 | FAIL | ✓ |
| active10 | jodi_saudi | level_anom | +1 | -0.0483 | 0.1329 | 0.5764 | FAIL | ✗ |

## Stage 4 — ASC 101 Pilot

```
ASC 101 pilot — 10 dates  cadence median 6.0d  (2026-09-28 00:00:00 → 2026-08-05 00:00:00)
North WL tanks screened: 4  pass step<2.5: 3/4
  rt_5        step_asc=0.420    step_desc=1.190    ✓
  rt_6        step_asc=0.488    step_desc=0.538    ✓
  rt_7        step_asc=2.574    step_desc=1.938    ✗
  rt_8        step_asc=1.718    step_desc=1.431    ✓

```

## Final Verdict

**3 combination(s) promising (p<0.10, sign preserved) but below Bonferroni threshold.** Directional only.