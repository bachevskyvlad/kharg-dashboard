-- Migration: plain table for Kharg gross flows computed in Python (scripts/gross_flows.py).
-- Replaces the in-database logic (RPC refresh_gross_flows() + matview kharg_gross_flows).
-- No functions, triggers, views or generated columns: the DB only stores rows.
--
-- Run once in Supabase SQL Editor BEFORE the next monitor.yml run.
-- Writes come from the pipeline with the publishable key (same as volume_timeseries),
-- hence the anon insert/update policies below.

BEGIN;

CREATE TABLE IF NOT EXISTS public.kharg_gross_flows_v2 (
    date_to         DATE        PRIMARY KEY,          -- upsert key (on_conflict=date_to)
    date_from       DATE        NOT NULL,
    days            INT         NOT NULL,
    gross_out_mb    NUMERIC(10,4) NOT NULL,
    gross_in_mb     NUMERIC(10,4) NOT NULL,
    net_mb          NUMERIC(10,4) NOT NULL,
    tanks_draining  INT         NOT NULL,
    tanks_filling   INT         NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.kharg_gross_flows_v2 ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS gf_v2_read   ON public.kharg_gross_flows_v2;
DROP POLICY IF EXISTS gf_v2_insert ON public.kharg_gross_flows_v2;
DROP POLICY IF EXISTS gf_v2_update ON public.kharg_gross_flows_v2;
CREATE POLICY gf_v2_read   ON public.kharg_gross_flows_v2 FOR SELECT USING (true);
CREATE POLICY gf_v2_insert ON public.kharg_gross_flows_v2 FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY gf_v2_update ON public.kharg_gross_flows_v2 FOR UPDATE TO anon USING (true) WITH CHECK (true);

-- Legacy objects: DEPRECATED 2026-11-06, kept for the 2-week transition.
-- DO-block so it works whatever kharg_gross_flows actually is (matview / view / table)
-- and whatever the RPC signature is.
DO $$
DECLARE k "char"; f regprocedure;
BEGIN
  SELECT c.relkind INTO k FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname='kharg_gross_flows';
  IF k IS NOT NULL THEN
    EXECUTE format('COMMENT ON %s public.kharg_gross_flows IS %L',
      CASE k WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'v' THEN 'VIEW' ELSE 'TABLE' END,
      'DEPRECATED 2026-11-06: replaced by table kharg_gross_flows_v2 (scripts/gross_flows.py). Drop after 2026-11-20.');
  END IF;
  FOR f IN SELECT p.oid::regprocedure FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname='refresh_gross_flows' LOOP
    EXECUTE format('COMMENT ON FUNCTION %s IS %L', f,
      'DEPRECATED 2026-11-06: no longer called by the pipeline. Drop after 2026-11-20.');
  END LOOP;
END $$;

COMMIT;

-- Audit (run after; expect: only the deprecated function/matview, both to be dropped 2026-11-20):
-- select p.proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public';
-- select tgrelid::regclass, tgname from pg_trigger where not tgisinternal;
-- select table_name from information_schema.views where table_schema='public';
-- select matviewname from pg_matviews where schemaname='public';
-- select table_name, column_name from information_schema.columns where table_schema='public' and is_generated='ALWAYS';
--
-- 2026-11-20 cleanup:
-- DROP FUNCTION IF EXISTS public.refresh_gross_flows();
-- DROP MATERIALIZED VIEW IF EXISTS public.kharg_gross_flows;
