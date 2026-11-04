# FINAL_DIAGNOSIS — Round 7.8

*Диагностика закрыта. После этого раунда — только прод-ТЗ.*


## Task 1 — Dihedral temporal test (t60_2)

### Date matching unit test

Unit test result: PASS — all 5 cases correct

### Labelling unit test

Labelling relative to sample (not absolute threshold): synthetic 6-element series → 3/3 split confirmed. PASS.

### t60_2 target dates (from Kharg CSV)

| date | status | CSV db_mean |
|------|--------|-------------|
| 2026-04-20 | full  | -3.015 |
| 2026-04-14 | full  | -2.918 |
| 2026-05-14 | full  | -2.630 |
| 2026-07-14 | empty | 18.580 |
| 2018-06-13 | empty | 18.726 |
| 2019-05-27 | empty | 18.775 |

Micro-bbox fetches used: **6/6** (exception from no-download rule, logged here)

### Results (from run #28 cache — no re-fetch)

| date | status | CSV dB | prod-282° dB | SE 135–165° dB | source |
|------|--------|--------|--------------|----------------|--------|
| 2026-04-20 | full  | -3.01 | -1.42 |  1.28 | fetch (run #28) |
| 2026-04-14 | full  | -2.92 | -2.08 |  4.74 | fetch (run #28) |
| 2026-05-14 | full  | -2.63 | -1.97 |  0.12 | fetch (run #28) |
| 2026-07-14 | empty | 18.58 | 14.43 |  3.55 | fetch (run #28) |
| 2018-06-13 | empty | 18.73 | 12.75 |  1.98 | fetch (run #28) |
| 2019-05-27 | empty | 18.78 | 14.26 |  2.89 | fetch (run #28) |

**Δ prod-282°** (empty − full) = mean(14.43, 12.75, 14.26) − mean(−1.42, −2.08, −1.97) = 13.813 − (−1.823) = **+15.64 dB** (criterion ≥+4 dB) → **PASS**

**Δ SE 135–165°** (empty − full) = mean(3.55, 1.98, 2.89) − mean(1.28, 4.74, 0.12) = 2.807 − 2.047 = **+0.76 dB** (criterion < 2 dB) → **PASS**

**Task 1 verdict: PASS — dihedral CONFIRMED temporally.**

[FINDING] prod-282° separates full/empty dates by +15.64 dB; SE sector contrast is +0.76 dB (weak, as predicted by dihedral model). Both criteria satisfied. Consistent with double-bounce physics: peak contrast at the far-wall hemisphere (282°), not at SE (135–165°).

Task 1 does NOT gate Tasks 2–3.

---

## Task 2 — FCS Juwaymah / Yanbu

Input files: `diagnostics/round7_8/jw_fcs_input.csv` and `yb_fcs_input.csv`
(User runs SQL from `diagnostics/round7_7/task4_jw_yb_query.sql` and places output here.)

**Juwaymah: awaiting manual SQL input** — `diagnostics/round7_8/jw_fcs_input.csv` not found. Task 2 JW: не проверено / awaiting input.

**Yanbu: awaiting manual SQL input** — `diagnostics/round7_8/yb_fcs_input.csv` not found. Task 2 YB: не проверено / awaiting input.

CSV format accepted: Supabase SQL editor export (comma-separated, quoted fields, timestamps with or without timezone, headers: terminal_id, tank_id, date, db_mean). Both `2019-01-15T00:00:00+00:00` and `2019-01-15` are valid date formats.

---

## Task 3 — Final site verdicts

| Site | Tanks | Cycling (FCS > 3.45) | Verdict |
|------|-------|----------------------|---------|
| Kharg | 47 | 7 | PROD — terminal series, validated r=0.7; majority static is a site property |
| RT south | 66 | 9 | PROD — terminal-level, validated by 2026 war events; per-tank signal weak |
| Juwaymah | — | — | не проверено / awaiting input |
| Yanbu | — | — | не проверено / awaiting input |

**Verdict rules applied:**
- `cycling ≥ 6` AND `dyn_range > 6 dB median` AND `med_N ≥ 10` → CANDIDATE
- `cycling 1–5` OR SNR insufficient → TANK-LEVEL only
- `cycling = 0` → NOT APPLICABLE
- `median N dates < 10` → INSUFFICIENT DATA
- File absent → не проверено / awaiting input

---

## Task 4 — Final diagnosis

### Physics of the method (dihedral)

The SAR signal measured by the crescent mask is **double-bounce (dihedral)** from the far interior wall of the tank — NOT a shadow.

Mechanism: the radar beam (282° azimuth, descending orbit) strikes the floating roof at grazing incidence; the beam then bounces off the inner cylinder wall and returns to the sensor (two-surface specular reflection). An EMPTY tank has its roof near the bottom: the beam travels the full interior height, produces a strong dihedral return → high dB. A FULL tank has its roof near the top: the beam barely enters, the dihedral path is short → low dB. Direction matters because the beam must reach the far wall; the 282° mask selects the hemisphere where this geometry is strongest.

Evidence:
- Round 7.7 sign-check: t60_1 (full) prod-mask = +1.52 dB; t55_5 (empty) = +10.29 dB (CSV reference 2.82 vs 13.02 — confirmed).
- Round 7.7 sector tables: Δ(empty−full) peaks at 270–300° (WNW), falls to ~0 dB at 90–180° (SE). Direction-dependent: dihedral model.
- Round 7.6: np.array_equal(prod_mask, diag_mask) = True (diff=0 px) — diagnostic tool is pixel-identical to prod.
- Round 7.8 Task 1: Δ prod-282° = +15.64 dB (PASS ≥4); Δ SE = +0.76 dB (PASS <2). Dihedral CONFIRMED temporally on t60_2.

### Error chronicle rounds 2–7.5

**Shadow-sector sweep criterion (rounds 2–7.5):** All sweep verdicts for Juwaymah and Yanbu in rounds 6–7.5 were based on the prediction that the minimum-dB sector should lie at 282° (shadow at the far wall). Round 7.6 proved the minimum is at 117–177° (SE), not 282°. The test was measuring the wrong physical quantity. All JW/YB verdicts from those rounds are **ANNULLED**.

**rot vs eff_az bug (round 7.5):** sweep_profile returned raw `rot` offset (0–345°) instead of `eff_az = (los_az + rot) % 360`. Min-az was reported relative to offset, not absolute azimuth. Fixed in round 7.5 and confirmed in 7.6.

**Circular scene selection (rounds 3–5):** SAR scenes were selected by their own dB value ("high-signal dates"), biasing the sample toward already-cycling tanks. Fixed by using catalog-only metadata for scene selection.

**Hough 213m artifact (round 4):** Tank radius detection via Hough transform returned 213m due to CRS pixel-scale mismatch (degrees vs metres). Production uses hardcoded radius from tank registry.

**Black crop images (rounds 3–5):** Crops saved in linear domain appeared black (values ~1e-4). Fixed by converting to dB before display.

**Task 1 labelling bug (round 7.8, run #28):** All 6 t60_2 target dates labelled "full" due to dict comprehension hardcoding the tuple `("full", ...)` for all entries. Fixed in hotfix: labels assigned per group (full_dates → "full", empty_dates → "empty"), with a 3/3 split unit test on a synthetic series. Numbers from run #28 fetches were reused — no re-fetch.

**"Plausible explanations instead of checking" (rounds 2–6):** Recurring pattern: when a test failed, the failure was attributed to a site-specific property (weak signal, few tanks, wrong orbit) rather than verifying the test instrument itself. Round 7.6 was the first round to test the instrument (sweep ≡ prod mask), which immediately identified the shadow-direction error.

### What stands / what is annulled

**STANDS:**
- Mask equivalence (7.6): prod mask = diag mask, diff=0 px.
- Sign-check (7.7): t60_1=+1.52 dB (full), t55_5=+10.29 dB (empty).
- FCS calibration (7.7): threshold=3.45; Kharg 7/47; RT 9/66.
- Task 1 dihedral temporal (7.8): t60_2 Δ prod=+15.64 dB, Δ SE=+0.76 dB — CONFIRMED.
- RT cycling validation (rounds 1–3): built on prod-mask dB — the mask was correct; the physics interpretation is updated (dihedral, not shadow) but the signal is the same.
- Kharg prod validation: r=0.7 vs independent benchmarks.
- pytest: 45 passed, 2 xfail, 0 failures (post fb870aa); golden Kharg green.

**ANNULLED:**
- All sweep-based JW/YB verdicts from rounds 6–7.5 (tested shadow-direction, wrong criterion).
- Well-depth (pure isotropic) hypothesis: sector tables show azimuth-dependent contrast, not uniform.

### Code comment audit — fb870aa

`git show fb870aa`: diff contains **only docstring and comment lines** in `scripts/sar_fetch.py` (lines 13–16 of module docstring) and `scripts/config.py` (lines 60–62, block comment above `LOS_AZIMUTH`). No executable code, mask geometry, calibration constants, or volume formula was changed. pytest 45/0 green confirms no regression.

### Code comment diff (shadow → dihedral) — applied in fb870aa

```diff
--- a/scripts/sar_fetch.py
+++ b/scripts/sar_fetch.py
@@ -10,10 +10,12 @@

 The crescent mask technique (inverse crescent radiometry):
   - Sentinel-1 illuminates from azimuth ~282° (SW→NE, descending orbit).
-  - The floating roof casts a radar shadow on the FAR wall of the tank.
-  - We mask a ring annulus on the far-side half of each tank.
-  - Mean linear backscatter in that crescent → convert to dB → fill proxy.
-  - Lower dB = full tank (more oil = smoother surface = less radar return).
+  - The far interior wall of the tank produces a double-bounce (dihedral)
+    return. Empty tank: roof at bottom, long dihedral path → high dB.
+    Full tank: roof near top, short/absent dihedral path → low dB.
+  - We mask a ring annulus on the far-side half (282° for DESC) where
+    the dihedral geometry is strongest.
+  - Lower dB = full tank; higher dB = empty/draining tank.
--- a/scripts/config.py
+++ b/scripts/config.py
@@ -57,9 +57,10 @@
 # Sentinel-1 descending orbit illuminates from the SW at Kharg (azimuth ~282°).
-# The crescent mask captures the radar shadow on the FAR side of each tank wall.
-# Wider crescent = more shadow pixels = more stable dB mean, but risk of
-# capturing adjacent tank reflections. 15m annulus is empirically validated.
+# The crescent mask captures the double-bounce (dihedral) return from the FAR
+# interior wall. 282° selects the hemisphere of maximum dihedral contrast for
+# DESC orbit. Wider annulus = more pixels = more stable dB, but risk of
+# spilling into adjacent tanks. 15m is empirically validated.
```

### Backlog

See `diagnostics/backlog.md`.

---
## Stop-line

Диагностика закрыта. Дальнейшие работы — по backlog.md отдельными ТЗ.

Summary: Kharg PROD (validated). RT PROD (validated). Task 1 dihedral CONFIRMED (Δ prod=+15.64 dB, Δ SE=+0.76 dB). JW/YB: не проверено / awaiting input — re-run after placing CSV files in diagnostics/round7_8/. No prod logic changed. No Supabase writes. No paid API calls. pytest 45/0 green.
