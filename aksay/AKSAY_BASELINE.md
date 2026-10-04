# AKSAY_BASELINE — Ak-Sai glacier, Landsat + Sentinel-2 1993–2026 (rev6-final)

R&D mini-project (`aksay/`), independent of OilRadar. Frozen geometry: `aksay_geometry.json` v1, `aksay_tongue_polygon.json` v1 (untouched). **Base year: 2018** (changed from 2017 by rule C3, see Part C).

rev6-final: sensor cross-calibration (I), SLC-off and flag checks (J), breakpoint test (K), final 30-year series (L). Earlier parts below: E (is the day-of-year covariate justified), Part G (report corrections), Part F (migration of the active-ice limit). rev4 parts C, D, B-pilot kept. Frozen D-sensitivity parameters.

## Verdict

1. **Not measured: the terminus, thinning, acceleration.** The snout has been debris-covered since at least 1993–1996 and is invisible to optical data; 12-day C-band coherence does not separate stagnant debris-covered ice from moraine (B-pilot); glacier-wide thinning from Hugonnet 2021 is -0.09 ± 0.17 m/yr (not distinguishable from zero); an acceleration after 2013 is not supported (one-breakpoint model loses on BIC by 0.95; F-test p = 0.1).
2. **Bright (snow + bare ice) area on the lower tongue shrinks, 1996–2026.** All sensors on the Landsat-8 scale (I1 offset L7−L8 = +0.014 km², n = 4; I2 offset L8−S2 = +0.022 km², n = 4): -0.0103 ± 0.0020 km²/yr, t(18) = -5.25, p = 5.4e-05 (n = 20). Within sensor families the sign is the same (L5/7 p = 0.051, L8/9 p = 0.064, S2 p = 0.071).
3. **The clean-ice edge moves up-glacier.** Single series 1996–2026, every clean frame with a detected edge (Landsat and S2 both counted in 2016–2026), no edge offsets applied: -6.7 ± 1.6 m/yr, t(18) = -4.32, p = 0.00042 (n = 20) — ≈ 202 m in 30 years. It is the clean-ice/debris boundary, not the terminus.
4. **Surface darkening / debris expansion, consistent with thinning.** S2 2018–2026: debris clean: +0.021 ± 0.009 km²/yr, t(5) = 2.28, p = 0.072 | clean+DOY: +0.021 ± 0.004 km²/yr, t(4) = 5.13, p = 0.0068; 27% of 2018 bare ice turned to debris by 2026, concentrated at the lateral margins.
5. **Where active ice ends (snapshot, not trend):** S2 offset tracking puts the down-glacier limit of measurable flow at ≈ 3.65–3.96 km along the centerline in every pair (clean tongue ~30 m/yr, 4–16 m/yr under the debris, then noise); its migration is not resolved on 6 pairs; the debris lobe below is stagnant within noise.

## What is measured / what is not

| | quantity | status | evidence |
|---|---|---|---|
| measured | bright area in tongue polygon, by sensor | 1996–2026, joined on L8 scale | -0.0103 ± 0.0020 km²/yr, t(18) = -5.25, p = 5.4e-05 |
| measured | clean-ice edge on the centerline | 1996–2026, all sensors | -6.7 ± 1.6 m/yr, t(18) = -4.32, p = 0.00042 |
| measured | active-ice limit (S2 offset tracking) | snapshot 2018–2026 | ≈ 3.65–3.96 km along the centerline; trend not resolved |
| measured | surface speed | 2018–2026 | clean tongue ~30 m/yr; under debris 4–16 m/yr; lobe < noise |
| not measured | terminus | debris-covered since ≥ 1993–1996 | not visible optically; S1 coherence does not separate it |
| not measured | thinning | external only | Hugonnet 2021: -0.09 ± 0.17 m/yr (≈ 0) |
| not measured | acceleration | breakpoint test | BIC gain -0.95 (< 2), F p = 0.1 → "no acceleration detected" |

## Part E — is the day-of-year covariate justified?

Model clean_doy on the 7 clean years (y = a + b·year + c·(DOY − 240)); the DOY coefficient c:

