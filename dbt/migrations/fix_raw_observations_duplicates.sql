-- Migration: fix duplicate consecutive-day SAR scenes in raw_observations
-- Root cause: Sentinel-1 catalog sometimes returns adjacent-day timestamps for
-- the same physical pass (scene straddles UTC midnight). The upsert key is
-- (date, tank_id), so different calendar dates bypass the constraint.
--
-- Step 1: delete rows where every tank on date D has db_mean within 0.001 dB
--         of the same tank on date D-1 (i.e., identical scene processed twice).
--
-- Run in Supabase SQL Editor.

BEGIN;

-- identify the "second" date of each duplicate pair (keep the earlier date)
WITH dup_dates AS (
  SELECT DISTINCT a.date AS dup_date
  FROM raw_observations a
  JOIN raw_observations b
    ON a.tank_id   = b.tank_id
   AND a.date      = b.date + 1          -- consecutive calendar days
   AND ABS(a.db_mean - b.db_mean) < 0.001
  GROUP BY a.date, b.date
  -- only flag if ALL tanks on that date are duplicates of the previous day
  HAVING COUNT(*) = (SELECT COUNT(DISTINCT tank_id) FROM raw_observations)
)
DELETE FROM raw_observations
WHERE date IN (SELECT dup_date FROM dup_dates);

-- Step 2: add a unique constraint so the upsert key holds
-- (already enforced by upsert on_conflict, this makes it explicit in the DB)
ALTER TABLE raw_observations
  DROP CONSTRAINT IF EXISTS raw_observations_date_tank_id_key;

ALTER TABLE raw_observations
  ADD CONSTRAINT raw_observations_date_tank_id_key
  UNIQUE (date, tank_id);

COMMIT;
