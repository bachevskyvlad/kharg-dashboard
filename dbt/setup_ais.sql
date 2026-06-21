-- AIS vessel positions table
CREATE TABLE IF NOT EXISTS ais_positions (
    id          bigserial PRIMARY KEY,
    mmsi        text NOT NULL,
    vessel_name text,
    vessel_type int,
    is_tanker   boolean DEFAULT false,
    flag        text,
    lat         double precision,
    lon         double precision,
    speed       float,
    course      float,
    draught     float,
    terminal    text,
    timestamp   timestamptz NOT NULL,
    created_at  timestamptz DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ais_positions_mmsi_ts
    ON ais_positions (mmsi, timestamp);

CREATE INDEX IF NOT EXISTS ais_positions_terminal_ts
    ON ais_positions (terminal, timestamp DESC);

CREATE INDEX IF NOT EXISTS ais_positions_tanker
    ON ais_positions (is_tanker, terminal, timestamp DESC);

-- View: tanker arrivals (speed drops near 0 after being underway)
CREATE OR REPLACE VIEW ais_tanker_activity AS
SELECT
    terminal,
    mmsi,
    vessel_name,
    draught,
    MIN(timestamp) AS first_seen,
    MAX(timestamp) AS last_seen,
    MIN(speed)     AS min_speed,
    MAX(speed)     AS max_speed,
    COUNT(*)       AS position_count,
    DATE_TRUNC('day', MIN(timestamp)) AS day
FROM ais_positions
WHERE is_tanker = true
GROUP BY terminal, mmsi, vessel_name, draught, DATE_TRUNC('day', MIN(timestamp))
ORDER BY first_seen DESC;
