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
