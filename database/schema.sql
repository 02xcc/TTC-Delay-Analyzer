-- TTC Delay Analyzer: PostgreSQL / Neon schema
-- Safe to rerun for already-existing tables, indexes, and the all_delays view.
-- CREATE TABLE IF NOT EXISTS does not change columns on an existing table.

-- Subway delays (original dataset)
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

-- Human-readable descriptions of subway delay codes
CREATE TABLE IF NOT EXISTS delay_codes (
    code VARCHAR(10) PRIMARY KEY,
    description TEXT
);

-- Existing subway summary table (maintained by the pipeline)
CREATE TABLE IF NOT EXISTS route_summary (
    line TEXT PRIMARY KEY,
    mean_delay NUMERIC(12,2),
    max_delay INTEGER,
    total_incidents BIGINT
);

-- Bus delays
CREATE TABLE IF NOT EXISTS bus_delays (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_date DATE NOT NULL,
    time TIME,
    day TEXT,
    route TEXT,
    location TEXT,
    incident TEXT,
    min_delay INTEGER,
    min_gap INTEGER,
    direction TEXT,
    vehicle TEXT
);

-- Streetcar delays
CREATE TABLE IF NOT EXISTS streetcar_delays (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_date DATE NOT NULL,
    time TIME,
    day TEXT,
    route TEXT,
    location TEXT,
    incident TEXT,
    min_delay INTEGER,
    min_gap INTEGER,
    direction TEXT,
    vehicle TEXT
);

-- Existing subway indexes
CREATE INDEX IF NOT EXISTS idx_subway_delays_date
    ON subway_delays (date);
CREATE INDEX IF NOT EXISTS idx_subway_delays_line_date
    ON subway_delays (line, date DESC);
CREATE INDEX IF NOT EXISTS idx_subway_delays_code
    ON subway_delays (code);

-- Bus indexes
CREATE INDEX IF NOT EXISTS idx_bus_route_date
    ON bus_delays (route, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_bus_date
    ON bus_delays (report_date);

-- Streetcar indexes
CREATE INDEX IF NOT EXISTS idx_streetcar_route_date
    ON streetcar_delays (route, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_streetcar_date
    ON streetcar_delays (report_date);

-- A shared read-only interface across the three transportation modes.
-- Note: (mode, source_id) identifies a row within the current import snapshot.
CREATE OR REPLACE VIEW all_delays AS
SELECT
    'subway' AS mode,
    s.id AS source_id,
    s.date AS report_date,
    s.time,
    s.day,
    s.line AS route,
    s.station AS location,
    s.code AS code,
    COALESCE(dc.description, s.code) AS incident,
    s.min_delay,
    s.min_gap,
    s.bound AS direction,
    s.vehicle
FROM subway_delays AS s
LEFT JOIN delay_codes AS dc
    ON s.code = dc.code

UNION ALL

SELECT
    'bus' AS mode,
    b.id AS source_id,
    b.report_date,
    b.time,
    b.day,
    b.route,
    b.location,
    NULL AS code,
    b.incident,
    b.min_delay,
    b.min_gap,
    b.direction,
    b.vehicle
FROM bus_delays AS b

UNION ALL

SELECT
    'streetcar' AS mode,
    st.id AS source_id,
    st.report_date,
    st.time,
    st.day,
    st.route,
    st.location,
    NULL AS code,
    st.incident,
    st.min_delay,
    st.min_gap,
    st.direction,
    st.vehicle
FROM streetcar_delays AS st;
