# hormuz_passes migration 2026-11-15

## Change 1
ALTER TABLE hormuz_passes ADD COLUMN IF NOT EXISTS fail boolean;

## Result
Success. No rows returned (DDL, existing rows unmodified).

## Trigger
Pipeline job `hormuz` was failing with PGRST204:
"Could not find the 'fail' column of 'hormuz_passes' in the schema cache"

## Rollback
ALTER TABLE hormuz_passes DROP COLUMN fail;

---

## Change 2
ALTER TABLE hormuz_passes
  ADD COLUMN IF NOT EXISTS gap_days integer,
  ADD COLUMN IF NOT EXISTS stay     integer,
  ADD COLUMN IF NOT EXISTS gone     integer;

## Trigger
After adding `fail`, pipeline failed with PGRST204 on `gap_days`.
Full analysis of hormuz_queue_backfill.py confirmed all three columns
are written by the script but were absent from the schema.

## Rollback
ALTER TABLE hormuz_passes DROP COLUMN gap_days, DROP COLUMN stay, DROP COLUMN gone;

---

## Change 3
ALTER TABLE hormuz_passes
  ADD COLUMN IF NOT EXISTS n150   integer,
  ADD COLUMN IF NOT EXISTS n250   integer,
  ADD COLUMN IF NOT EXISTS thr_db real,
  ADD COLUMN IF NOT EXISTS n_all  integer;

## Trigger
Pipeline PGRST204: "Could not find the 'n150' column of 'hormuz_passes' in the schema cache"
Full column audit of hormuz_queue_backfill.py — added all remaining missing columns at once.

## Rollback
ALTER TABLE hormuz_passes DROP COLUMN n150, DROP COLUMN n250, DROP COLUMN thr_db, DROP COLUMN n_all;

---

## Change 4
ALTER TABLE hormuz_passes ADD COLUMN IF NOT EXISTS sat text;

## Trigger
Pipeline PGRST204: "Could not find the 'sat' column of 'hormuz_passes' in the schema cache"

## Rollback
ALTER TABLE hormuz_passes DROP COLUMN sat;

---

## Change 5 — hormuz_ships table
ALTER TABLE hormuz_ships
  ADD COLUMN IF NOT EXISTS pass_date  date,
  ADD COLUMN IF NOT EXISTS pass_orbit integer,
  ADD COLUMN IF NOT EXISTS lat        real,
  ADD COLUMN IF NOT EXISTS lon        real,
  ADD COLUMN IF NOT EXISTS length_m   integer;

## Trigger
After hormuz_passes upsert succeeded, pipeline failed with PGRST204:
"Could not find the 'pass_date' column of 'hormuz_ships' in the schema cache"
Full audit of hormuz_queue_backfill.py ship_rows dict: pass_date, pass_orbit, lat, lon, length_m.

## Rollback
ALTER TABLE hormuz_ships DROP COLUMN pass_date, DROP COLUMN pass_orbit, DROP COLUMN lat, DROP COLUMN lon, DROP COLUMN length_m;
