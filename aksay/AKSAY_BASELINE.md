# AKSAY_BASELINE — Ak-Sai glacier, Sentinel-2 timelapse 2016–2026

R&D mini-project (`aksay/`), independent of OilRadar. Sentinel-2 L2A via CDSE Sentinel Hub Process API, 10 m.

## Verdict (from the CSV fit only)

- **Front retreat: 10.2 ± 3.8 m/yr** (OLS slope ± 1σ, 8 clean frames 2017–2026; snow-flagged 2016/2020/2024 excluded). All 11 frames: 9.9 ± 4.8 m/yr — same answer, so the snow flags do not drive the result.
- **Accumulated retreat 2017→2026: ≈ 91 ± 34 m** from the fit; raw frame difference 2017→2026 = -115 m (±14 m, two 1-px errors).
- Snow/ice area in frame: -0.128 ± 0.063 km²/yr (clean years). Not glacier area: it is every NDSI pixel in the 3.9×4.8 km frame, incl. firn/snow patches; trend only.
- Order-of-magnitude check: published estimates for Ala-Archa valley glaciers (Ak-Sai, Golubin; WGMS / Tien Shan literature) are a few to ~15 m/yr of terminus retreat — our 10 m/yr sits inside that band. One-line check, no deep research.

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

- Selection: catalog L2A Aug–Sep, tile CC<15 %; each date scored inside the bbox by SCL (cloud = classes 3/8/9/10, snow = 11). Pick = cloud ≤ min+0.5 pp (floor 1 %), then least snow, then latest date. Candidates + quicklooks: `aksay/data/candidates.json`, `aksay/data/quicklooks/`.
- Snow flag = snow fraction > 1.25 × series median (23.1 %). 2016/2020/2024 had no cleaner neighbour date (every other clear scene that season was snowier or cloudy) → kept, flagged, excluded from the main fit. 2016 is a single-scene year (only S2A in orbit).
- No year needed the widened Jul 15 – Oct 10 window. 2015: no L2A, no clear L1C over the bbox → series starts 2016.
- Render: TRUE_COLOR B04/B03/B02, identical bbox/size every year, **one** p2–p98 stretch for the whole series (375–9560 reflectance ×1e4, common to all bands) + one gamma 0.6 → no brightness flicker.
- Landsat 1990s–2000s (stretch goal): skipped — CDSE Sentinel Hub has no Landsat-5/7 archive, the USGS route needs separate auth. Can add later.

## Metrics (Stage 2)

| year | date | L_front_m | W1_m | W2_m | W3_m | W4_m | area_km2 | ±m | snow flag |
|---|---|---|---|---|---|---|---|---|---|
| 2016 | 2016-08-09 | 3577.4 | 1155.0 | 775.0 | 770.0 | 310.0 | 7.952 | 10 | ⚠ |
| 2017 | 2017-09-16 | 3577.4 | 995.0 | 755.0 | 525.0 | 305.0 | 6.656 | 10 |  |
| 2018 | 2018-09-08 | 3577.4 | 1005.0 | 615.0 | 350.0 | 295.0 | 6.374 | 10 |  |
| 2019 | 2019-08-12 | 3537.3 | 745.0 | 245.0 | 185.0 | 250.0 | 5.327 | 10 |  |
| 2020 | 2020-09-25 | 3682.7 | 1155.0 | 790.0 | 780.0 | 335.0 | 8.9 | 10 | ⚠ |
| 2021 | 2021-09-27 | 3587.5 | 970.0 | 765.0 | 490.0 | 305.0 | 6.852 | 10 |  |
| 2022 | 2022-09-07 | 3537.3 | 835.0 | 620.0 | 330.0 | 275.0 | 5.993 | 10 |  |
| 2023 | 2023-08-21 | 3487.2 | 855.0 | 530.0 | 325.0 | 235.0 | 5.834 | 10 |  |
| 2024 | 2024-08-20 | 3537.3 | 990.0 | 730.0 | 380.0 | 295.0 | 6.918 | 10 | ⚠ |
| 2025 | 2025-09-04 | 3537.3 | 910.0 | 620.0 | 360.0 | 210.0 | 5.472 | 10 |  |
| 2026 | 2026-08-30 | 3462.1 | 830.0 | 450.0 | 340.0 | 160.0 | 5.023 | 10 |  |

![metrics](out/aksay_metrics.png)

## Method & honest caveats

- Geometry frozen in `aksay/aksay_geometry.json` (v1): crop, 4.47 km centerline from the top of the accumulation basin down the flow axis to ~0.8 km below every front, transects perpendicular at 1000/1600/2200/2800 m from the top, 1200 m long. Drawn on the earliest frames (2016; axis cross-checked on 2017 because 2016 has fresh snow).
- Ice mask: NDSI = (B03−B11)/(B03+B11) > 0.4 (B11 20 m → resampled to 10 m by SH) **and** B08 > 0.11 (standard water rejection, Hall et al.); SCL cloud pixels removed.
- **Front = end of clean ice, not the debris-covered snout.** Ak-Sai's tongue is split by medial moraines into parallel ice strips; the raw centerline crossed debris and the mask broke mid-glacier. Fix: ice counts on the centerline if present within ±40 m across it; gaps ≤100 m bridged. The debris-covered terminus below is invisible to NDSI — this measures the retreat of the clean-ice front, which is what moves visibly in the GIF.
- Front positions are quantised (repeat values 3577/3537 m): the clean-ice end jumps between crevassed strips. That is why the single-year readout is noisy (2021 shows +10 m) and the verdict comes only from the fit.
- Snow-flagged years push the front down-glacier (snow on moraine reads as ice): 2020 = +105 m vs trend. 2019-08-12 shows dark/wet bare ice in the mid-glacier → low area and W2; front unaffected.
- Widths = total mask ∩ transect length, so they include adjacent snow patches on the transect; use as a relative trend.
- If an independent check is wanted: manual front digitisation on 11 frames (≈20 min) would remove the quantisation noise.

## Files

`aksay/out/aksay_timelapse.gif`, `aksay/out/aksay_timelapse_ruler.gif`, `aksay/out/frames/*.png`, `aksay/out/frames_ruler/*.png`, `aksay/out/aksay_metrics.csv`, `aksay/out/aksay_frames.csv`, `aksay/out/aksay_metrics.png`. Re-run: `python aksay/process.py` (offline); new frames: workflow `aksay_fetch.yml`.