| series | DOY coefficient (per day) | σ | t | df | p |
|---|---|---|---|---|---|
| tongue_ice_km2 | +0.0033 | 0.0007 | +4.71 | 4 | 0.0082 |
| tongue_debris_km2 | -0.0039 | 0.0008 | -4.87 | 4 | 0.0098 |
| tongue_bright_km2 | +0.0039 | 0.0008 | +4.87 | 4 | 0.0093 |
| tongue_snow_km2 | +0.0005 | 0.0002 | +2.50 | 4 | 0.082 |
| L_clean_edge_m | +1.8133 | 0.6196 | +2.93 | 4 | 0.043 |

**Rule E2:** p(DOY) < 0.10 for ice and debris → the covariate is justified; the Verdict shows both models side by side.
Physical reading: later acquisitions show more bright/ice and less debris area (+0.003 km²/day for ice) — late-season frames carry more residual snow/firn and wetter, brighter ice, so DOY is a confounder, not a nuisance fit. With df = 4 the DOY model is still fragile; both numbers are reported.

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
- Pixels clear on both 09-03 and 09-16: 37% of the polygon; by 2018 class: ice 68%, debris 31% → comparison adequate (≥ 30 % clear, not debris-dominated). On those pixels B08: ice 0.195 → 0.393, debris 0.289 → 0.237 (brightening only on ice).
- The 2017 flag is justified by an independent signal (darker before the overcast spell, brighter after), not by its influence on the fit.
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
- Clean-ice edge L_clean_edge_m: 3577.4 m (2018), 3462.1 m (2026). Motion continues at ~4–16 m/yr (pair-dependent, 1.5–5× the noise floor) for ~300–400 m below it, under debris, and reaches the noise floor at ≈ 3.95 km.
- **Interpretation (strict):** where it moves → ice under debris; where it does not → dead moraine or stagnant (dead) ice. The ≈ 3.95 km point is the limit of *active* ice, not necessarily the terminus — dead ice is still ice.
- **Why no single L_moving_opt rate:** the threshold-based end point jumps between 3.1 and 3.6 km depending on pair, threshold (parameter) and how rejected windows are treated; with 3 annual pairs it cannot carry a trend. Use the velocity profile.
- L_moving_opt vs L_clean_edge: the clean-ice edge is a surface-albedo boundary (bare ice vs debris); active flow extends ≈ 350–450 m further under the debris. They measure different things.

## Part F — migration of the active-ice limit (frozen D-sensitivity parameters)

![profiles](out/f_migration/f_profiles.png)

Pairs on clean frames; 2-year pairs cross a flagged year. |d| normalised to m/yr. QC: stable-rock median |d| ≤ 0.5 px/yr. L_active = last point down-glacier where the 150 m running median of |d| is ≥ k × the pair's noise floor (k = 2, 3: parameters).

| pair | yr | mid | valid windows | noise (m/yr) | QC | L_active 2× (m) | L_active 3× (m) | 2800–3350 | 3350–3600 | 3600–3950 | 3950–4470 (m/yr) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2018→2019 | 0.93 | 2019.15 | 828/1065 | 3.55 | pass | 3652.6 | 3652.6 | 31.2 (n=44) | 17.5 (n=49) | 5.0 (n=28) | 4.7 (n=99) |
| 2019→2021 (2 yr) | 2.13 | 2020.67 | 664/1065 | 1.83 | pass | 3737.9 | 2798.9 | 26.7 (n=2) | 4.9 (n=9) | 4.9 (n=21) | 2.4 (n=93) |
| 2021→2022 | 0.94 | 2022.21 | 782/1065 | 1.81 | pass | 3883.2 | 2798.9 | 2.9 (n=20) | 6.7 (n=1) | 2.6 (n=34) | 1.9 (n=89) |
| 2022→2023 | 0.95 | 2023.16 | 860/1065 | 3.47 | pass | 3923.3 | 3652.6 | 35.1 (n=76) | 13.5 (n=49) | 9.2 (n=38) | 3.9 (n=99) |
| 2023→2025 (2 yr) | 2.04 | 2024.65 | 839/1065 | 1.23 | pass | 3652.6 | 3346.7 | 1.5 (n=81) | 3.3 (n=49) | 0.7 (n=5) | 0.8 (n=98) |
| 2025→2026 | 0.99 | 2026.17 | 935/1065 | 1.01 | pass | 3958.4 | 3958.4 | 30.3 (n=81) | 3.8 (n=49) | 8.1 (n=55) | 0.9 (n=91) |

