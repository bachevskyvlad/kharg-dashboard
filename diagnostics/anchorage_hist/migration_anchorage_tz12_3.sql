-- ТЗ-12.3 Phase 1: Kharg anchorage history on the ТЗ-12.2 pipeline.
-- Run manually in Supabase SQL editor (project yoekhkczwdeyauwarsyy). Idempotent.
-- Reads: anon/publishable key (site). Writes: service_role key only (GHA secret).

-- ── anchorage_daily ─────────────────────────────────────────────────────────
create table if not exists public.anchorage_daily (
  date            date primary key,
  orbit           smallint     not null default 35,
  threshold       real,
  total           integer      not null,
  total_no_small  integer      not null,
  vlcc            integer      not null,
  suezmax         integer      not null,
  aframax         integer      not null,
  mr              integer      not null,
  small           integer      not null,
  masked_blobs    integer,                     -- null for dates reused from ТЗ-12.1a
  run_id          text,
  source          text         not null default 'tz12_frame_v2',
  created_at      timestamptz  not null default now()
);

-- ── anchorage_ships ─────────────────────────────────────────────────────────
create table if not exists public.anchorage_ships (
  date      date             not null references public.anchorage_daily(date) on delete cascade,
  lat       double precision not null,
  lon       double precision not null,
  length_m  integer          not null,
  class     text             not null check (class in ('vlcc','suezmax','aframax','mr','small')),
  primary key (date, lat, lon)
);
create index if not exists anchorage_ships_date_idx on public.anchorage_ships (date);

-- ── RLS: public read, writes only via service_role (bypasses RLS) ───────────
alter table public.anchorage_daily enable row level security;
alter table public.anchorage_ships enable row level security;

drop policy if exists anon_select_anchorage_daily on public.anchorage_daily;
create policy anon_select_anchorage_daily on public.anchorage_daily
  for select to anon, authenticated using (true);

drop policy if exists anon_select_anchorage_ships on public.anchorage_ships;
create policy anon_select_anchorage_ships on public.anchorage_ships
  for select to anon, authenticated using (true);

grant select on public.anchorage_daily, public.anchorage_ships to anon, authenticated;

-- ── Storage bucket for the two latest full PNGs (public read) ───────────────
insert into storage.buckets (id, name, public)
values ('anchorage_snapshots', 'anchorage_snapshots', true)
on conflict (id) do update set public = true;

drop policy if exists anchorage_snapshots_public_read on storage.objects;
create policy anchorage_snapshots_public_read on storage.objects
  for select to anon, authenticated using (bucket_id = 'anchorage_snapshots');

-- Verify:
--   select count(*) from public.anchorage_daily;
--   select * from pg_policies where tablename in ('anchorage_daily','anchorage_ships');
--   select id, public from storage.buckets where id = 'anchorage_snapshots';
