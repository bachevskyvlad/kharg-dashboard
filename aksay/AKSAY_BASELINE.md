# AKSAY_BASELINE — Ak-Sai glacier, Sentinel-2 2016–2026 (rev4)

R&D mini-project (`aksay/`), independent of OilRadar. Frozen geometry: `aksay_geometry.json` v1, `aksay_tongue_polygon.json` v1 (untouched). **Base year: 2018** (changed from 2017 by rule C3, see Part C).

rev4: Part C (2017 base-year check → 2017 flagged as fresh snow, base → 2018, Part A re-run), Part D (optical offset tracking), Part B-pilot (one Sentinel-1 pair).

## Verdict

1. **2017 was fresh snow, not bright ice.** On common clear tongue pixels B08 rose from 0.217 (2017-09-03) to 0.310 (2017-09-16) across a fully overcast 2017-09-11; no B08 ≥ 0.5 area existed on 09-03. → 2017 flagged, base year 2018.
2. **With base 2018, the bare-ice decline no longer depends on the snow/ice split.** Clean years + DOY: B08 0.50 → -0.020 ± 0.003 km²/yr, t(4) = -7.27, p = 0.0019; **0.60 → -0.021 ± 0.003 km²/yr, t(4) = -6.15, p = 0.0035**; 0.70 → -0.021 ± 0.004 km²/yr, t(4) = -5.39, p = 0.0057. The rev3 sensitivity was entirely the 2017 snow.
3. **Debris expansion:** +0.021 ± 0.004 km²/yr, t(4) = 5.13, p = 0.0068 (clean + DOY); without DOY +0.021 ± 0.009 km²/yr, t(5) = 2.28, p = 0.072. 27% of 2018 bare ice is debris in 2026, concentrated at the lateral margins.
4. **Optical offset tracking (Part D):** the clean, crevassed tongue flows at ~30 m/yr; flow drops to ~5–15 m/yr across the clean-ice/debris boundary and fades into the noise (≤ 3–4 m/yr) at ≈ 3.95 km along the centerline. The debris lobe below that is stagnant within noise (< ~0.5 m/yr over 7–8 years). A single L_moving_opt number is **not** stable — reported as a profile, not a rate.
5. **Surface darkening / debris expansion, consistent with thinning.** External check (Hugonnet 2021, RGI60-13.11414): glacier-wide dh/dt 2000–2019 = -0.09 ± 0.17 m/yr — not distinguishable from zero; neither confirms nor rules out tongue thinning.
6. **Sentinel-1 B-pilot:** see Part B section (verdict per the B4 stop rule).

## Part C — base-year check (September 2017)

| date | tile CC % | cloud % (bbox, SCL) | snow % (bbox, SCL) | cloud % (polygon) | B08 median, polygon | B08 median, bright px | B08 ≥ 0.5 km² | B08 ≥ 0.6 km² |
|---|---|---|---|---|---|---|---|---|
| 2017-09-03 | 7.5 | 63.6 | 2.6 | 62.9 | 0.217 | 0.195 | 0.0 | 0.0 |
| 2017-09-11 | 17.4 | 91.7 | 0.1 | 100.0 | None | None | 0.0 | 0.0 |
| 2017-09-16 | 0.0 | 0.0 | 23.1 | 0.0 | 0.286 | 0.394 | 0.248 | 0.099 |
| 2017-09-18 | 31.7 | 18.9 | 15.4 | 33.2 | 0.312 | 0.303 | 0.084 | 0.043 |
| 2017-09-23 | 22.4 | 93.9 | 0.2 | 100.0 | 0.113 | None | 0.0 | 0.0 |

![sept 2017](out/c2017_quicklooks.png)

- Only five S2 acquisitions in September 2017 (Sentinel-2B not yet routinely acquiring here). Within ±7 days of 09-16: 09-11 and 09-23 fully clouded over the polygon, 09-18 partly clouded/hazy (33 % of polygon).
- Same-pixel comparison (pixels clear on both dates): B08 median 0.217 on 09-03 → 0.310 on 09-16; 09-16 → 09-18: 0.276 → 0.312 (haze-affected).
- **Decision (rule C3):** bright B08 0.5–0.6 surface is present only after the 09-11 overcast spell, the pre-storm date is darker → **2017 flagged as fresh snow** (`aksay_flags.json`), **base year → 2018**; all Part A fits and the transition map re-run with 2018.

## Part A (re-run with base 2018) — snow / ice / debris in the tongue polygon

![metrics](out/aksay_metrics.png)

