# Jask Center Gate A/B

Run: 2026-10-08 21:48 UTC

**Config A**: original eye-placed centers + ESRI r_fit
**Config B**: ESRI-measured centers (32-ray circle fit) + ESRI r_fit

**Gate criterion (fixed before running)**:
- step_db = median |Δdb_mean| over consecutive observations per tank
- median_B > median_A by ≥15% → B wins
- median_A > median_B by ≥15% → A wins
- |diff| < 15% → B wins (tiebreak: documented measurement beats eyeball)
- Per-tank: if winning config step >20% worse than losing → individual override

## Results

| Tank | step_A | step_B | Δ% | winner |
|---|---|---|---|---|
| jk_0 | 0.998 | 0.919 | -7.9% | **B** |
| jk_1 | 0.644 | 0.567 | -11.8% | **B** |
| jk_10 | 0.828 | 0.781 | -5.6% | **B** |
| jk_11 | 0.993 | 0.828 | -16.6% | **B** |
| jk_12 | 0.588 | 0.588 | +0.0% | **B** |
| jk_13 | 0.916 | 0.752 | -17.9% | **B** |
| jk_14 | 0.879 | 0.876 | -0.4% | **B** |
| jk_15 | 1.371 | 0.881 | -35.7% | **A** |
| jk_16 | 0.683 | 0.745 | +9.1% | **B** |
| jk_17 | 0.492 | 0.596 | +21.2% | **B** |
| jk_18 | 0.726 | 0.672 | -7.4% | **B** |
| jk_19 | 0.857 | 0.471 | -45.1% | **A** |
| jk_2 | 0.952 | 0.976 | +2.6% | **B** |
| jk_3 | 0.869 | 0.906 | +4.3% | **B** |
| jk_4 | 0.538 | 0.39 | -27.4% | **A** |
| jk_5 | 1.015 | 0.394 | -61.1% | **A** |
| jk_6 | 0.568 | 0.776 | +36.8% | **B** |
| jk_7 | 0.525 | 0.581 | +10.7% | **B** |
| jk_8 | 1.166 | 0.893 | -23.4% | **A** |
| jk_9 | 0.641 | 0.513 | -20.0% | **B** |

| **MEDIAN** | **0.843** | **0.749** | **-11.1%** | |

## Verdict

**TIE (diff=-11.1% < 15%) — B wins tiebreak (measured center beats eyeball per protocol)**

### Per-tank overrides

- jk_4: override → A (step_B=0.390 vs step_A=0.538)
- jk_5: override → A (step_B=0.394 vs step_A=1.015)
- jk_8: override → A (step_B=0.893 vs step_A=1.166)
- jk_15: override → A (step_B=0.881 vs step_A=1.371)
- jk_19: override → A (step_B=0.471 vs step_A=0.857)

Overall winner: **Config B**

Registry action: see TERMINAL_PASSPORT_jask.md for final center_source per tank.