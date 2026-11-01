-- Run this as the Neon database owner after creating mode_delay_codes.
-- Keeps the existing all_delays column names/order for app.py.
CREATE TABLE IF NOT EXISTS mode_delay_codes (
    mode TEXT NOT NULL,
    code VARCHAR(20) NOT NULL,
    description TEXT,
    PRIMARY KEY (mode, code),
    CONSTRAINT valid_transit_mode CHECK (mode IN ('subway', 'bus', 'streetcar'))
);

CREATE OR REPLACE VIEW all_delays AS
SELECT 'subway'::text AS mode, s.id::bigint AS source_id,
       s.date AS report_date, s.time, s.day::text AS day,
       s.line AS route, s.station::text AS location,
       s.code::text AS code,
       COALESCE(mc.description, dc.description, s.code)::text AS incident,
       s.min_delay, s.min_gap, s.bound::text AS direction, s.vehicle::text AS vehicle
FROM subway_delays s
LEFT JOIN mode_delay_codes mc ON mc.mode = 'subway' AND mc.code = s.code
LEFT JOIN delay_codes dc ON dc.code = s.code
UNION ALL
SELECT 'bus', b.id, b.report_date, b.time, b.day, b.route, b.location,
       b.incident::text AS code, COALESCE(mc.description, b.incident)::text AS incident,
       b.min_delay, b.min_gap, b.direction, b.vehicle
FROM bus_delays b
LEFT JOIN mode_delay_codes mc ON mc.mode = 'bus' AND mc.code = b.incident
UNION ALL
SELECT 'streetcar', st.id, st.report_date, st.time, st.day, st.route, st.location,
       st.incident::text AS code, COALESCE(mc.description, st.incident)::text AS incident,
       st.min_delay, st.min_gap, st.direction, st.vehicle
FROM streetcar_delays st
LEFT JOIN mode_delay_codes mc ON mc.mode = 'streetcar' AND mc.code = st.incident;

GRANT SELECT ON mode_delay_codes TO ttc_web;
GRANT SELECT ON all_delays TO ttc_web;