Clean years: 2018, 2019, 2021, 2022, 2023, 2025, 2026 (n = 7). OLS on decimal year; σ = standard error of the slope; two-sided t-test, df = n − p.

| series | model | slope / yr | σ | t | df | p |
|---|---|---|---|---|---|---|
| tongue_ice_km2 | clean | -0.0209 | 0.0080 | -2.61 | 5 | 0.048 |
| tongue_ice_km2 | clean_doy | -0.0208 | 0.0034 | -6.15 | 4 | 0.0035 |
| tongue_ice_km2 | clean_no2021 | -0.0187 | 0.0048 | -3.90 | 4 | 0.018 |
| tongue_ice_km2 | clean_doy_no2021 | -0.0200 | 0.0032 | -6.25 | 3 | 0.0083 |
| tongue_snow_km2 | clean | -0.0006 | 0.0016 | -0.36 | 5 | 0.74 |
| tongue_snow_km2 | clean_doy | -0.0006 | 0.0012 | -0.48 | 4 | 0.65 |
| tongue_debris_km2 | clean | +0.0215 | 0.0094 | +2.28 | 5 | 0.072 |
| tongue_debris_km2 | clean_doy | +0.0214 | 0.0042 | +5.13 | 4 | 0.0068 |
| tongue_bright_km2 | clean | -0.0215 | 0.0094 | -2.29 | 5 | 0.071 |
| tongue_bright_km2 | clean_doy | -0.0214 | 0.0041 | -5.23 | 4 | 0.0064 |
| L_clean_edge_m | clean | -10.6778 | 4.8802 | -2.19 | 5 | 0.08 |
| L_clean_edge_m | clean_doy | -10.6608 | 3.0785 | -3.46 | 4 | 0.026 |

B08 snow/ice split (parameter) sweep:

| B08 | ice, clean | ice, clean + DOY |
|---|---|---|
| 0.50 | -0.0198 ± 0.0061 (t(5) = -3.24, p = 0.023) | -0.0198 ± 0.0027 (t(4) = -7.27, p = 0.0019) |
| 0.60 | -0.0209 ± 0.0080 (t(5) = -2.61, p = 0.048) | -0.0208 ± 0.0034 (t(4) = -6.15, p = 0.0035) |
| 0.70 | -0.0213 ± 0.0091 (t(5) = -2.35, p = 0.066) | -0.0212 ± 0.0039 (t(4) = -5.39, p = 0.0057) |

2021 (2021-09-27): snow 0.028 km², ice 0.784 km² — not snow. Ice without 2021: -0.020 ± 0.003 km²/yr, t(3) = -6.25, p = 0.0083 (clean + DOY).

### Transition map 2018 → 2026

![transition](out/aksay_transition_2018_2026.png)

| transition | km² |
|---|---|
| snow (unchanged) | 0.0 |
| snow→ice | 0.0004 |
| snow→debris | 0.001 |
| ice→snow | 0.0002 |
| ice (unchanged) | 0.546 |
| ice→debris | 0.2031 |
| debris→snow | 0.0 |
| debris→ice | 0.002 |
| debris (unchanged) | 0.5362 |

- Lateral margins (outer 20 % of 2018 ice width): 62% of ice→debris vs 40% of all 2018 ice → enriched ≈1.6×.
- Down-glacier quarter: 18% vs 16% baseline → not enriched.
- 86% of ice→debris pixels in connected patches ≥ 50 px → coherent margin/moraine widening, not scatter.

## Part D — optical offset tracking (S2 B08, 10 m)

Method (parameters): NCC 32×32 px windows, 8 px step, ±16 px search, parabolic sub-pixel peak; reject NCC < 0.5 or peak/2nd-peak < 1.3; 3×3 nan-median; ROI ±400 m around the centerline from mid-polygon to 1 km below the polygon (last centerline segment extrapolated for the ROI only). L_moving_opt_m = end of the |d| > 2 px run along the frozen centerline, gaps ≤ 100 m bridged.

### As specified (multi-year pairs, 32 px windows)

| pair | years | valid windows | stable-rock median d (px) | stable-rock median abs d (px) | tongue-polygon median abs d (px) | L_moving_opt_m |
|---|---|---|---|---|---|---|
| 2018→2026 | 7.98 | 204/266 | (-0.09, 0.21) | 0.27 | 0.6 | none |
| 2019→2026 | 7.05 | 195/266 | (0.11, 0.02) | 0.14 | 0.24 | none |

![offset 2018-2026](out/offset/2018_2026_map.png)

