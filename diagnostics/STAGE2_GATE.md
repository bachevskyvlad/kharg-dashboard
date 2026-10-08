# Stage 2 Gate Questionnaire — Calibration Run

Run: 2026-10-08 11:55 UTC  
Gate passed: False  

## Results

| Terminal | Expected | Actual | Pass |
|---|---|---|---|
| kharg | ACCUMULATOR | FLOW-THROUGH | ✗ |
| yanbu | PIPELINE-BUFFER | PIPELINE-BUFFER | ✓ |
| ras_tanura | FLOW-THROUGH | FLOW-THROUGH | ✓ |

## Metrics

| Terminal | Metric | Value |
|---|---|---|
| kharg | n_weeks | 310 |
| kharg | vol_mean_mn | 18.76 |
| kharg | vol_std_mn | 1.92 |
| kharg | cap_util_mean | 0.565 |
| kharg | cap_util_std | 0.058 |
| kharg | n_drawdown_events | 97 |
| kharg | cv_drawdown | 0.489 |
| kharg | autocorr_lag1 | -0.374 |
| kharg | cycle_days_median | 21.0 |
| kharg | stuck_proxy | 0.003 |
| yanbu | n_weeks | 326 |
| yanbu | vol_mean_mn | 9.19 |
| yanbu | vol_std_mn | 1.48 |
| yanbu | cap_util_mean | 1.313 |
| yanbu | cap_util_std | 0.212 |
| yanbu | n_drawdown_events | 113 |
| yanbu | cv_drawdown | 0.496 |
| yanbu | autocorr_lag1 | -0.402 |
| yanbu | cycle_days_median | 14.0 |
| yanbu | stuck_proxy | 0.003 |
| ras_tanura | n_weeks | 404 |
| ras_tanura | vol_mean_mn | 17.09 |
| ras_tanura | vol_std_mn | 1.0 |
| ras_tanura | cap_util_mean | 1.627 |
| ras_tanura | cap_util_std | 0.095 |
| ras_tanura | n_drawdown_events | 130 |
| ras_tanura | cv_drawdown | 0.455 |
| ras_tanura | autocorr_lag1 | -0.488 |
| ras_tanura | cycle_days_median | 21.0 |
| ras_tanura | stuck_proxy | 0.002 |

## Thresholds

| Threshold | Value |
|---|---|
| cv_drawdown_accumulator_min | 0.8 |
| cv_drawdown_flow_through_max | 0.55 |
| autocorr_accumulator_min | 0.35 |
| autocorr_pipeline_max | 0.25 |
| cycle_accumulator_min_days | 28 |
| cycle_flow_through_max_days | 25 |
| cycle_pipeline_max_days | 14 |
| cap_util_std_accumulator_min | 0.06 |
| cap_util_std_pipeline_max | 0.03 |
| stuck_ratio_accumulator_max | 0.15 |
| stuck_ratio_pipeline_max | 0.05 |

## Action required

Gate failed. Adjust thresholds in `THRESHOLDS` dict before proceeding to new terminals.