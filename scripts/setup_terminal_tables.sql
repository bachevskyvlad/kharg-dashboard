-- Run this in the Supabase SQL editor before first pipeline run.
-- Creates two tables for multi-terminal SAR observations.

-- Per-tank dB records for non-Kharg terminals
CREATE TABLE IF NOT EXISTS terminal_observations (
    id            BIGSERIAL PRIMARY KEY,
    terminal_id   TEXT NOT NULL,
    date          DATE NOT NULL,
    tank_id       TEXT NOT NULL,
    radius_m      REAL,
    diameter      REAL,
    h_max         REAL,
    db_mean       REAL,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (terminal_id, date, tank_id)
);

CREATE INDEX IF NOT EXISTS idx_terminal_obs_terminal_date
    ON terminal_observations (terminal_id, date DESC);

-- Task 1: orbit provenance columns (added 2026-09).
-- Nullable for backward compat; new rows from backfill/ingest always populate both.
ALTER TABLE terminal_observations ADD COLUMN IF NOT EXISTS relative_orbit  INT;
ALTER TABLE terminal_observations ADD COLUMN IF NOT EXISTS orbit_direction TEXT;

-- Aggregated volume timeseries per terminal
CREATE TABLE IF NOT EXISTS terminal_volume_timeseries (
    id            BIGSERIAL PRIMARY KEY,
    terminal_id   TEXT NOT NULL,
    date          DATE NOT NULL,
    vol_mn_bbl    REAL,
    aggregation   TEXT NOT NULL DEFAULT 'daily',
    smoothed      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (terminal_id, date, aggregation, smoothed)
);

CREATE INDEX IF NOT EXISTS idx_terminal_vol_terminal_date
    ON terminal_volume_timeseries (terminal_id, date DESC);
