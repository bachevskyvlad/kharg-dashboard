# event_csv — Retrospective Event Analysis Export

**Export run**: 2026-10-22T22:39:57Z  
**SHA**: `0cdca42fb22c`  
**GHA Run ID**: `39866524702`  
**Script**: `scripts/research/export_event_csv.py`

Read-only export from Supabase + CDSE catalog. NO production writes.

## Files

| File | n rows | Period | Size | Notes |
|---|---|---|---|---|
| kharg_raw_observations.csv | 15181 | 2015-04-06–2026-10-18 | 1049.8 KB | raw_observations; relOrbit/direction from CDSE date-match |
| rt_terminal_observations.csv | 37081 | 2017-03-14–2026-10-18 | 2495.8 KB | orbit_cols=yes; CDSE match appended |
| yanbu_terminal_observations.csv | 3984 | 2014-10-09–2026-09-22 | 239.1 KB | orbit_cols=yes; CDSE match appended |
| registry_kharg_rt_yanbu.csv | 125 | — | 8.5 KB | Source: tank_registry.py; RT whitelist: diagnostics/stage3/whitelist_results.json |
| tvt_kharg_rt_yanbu.csv | 1529 | 2014-10-09–2026-10-18 | 115.9 KB | volume_timeseries (Kharg) + terminal_volume_timeseries (RT, Yanbu) |
| calibration_params.csv | 5 | — | 1.2 KB | Live percentiles from DB data; model: scripts/volume.py:41 |
| anchors.csv | 171 | — | 20.3 KB | Available: hormuz_timeseries_repo, jodi_saudi_crude |
| missing_anchors.txt | — | — | 0.5 KB | Anchor files not found in repo |

## Known Caveats

1. **DESCENDING/NULL in terminal_observations** — orbit_direction column may have NULLs
   for rows ingested before the orbit column was added. Use `relative_orbit_cdse` for
   authoritative orbit assignment.
2. **Yanbu DESC-50/52 orphans** — ~40 dates in CDSE catalog have relOrbit 50 or 52
   (2014–2017); these are in terminal_observations but not in ASC-14 series.
   Flag: `orbit_direction_cdse=DESCENDING AND relative_orbit_cdse IN (50,52)`.
3. **TVT Yanbu orphans** — terminal_volume_timeseries rows for Yanbu may include
   volumes computed from mixed-orbit data (before per-orbit calibration was applied).
4. **CDSE date-match** — one CDSE scene per date is assigned by majority-vote on
   relative_orbit. Dates with multiple passes (rare) may have ambiguous assignment.
5. **Kharg raw_observations** has no relative_orbit column in DB; orbit info comes
   entirely from CDSE catalog match.
6. **Calibration params** — Kharg uses global percentiles (no orbit split);
   RT/Yanbu use per-orbit split when orbit_direction column exists.

## SQL / Script Extracts

```sql
-- raw_observations (Kharg)
SELECT date, tank_id, db_mean, created_at FROM raw_observations ORDER BY date;

-- terminal_observations (RT)
SELECT date, tank_id, db_mean, orbit_direction, relative_orbit, created_at
FROM terminal_observations WHERE terminal_id = 'ras_tanura' ORDER BY date;

-- terminal_observations (Yanbu)
SELECT date, tank_id, db_mean, orbit_direction, relative_orbit, created_at
FROM terminal_observations WHERE terminal_id = 'yanbu' ORDER BY date;
```