# RT Decomposition — Report

Run: 2026-09-29 22:19 UTC

## Registration
- Train: 2020-01 → 2023-12
- Holdout: 2024-01 → 2026-02 (2026-03+ conflict excluded)
- Sub-series: 5 (frozen before calculation)
- Combinations: 40  Bonferroni α = 0.05/40 = 0.00125

## Calibration
- dB_min (global 5th pct) = -1.553
- dB_max (global 95th pct) = 13.741
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

| Event | Date | Expected | Sub-series | Δ (Mb) |
|---|---|---|---|---|
| E1: Attack/Halt | 2026-03-02 | BUILD | south_pier | -0.052 |
| E1: Attack/Halt | 2026-03-02 | BUILD | south_inland | +0.889 |
| E1: Attack/Halt | 2026-03-02 | BUILD | north | +1.232 |
| E1: Attack/Halt | 2026-03-02 | BUILD | t60_class | +1.505 |
| E1: Attack/Halt | 2026-03-02 | BUILD | active10 | +0.989 |
| E2: Ops resumed | 2026-06-26 | DRAW | south_pier | -0.032 |
| E2: Ops resumed | 2026-06-26 | DRAW | south_inland | -0.551 |
| E2: Ops resumed | 2026-06-26 | DRAW | north | -1.394 |
| E2: Ops resumed | 2026-06-26 | DRAW | t60_class | -1.429 |
| E2: Ops resumed | 2026-06-26 | DRAW | active10 | -1.527 |
| E3: Ramp ~90% | 2026-07-15 | DRAW | south_pier | +0.294 |
| E3: Ramp ~90% | 2026-07-15 | DRAW | south_inland | +0.249 |
| E3: Ramp ~90% | 2026-07-15 | DRAW | north | -1.230 |
| E3: Ramp ~90% | 2026-07-15 | DRAW | t60_class | -1.020 |
| E3: Ramp ~90% | 2026-07-15 | DRAW | active10 | -1.146 |

## Stage 3 — Correlation Grid (train 2020-01 → 2023-12)

Bonferroni α = 0.00125

| Sub-series | Anchor | Metric | Lag | r_train | p | n | Verdict |
|---|---|---|---|---|---|---|---|
| south_pier | jodi_saudi | delta | +0 | -0.0245 | 0.8701 | 47 | NULL |
| south_pier | jodi_saudi | delta | +1 | 0.1749 | 0.2451 | 46 | NULL |
| south_pier | jodi_saudi | level_anom | +0 | -0.2208 | 0.1547 | 43 | NULL |
| south_pier | jodi_saudi | level_anom | +1 | -0.1989 | 0.2067 | 42 | NULL |
| south_pier | rtju_mbpd | delta | +0 | n/a | n/a | 0 | NO DATA |
| south_pier | rtju_mbpd | delta | +1 | n/a | n/a | 0 | NO DATA |
| south_pier | rtju_mbpd | level_anom | +0 | n/a | n/a | 0 | NO DATA |
| south_pier | rtju_mbpd | level_anom | +1 | n/a | n/a | 0 | NO DATA |
| south_inland | jodi_saudi | delta | +0 | 0.0301 | 0.8408 | 47 | NULL |
| south_inland | jodi_saudi | delta | +1 | -0.0178 | 0.9064 | 46 | NULL |
| south_inland | jodi_saudi | level_anom | +0 | -0.2744 | 0.0750 | 43 | NULL |
| south_inland | jodi_saudi | level_anom | +1 | -0.3132 | 0.0434 | 42 | PROMISING |
| south_inland | rtju_mbpd | delta | +0 | n/a | n/a | 0 | NO DATA |
| south_inland | rtju_mbpd | delta | +1 | n/a | n/a | 0 | NO DATA |
| south_inland | rtju_mbpd | level_anom | +0 | n/a | n/a | 0 | NO DATA |
| south_inland | rtju_mbpd | level_anom | +1 | n/a | n/a | 0 | NO DATA |
| north | jodi_saudi | delta | +0 | -0.1065 | 0.4763 | 47 | NULL |
| north | jodi_saudi | delta | +1 | 0.1468 | 0.3302 | 46 | NULL |
| north | jodi_saudi | level_anom | +0 | 0.2771 | 0.0720 | 43 | NULL |
| north | jodi_saudi | level_anom | +1 | 0.3658 | 0.0172 | 42 | NULL |
| north | rtju_mbpd | delta | +0 | n/a | n/a | 0 | NO DATA |
| north | rtju_mbpd | delta | +1 | n/a | n/a | 0 | NO DATA |
| north | rtju_mbpd | level_anom | +0 | n/a | n/a | 0 | NO DATA |
| north | rtju_mbpd | level_anom | +1 | n/a | n/a | 0 | NO DATA |
| t60_class | jodi_saudi | delta | +0 | -0.1098 | 0.4624 | 47 | NULL |
| t60_class | jodi_saudi | delta | +1 | 0.1385 | 0.3586 | 46 | NULL |
| t60_class | jodi_saudi | level_anom | +0 | 0.0844 | 0.5906 | 43 | NULL |
| t60_class | jodi_saudi | level_anom | +1 | 0.1729 | 0.2735 | 42 | NULL |
| t60_class | rtju_mbpd | delta | +0 | n/a | n/a | 0 | NO DATA |
| t60_class | rtju_mbpd | delta | +1 | n/a | n/a | 0 | NO DATA |
| t60_class | rtju_mbpd | level_anom | +0 | n/a | n/a | 0 | NO DATA |
| t60_class | rtju_mbpd | level_anom | +1 | n/a | n/a | 0 | NO DATA |
| active10 | jodi_saudi | delta | +0 | 0.2784 | 0.0582 | 47 | NULL |
| active10 | jodi_saudi | delta | +1 | -0.0682 | 0.6523 | 46 | NULL |
| active10 | jodi_saudi | level_anom | +0 | 0.1253 | 0.4233 | 43 | NULL |
| active10 | jodi_saudi | level_anom | +1 | -0.0475 | 0.7650 | 42 | NULL |
| active10 | rtju_mbpd | delta | +0 | n/a | n/a | 0 | NO DATA |
| active10 | rtju_mbpd | delta | +1 | n/a | n/a | 0 | NO DATA |
| active10 | rtju_mbpd | level_anom | +0 | n/a | n/a | 0 | NO DATA |
| active10 | rtju_mbpd | level_anom | +1 | n/a | n/a | 0 | NO DATA |

