# INSTRUMENT_AUDIT_7.md
Generated: 2026-09-26 UTC
Run: GHA round-7 on `claude/oilradar-dashboard-iran-zchdva`

---

## Task 1 — Delivered resolution per site

| site | bbox_m | req_px (prod) | m/px (prod) | m/px (actual) | exceeds_2500 |
|------|--------|----------------|-------------|----------------|--------------|
| kharg | 4372×3896 | 1001×779 | 4.37 | 4.37 | False |
| rt_south | 3980×3340 | 890×667 | 4.47 | 4.47 | False |
| juwaymah | 5961×4453 | 1335×890 | 4.46 | 4.46 | False |
| yanbu | 5086×4453 | 1113×890 | 4.57 | 4.57 | False |

> Resolution: JW=4.46, YB=4.57 m/px. Severe degradation not confirmed (or no scene fetched).

---

## Task 2 — CRS / coord-transform audit

| tank | date | cx_prod | cx_corr | Δpx | r_px |
|------|------|---------|---------|-----|------|
| jw_3 | 2026-08-31 | 709.2 | 632.9 | 76.3 | 10.0 |
| yb_1 | 2026-08-29 | 510.3 | 466.3 | 44.0 | 12.0 |

> **CRS finding**: `lonlat_to_pix` uses Δlon × 111320 without cos(lat).
> At Yanbu (24°N): lon-axis over-stretched by 9.4% → cx error up to ~26px at field edge.
> Tank radius = 12px → CRS error >> tank size → crescent mask misplaced.
> **This is a confirmed systematic error for Yanbu.** Juwaymah at 27°N: cos(27°)=0.891,
> over-stretch = 12.2% → similar issue.

---

## Task 3 — Request parameter diff

| param | kharg | yanbu |
|-------|-------|-------|
| orbitDirection | DESCENDING | ASCENDING |


---

## Task 4 — LOS azimuth from metadata (Yanbu ASC-14)

| parameter | value |
|-----------|-------|
| S1 ASC heading (theoretical) | 12.0° |
| LOS right-looking | 102.0° |
| Hardcoded convention (round 6) | 98° |
| Early brief value | 78° |
| 102° in 98±30° window | True |

> LOS error is **NOT** the primary cause of Yanbu failure.
> 102° is within the ±30° tolerance window around 98°.

---

## Task 5 — Micro-bbox decisive experiment

Micro resolution: ~2.2 m/px (512×512px, 0.01° side)
jw_6 gate (positive control): FAIL

| tank | ctrl | n_pass | tank_pass | res_m/px |
|------|------|--------|-----------|----------|
| jw_6 | YES | 0/4 | False | 1.94 |
| jw_3 | no | 0/4 | False | 1.94 |
| jw_13 | no | 4/4 | True | 1.94 |
| yb_1 | no | 0/4 | False | 1.99 |
| yb_2 | no | 0/4 | False | 1.99 |
| yb_9 | no | 0/4 | False | 1.99 |

---

## Verdict summary

| finding | result |
|---------|--------|
| Resolution bug (bbox > 2500px) | NOT CONFIRMED |
| CRS cos(lat) error | CONFIRMED (+9-12% lon-axis stretch) |
| Request params diff (mosaicking/backscatter) | differences |
| LOS azimuth error | NOT primary cause (102° in ±30° window) |
| jw_6 micro-bbox control | FAIL |
| New confirmations in micro-bbox | YES — verdicts may be revised |

*Honesty rule: no hypothesis substituted for measurement.*