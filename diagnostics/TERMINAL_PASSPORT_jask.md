# TERMINAL_PASSPORT_jask

Terminal: Jask (Iran, Hormuz Strait eastern approach)
Generated: 2026-10-10
Branch: claude/oilradar-dashboard-iran-zchdva

---

## Coverage

| Field | Value |
|---|---|
| Terminal ID | jask |
| Tank count | 20 (jk_0 – jk_19) |
| Orbit | relOrbit=93, DESCENDING |
| Data start | 2024-06-01 (approximate, from terminal_observations) |
| SAR source | Sentinel-1 GRD, VV, CDSE SentinelHub Process API |
| S2 source | Sentinel-2 L2A, CDSE SentinelHub Process API |

---

## Root Cause — Uniform Tiles (resolved 2026-10-10)

`"resx": 5, "resy": 5` с CRS=EPSG:4326 → SentinelHub трактует значения как ГРАДУСЫ → тайл 1×1 пиксель.

**Fix**: use `width`/`height` pixel counts computed from physical span in metres.
All SH Process API calls in `jask_panels.py` use `_tile_px(bbox, res_m)` → `width`/`height`.
Assert: `sar_evalscript.assert_tile_size(arr)` — raises if either dimension < 64 px.

---

## Resolution Contract

| Sensor | res_m | typical w×h (jk_0, r=38m) |
|---|---|---|
| SAR (S1-GRD VV) | 5 m/px | 128×128 |
| S2 (B04/B03/B02) | 10 m/px | 128×128 |

Both use `width`/`height` (pixel counts), never `resx`/`resy` (degree units).

---

## SAR Platform / Orbit Labels

Labels on panels: `{platform}  {date}  orb={rel_orbit}  {direction}  db={db:.1f}dB`

- `platform` and `rel_orbit` taken from the scene actually used: SH Catalog search ±1 day of target date, closest feature by datetime.
- If catalog returns no match → label shows `orb=?`, not the query param `93`.
- Assert: both SAR panels must have the same `rel_orbit` from metadata; mismatch → PANEL FAIL, tile not rendered.
- S1B decommissioned 2021-12-23. Labels SAR-1/SAR-2 are TEMPORAL (earlier/current), not platform.

---

## Known Tile Issues

| Tank | Issue | Status |
|---|---|---|
| jk_18 | S2 n_unique=92 (low texture) | Show separately, human verdict |

---

## Data Quality

- No gap-filling on missing SAR dates.
- sat_frac threshold for S2: 0.5 (any-band reflectance ≥ 0.5); FAIL not excused as "sand".
- S2 stretch: per-channel 2–98% percentile (FLOAT32 evalscript, Python-side); avoids desert colour cast.

---

## Optical Gate (run 38043640725, SHA 25d98f6, 2026-10-10)

**Code**: shadow_centre = sun_az fix present (commit fb303bd ≺ 25d98f6). ✓

**Gate 1 — Kharg repères (n=30 S2 scenes)**

| Group | Tanks | Median shadow_frac | n |
|---|---|---|---|
| EMPTY | t55_5, t55_10 | 0.4546 | 30 each |
| FULL | t60_1, t40_33, t55_31 | 0.3058 | 30 each |

Gap (EMPTY − FULL) = **+0.1487** → Gate 1: **PASS**

Per-tank:

| Tank | Group | n | Median | p25 | p75 |
|---|---|---|---|---|---|
| t55_5 | EMPTY | 30 | 0.5370 | 0.4317 | 0.6569 |
| t55_10 | EMPTY | 30 | 0.3941 | 0.1293 | 0.5060 |
| t60_1 | FULL | 30 | 0.3520 | 0.3200 | 0.3936 |
| t40_33 | FULL | 30 | 0.3137 | 0.3019 | 0.3333 |
| t55_31 | FULL | 30 | 0.0095 | 0.0000 | 0.0833 |

**Gate 2 — RT cross-sensor corr**: DID NOT COMPLETE.
`SUPABASE_URL` secret was empty in the workflow → `MissingSchema` crash at SAR fetch.
No cross-sensor corr computed in this run. Gate 2 verdict: **PENDING**.

**Gate overall**: INCOMPLETE (Gate 1 PASS; Gate 2 not run).
Valid_fraction per tank: 30/30 = 1.0 for all 5 (S2 shadow metric; n=30).

**Note on run.txt**: `diagnostics/optical/run.txt` on branch shows gap=−0.1139 (FAIL) and
median_corr=0.0203 — this is from a pre-fix local run (wrong shadow_centre = anti-sun,
wrong EMPTY tanks t40_18/t40_22). Not the same as run 38043640725. Discard.

---

## cv_level Corridor (PENDING recalibration)

Current corridor [0.0980, 0.6358] was calibrated in dB domain (`std(dB)/mean(dB)`).
Formula changed to linear domain: `cv = std(10^(dB/10)) / mean(10^(dB/10))`.
New corridor pending run of `series_health_standard.py` — see SERIES_HEALTH_STANDARD.md.

---

## Panel Run Status

| Run | SHA | Tanks | Status |
|---|---|---|---|
| 38053989023 | 687cd54 | all 20 | in progress (orbit-assert fix) |
| 38048348143 | 9cd1f12 | jk_0 only | success (per-channel stretch OK) |
