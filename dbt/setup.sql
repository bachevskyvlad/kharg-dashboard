-- Run this once in Supabase SQL Editor to create the lng_raw table
-- Dashboard → SQL Editor → New query → paste → Run

create table if not exists lng_raw (
    date             date    not null,
    country_code     text    not null,
    country_name     text,
    full_pct         float,
    gas_in_storage_twh float,
    injection_twh    float,
    withdrawal_twh   float,
    trend            text,
    created_at       timestamptz default now(),
    primary key (date, country_code)
);

create index if not exists lng_raw_date_idx on lng_raw (date desc);
create index if not exists lng_raw_country_idx on lng_raw (country_code);
