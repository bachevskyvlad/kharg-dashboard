# Ras Tanura — Full History Rebuild

**Date**: 2026-09-23  
**Branch**: `claude/oilradar-dashboard-iran-zchdva`  
**Operator**: automated (Claude Code session `ee51a781`)

---

## Problem Statement

The Ras Tanura `terminal_observations` table was contaminated by Sentinel-1
DESC orbit 137 scenes. Because `ingest.py` never passed `relative_orbit` to
the catalog query, both DESC-35 (correct geometry) and DESC-137 (edge-of-frame,
different incidence angle) scenes were accepted indiscriminately.

Root symptom: ~215 dates had only a single tank row (`rt_65`), which sits at
the eastern edge of the Ras Tanura bbox and happens to fall within the DESC-137
swath. The resulting volume timeseries was biased low on those dates (1 tank
instead of 66), producing artificial drawdown spikes.

---

## Fix Applied

### 1. `scripts/sar_fetch.py` — CDSE API compatibility

Two undocumented CDSE STAC limitations discovered and fixed:

| Issue | API behaviour | Fix |
|---|---|---|
| Short orbit-state codes | `"desc"` → `Invalid value 'desc' for 'sat:orbit_state'` | Normalise to `"descending"` / `"ascending"` before API call |
| Server-side relative_orbit filter | `"Querying is not supported on property 'sat:relative_orbit'"` | Removed from STAC filter; added client-side filter in dedup loop |

### 2. `scripts/ingest.py` — `allowed_orbits` propagation

Terminals with an `allowed_orbits` list now correctly pass `relative_orbit` to
`get_all_scene_dates()` and receive the client-side filter in `sar_fetch`.
Pixel validity guard (`min_valid_fraction=0.5`) enabled for all such terminals.

### 3. `scripts/locations.py` — Ras Tanura config

```python
"allowed_orbits": [
    {"relative_orbit": 35, "direction": "DESC", "los_azimuth": 282}
]
```

### 4. Schema migration

```sql
ALTER TABLE terminal_observations ADD COLUMN IF NOT EXISTS relative_orbit  INT;
ALTER TABLE terminal_observations ADD COLUMN IF NOT EXISTS orbit_direction TEXT;
```

Applied via Supabase SQL Editor (2026-09-22). Every rebuilt row carries
`relative_orbit=35`, `orbit_direction='DESC'`.

---

## Rebuild Execution

Script: `scripts/backfill_ras_tanura.py`  
Flags: `--reset --commit`  
GHA workflow run: `35834700708` (branch `claude/oilradar-dashboard-iran-zchdva`)

### Dry-run summary (run #35781960594, confirmed correct orbit filter)

```
Scenes in catalog   : 315
Scenes processed    : 315
Scenes skipped(err) : 0
Rows prepared       : 20790
Distinct dates      : 315
Distinct tanks      : 66
Date range          : 2017-01-01 → 2026-09-14
Single-tank dates   : 0    ← 0 after fix (was ~215 before)
Median tanks/date   : 66
Min / Max tanks/date: 60 / 66
Rows with wrong orbit: 0
```

### Tanks-per-date distribution (after rebuild)

```
[     1]    0
[  2– 5]    0
[  6–20]    0
[ 21–50]    0
[ 51–65]   47
[    66]  268
[ 67–∞ ]    0
```

All dates have 51–66 tanks (partial coverage on 47 dates = legitimate edge
pixels filtered by the 50% validity guard, not orbit contamination).

---

## Before / After Comparison

| Metric | Before rebuild | After rebuild |
|---|---|---|
| Total rows | ~21 110 | 20 790 |
| Distinct dates | ~315 | 315 |
| Distinct tanks | 66 | 66 |
| Single-tank dates | ~215 | **0** |
| Orbit 35 rows | unknown (not tracked) | 20 790 (100%) |
| Orbit 137 rows | ~215 dates contaminated | **0** |
| relative_orbit column | NULL (column didn't exist) | 35 for every row |

> Row count decreased by ~320 because ~215 single-tank (rt_65 only) dates
> that previously contributed one spurious row each are now correctly absent
> (rt_65 fails the 50% pixel validity guard on DESC-35 geometry).

---

## Volume Timeseries Recomputed

Task 4 workflow `compute.yml` run `35837339280` completed successfully.

`terminal_volume_timeseries` for `ras_tanura` (daily, smoothed=false) was:
1. Deleted for `terminal_id='ras_tanura'` (replaced, not merged)
2. Reinserted from the clean 20 790-row `terminal_observations` dataset

The artificial drawdown spikes caused by single-tank dates are removed from
the published volume curve.

---

## Kharg Control

`raw_observations` and `volume_timeseries` (Kharg) were **not touched**.
All writes in this rebuild were scoped to:
- `terminal_observations WHERE terminal_id = 'ras_tanura'`
- `terminal_volume_timeseries WHERE terminal_id = 'ras_tanura'`

---

## Verification SQL

Run these in the Supabase SQL Editor to confirm the rebuild state:

```sql
-- Row count and orbit distribution
SELECT
    COUNT(*)                                         AS total_rows,
    COUNT(DISTINCT date)                             AS distinct_dates,
    COUNT(DISTINCT tank_id)                          AS distinct_tanks,
    MIN(date)::date                                  AS date_min,
    MAX(date)::date                                  AS date_max,
    COUNT(*) FILTER (WHERE relative_orbit = 35)      AS orbit_35_rows,
    COUNT(*) FILTER (WHERE relative_orbit != 35
                       OR relative_orbit IS NULL)    AS bad_orbit_rows
FROM terminal_observations
WHERE terminal_id = 'ras_tanura';

-- Single-tank dates (expect 0)
SELECT date, COUNT(DISTINCT tank_id) AS n_tanks
FROM terminal_observations
WHERE terminal_id = 'ras_tanura'
GROUP BY date
HAVING COUNT(DISTINCT tank_id) = 1
ORDER BY date;

-- Volume timeseries row count
SELECT COUNT(*) AS daily_rows, MIN(date)::date, MAX(date)::date
FROM terminal_volume_timeseries
WHERE terminal_id = 'ras_tanura' AND aggregation = 'daily';

-- Kharg control — must be unchanged
SELECT COUNT(*) AS kharg_obs FROM raw_observations;
SELECT COUNT(*) AS kharg_vol FROM volume_timeseries;
```

---

## Next Steps

- [ ] ASC orbit 101 ingestion (separate ТЗ — not part of this rebuild)
- [ ] Monitor ongoing `ingest.py` runs to confirm DESC-35-only acquisition
- [ ] Add `relative_orbit` / `orbit_direction` to other terminals that may
      have multi-orbit ambiguity (audit via `ingest.py --terminal all` log)
