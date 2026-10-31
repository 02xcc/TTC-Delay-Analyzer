-- PostgreSQL tables for TTC Delay Analyzer

CREATE TABLE IF NOT EXISTS subway_delays (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date DATE,
    time TIME,
    day VARCHAR(15),
    station VARCHAR(100),
    code VARCHAR(10),
    min_delay INTEGER,
    min_gap INTEGER,
    bound VARCHAR(5),
    line TEXT,
    vehicle VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS delay_codes (
    code VARCHAR(10) PRIMARY KEY,
    description TEXT
);

CREATE TABLE IF NOT EXISTS route_summary (
    line TEXT PRIMARY KEY,
    mean_delay NUMERIC(12,2),
    max_delay INTEGER,
    total_incidents BIGINT
);

CREATE INDEX IF NOT EXISTS idx_subway_delays_date
ON subway_delays (date);

CREATE INDEX IF NOT EXISTS idx_subway_delays_line_date
ON subway_delays (line, date DESC);

CREATE INDEX IF NOT EXISTS idx_subway_delays_code
ON subway_delays (code);