| fit | slope (m/yr per yr) | σ | t | df | p | residual SD | half fitted change over span | resolved |
|---|---|---|---|---|---|---|---|---|
| L_active_2x_m | +28.1 | 22.8 | 1.23 | 4 | 0.28 | 130.8 m | 98.6 m | NO |
| L_active_3x_m | +77.6 | 85.3 | 0.91 | 4 | 0.41 | 489.9 m | 272.4 m | NO |
| zone 3600–3950 speed | +0.17 | 0.62 | 0.28 | 4 | 0.79 | 3.6 m/yr | — | — |

- **Resolvability rule (F5, operationalised):** inter-pair scatter = residual SD around the OLS line; resolvable only if it is ≤ half the fitted change over the 7-year span. Fails for both k → **migration of the active-ice limit is not resolved on 6 pairs; no rate reported.**
- The 3× variant collapses to 2.8 km in two pairs (19→21, 21→22) because the fast clean-ice zone is barely matched there (n = 2 and 20 windows in 2800–3350 m, median 2.9 m/yr in 21→22 vs ~30 m/yr in all other 1-year pairs): 2-year pairs and the late-September 2021 frame decorrelate the crevasse field. The pairs disagree with each other more than any plausible 7-year migration.
- Under-debris zone 3600–3950 m: 0.7–9.2 m/yr across pairs, no trend (p above). No evidence of slowdown on this record.
- Range of L_active (2×) across pairs: 3653–3958 m — the active-ice limit sits ≈ 3.65–3.96 km in every pair, i.e. 100–500 m below the clean-ice edge; that location statement holds, the trend does not.

## Part I — sensor cross-calibration

**I1 — Landsat-7 (SLC-off) vs Landsat-8, 2013–2015, Aug–Sep, |Δdate| ≤ 16 d, both clean in the polygon; L7 stripe/cloud mask imposed on L8 (common valid pixels only):**

| L7 | L8 | Δ days | common valid | bright L7 (km²) | bright L8 (km²) | L7 − L8 | edge L7 | edge L8 |
|---|---|---|---|---|---|---|---|---|
| 2014-08-24 | 2014-09-08 | 15 | 0.534 | 0.801 | 0.96 | -0.158 | — | 3692.7 |
| 2014-08-24 | 2014-08-23 | 1 | 0.544 | 0.794 | 0.78 | +0.014 | — | 3557.4 |
| 2014-08-08 | 2014-08-23 | 15 | 0.603 | 0.779 | 0.766 | +0.014 | — | 3557.4 |
| 2014-08-08 | 2014-08-07 | 1 | 0.603 | 0.779 | 0.751 | +0.028 | — | 3557.4 |

Offset L7 − L8 = **median +0.014 km²** (n = 4 ≥ 3 → accepted by rule I4). Caveat: the 4 pairs share 2 L7 frames, all in 2014 (2013 and 2015 had no clean pair); the 15-day pair 08-24/09-08 differs by −0.16 km² (late-season change), the three others by +0.01…+0.03. L5 is assumed to equal L7 (TM ≈ ETM+ bands); not testable here.

**I2 — Landsat-8/9 vs Sentinel-2, clean S2 years 2017–2026, |Δdate| ≤ 10 d, common valid pixels:**

| year | Landsat | S2 | Δ days | bright L (km²) | bright S2 (km²) | L − S2 | edge L | edge S2 | edge L − S2 |
|---|---|---|---|---|---|---|---|---|---|
| 2019 | 2019-08-05 (8) | 2019-08-12 | 7 | 0.675 | 0.655 | +0.020 | 3557.4 | 3537.3 | 20.1 |
| 2021 | 2021-09-27 (8) | 2021-09-27 | 0 | 0.846 | 0.806 | +0.041 | — | 3587.5 | — |
| 2022 | 2022-09-06 (9) | 2022-09-07 | 1 | 0.678 | 0.673 | +0.005 | 3411.9 | 3537.3 | -125.4 |
| 2025 | 2025-08-29 (9) | 2025-09-04 | 6 | 0.63 | 0.606 | +0.024 | 3241.3 | 3537.3 | -296.0 |

