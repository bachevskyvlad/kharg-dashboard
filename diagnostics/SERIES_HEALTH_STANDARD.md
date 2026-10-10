# SERIES_HEALTH_STANDARD

Reference health passport from Kharg (Iran) and Yanbu (Saudi Arabia).
Corridors = union of both references × [0.5, 2.0].
**These corridors are fixed. Do not adjust after numbers are known.**

Generated: 2026-10-10

## Reference Coverage

| Terminal | Dates | Tanks | Date range | dynamic_range_db |
|---|---|---|---|---|
| Kharg | 321 | 47 | 2015-04-06 → 2026-10-06 | 4.40 dB |
| Yanbu | 249 | 12 | 2014-10-09 → 2026-09-22 | 6.36 dB |

## Normalization Contract

| Metric key | Raw formula | Raw unit | Normalization | Normalized |
|---|---|---|---|---|
| dynamic_range_db | p95(mean_db) − p5(mean_db) | dB | (base — kept absolute) | dB |
| cv_level | σ(mean_db) / μ(mean_db) | dimensionless | (self) | dimensionless |
| vel_std_norm | std(Δmean_db) / dynamic_range_db | dB/pass | ÷ dynamic_range_db | fraction |
| delta_p50_norm | median(abs(Δmean_db)) / dynamic_range_db | dB | ÷ dynamic_range_db | fraction |
| delta_p90_norm | p90(abs(Δmean_db)) / dynamic_range_db | dB | ÷ dynamic_range_db | fraction |
| delta_p99_norm | p99(abs(Δmean_db)) / dynamic_range_db | dB | ÷ dynamic_range_db | fraction |
| cycles_per_year | zero-crossings/2 / span_yr | 1/yr | (self) | 1/yr |
| cycle_depth_norm | median_peak(|demeaned_30d_ma|) / dynamic_range_db | dB | ÷ dynamic_range_db | fraction |
| max_flat_passes | longest run with |Δ| < 1% dynamic_range | passes | (self) | passes |
| per_tank_step_norm | median(per-tank median|Δdb|) / dynamic_range_db | dB | ÷ dynamic_range_db | fraction |
| live_fraction | frac(tanks: per-tank-step ≥ 5% dynamic_range) | [0,1] | (threshold normalized) | [0,1] |

Notes:
- dynamic_range_db is NOT in the corridor comparison; it is the normalization base.
  A near-zero dynamic_range invalidates all derived metrics; checked separately in gate.
- Flat threshold: |Δ| < 0.01 × dynamic_range_db (1% of range), not a fixed dB.
- Live threshold: per-tank step ≥ 0.05 × dynamic_range_db (5% of range), not fixed 0.5 dB.
- All normalized metrics are ∈ [0, ∞) but practically ∈ [0, 1] for healthy series.
- No Mb metrics. Cross-terminal validity: normalization removes tank-size and orbit-geometry
  dependence from dB delta metrics.

## Standard Metric Corridors

| Metric | Kharg | Yanbu | Corridor lo | Corridor hi |
|---|---|---|---|---|
| CV of level (σ/μ mean_db) | 0.1960 | 0.3179 | 0.0980 | 0.6358 |
| Velocity std (norm by dr) | 0.1555 | 0.1495 | 0.0748 | 0.3110 |
| Δ p50 (norm by dr) | 0.1581 | 0.1734 | 0.0790 | 0.3468 |
| Δ p90 (norm by dr) | 0.3908 | 0.3977 | 0.1954 | 0.7954 |
| Δ p99 (norm by dr) | 0.7331 | 0.5868 | 0.2934 | 1.4662 |
| Build-draw cycles/year | 1.83 | 1.34 | 0.67 | 3.65 |
| Cycle depth (norm by dr) | 0.1115 | 0.1267 | 0.0558 | 0.2533 |
| Max flat segment (passes) | 1 | 1 | 0 | 2 |
| Per-tank step (norm by dr) | 0.5433 | 0.2185 | 0.1093 | 1.0866 |
| Live fraction (step≥5% dr) | 1.000 | 1.000 | 0.500 | 2.000 |

## Diagnostic Thresholds

**DEAD** — series is flat/inactive; ≥2 of the following below corridor lo:
- `delta_p90_norm < lo`  (no velocity)
- `live_fraction < lo`   (most tanks flat)
- (dynamic_range_db < 0.5 dB is an unconditional DEAD regardless)

**WILD** — series has extreme noise/breaks; any of the following above corridor hi:
- `delta_p99_norm > hi`  (extreme single-pass jumps)
- `vel_std_norm > hi`    (velocity noise above corridor)

**PASS** — all metrics within corridors.

## Self-Validation Gate

Run before applying to any new terminal:
- **RT-DESC** (`--terminal ras_tanura --orbit 35`): expect PASS
- **alfaw_stuck7** (`--terminal al_flaw --tanks af_0,af_2,af_3,af_6,af_7,af_9,af_11`): expect FLAG/DEAD

PASS + DEAD → instrument valid. Else fix health-check before proceeding.

### Note: RT-ASC orbit 101 excluded from gate

RT-ASC orbit 101 passes health-check (dynamic_range=2.5dB, cycles=3/yr,
live_fraction=1.0) and is therefore alive. It is excluded from the gate because
it is E1-blind (decoupled from storage events). This is the canonical example
that **health-check ≠ informativeness**: a series can be alive and still carry
no storage signal. Informativeness is verified only at step 7 (event-response),
not here. Using RT-ASC as a dead control would be a type error.

## Notes

- Kharg is READ-ONLY (calibration/reference only).
- Corridors fixed at generation time. No post-hoc adjustment.
- FLAG always reported with metric value and corridor numbers.
- «вероятно» prohibited in all pipeline output.
