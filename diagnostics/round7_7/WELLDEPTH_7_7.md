# Round 7.7 — Well-depth physics test + FCS criterion


## Task 1 — Spatial sector test (well-depth hypothesis)
Prediction: empty tanks (t55_5, t55_10) brighter than full tanks (t60_1, t40_33) in ALL 24 sectors by ≥6 dB.

### Sign-check — scene 2026-09-24: raw linear + dB per sector
Cross-reference: CSV global means t60_1=2.82 dB (full), t55_5=13.02 dB (empty/idle).
If sign is correct: prod-mask mean for t55_5 >> t60_1.

**t60_1** (full): prod-mask linear=1.41748, dB=1.52, N_px=99, CSV_mean=2.82 dB — OK (low dB as expected)

| Az° | linear mean | dB mean |
|-----|------------|--------|
|   0 | 1.45022 | 1.61 |
|  15 | 1.22199 | 0.87 |
|  30 | 0.92531 | -0.34 |
|  45 | 0.87914 | -0.56 |
|  60 | 0.87656 | -0.57 |
|  75 | 0.87939 | -0.56 |
|  90 | 0.85158 | -0.70 |
| 105 | 0.85016 | -0.70 |
| 120 | 0.83795 | -0.77 |
| 135 | 0.87736 | -0.57 |
| 150 | 0.89941 | -0.46 |
| 165 | 0.88439 | -0.53 |
| 180 | 0.81819 | -0.87 |
| 195 | 1.04697 | 0.20 |
| 210 | 1.34847 | 1.30 |
| 225 | 1.38990 | 1.43 |
| 240 | 1.39245 | 1.44 |
| 255 | 1.38964 | 1.43 |
| 270 | 1.42884 | 1.55 |
| 285 | 1.41858 | 1.52 |
| 300 | 1.43672 | 1.57 |
| 315 | 1.39166 | 1.44 |
| 330 | 1.36983 | 1.37 |
| 345 | 1.37970 | 1.40 |

**t55_5** (empty): prod-mask linear=10.69666, dB=10.29, N_px=88, CSV_mean=13.02 dB — OK (high dB as expected)

| Az° | linear mean | dB mean |
|-----|------------|--------|
|   0 | 10.56976 | 10.24 |
|  15 | 7.33260 | 8.65 |
|  30 | 2.54615 | 4.06 |
|  45 | 0.91954 | -0.36 |
|  60 | 0.81426 | -0.89 |
|  75 | 0.80099 | -0.96 |
|  90 | 0.80861 | -0.92 |
| 105 | 0.87778 | -0.57 |
| 120 | 0.96424 | -0.16 |
| 135 | 1.07581 | 0.32 |
| 150 | 1.19330 | 0.77 |
| 165 | 1.23191 | 0.91 |
| 180 | 1.30791 | 1.17 |
| 195 | 4.26419 | 6.30 |
| 210 | 8.94308 | 9.51 |
| 225 | 10.53314 | 10.23 |
| 240 | 10.86185 | 10.36 |
| 255 | 10.76093 | 10.32 |
| 270 | 11.10431 | 10.45 |
| 285 | 10.68415 | 10.29 |
| 300 | 10.59769 | 10.25 |
| 315 | 10.59429 | 10.25 |
| 330 | 10.47409 | 10.20 |
| 345 | 10.43459 | 10.18 |


### Scene 2026-09-24 — FAIL
| Az° | Δ dB (empty−full) |
|-----|------------------|
|   0 | +5.81 |
|  15 | +5.26 |
|  30 | +3.35 |
|  45 | +1.12 |
|  60 | +0.52 |
|  75 | +0.15 |
|  90 | +0.10 |
| 105 | +0.33 |
| 120 | +0.68 |
| 135 | +0.77 |
| 150 | +0.96 |
| 165 | +1.49 |
| 180 | +2.10 |
| 195 | +4.39 |
| 210 | +5.76 |
| 225 | +6.14 |
| 240 | +6.50 |
| 255 | +6.68 |
| 270 | +6.77 |
| 285 | +6.65 |
| 300 | +6.57 |
| 315 | +6.64 |
| 330 | +6.57 |
| 345 | +6.17 |

Min Δ=+0.10 dB  Max Δ=+6.77 dB  Mean Δ=+3.81 dB

### Scene 2026-09-18 — FAIL
| Az° | Δ dB (empty−full) |
|-----|------------------|
|   0 | +1.92 |
|  15 | +2.30 |
|  30 | +1.85 |
|  45 | +1.27 |
|  60 | +0.78 |
|  75 | +0.32 |
|  90 | +0.37 |
| 105 | +0.69 |
| 120 | +1.07 |
| 135 | +1.34 |
| 150 | +1.65 |
| 165 | +2.32 |
| 180 | +2.91 |
| 195 | +2.34 |
| 210 | +2.69 |
| 225 | +2.80 |
| 240 | +3.01 |
| 255 | +3.22 |
| 270 | +3.26 |
| 285 | +3.09 |
| 300 | +2.93 |
| 315 | +2.84 |
| 330 | +2.67 |
| 345 | +2.18 |

Min Δ=+0.32 dB  Max Δ=+3.26 dB  Mean Δ=+2.08 dB

### Scene 2026-09-12 — FAIL
| Az° | Δ dB (empty−full) |
|-----|------------------|
|   0 | +3.35 |
|  15 | +3.69 |
|  30 | +3.81 |
|  45 | +3.53 |
|  60 | +3.23 |
|  75 | +2.97 |
|  90 | +2.91 |
| 105 | +3.16 |
| 120 | +3.54 |
| 135 | +3.62 |
| 150 | +3.79 |
| 165 | +4.49 |
| 180 | +4.96 |
| 195 | +4.18 |
| 210 | +4.26 |
| 225 | +4.32 |
| 240 | +4.52 |
| 255 | +4.65 |
| 270 | +4.70 |
| 285 | +4.52 |
| 300 | +4.34 |
| 315 | +4.29 |
| 330 | +4.13 |
| 345 | +3.64 |

Min Δ=+2.91 dB  Max Δ=+4.96 dB  Mean Δ=+3.94 dB

### Scene 2026-09-06 — FAIL
| Az° | Δ dB (empty−full) |
|-----|------------------|
|   0 | +3.86 |
|  15 | +3.66 |
|  30 | +2.96 |
|  45 | +2.89 |
|  60 | +2.64 |
|  75 | +2.15 |
|  90 | +1.98 |
| 105 | +2.06 |
| 120 | +2.28 |
| 135 | +2.53 |
| 150 | +2.93 |
| 165 | +3.54 |
| 180 | +3.86 |
| 195 | +4.12 |
| 210 | +4.67 |
| 225 | +4.56 |
| 240 | +4.69 |
| 255 | +4.95 |
| 270 | +5.07 |
| 285 | +5.02 |
| 300 | +4.94 |
| 315 | +4.82 |
| 330 | +4.56 |
| 345 | +4.07 |

Min Δ=+1.98 dB  Max Δ=+5.07 dB  Mean Δ=+3.70 dB

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

**Worked example — t60_2:**
  numerator   = p95 − p05 = 16.837 − -1.503 = 18.341 dB
  denominator = median intra-scene noise = 4.250 dB
  FCS = 18.341 / 4.250 = **4.316**

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
