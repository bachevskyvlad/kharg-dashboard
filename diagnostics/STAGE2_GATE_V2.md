# Stage 2 Gate Questionnaire v2 — Calibration + Holdout

Run: 2026-10-08 14:38 UTC  
Gate passed: True  

## Scoring design

| Metric | Weight | Rule |
|---|---|---|
| dwell_days = vol_mean / throughput | 3 pts (primary) | ≥12d→ACC; <4d→FLOW; 4–12d→BUF |
| cycle_days_median | 2 pts | ≤14d→BUF |
| cv_level = std(V)/mean(V) | 1 pt | ≤0.07→FLOW |

Throughput source: EIA country briefs https://www.eia.gov/international/analysis/ ; IMF PortWatch https://portwatch.imf.org/

## Results

| Terminal | Expected | Actual | Pass |
|---|---|---|---|
| kharg | ACCUMULATOR | ACCUMULATOR | ✓ |
| yanbu | PIPELINE-BUFFER | PIPELINE-BUFFER | ✓ |
| ras_tanura | FLOW-THROUGH | FLOW-THROUGH | ✓ |
| al_flaw | non-ACCUMULATOR | FLOW-THROUGH | ✓ |

## Metrics

| Terminal | Metric | Value |
|---|---|---|
| kharg | n_weeks | 310 |
| kharg | vol_mean_mn | 18.76 |
| kharg | vol_std_mn | 1.92 |
| kharg | cv_level | 0.102 |
| kharg | dwell_days | 12.5 |
| kharg | throughput_mn_d | 1.5 |
| kharg | n_drawdown_events | 97 |
| kharg | cv_drawdown | 0.489 |
| kharg | autocorr_lag1 | -0.374 |
| kharg | cycle_days_median | 21.0 |
| kharg | stuck_proxy | 0.003 |
| yanbu | n_weeks | 326 |
| yanbu | vol_mean_mn | 9.19 |
| yanbu | vol_std_mn | 1.48 |
| yanbu | cv_level | 0.161 |
| yanbu | dwell_days | 7.1 |
| yanbu | throughput_mn_d | 1.3 |
| yanbu | n_drawdown_events | 113 |
| yanbu | cv_drawdown | 0.496 |
| yanbu | autocorr_lag1 | -0.402 |
| yanbu | cycle_days_median | 14.0 |
| yanbu | stuck_proxy | 0.003 |
| ras_tanura | n_weeks | 404 |
| ras_tanura | vol_mean_mn | 17.09 |
| ras_tanura | vol_std_mn | 1.0 |
| ras_tanura | cv_level | 0.059 |
| ras_tanura | dwell_days | 3.1 |
| ras_tanura | throughput_mn_d | 5.5 |
| ras_tanura | n_drawdown_events | 130 |
| ras_tanura | cv_drawdown | 0.455 |
| ras_tanura | autocorr_lag1 | -0.488 |
| ras_tanura | cycle_days_median | 21.0 |
| ras_tanura | stuck_proxy | 0.002 |
| al_flaw | n_weeks | 303 |
| al_flaw | vol_mean_mn | 4.65 |
| al_flaw | vol_std_mn | 1.02 |
| al_flaw | cv_level | 0.218 |
| al_flaw | dwell_days | 3.9 |
| al_flaw | throughput_mn_d | 1.2 |
| al_flaw | n_drawdown_events | 33 |
| al_flaw | cv_drawdown | 0.51 |
| al_flaw | autocorr_lag1 | -0.38 |
| al_flaw | cycle_days_median | 35.0 |
| al_flaw | stuck_proxy | 0.007 |

## Thresholds

| Threshold | Value |
|---|---|
| dwell_accumulator_min_days | 12.0 |
| dwell_flow_max_days | 4.0 |
| cycle_pipeline_max_days | 14.0 |
| cv_level_flow_max | 0.07 |