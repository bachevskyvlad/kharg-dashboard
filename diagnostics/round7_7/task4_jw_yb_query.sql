-- Round 7.7 Task 4 — JW/YB FCS: terminal_observations
-- Run these two queries in Supabase SQL editor (or psql).
-- Paste output as CSV → diagnostics/round7_7/jw_obs.csv and yb_obs.csv

-- ── Juwaymah ──────────────────────────────────────────────────────────────────
-- Expected: ~6004 rows
SELECT
    terminal_id,
    tank_id,
    date::date        AS date,
    db_mean
FROM terminal_observations
WHERE terminal_id = 'juwaymah'
ORDER BY date, tank_id;

-- ── Yanbu — POST-PIVOT ONLY (ascending orbit, after 2016-12-31) ───────────────
-- Expected: ~3984 rows (pre-pivot rows excluded by the WHERE clause)
SELECT
    terminal_id,
    tank_id,
    date::date        AS date,
    db_mean
FROM terminal_observations
WHERE terminal_id = 'yanbu'
  AND date > '2016-12-31'
ORDER BY date, tank_id;
