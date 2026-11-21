-- Tanker Queue track system migration
-- Run once in Supabase SQL editor before first tracks.py execution

-- 1. Add track_id to anchorage_ships (nullable; filled by tracks.py)
ALTER TABLE anchorage_ships
  ADD COLUMN IF NOT EXISTS track_id INTEGER;

-- 2. Create anchorage_tracks
CREATE TABLE IF NOT EXISTS anchorage_tracks (
  track_id      INTEGER          PRIMARY KEY,
  first_date    DATE             NOT NULL,
  last_date     DATE             NOT NULL,
  sightings     INTEGER          NOT NULL,
  lat           DOUBLE PRECISION NOT NULL,
  lon           DOUBLE PRECISION NOT NULL,
  length_median DOUBLE PRECISION NOT NULL,
  class         TEXT             NOT NULL
);

-- 3. Create anchorage_queue
CREATE TABLE IF NOT EXISTS anchorage_queue (
  date        DATE    PRIMARY KEY,
  total       INTEGER NOT NULL,
  vlcc        INTEGER NOT NULL,
  suezaframax INTEGER NOT NULL,
  small       INTEGER NOT NULL,
  arrived     INTEGER NOT NULL,
  departed    INTEGER NOT NULL
);

-- anchorage_daily: DO NOT CHANGE