- Co-registration: stable-rock median |d| 0.14–0.27 px < 1 px → no shift applied.
- **Result:** over 7–8 years the crevassed clean-ice tongue decorrelates (matches rejected), so no connected |d| > 2 px run exists → **L_moving_opt undefined with these parameters**. The debris lobe below ≈ 3.8 km matches well (NCC ≈ 0.8) with |d| ≈ 0.1–0.5 px over 7–8 years → stagnant within noise (< ~0.5 m/yr).

### Sensitivity (parameters changed, labelled): annual pairs, 16×16 px windows, 4 px step, ±8 px search

| pair | stable-rock median abs d (px) | 2800–3350 m | 3350–3600 m | 3600–3950 m | 3950–4470 m | L_moving_opt (2 / 1 / 0.5 px, gaps as NaN-stops) | same, NaN-skipping variant |
|---|---|---|---|---|---|---|---|
| 2018→2019 | 0.33 | 29 m/yr | 16 m/yr | 5 m/yr | 4 m/yr | 3095.7 / 3095.7 / 3095.7 | None / None / 3592.5 |
| 2022→2023 | 0.33 | 33 m/yr | 13 m/yr | 9 m/yr | 4 m/yr | None / None / 3592.5 | None / None / None |
| 2025→2026 | 0.1 | 30 m/yr | 4 m/yr | 8 m/yr | 1 m/yr | 3145.9 / 3145.9 / 3145.9 | 3145.9 / 3386.8 / 3467.1 |

![profile](out/offset_win16_annual/offset_profile.png)

- Zone values are medians of |d| along the centerline, ×10 m, per year. Noise floor = stable-rock median |d| ≈ 1–3 m/yr.
- Clean-ice edge L_clean_edge_m: 3577.4 m (2018), 3462.1 m (2026). Motion continues 5–15 m/yr for ~300–400 m below it, under debris, and reaches the noise floor at ≈ 3.95 km.
- **Interpretation (strict):** where it moves → ice under debris; where it does not → dead moraine or stagnant (dead) ice. The ≈ 3.95 km point is the limit of *active* ice, not necessarily the terminus — dead ice is still ice.
- **Why no single L_moving_opt rate:** the threshold-based end point jumps between 3.1 and 3.6 km depending on pair, threshold (parameter) and how rejected windows are treated; with 3 annual pairs it cannot carry a trend. Use the velocity profile.
- L_moving_opt vs L_clean_edge: the clean-ice edge is a surface-albedo boundary (bare ice vs debris); active flow extends ≈ 350–450 m further under the debris. They measure different things.

## Part B-pilot — Sentinel-1 coherence, one pair (DES 107)


## Frames

| year | date | cloud % in bbox | snow % in bbox | flag |
|---|---|---|---|---|
| 2016 | 2016-08-09 | 0.32 | 37.1 | auto snow fraction |
| 2017 | 2017-09-16 | 0.0 | 23.1 | manual (aksay_flags.json) |
| 2018 | 2018-09-08 | 0.0 | 24.5 |  |
| 2019 | 2019-08-12 | 0.0 | 20.5 |  |
| 2020 | 2020-09-25 | 0.0 | 31.8 | auto snow fraction |
| 2021 | 2021-09-27 | 0.0 | 24.1 |  |
| 2022 | 2022-09-07 | 0.0 | 21.5 |  |
| 2023 | 2023-08-21 | 0.0 | 20.4 |  |
| 2024 | 2024-08-20 | 0.01 | 31.1 | auto snow fraction |
| 2025 | 2025-09-04 | 0.0 | 17.9 |  |
| 2026 | 2026-08-30 | 0.0 | 15.0 |  |

## Caveats

- n = 7 clean frames; DOY models have df = 4. p-values assume independent Gaussian residuals — indicative.
- Thresholds are parameters: NDSI 0.1 (tongue), B08 0.60 (snow/ice, swept), offset-tracking NCC/peak-ratio/window/threshold (sensitivity shown), coherence contrasts 0.15/0.10.
- `L_clean_edge_m` = clean-ice/debris boundary on the centerline; not the terminus.
- Hugonnet 2021 via OGGM per-glacier file (`aksay/data/hugonnet_aksay.json`).

## Files

`aksay/out/aksay_metrics.csv`, `aksay_metrics.png`, `aksay_transition_*_2026.png`, `rev3_stats.json`, `c2017_table.json`, `c2017_quicklooks.png`, `offset/`, `offset_win16_annual/`, `s1_pilot_*`. Re-run offline: `python aksay/process.py && python aksay/c_check.py && python aksay/d_offset.py 2018 2026 2019 2026 && python aksay/report_rev4.py`.
