# HOTFIX_7_8 — post run #28 corrections

## 1. Task 1 labelling bug — fixed

**Bug:** In `diag_final_7_8.py` lines 256–257, the dict comprehension iterated over `full_dates + empty_dates` but assigned `("full", ...)` to every entry. Result: all 6 t60_2 dates labelled "full", `empty_rows = []`, Δ not computed, Task 1 reported INSUFFICIENT DATA despite all 6 fetches returning valid data.

**Fix:** Labels now assigned per group — `full_dates` → `"full"`, `empty_dates` → `"empty"` — in separate loops. No absolute threshold used; labels are relative to the 6-element sample (bottom-3 dB = full, top-3 dB = empty).

**Labelling unit test added:** synthetic series `[1,2,3,10,11,12]` → exactly 3 "full" + 3 "empty", no overlap. PASS.

**Re-computation:** Numbers from run #28 fetches (stored in FINAL_DIAGNOSIS.md) were reused. No re-fetch.

| group | dates | prod-282° dB | SE 135–165° dB |
|-------|-------|-------------|----------------|
| full  | 2026-04-20, 2026-04-14, 2026-05-14 | −1.42, −2.08, −1.97 | 1.28, 4.74, 0.12 |
| empty | 2026-07-14, 2018-06-13, 2019-05-27 | 14.43, 12.75, 14.26 | 3.55, 1.98, 2.89 |

Δ prod = 13.813 − (−1.823) = **+15.64 dB** ≥ 4 dB → **PASS**
Δ SE   = 2.807 − 2.047   = **+0.76 dB** < 2 dB → **PASS**

**Task 1 verdict (corrected): PASS — dihedral CONFIRMED temporally.**

---

## 2. fb870aa audit — CLEAN

`git show fb870aa`: two files changed, both contain only docstring/comment lines.

- `scripts/sar_fetch.py`: 4 lines removed, 6 lines added — module docstring only.
- `scripts/config.py`: 3 lines removed, 4 lines added — block comment above `LOS_AZIMUTH` only.

No executable code, mask geometry, calibration constants (LOS_AZIMUTH=282, WIDTH_WIDE=15), or volume formula touched. `LOS_AZIMUTH = 282` and `WIDTH_WIDE = 15` are unchanged.

pytest post-fb870aa: **45 passed, 2 xfail, 0 failures.** golden Kharg (test_golden_kharg.py): all 4 tests green.

**Verdict: fb870aa is safe. No rollback needed.**

---

## 3. Task 2 CSV robustness — tested

`_read_fcs_csv()` added to diag_final_7_8.py. Handles:
- Supabase SQL editor format: headers `terminal_id, tank_id, date, db_mean`
- ISO timestamps with timezone: `2019-01-15T00:00:00+00:00` → parsed correctly
- Plain dates: `2019-01-15` → parsed correctly
- Quoted fields, comma separator

Mini unit test (`_test_fcs_csv_parser`) runs both formats on a synthetic 1-row CSV before each FCS computation. Both formats: PASS.

---

## Status after hotfix

| Task | Status |
|------|--------|
| T1 dihedral temporal | **CONFIRMED** (Δ prod=+15.64 dB, Δ SE=+0.76 dB) |
| T2 FCS JW | не проверено / awaiting input |
| T2 FCS YB | не проверено / awaiting input |
| T3 Kharg verdict | PROD |
| T3 RT verdict | PROD |
| T3 JW/YB verdict | не проверено / awaiting input |
| T4 code audit fb870aa | CLEAN — pytest 45/0 |
| T4 shadow→dihedral patch | Applied (fb870aa) |
