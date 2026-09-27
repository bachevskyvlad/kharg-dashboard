# Round 7.7 — Well-depth physics test + FCS criterion


## Task 1 — Spatial sector test (well-depth hypothesis)
Prediction: empty tanks (t55_5, t55_10) brighter than full tanks (t60_1, t40_33) in ALL 24 sectors by ≥6 dB.

### Scene 2026-09-24 — FAIL
| Az° | Δ dB (empty−full) |
|-----|------------------|
|   0 | -7.83 |
|  15 | -7.43 |
|  30 | -6.52 |
|  45 | -5.32 |
|  60 | -4.00 |
|  75 | -3.84 |
|  90 | -4.54 |
| 105 | -4.74 |
| 120 | -4.61 |
| 135 | -4.18 |
| 150 | -3.51 |
| 165 | -2.96 |
| 180 | -2.18 |
| 195 | -2.65 |
| 210 | -3.48 |
| 225 | -4.25 |
| 240 | -5.48 |
| 255 | -6.51 |
| 270 | -6.47 |
| 285 | -6.14 |
| 300 | -6.10 |
| 315 | -6.22 |
| 330 | -6.71 |
| 345 | -7.26 |

Min Δ=-7.83 dB  Max Δ=-2.18 dB  Mean Δ=-5.12 dB

### Scene 2026-09-18 — FAIL
| Az° | Δ dB (empty−full) |
|-----|------------------|
|   0 | -8.04 |
|  15 | -7.30 |
|  30 | -6.26 |
|  45 | -4.95 |
|  60 | -3.47 |
|  75 | -3.04 |
|  90 | -3.43 |
| 105 | -3.30 |
| 120 | -3.11 |
| 135 | -2.78 |
| 150 | -2.23 |
| 165 | -1.81 |
| 180 | -1.24 |
| 195 | -2.26 |
| 210 | -3.21 |
| 225 | -3.94 |
| 240 | -5.19 |
| 255 | -6.50 |
| 270 | -6.88 |
| 285 | -6.80 |
| 300 | -6.78 |
| 315 | -6.89 |
| 330 | -7.34 |
| 345 | -7.69 |

Min Δ=-8.04 dB  Max Δ=-1.24 dB  Mean Δ=-4.77 dB

### Scene 2026-09-12 — FAIL
| Az° | Δ dB (empty−full) |
|-----|------------------|
|   0 | -7.70 |
|  15 | -6.82 |
|  30 | -5.38 |
|  45 | -3.76 |
|  60 | -2.01 |
|  75 | -1.21 |
|  90 | -1.51 |
| 105 | -1.47 |
| 120 | -1.14 |
| 135 | -0.83 |
| 150 | -0.32 |
| 165 | +0.12 |
| 180 | +0.84 |
| 195 | -0.49 |
| 210 | -1.64 |
| 225 | -2.41 |
| 240 | -3.46 |
| 255 | -5.18 |
| 270 | -6.09 |
| 285 | -6.12 |
| 300 | -6.24 |
| 315 | -6.45 |
| 330 | -6.96 |
| 345 | -7.38 |

Min Δ=-7.70 dB  Max Δ=+0.84 dB  Mean Δ=-3.48 dB

### Scene 2026-09-06 — FAIL
| Az° | Δ dB (empty−full) |
|-----|------------------|
|   0 | -6.95 |
|  15 | -6.82 |
|  30 | -6.07 |
|  45 | -4.09 |
|  60 | -2.10 |
|  75 | -1.48 |
|  90 | -1.86 |
| 105 | -1.93 |
| 120 | -1.64 |
| 135 | -1.17 |
| 150 | -0.69 |
| 165 | -0.29 |
| 180 | +0.35 |
| 195 | +0.06 |
| 210 | -0.72 |
| 225 | -1.60 |
| 240 | -3.01 |
| 255 | -4.70 |
| 270 | -5.25 |
| 285 | -5.13 |
| 300 | -5.24 |
| 315 | -5.56 |
| 330 | -6.05 |
| 345 | -6.48 |

Min Δ=-6.95 dB  Max Δ=+0.35 dB  Mean Δ=-3.27 dB

**Task 1 final: FAIL** (0/4 scenes pass all-sector Δ ≥ 6 dB criterion)
[FINDING] Well-depth spatial prediction NOT CONFIRMED — empty tanks are NOT uniformly brighter than full tanks in all sectors.

## Task 2 — Temporal rotation test (cycling tanks)
Tanks: t60_2 (std=6.16 dB), t55_17 (std=5.56 dB)
Masks: prod (282°), full ring, 90°-rotated (12°), 180°-rotated (102°)
Prediction if well-depth: all mask correlations ≥0.8 with CSV db_mean

