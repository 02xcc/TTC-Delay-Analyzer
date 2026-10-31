"""Refresh TTC subway delays and code descriptions in PostgreSQL.

The published 'since 2025' data is treated as a complete snapshot. Each run
atomically refreshes the newest ROLLING_MONTHS calendar months, so rerunning
cannot duplicate records and upstream corrections are picked up.
"""
import io
import os
from datetime import date, datetime

import pandas as pd
from sqlalchemy import text

from fetch_data import list_resources, find_resource, download_resource

COLUMNS = (
    "date", "time", "day", "station", "code", "min_delay",
    "min_gap", "bound", "line", "vehicle",
)
REQUIRED_COLUMNS = {"date", "time", "station", "code", "min_delay", "line"}
MAX_LENGTHS = {"day": 15, "station": 100, "code": 10,
               "bound": 5, "vehicle": 20}


def parse_resource(resource, response):
    fmt = str(resource.get("format", "")).upper()
    if fmt == "CSV":
        return pd.read_csv(io.BytesIO(response.content), dtype=str,
                           keep_default_na=False, encoding="utf-8-sig")
    if fmt == "JSON":
        data = response.json()
        if not isinstance(data, list):
            raise ValueError("Unexpected JSON resource format: expected a list")
        return pd.DataFrame(data)
    raise ValueError(f"Unsupported resource format: {fmt}")


def parse_time(raw):
    value = str(raw).strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return datetime.strptime(value, "%H:%M:%S").time()


def normalize_delays(raw):
    """Validate and convert downloaded data to the existing 10 non-ID columns."""
    df = raw.copy()
    df.columns = [str(col).strip().lower().replace(" ", "_").replace(".", "_")
                  for col in df.columns]
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Delay data is missing required columns: {sorted(missing)}")
    if df.empty:
        raise ValueError("Downloaded TTC delay dataset contains no records")

    for column in COLUMNS:
        if column not in df:
            df[column] = None

    # Reject invalid dates before touching the database.
    df["date"] = pd.to_datetime(df["date"], errors="raise").dt.date
    df["time"] = df["time"].map(parse_time)
    if df["time"].isna().any():
        raise ValueError("Some TTC records are missing a time")

    for column in ("min_delay", "min_gap"):
        numeric = pd.to_numeric(df[column], errors="coerce")
        # NULL is allowed in the table, but invalid nonblank numeric values are not.
        invalid = numeric.isna() & df[column].notna() & (df[column].astype(str).str.strip() != "")
        if invalid.any():
            raise ValueError(f"Invalid numeric value in {column}")
        df[column] = numeric.astype("Int64").astype(object).where(numeric.notna(), None)

    for column, limit in MAX_LENGTHS.items():
        values = df[column].astype("string").str.strip()
        if column == "day":
            missing_day = values.isna() | (values == "")
            values = values.mask(missing_day, pd.Series(df["date"]).map(
                lambda value: value.strftime("%A")
            ).astype("string"))
        if values.str.len().gt(limit).fillna(False).any():
            raise ValueError(f"Value in {column} exceeds VARCHAR({limit})")
        df[column] = values.astype(object).where(values.notna() & (values != ""), None)

    if df["date"].isna().any() or df["line"].isna().any():
        raise ValueError("Date or line cannot be empty")
    return df[list(COLUMNS)]


def first_month_to_keep(latest_date, months_to_keep):
    if months_to_keep < 1:
        raise ValueError("ROLLING_MONTHS must be at least 1")
    index = latest_date.year * 12 + (latest_date.month - 1) - (months_to_keep - 1)
    return date(index // 12, index % 12 + 1, 1)


def normalize_codes(resource, response):
    raw = parse_resource(resource, response)
    raw.columns = [str(column).strip().lower() for column in raw.columns]
    if "code" not in raw or "description" not in raw:
        raise ValueError("Code resource lacks code or description")
    # Deduplicate codes if the resource repeats any; one row per code in SQL.
    codes = {}
    for row in raw[["code", "description"]].itertuples(index=False):
        code = str(row.code).strip()
        if code and len(code) <= 10:
            codes[code] = str(row.description).strip()
    if not codes:
        raise ValueError("Code resource had no usable codes")
    return [{"code": code, "description": desc} for code, desc in codes.items()]


def refresh_database(delays, codes, months_to_keep, database_engine):
    latest_date = max(delays["date"])
    cutoff = first_month_to_keep(latest_date, months_to_keep)
    selected = delays[delays["date"] >= cutoff].copy()
    if selected.empty:
        raise ValueError("No TTC delay records were found inside the rolling window")
    # Everything is committed together or rolled back together on failure.
    with database_engine.begin() as conn:
        if codes:
            conn.execute(text("""
                INSERT INTO delay_codes (code, description)
                VALUES (:code, :description)
                ON CONFLICT (code) DO UPDATE
                SET description = EXCLUDED.description
            """), codes)

        # Full snapshot replacement: old months fall out, existing months receive
        # TTC corrections, and the newly published month enters the window.
        conn.execute(text("DELETE FROM subway_delays"))
        selected.to_sql("subway_delays", conn, if_exists="append", index=False,
                        chunksize=400, method="multi")
        conn.execute(text("DELETE FROM route_summary"))
        conn.execute(text("""
            INSERT INTO route_summary (line, mean_delay, max_delay, total_incidents)
            SELECT line,
                   ROUND(AVG(min_delay), 2),
                   MAX(min_delay),
                   COUNT(*)
            FROM subway_delays
            WHERE min_delay > 0 AND line IS NOT NULL
            GROUP BY line
        """))
    return cutoff, latest_date, len(selected)


def run():
    months_to_keep = int(os.getenv("ROLLING_MONTHS", "12"))
    resources = list_resources()
    delay_resource = find_resource(resources, "delays")
    print(f"Downloading: {delay_resource['name']}")
    delays = normalize_delays(parse_resource(delay_resource, download_resource(delay_resource)))
    print(f"Validated {len(delays)} raw delay records")

    code_resource = find_resource(resources, "codes", formats=("JSON", "CSV"))
    codes = normalize_codes(code_resource, download_resource(code_resource))
    print(f"Validated {len(codes)} delay codes")

    from db import engine  # Only connect when actually importing.
    from pathlib import Path
    schema_path = Path(__file__).resolve().parent / "database" / "schema.sql"
    # The tables should already exist from schema.sql or a prior pg_restore.
    # Existence check protects existing user-created tables and their data.
    with engine.connect() as conn:
        existing = conn.execute(text("SELECT to_regclass('public.subway_delays')")).scalar()
        codes_table = conn.execute(text("SELECT to_regclass('public.delay_codes')")).scalar()
        summary_table = conn.execute(text("SELECT to_regclass('public.route_summary')")).scalar()
    if not all((existing, codes_table, summary_table)):
        raise RuntimeError(f"Some tables are missing. First run: psql '$DATABASE_URL' -f {schema_path}")

    cutoff, latest, count = refresh_database(delays, codes, months_to_keep, engine)
    print(f"Success: {count} rows retained ({cutoff} through {latest}).")


if __name__ == "__main__":
    run()
