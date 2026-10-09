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

### Per-tank overrides — detail

Higher step_db = more signal variation = better crescent placement. Δ% = (step_B − step_A)/step_A. **Negative Δ% means B captures less variation than A → A is locally better → override to A.**

| Tank | step_A | step_B | Δ% | Interpretation |
|---|---|---|---|---|
| jk_4  | 0.538 | 0.390 | −27.4% | A captures 27% more signal variation. Genuine local inversion: ESRI center lands mask on wrong shadow side or straddling edge. Override to A correct. |
| jk_5  | 1.015 | 0.394 | −61.1% | A captures 2.6× more variation. ESRI shift of −3.4m E is largest in this cluster (northwest group). Override to A correct; difference is real, not gate noise. |
| jk_8  | 1.166 | 0.893 | −23.4% | jk_8 has r_fit=41.2m (enlarged tank). ESRI center shifts mask fractionally off-shadow. 23% gap is real. Override to A correct. |
| jk_15 | 1.371 | 0.881 | −35.7% | step_A 1.371 is highest in dataset; step_B 0.881 is substantial drop. ESRI center likely clips part of the far-shadow annulus. Override to A correct. |
| jk_19 | 0.857 | 0.471 | −45.1% | A nearly 2× B; largest inversion after jk_5. Override to A correct. |

**One-line verdict: Global B (tiebreak), 5 local A-overrides where B step is ≥23% lower than A. Overrides are signal differences, not gate noise — smallest gap is 23.4%, not single-digit.**

Overall winner: **Config B (15 tanks) + Config A (jk_4/5/8/15/19)**

Registry v2.1 is correct as committed. Re-ingest v2.1 completed 2026-10-09 08:43 UTC.