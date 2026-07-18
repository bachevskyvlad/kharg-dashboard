# Kharg Per-Tank Interpretation Layer — Findings

## Step 1 — Data
- Rows: 7,708 | tanks: 47 | dates: 164 (2022-01-11 → 2026-07-14)
- Global clip: dB[0.17, 14.53] (5/95 pctl)

## Step 2 — Clusters
Type counts: swing=31, loading=12, idle=2, reserve=2
Geo counts: south=16, north=16, central=15

- **reserve** (2): t55_31, t60_1
- **loading** (12): t25_35, t25_43, t40_22, t40_26, t40_30, t40_34, t55_15, t55_17, t55_6, t55_8, t60_2, t60_4
- **idle** (2): t55_10, t55_5
- **swing** (31): t25_36, t25_37, t25_38, t25_39, t25_40, t25_41, t25_42, t25_44, t25_45, t25_46, t40_23, t40_24, t40_25, t40_27, t40_28, t40_29, t40_32, t40_33, t55_11, t55_12, t55_13, t55_14, t55_16, t55_18, t55_19, t55_20, t55_21, t55_7, t55_9, t60_0, t60_3

## Step 3 — Rotation episodes (one cluster draws, another fills, terminal ~flat)
- Terminal daily-move std ≈ 1.89 Mn bbl; flat<0.4, cluster move>0.3
- Episodes found: 19

- Of 19 episodes, 8 were followed by a terminal drawdown >0.5 Mn bbl within ~14d (42%).

| date | terminal Δ | drawer | filler | tot Δ7d | tot Δ14d |
|---|---|---|---|---|---|
| 2022-02-04 | -0.24 | north-1.09 | south+0.65 | 1.462 | 1.462 |
| 2022-06-04 | +0.04 | south-0.45;central-0.70 | north+1.18 | -4.316 | -4.316 |
| 2022-08-27 | +0.24 | central-0.37 | north+0.46 | -1.566 | -1.566 |
| 2022-11-19 | -0.17 | north-0.35 | central+0.38 | -2.536 | -2.536 |
| 2023-03-31 | -0.24 | south-0.47 | north+0.36 | 1.539 | 1.539 |
| 2023-06-23 | +0.02 | south-0.90 | central+0.63 | 2.74 | 2.74 |
| 2023-08-10 | -0.06 | central-0.34 | north+0.52 | 0.882 | 0.882 |
| 2023-11-26 | +0.39 | south-1.08;central-0.60 | north+2.08 | -0.226 | -0.226 |
| 2023-12-08 | -0.23 | north-1.84 | south+0.58;central+1.03 | -1.298 | -1.298 |
| 2024-05-12 | +0.05 | north-0.74 | south+0.75 | -2.496 | -2.496 |
| 2024-08-04 | +0.32 | central-0.56 | south+1.07 | 2.133 | 2.133 |
| 2024-12-14 | -0.20 | south-0.31;north-0.70 | central+0.81 | 0.072 | 0.072 |
| 2024-12-26 | +0.07 | central-0.81 | north+0.87 | 1.621 | 1.621 |
| 2025-04-13 | +0.24 | south-0.57;north-0.43 | central+1.24 | 1.947 | 1.947 |
| 2025-05-19 | +0.26 | south-0.52 | central+0.81 | -1.521 | -1.521 |
| 2025-12-27 | -0.03 | north-1.02;central-0.62 | south+1.61 | -1.358 | -2.073 |
| 2026-05-08 | -0.32 | south-0.43 | north+0.36 | 2.684 | 2.124 |
| 2026-06-01 | +0.29 | north-0.79 | south+1.14 | 0.0 | 0.978 |
| 2026-07-02 | +0.24 | south-0.71 | north+1.09 | -1.855 | -1.238 |

## Step 5 — Cross-check
- Export-pulse events (≥2 Mn bbl drop in 6–12d): 28
  - 2022-05-11 → 2022-05-23: −3.01 Mn bbl (12d)
  - 2022-06-04 → 2022-06-16: −4.32 Mn bbl (12d)
  - 2022-09-20 → 2022-10-02: −2.16 Mn bbl (12d)
  - 2022-10-26 → 2022-11-07: −3.09 Mn bbl (12d)
  - 2022-11-19 → 2022-12-01: −2.54 Mn bbl (12d)
  - 2023-01-18 → 2023-01-30: −4.95 Mn bbl (12d)
  - 2023-03-07 → 2023-03-19: −2.58 Mn bbl (12d)
  - 2023-05-30 → 2023-06-11: −2.64 Mn bbl (12d)
  - 2023-09-27 → 2023-10-09: −2.11 Mn bbl (12d)
  - 2024-02-18 → 2024-03-01: −2.89 Mn bbl (12d)
  - 2024-05-12 → 2024-05-24: −2.5 Mn bbl (12d)
  - 2024-06-29 → 2024-07-11: −2.8 Mn bbl (12d)
  - 2024-10-03 → 2024-10-15: −3.08 Mn bbl (12d)
  - 2025-01-07 → 2025-01-19: −2.63 Mn bbl (12d)
  - 2025-09-10 → 2025-09-22: −3.03 Mn bbl (12d)
  - 2025-10-10 → 2025-10-16: −2.34 Mn bbl (6d)
  - 2025-11-09 → 2025-11-15: −2.47 Mn bbl (6d)
  - 2025-11-09 → 2025-11-21: −2.01 Mn bbl (12d)
  - 2025-12-27 → 2026-01-08: −2.07 Mn bbl (12d)
  - 2026-02-13 → 2026-02-19: −5.75 Mn bbl (6d)
- Full external overlay (tanker AIS / EIA / news) requires those feeds; flagged for manual overlay.

## Step 6 — Key questions
- **Tanks fully stalling** (≥12d gap with <3% fill change): most frequent — t55_5(75), t40_25(40), t55_31(40), t55_19(31), t60_1(27), t55_10(26)
- **Always full (reserve)**: t55_31(81%), t60_1(80%)
- **Always working (loading)**: t60_2(σ36), t25_43(σ35), t55_17(σ34), t40_30(σ34), t55_15(σ32), t55_8(σ30), t25_35(σ29), t40_26(σ29), t40_22(σ29), t40_34(σ29), t60_4(σ29), t55_6(σ28)
- **Leader tanks** (move ~1 pass before the terminal, corr>0.3): none clearly ahead of aggregate
- **Cyclicality**: assessed per active tank via fill oscillation (see tank_stats.csv std_fill).

## Step 7 — Signal dictionary (pattern → interpretation → confirmations)

| # | Pattern | Interpretation | Confirmed on |
|---|---|---|---|
| 1 | Loading cluster (t55_17, t60_2) fill drops >5% pass-on-pass | Active VLCC offtake underway | 133 pass-events |
| 2 | Terminal drop ≥2 Mn bbl in 6–12d | Export pulse (probable tanker loading) | 28 events |
| 3 | Reserve tanks (t55_31, t60_1) flat & >85% | Strategic hold, not in rotation | 0/2 tanks stable |
| 4 | Idle tanks (t55_10, t55_5) mean fill <20% for full period | Offline / out of service | 2/2 confirmed |
| 5 | Cluster rotation (one draws, one fills, terminal flat) | Internal repositioning ahead of offtake | 19 episodes |
| 6 | Leader tank moves before aggregate | Early tell for terminal direction | 0 candidate leaders |
| 7 | Tank stall ≥12d no change | Tank parked / filled-and-held | 47 tanks show ≥1 stall |

_Generated by analysis/kharg_patterns/analyze_patterns.py_
DONE