### t60_2
[FINDING] Only 0 matched scenes — insufficient for correlation

### t55_17
[FINDING] Only 0 matched scenes — insufficient for correlation

## Task 3 — FCS calibration (Kharg CSV + RT backup CSV)
FCS = (p95 − p05 temporal dB per tank) / median intra-scene ring noise
Kharg median intra-scene noise (std across tanks per date) = 4.250 dB

### Kharg FCS (top 10)

| tank_id | N dates | p05 dB | p95 dB | range dB | FCS |
|---------|---------|--------|--------|----------|-----|
| t60_2 | 310 | -1.50 | 16.84 | 18.34 | 4.32 |
| t55_17 | 310 | -1.10 | 16.69 | 17.79 | 4.19 |
| t25_39 | 310 | 3.50 | 19.22 | 15.72 | 3.70 |
| t40_30 | 310 | -1.80 | 13.84 | 15.64 | 3.68 |
| t40_32 | 310 | -3.90 | 11.24 | 15.14 | 3.56 |
| t25_41 | 310 | -0.04 | 14.91 | 14.95 | 3.52 |
| t55_15 | 310 | 0.84 | 15.56 | 14.72 | 3.46 |
| t55_6 | 310 | -0.49 | 13.86 | 14.35 | 3.38 |
| t55_8 | 310 | -1.84 | 12.24 | 14.08 | 3.31 |
| t25_35 | 310 | 2.42 | 16.16 | 13.74 | 3.23 |

Calibrated FCS threshold = **3.45** (median + 1σ of Kharg distribution)
Kharg: 7/47 tanks above threshold

### RT validation (backup CSV, scene noise = 5.103 dB)

| tank_id | N dates | range dB | FCS |
|---------|---------|----------|-----|
| rt_51 | 315 | 19.69 | 3.86 (ABOVE) |
| rt_57 | 320 | 19.65 | 3.85 (ABOVE) |
| rt_48 | 315 | 19.40 | 3.80 (ABOVE) |
| rt_49 | 315 | 18.67 | 3.66 (ABOVE) |
| rt_40 | 315 | 18.52 | 3.63 (ABOVE) |
| rt_55 | 335 | 18.46 | 3.62 (ABOVE) |
| rt_47 | 315 | 18.04 | 3.53 (ABOVE) |
| rt_52 | 315 | 18.03 | 3.53 (ABOVE) |
| rt_46 | 315 | 17.94 | 3.52 (ABOVE) |
| rt_10 | 315 | 17.57 | 3.44 (below) |

RT: 9/66 tanks above threshold (Kharg-calibrated threshold=3.45)

## Task 4 — JW/YB FCS from Supabase terminal_observations
[FINDING] juwaymah: Supabase unavailable (403 or no data). Task 4 juwaymah INSUFFICIENT DATA.
[FINDING] yanbu: Supabase unavailable (403 or no data). Task 4 yanbu INSUFFICIENT DATA.

## Task 5 — Verdicts

### Well-depth hypothesis assessment

| Site | Task | Verdict |
|------|------|---------|
| Kharg | T1 spatial (empty vs full, all sectors) | FAIL |
| Kharg | T2 temporal (all-rotation correlation ≥0.8) | see T2 table |
| Kharg | T3 FCS calibration | see T3 histogram |
| RT    | T3 FCS validation | see T3 histogram |
| JW    | T4 FCS from Supabase | see T4 table |
| YB    | T4 FCS from Supabase (post-pivot only) | see T4 table |

### Well-depth implications for prod code

If Task 1 PASS: well-depth hypothesis supported — full/empty tank dB contrast is visible across all azimuths, not only in the shadow direction. This means the prod crescent mask direction (282°) is NOT the sole cause of the dB fill-proxy signal. Future A/B test (not in this round): compare vol estimates from prod mask vs full-ring mask on historical data.

If Task 1 FAIL: well-depth hypothesis NOT supported — the dB contrast is azimuth-dependent. Prod crescent mask direction remains the critical geometric choice; JW/YB orbit geometry must be verified before applying the pipeline to those sites.

No prod code changes in this round. Comments above are hypotheses for future investigation only.

---
## Stop-line

Round 7.7 diagnostic complete. All findings are observational only. No prod code was modified. No Supabase writes were made. No paid API calls were made. FCS threshold is calibrated on Kharg data only and must be re-validated on live Sentinel Hub imagery before any operational use. JW/YB verdicts depend on Supabase availability in GHA; if Task 4 shows INSUFFICIENT DATA, re-run with valid secrets.
