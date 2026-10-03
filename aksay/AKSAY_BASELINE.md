# AKSAY_BASELINE — Ak-Sai glacier, Sentinel-2 2016–2026

R&D mini-project (`aksay/`), independent of OilRadar. Sentinel-2 L2A via CDSE Sentinel Hub Process API, 10 m. Revision 2: the v1 "front retreat 10 m/yr" claim is withdrawn — see below.

## Verdict (from the CSV fits only)

- **Measured: the exposed-ice area of the lower tongue is shrinking.** Inside a fixed 1.29 km² tongue polygon, ice (NDSI>0.1) went 0.806 km² (2017) → 0.549 km² (2026), 62.6% → 42.6% of the polygon.
  - Fit, 8 clean years: **-0.023 ± 0.007 km²/yr**; controlling for acquisition day-of-year: **-0.020 ± 0.003 km²/yr** (6σ). All 11 years: -0.025 ± 0.007.
  - Holds at every NDSI threshold 0.0 / 0.1 / 0.2 / 0.4 (not a threshold artefact). The NDSI histogram inside the polygon is bimodal (debris ≈ −0.2, ice ≈ +0.65); 0.1 sits in the valley.
- **Interpretation:** the tongue's outline (valley-wall margins, crevasse field, medial moraines) does not visibly move at 10 m. What changes is the surface: bare ice is being replaced by debris cover / very dirty ice — the typical signature of a thinning, debris-covered tongue. This is evidence of ice loss, **not** a measured terminus retreat.
- **Not measured: terminus retreat.** Ak-Sai's real snout is debris-covered (the grey-brown lobe below the clean ice) and invisible to NDSI. The v1 "front" was the clean-ice/debris boundary; its fit (-10.2 ± 3.8 m/yr) is kept in the CSV as `L_clean_edge_m` but it is a surface boundary, it moves in 25–50 m jumps (3–5 px, at the noise level), and it is not glacier length.
- Literature line (no deep research): Ala-Archa glaciers (Ak-Sai, Golubin; WGMS) are reported to lose mass and retreat by a few to ~15 m/yr; a darkening/debris-expanding tongue is consistent with that. Our data cannot confirm the retreat rate itself.

## Why the GIF does not show it obviously

- 10 years × ~10 m/yr = ~100 m = ~10 px in a 390-px frame. Below what the eye catches next to year-to-year snow differences.
- The visible frame-to-frame change is dominated by snow cover and by the white→grey transition of the tongue — the latter is the real signal.
- To make change obvious: a 30-year baseline (Landsat 1990s) or a DEM-difference (thinning in metres) — see next steps.

## Frames (Stage 0)

| year | date | cloud % in bbox | snow % in bbox | snow flag | window |
|---|---|---|---|---|---|
| 2016 | 2016-08-09 | 0.32 | 37.1 | ⚠ fresh snow | aug-sep |
| 2017 | 2017-09-16 | 0.0 | 23.1 | — | aug-sep |
| 2018 | 2018-09-08 | 0.0 | 24.5 | — | aug-sep |
| 2019 | 2019-08-12 | 0.0 | 20.5 | — | aug-sep |
| 2020 | 2020-09-25 | 0.0 | 31.8 | ⚠ fresh snow | aug-sep |
| 2021 | 2021-09-27 | 0.0 | 24.1 | — | aug-sep |
| 2022 | 2022-09-07 | 0.0 | 21.5 | — | aug-sep |
| 2023 | 2023-08-21 | 0.0 | 20.4 | — | aug-sep |
| 2024 | 2024-08-20 | 0.01 | 31.1 | ⚠ fresh snow | aug-sep |
| 2025 | 2025-09-04 | 0.0 | 17.9 | — | aug-sep |
| 2026 | 2026-08-30 | 0.0 | 15.0 | — | aug-sep |

- Selection: catalog L2A Aug–Sep, tile CC<15 %; every date scored inside the bbox by SCL (cloud = 3/8/9/10, snow = 11). Pick = cloud ≤ min+0.5 pp (floor 1 %), then least snow, then latest. Candidates + quicklooks in `aksay/data/`.
- Snow flag = snow fraction > 1.25 × series median (23.1 %). 2016/2020/2024 had no cleaner date that season → kept, flagged, excluded from the main fits. 2016 is a single-scene year (S2A only).
- Widened window never needed. 2015: no L2A and no clear L1C → series starts 2016.
- Render: TRUE_COLOR, identical bbox/size, one p2–p98 stretch for the whole series (375–9560, ×1e4 reflectance) + one gamma 0.6 → no flicker.
- Landsat 1990s–2000s: skipped (no Landsat-5/7 on CDSE; USGS needs separate auth).