Offset L8 − S2 = **median +0.022 km²** (n = 4; range +0.005…+0.041).

**I3 — clean-ice edge:** L7 − L8 not determinable (no L7 edge detected through SLC-off stripes in any pair, n = 0); L8 − S2 median -125 m (n = 3: +20, −125, −296 m) — **not within 30 m**: single 30 m frames can lose the narrow clean strips at the tongue end. Edge offsets are therefore **not applied**; the edge result rests on the 30-year span, not on frame-to-frame agreement.

**Rule I4:** I1 gave an offset (n ≥ 3) → series joined on the Landsat-8 scale (L5/7 − (L7−L8), S2 + (L8−S2); one value per year: Landsat ≤ 2015, S2 ≥ 2016). Separate per-family fits shown alongside.

**QA fix in rev6-final (documented, not a new threshold):** CFMask's high-confidence 'cloud' over dirty ice dropped valid ice pixels and biased Landsat bright area up (rev6: Landsat − S2 ≈ +0.10 km²). Cloud now also requires blue SR > 0.2 — validated on the visually-QA'd frames (12 clear frames ≤ 2 % cloud in the polygon; cloudy 2003/2004/2011 10–80 %). After the fix L8 − S2 is +0.02 km².

## Part J — SLC-off and flag checks

- **J1:** L5/7 bright area 1996–2012: all clean -0.0046 ± 0.0042 km²/yr, t(8) = -1.11, p = 0.3 (n = 10); without SLC-off frames -0.0048 ± 0.0029 km²/yr, t(3) = -1.65, p = 0.2 (n = 5). Slopes differ by 0.0002 < larger σ → **SLC-off frames stay in the area fit.**
- **J2:** 1999 not flagged (bbox NDSI-snow 30.2 % < 32.8 % flag; polygon QA-snow 32 % ≈ 1996/2001; no NIR ≥ 0.6) → not flagged.
- **J3:** valid-pixel fraction in the polygon is in the CSV (`valid_poly_frac`); frames < 60% would leave the area fit — none of the clean frames is below (min ≈ 0.70, L7 SLC-off).

## Part K — break in the trend

Joined bright-area series (L8 scale, n = 20): linear -0.0103 ± 0.0020 km²/yr, t(18) = -5.25, p = 5.4e-05. One-breakpoint model (two independent segments, breakpoint on a 0.25-yr grid 2005–2018 by minimum RSS): best break 2015.25, slope -0.0075 → -0.0285 km²/yr, level jump +0.107 km².

| model | RSS | AIC | BIC |
|---|---|---|---|
| linear | 0.105 | -99.0 | -96.01 |
| one breakpoint | 0.0702 | -101.04 | -95.06 |

F-test (segmented vs linear): F(3, 15) = 2.47, p = 0.1. BIC gain = -0.95 < 2 → **no breakpoint declared (rule K2).** The best break (2015.25) sits at the Landsat → S2 switch of the joined series (S2 from 2016), so even the AIC preference cannot be separated from a sensor change. **Acceleration after 2013 is not distinguishable from noise and sensor change.**

## Part L — final series

![metrics](out/h_landsat/h_metrics.png)

![timelapse](out/h_landsat/aksay_timelapse_1990_2026.gif)

- **L1 clean-ice edge 1996–2026** (single series, sensor column in the CSV; Landsat quantised at 30 m, S2 at 10 m; I3 offsets not applied): -6.7 ± 1.6 m/yr, t(18) = -4.32, p = 0.00042, n = 20, sensors L5, L7, L8, L9, S2. Per family (same sign, each alone not significant): L5/7 -11.4 ± 5.5 m/yr, t(3) = -2.07, p = 0.13; L8/9 -11.2 ± 7.6 m/yr, t(6) = -1.47, p = 0.19; S2 -10.7 ± 4.9 m/yr, t(5) = -2.19, p = 0.08.
- Bright area per family: L5/7 -0.0070 ± 0.0031 km²/yr, t(9) = -2.25, p = 0.051; L8/9 -0.0145 ± 0.0070 km²/yr, t(10) = -2.08, p = 0.064; S2 -0.0215 ± 0.0094 km²/yr, t(5) = -2.29, p = 0.071; joined -0.0103 ± 0.0020 km²/yr, t(18) = -5.25, p = 5.4e-05.
- GIF: one S2 display stretch for all frames; each Landsat family mapped band-by-band onto S2 radiometry by matching p5/p60 (TM/ETM+ saturate on snow, so not p98); saturated pixels white; flagged frames marked; caption shows the frame's tongue bright area.
- Data gaps: no usable Tier-1 frame 1990–1992, 1994–1995, 1997, 2000; 1998, 2003, 2004, 2007, 2011 cloud (visual QA); 1993 auto-snow flag.