## Stage 3 — Holdout (2024-01 → 2026-02)

Bonferroni PASS: 0  |  Promising (p<0.10, sign preserved): 2

| Sub-series | Anchor | Metric | Lag | r_train | r_holdout | p_holdout | Bonf | Sign |
|---|---|---|---|---|---|---|---|---|
| south_pier | jodi_saudi | delta | +0 | -0.0245 | 0.0719 | 0.7327 | FAIL | ✗ |
| south_pier | jodi_saudi | delta | +1 | 0.1749 | 0.2165 | 0.3095 | FAIL | ✓ |
| south_pier | jodi_saudi | level_anom | +0 | -0.2208 | 0.0750 | 0.7466 | FAIL | ✗ |
| south_pier | jodi_saudi | level_anom | +1 | -0.1989 | 0.1123 | 0.6373 | FAIL | ✗ |
| south_inland | jodi_saudi | delta | +0 | 0.0301 | 0.2327 | 0.2630 | FAIL | ✓ |
| south_inland | jodi_saudi | delta | +1 | -0.0178 | -0.1069 | 0.6190 | FAIL | ✓ |
| south_inland | jodi_saudi | level_anom | +0 | -0.2744 | -0.1626 | 0.4813 | FAIL | ✓ |
| south_inland | jodi_saudi | level_anom | +1 | -0.3132 | -0.3372 | 0.1460 | FAIL | ✓ |
| north | jodi_saudi | delta | +0 | -0.1065 | -0.0050 | 0.9812 | FAIL | ✓ |
| north | jodi_saudi | delta | +1 | 0.1468 | 0.0730 | 0.7346 | FAIL | ✓ |
| north | jodi_saudi | level_anom | +0 | 0.2771 | 0.4286 | 0.0525 | FAIL | ✓ |
| north | jodi_saudi | level_anom | +1 | 0.3658 | 0.1779 | 0.4530 | FAIL | ✓ |
| t60_class | jodi_saudi | delta | +0 | -0.1098 | 0.1955 | 0.3490 | FAIL | ✗ |
| t60_class | jodi_saudi | delta | +1 | 0.1385 | 0.0551 | 0.7982 | FAIL | ✓ |
| t60_class | jodi_saudi | level_anom | +0 | 0.0844 | 0.3969 | 0.0748 | FAIL | ✓ |
| t60_class | jodi_saudi | level_anom | +1 | 0.1729 | 0.1139 | 0.6325 | FAIL | ✓ |
| active10 | jodi_saudi | delta | +0 | 0.2784 | -0.0742 | 0.7245 | FAIL | ✗ |
| active10 | jodi_saudi | delta | +1 | -0.0682 | 0.0483 | 0.8228 | FAIL | ✗ |
| active10 | jodi_saudi | level_anom | +0 | 0.1253 | 0.0081 | 0.9720 | FAIL | ✓ |
| active10 | jodi_saudi | level_anom | +1 | -0.0475 | 0.1340 | 0.5734 | FAIL | ✗ |

## Stage 4 — ASC 101 Pilot

```
Stage 4 skipped — CLIENT_ID/CLIENT_SECRET not set
```

## Final Verdict

**2 combination(s) promising (p<0.10, sign preserved) but below Bonferroni threshold.** Directional only.