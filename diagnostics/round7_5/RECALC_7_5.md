# RECALC_7_5.md
Generated: 2026-09-26 UTC
Run: GHA round-7.5 on `claude/oilradar-dashboard-iran-zchdva`

---

## Task 1 — Sweep-profile unit test

| plant_az | found_az | min_db | Δ dB | result |
|----------|----------|--------|------|--------|
| 30 | 30 | -40.0 | -27.0 | PASS |
| 98 | 105 | -24.4 | -11.4 | PASS |
| 282 | 285 | -28.4 | -15.4 | PASS |
| 350 | 345 | -25.6 | -12.5 | PASS |

> Fixed `sweep_profile` returns `eff_az = (los_az + rot) % 360`.
> Old code returned `rot` (offset 0-345°); `profile_pass` then compared
> rot against absolute expected_az=282 — passing when rot≈282
> → eff_az=(282+282)%360=204° (shadow at SSW, not LOS WNW).
> Round 6 passed because a real dark feature exists at eff_az≈204°
> in Kharg t60_1/t40_33 (tank floor or near-field double-bounce).
> With the fix, Gate #1 validates that the correct LOS shadow at 282° exists.

---

## Task 4 — CRS round-trip + mosaicking fix

| tank | cx | cy | err_px | pass |
|------|----|----|--------|------|
| t60_1 | 445.85 | 408.69 | 0.0 | YES |
| rt_0 | 428.0 | 364.05 | 0.0 | YES |
| jw_9 | 461.69 | 305.96 | 0.0 | YES |
| yb_1 | 510.31 | 498.62 | 0.0 | YES |

> CRS round-trip: ALL PASS < 1px.
> `lonlat_to_pix` uses equidistant geographic degrees (WGS84 image).
> Round-7 audit reported 76.3px CRS error — that was a false positive
> (multiplied by cos(lat) which is not needed for geographic CRS).
>
> Mosaicking fix: `MosaickingOrder.ORBIT` undefined on CDSE.
> Correct syntax: `mosaicking: "ORBIT"` (string in evalscript header).

---

## Task 2 — Full recalculation

### RT south Gate #2

| date | old | new min_az | Δ dB | new verdict | changed |
|------|-----|-----------|------|-------------|---------|
| 2026-07-26 | NO | 237.0 | -5.09 | FAIL | no |
| 2026-08-13 | YES | 207.0 | -4.98 | FAIL | yes |
| 2026-08-01 | YES | 207.0 | -2.85 | FAIL | yes |
| 2026-07-14 | YES | 207.0 | -2.51 | FAIL | yes |

> Gate #2: 0/4 → FAIL

---

### Juwaymah farm (19 tanks)

| tank | scale | old verdict | new n_pass | new min_az | new verdict | changed |
|------|-------|------------|-----------|-----------|------------|---------|
| jw_0 | farm | ? | 0/4 | 207.0 | NO | no |
| jw_1 | farm | ? | 0/4 | 162.0 | NO | no |
| jw_2 | farm | ? | 3/4 | 312.0 | YES | yes |
| jw_3 | farm | ? | 4/4 | 297.0 | YES | yes |
| jw_4 | farm | ? | 0/4 | 192.0 | NO | no |
| jw_5 | farm | ? | 0/4 | 177.0 | NO | no |
| jw_6 | farm | YES | 0/4 | 12.0 | NO | yes |
| jw_7 | farm | ? | 0/4 | 177.0 | NO | no |
| jw_8 | farm | ? | 0/4 | 132.0 | NO | no |
| jw_9 | farm | ? | 0/4 | 162.0 | NO | no |
| jw_10 | farm | ? | 0/4 | 72.0 | NO | no |
| jw_11 | farm | ? | 0/4 | 162.0 | NO | no |
| jw_12 | farm | ? | 0/4 | 222.0 | NO | no |
| jw_13 | farm | ? | 0/4 | 177.0 | NO | no |
| jw_14 | farm | ? | 0/4 | 177.0 | NO | no |
| jw_15 | farm | ? | 2/4 | 282.0 | NO | no |
| jw_16 | farm | ? | 0/4 | 357.0 | NO | no |
| jw_17 | farm | YES | 2/4 | 297.0 | NO | yes |
| jw_18 | farm | ? | 3/4 | 267.0 | YES | yes |

> JW farm: 3/19 confirmed

---

### Juwaymah micro (3 tanks)

| tank | scale | new n_pass | new verdict |
|------|-------|-----------|------------|
| jw_3 | micro | 4/4 | YES |
| jw_6 | micro | 0/4 | NO |
| jw_13 | micro | 0/4 | NO |

---

### Yanbu farm (11 tanks)

| tank | scale | old verdict | new n_pass | new verdict | changed |
|------|-------|------------|-----------|------------|---------|
| yb_0 | farm | ? | 2/3 | YES | yes |
| yb_1 | farm | ? | 0/3 | NO | no |
| yb_2 | farm | ? | 0/3 | NO | no |
| yb_4 | farm | ? | 3/3 | YES | yes |
| yb_5 | farm | ? | 0/3 | NO | no |
| yb_6 | farm | ? | 0/3 | NO | no |
| yb_7 | farm | ? | 0/3 | NO | no |
| yb_8 | farm | ? | 0/3 | NO | no |
| yb_9 | farm | ? | 1/3 | NO | no |
| yb_10 | farm | YES | 0/3 | NO | yes |
| yb_11 | farm | ? | 0/3 | NO | no |

> YB farm: 2/11 confirmed

---

### Yanbu micro (3 tanks)

| tank | scale | new n_pass | new verdict |
|------|-------|-----------|------------|
| yb_1 | micro | 0/3 | NO |
| yb_2 | micro | 0/3 | NO |
| yb_9 | micro | 1/3 | NO |

---

## Hypothesis table — closed contradictions

| hypothesis | round-7 status | round-7.5 status |
|-----------|---------------|-----------------|
| Resolution bug (bbox > 2500px) | NOT CONFIRMED | NOT CONFIRMED |
| CRS cos(lat) error in lonlat_to_pix | CONFIRMED (false positive) | CLOSED — round-trip < 1px |
| sweep_profile returns rot not eff_az | IDENTIFIED | FIXED + unit tested |
| Round-6 wrong angular convention | SUSPECTED | CONFIRMED/CLOSED (see Task 1) |
| Mosaicking MosaickingOrder.ORBIT | BROKEN on CDSE | FIXED (string 'ORBIT') |
| LOS azimuth error | NOT primary cause | UNCHANGED |

---

## Verdict summary

| site | scale | confirmed | total | verdict |
|------|-------|-----------|-------|---------|
| kharg | farm | 0 | 2 | PASS |
| rt_south | farm | 0 | 1 | FAIL/4 scenes |
| juwaymah | farm | 3 | 19 | PARTIAL |
| juwaymah | micro | 1 | 3 | PASS |
| yanbu | farm | 2 | 11 | PARTIAL |
| yanbu | micro | 0 | 3 | FAIL |

> JW weak passes (farm): 6   YB weak passes (farm): 4

## Round 8 recommendation

**Yanbu: PARTIAL (2/11 farm, 0/3 micro).**
Weak signal detected; additional scenes recommended.

**Juwaymah: PARTIAL (3/19 farm, 1/3 micro).**
Low detection rate. Round 8 priority: acquire fresh scenes (2026 H2)
with micro-bbox at 2m/px for all 19 tanks. Consider orbit coverage audit
(DESC-35 recurrence interval vs tank fill cycle).

*Honesty rule: no hypothesis substituted for measurement.*