## Metrics

| year | date | tongue_ice_km2 | tongue_ice % | L_clean_edge_m | W1 | W2 | W3 | W4 | frame snow/ice km² | snow flag |
|---|---|---|---|---|---|---|---|---|---|---|
| 2016 | 2016-08-09 | 0.856 | 66.4 | 3577.4 | 1155.0 | 775.0 | 770.0 | 310.0 | 7.952 | ⚠ |
| 2017 | 2017-09-16 | 0.806 | 62.6 | 3577.4 | 995.0 | 755.0 | 525.0 | 305.0 | 6.656 |  |
| 2018 | 2018-09-08 | 0.751 | 58.2 | 3577.4 | 1005.0 | 615.0 | 350.0 | 295.0 | 6.374 |  |
| 2019 | 2019-08-12 | 0.655 | 50.8 | 3537.3 | 745.0 | 245.0 | 185.0 | 250.0 | 5.327 |  |
| 2020 | 2020-09-25 | 0.956 | 74.2 | 3682.7 | 1155.0 | 790.0 | 780.0 | 335.0 | 8.9 | ⚠ |
| 2021 | 2021-09-27 | 0.812 | 63.0 | 3587.5 | 970.0 | 765.0 | 490.0 | 305.0 | 6.852 |  |
| 2022 | 2022-09-07 | 0.673 | 52.2 | 3537.3 | 835.0 | 620.0 | 330.0 | 275.0 | 5.993 |  |
| 2023 | 2023-08-21 | 0.638 | 49.5 | 3487.2 | 855.0 | 530.0 | 325.0 | 235.0 | 5.834 |  |
| 2024 | 2024-08-20 | 0.703 | 54.6 | 3537.3 | 990.0 | 730.0 | 380.0 | 295.0 | 6.918 | ⚠ |
| 2025 | 2025-09-04 | 0.606 | 47.0 | 3537.3 | 910.0 | 620.0 | 360.0 | 210.0 | 5.472 |  |
| 2026 | 2026-08-30 | 0.549 | 42.6 | 3462.1 | 830.0 | 450.0 | 340.0 | 160.0 | 5.023 |  |

![metrics](out/aksay_metrics.png)

## Method & caveats

- Frozen geometry: `aksay_geometry.json` v1 (crop, 4.47 km centerline, transects at 1000/1600/2200/2800 m, 1200 m long) and `aksay_tongue_polygon.json` v1 (lower-tongue polygon, valley wall to valley wall, added in revision 2; v1 geometry untouched).
- Day-of-year matters: +0.0037 ± 0.0007 km²/day — late-September frames read icier. The adjusted fit removes it; the trend survives.
- `L_clean_edge_m`: clean-ice edge on the centerline (ice counted within ±40 m across the line, gaps ≤100 m bridged). Quantised, surface-driven — do not quote as retreat.
- Widths W1–W4 = total ice ∩ transect length (include adjacent snow patches) and frame snow/ice area = every NDSI>0.4 pixel in the frame: trends only, not glacier geometry.
- Mask for widths/edge: NDSI>0.4 & B08>0.11 (water rejection), SCL clouds removed. Tongue metric: NDSI>0.1 inside the polygon, clouds removed.

## Next steps (if we want a retreat number)

1. Landsat 5/7/8 1990–2015 via USGS M2M or Google Earth Engine → 30-year baseline; change becomes obvious in the GIF.
2. Thinning: difference two DEMs (Copernicus GLO-30 ~2011–2015 vs a recent ASTER/Pléiades/ArcticDEM-style DEM) over the tongue → metres of surface lowering.
3. Manual digitising of the debris-covered snout on 11 frames — feasible, but at 10 m the snout edge is fuzzy (±30–50 m); marginal over 10 years.

## Files

`aksay/out/aksay_timelapse.gif`, `aksay/out/aksay_timelapse_ruler.gif`, `aksay/out/frames/`, `aksay/out/frames_ruler/`, `aksay/out/aksay_metrics.csv`, `aksay/out/aksay_frames.csv`, `aksay/out/aksay_metrics.png`. Re-run offline: `python aksay/process.py`; new frames: workflow `aksay_fetch.yml`.