## Part B-pilot — Sentinel-1 coherence, one pair (DES 107)

Pair 2026-08-06 → 2026-08-18 (12 days), coherence with 5×1 looks, resampled to the S2 grid.

| zone | pixels | median | IQR |
|---|---|---|---|
| a_bare_ice_polygon | 5517 | 0.332 | 0.281–0.387 |
| b_debris_lobe | 6004 | 0.606 | 0.436–0.728 |
| c_valley_rock | 90493 | 0.472 | 0.356–0.645 |

b − c = 0.134, b − a = 0.274. Rule (parameters, read as absolute contrasts): |b−c| ≥ 0.15 and |b−a| ≥ 0.1 (parameters).

![coherence](out/s1_pilot_2026-08-06_2026-08-18_coherence.png)

**Verdict: 12-day C-band coherence does not separate debris-covered ice from ice-free moraine on a near-stagnant tongue; not pursued.** This is a result, not a failure.

- Stop rule triggered by b − c = 0.134 < 0.15. The lobe is coherent; the rock reference is itself degraded by steep, foreshortened terrain and by the 5×1 estimator floor (≈ 0.3), so b − c understates the contrast to truly stable ground. Consistent with Part D (no measurable motion below ≈ 3.95 km): stagnant debris-covered ice and ice-free moraine are not separable by 12-day coherence.
- Bare ice in the polygon is clearly decorrelated (median 0.33) — the active, melting tongue is visible; the method fails only at the boundary that matters (dead ice vs moraine).
- Processing: CDSE OData (SH OAuth token accepted for download), IW3 VV only (2 × 1.35 GB), POEORB from ESA STEP, Copernicus GLO-30 + EGM2008 undulation −37.2 m → WGS84, ISCE2 2.6.5 topsApp (no ESD, no unwrapping), geocoded and resampled to the S2 grid; ≈ 5 min ISCE time. Logs: `aksay/data/s1_pilot/`.
- The 9-year S1 series was **not** downloaded (brief B5).

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

## Next steps

- Landsat done (Part H). Remaining free-data option: manual digitising of the debris-covered snout on the best Landsat/S2 frames (±30–50 m) for a terminus estimate over 30 years.
- DEM differencing: not pursued — no free post-2016 DEM over the site; on free data it would only reproduce Hugonnet 2021.

## Caveats

- n = 7 clean frames; DOY models have df = 4. p-values assume independent Gaussian residuals — indicative.
- Thresholds are parameters: NDSI 0.1 (tongue), B08 0.60 (snow/ice, swept), offset-tracking NCC/peak-ratio/window/threshold (sensitivity shown), coherence contrasts 0.15/0.10.
- `L_clean_edge_m` = clean-ice/debris boundary on the centerline; not the terminus.
- Hugonnet 2021 via OGGM per-glacier file (`aksay/data/hugonnet_aksay.json`).

## Files

`aksay/out/aksay_metrics.csv`, `aksay_metrics.png`, `aksay_transition_*_2026.png`, `rev3_stats.json`, `c2017_table.json`, `c2017_quicklooks.png`, `offset/`, `offset_win16_annual/`, `f_migration/`, `h_landsat/` (aksay_landsat_s2_metrics.csv, rev6_stats.json, h_metrics.png, aksay_timelapse_1990_2026.gif), `c2017_g1.json`, `s1_pilot_*`. Re-run offline: `python aksay/process.py && python aksay/c_check.py && python aksay/d_offset.py 2018 2026 2019 2026 && python aksay/f_migration.py && python aksay/h_process.py && python aksay/rev6_final.py && python aksay/report_final.py`.
