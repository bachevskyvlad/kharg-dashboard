# ТЗ-16 — data cleanup after the audit (2026-11-07)

Kharg `raw_observations` was not touched (15 322 rows, last `created_at` 2026-11-06 11:03).
Workflows were not changed.

## Deleted from `terminal_observations` (Supabase)

| Step | Terminal | Dates deleted | Rows | Backup |
|---|---|---|---|---|
| 2 ASCE (fetched with DESC filter, 30-row calibration) | goreh_omid | 2026-05-13, 05-25, 06-18 | 30 | `diagnostics/tz16_backup/step2_asce_goreh_omid_terminal_observations.csv` |
| 2 ASCE | bahregan | 2026-05-13, 05-25, 06-18 | 27 | `diagnostics/tz16_backup/step2_asce_bahregan_terminal_observations.csv` |
| 4 duplicate scene (2nd day, rule from `dbt/migrations/fix_raw_observations_duplicates.sql`) | goreh_omid | 2026-05-09, 05-21, 06-02 | 30 | `diagnostics/tz16_backup/step4_dupes_goreh_omid_terminal_observations.csv` |
| 4 duplicate scene | ras_tanura_v2 | 2026-05-09, 05-21, 06-02 | 90 | `diagnostics/tz16_backup/step4_dupes_ras_tanura_v2_terminal_observations.csv` |
| 4 duplicate scene | jask | 2026-05-13, 05-25, 06-06 | 120 (20 `jask_*` + 20 legacy `jk_*` × 3) | `diagnostics/tz16_backup/step4_dupes_jask_terminal_observations.csv` |
| | **Total** | | **297** | |

Full pre-cleanup snapshots: `terminal_observations_{goreh_omid,bahregan,jask,ras_tanura_v2}_full_before.csv`
and `terminal_volume_timeseries_before.csv` in `diagnostics/tz16_backup/`.

Not deleted: bahregan has the same duplicate second days (2026-05-09, 05-21, 06-02 — 27 rows); it was not in the step 4 scope.

## Code change

`scripts/compute.py`:
- `CALIBRATION_START = {"jask": "2022-01-01"}` — p5/p95 for jask are taken from rows with `date >= 2022-01-01`; volumes are still computed for all dates; `db_mean` history untouched.
- `--terminals-only` / `--terminal a,b` flags for targeted recomputes (defaults = previous prod behaviour).

Jask calibration: old p5/p95 = −5.0000 / 14.2322 dB (all dates, 51.5 % rows at the −5 floor);
new = −5.0000 / 15.3797 dB (≥ 2022, 16.4 % at floor). Plot: `diagnostics/tz16/jask_before_after.png`.

## `terminal_volume_timeseries` recomputed (goreh_omid, bahregan, jask, ras_tanura_v2)

| Terminal | Daily rows | Values changed | max abs Δ |
|---|---|---|---|
| goreh_omid | 330 → 324 | 324 | 0.017 Mb |
| bahregan | 330 → 327 | 0 | — |
| jask | 324 → 321 | 227 | 0.337 Mb |
| ras_tanura_v2 | 323 → 320 | 320 | 0.021 Mb |

All 18 other terminals: identical before/after.
JSON regenerated with `scripts/export_terminal_json.py` (goreh_omid, bahregan, jask, lavan, siri, iran_total).
