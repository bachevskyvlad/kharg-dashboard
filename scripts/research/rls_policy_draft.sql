-- RLS policy draft for OilRadar Supabase tables
-- Author: research/2026-10-auto
-- Status: DRAFT — DO NOT APPLY without explicit approval
-- Rules:
--   anon/publishable key: SELECT only on public-facing tables
--   writes (INSERT/UPDATE/DELETE): service_role only
--   authenticated (logged-in users): SELECT only (same as anon for data tables)
--
-- Apply order: (1) enable RLS, (2) add policies, (3) verify with pg_policies

-- ── terminal_observations ────────────────────────────────────────────────────
ALTER TABLE terminal_observations ENABLE ROW LEVEL SECURITY;

-- anon SELECT: allow all reads (public data)
CREATE POLICY "anon_select_terminal_observations"
  ON terminal_observations
  FOR SELECT
  TO anon
  USING (true);

-- service_role: full access (INSERT/UPDATE/DELETE via service_role key only)
-- service_role bypasses RLS by default in Supabase; no explicit policy needed.
-- To restrict even service_role: ALTER TABLE terminal_observations FORCE ROW LEVEL SECURITY;
-- then add: CREATE POLICY "service_role_all" ON terminal_observations FOR ALL TO service_role USING (true);

-- ── terminal_volume_timeseries ───────────────────────────────────────────────
ALTER TABLE terminal_volume_timeseries ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_select_terminal_volume_timeseries"
  ON terminal_volume_timeseries
  FOR SELECT
  TO anon
  USING (true);

-- ── raw_observations (Kharg — read-only reference) ──────────────────────────
ALTER TABLE raw_observations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_select_raw_observations"
  ON raw_observations
  FOR SELECT
  TO anon
  USING (true);

-- ── volume_timeseries ────────────────────────────────────────────────────────
ALTER TABLE volume_timeseries ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_select_volume_timeseries"
  ON volume_timeseries
  FOR SELECT
  TO anon
  USING (true);

-- ── kharg_daily (dbt mart) ───────────────────────────────────────────────────
ALTER TABLE kharg_daily ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_select_kharg_daily"
  ON kharg_daily
  FOR SELECT
  TO anon
  USING (true);

-- ── kharg_signals ────────────────────────────────────────────────────────────
ALTER TABLE kharg_signals ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_select_kharg_signals"
  ON kharg_signals
  FOR SELECT
  TO anon
  USING (true);

-- ── Verification queries (run after applying) ────────────────────────────────
-- SELECT relname, relrowsecurity, relforcerowsecurity
--   FROM pg_class
--   WHERE relname IN (
--     'terminal_observations','terminal_volume_timeseries',
--     'raw_observations','volume_timeseries','kharg_daily','kharg_signals'
--   );
--
-- SELECT tablename, policyname, cmd, roles, qual
--   FROM pg_policies
--   WHERE tablename IN (
--     'terminal_observations','terminal_volume_timeseries',
--     'raw_observations','volume_timeseries','kharg_daily','kharg_signals'
--   );
