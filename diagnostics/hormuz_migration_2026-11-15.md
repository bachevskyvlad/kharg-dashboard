# hormuz_passes migration 2026-11-15

## Change
ALTER TABLE hormuz_passes ADD COLUMN IF NOT EXISTS fail boolean;

## Result
Success. No rows returned (DDL, existing rows unmodified).

## Trigger
Pipeline job `hormuz` was failing with PGRST204:
"Could not find the 'fail' column of 'hormuz_passes' in the schema cache"

## Rollback
ALTER TABLE hormuz_passes DROP COLUMN fail;
