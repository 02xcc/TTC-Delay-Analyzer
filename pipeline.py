"""Refresh TTC subway, bus, and streetcar delays in PostgreSQL."""
import io
import os
from datetime import date, datetime

import pandas as pd
from sqlalchemy import text

from fetch_data import list_resources, find_resource, download_resource

# Subway definitions
SUBWAY_COLUMNS = ("date", "time", "day", "station", "code", "min_delay", "min_gap", "bound", "line", "vehicle")
SUBWAY_REQUIRED = {"date", "time", "station", "code", "min_delay", "line"}
MAX_LENGTHS = {"day": 15, "station": 100, "code": 10, "bound": 5, "vehicle": 20}

# Surface definitions (Bus & Streetcar)
SURFACE_COLUMNS = ("report_date", "time", "day", "route", "location", "incident", "min_delay", "min_gap", "direction", "vehicle")
SURFACE_REQUIRED = {"report_date", "time", "route", "location", "incident", "min_delay"}

def parse_resource(resource, response):
    fmt = str(resource.get("format", "")).upper()
    if fmt == "CSV":
        return pd.read_csv(io.BytesIO(response.content), dtype=str, keep_default_na=False, encoding="utf-8-sig")
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
    for time_format in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(value, time_format).time()
        except ValueError:
            continue
    return None

def normalize_delays(raw):
    """Validate and convert downloaded SUBWAY data."""
    df = raw.copy()
    df.columns = [str(col).strip().lower().replace(" ", "_").replace(".", "_") for col in df.columns]
    missing = SUBWAY_REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"Subway delay data is missing required columns: {sorted(missing)}")
    if df.empty:
        raise ValueError("Downloaded TTC subway dataset contains no records")

    for column in SUBWAY_COLUMNS:
        if column not in df:
            df[column] = None

    df["date"] = pd.to_datetime(df["date"], errors="raise").dt.date
    df["time"] = df["time"].map(parse_time)
    if df["time"].isna().any():
        raise ValueError("Some TTC subway records are missing a time")

    for column in ("min_delay", "min_gap"):
        numeric = pd.to_numeric(df[column], errors="coerce")
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
    return df[list(SUBWAY_COLUMNS)]

def normalize_surface(raw, mode):
    """Validate and convert downloaded BUS and STREETCAR data."""
    df = raw.copy()
    df.columns = [str(col).strip().lower().replace(" ", "_").replace(".", "_") for col in df.columns]
    
    # TTC's 2025+ CSVs changed several surface-mode field names.
    # Map both the newer subway-style headers and the older bus/streetcar headers
    # onto the stable columns in our bus_delays and streetcar_delays tables.
    aliases = {
        "date": "report_date",
        "line": "route",
        "station": "location",
        "code": "incident",  # 2025+ uses delay codes instead of free-text reasons.
        "bound": "direction",
    }
    for source, target in aliases.items():
        if source in df.columns and target not in df.columns:
            df = df.rename(columns={source: target})
        
    missing = SURFACE_REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"{mode.title()} data is missing required columns: {sorted(missing)}")
    if df.empty:
        raise ValueError(f"Downloaded {mode} dataset contains no records")

    for column in SURFACE_COLUMNS:
        if column not in df:
            df[column] = None

    # Some source exports contain incomplete rows with no route or date.
    # These cannot be imported (report_date is NOT NULL), but valid rows
    # should not be discarded because a few source rows are incomplete.
    required_values = df[["report_date", "route"]].astype("string").apply(lambda col: col.str.strip())
    incomplete = required_values.isna().any(axis=1) | required_values.eq("").any(axis=1)
    skipped = int(incomplete.sum())
    if skipped:
        print(f"{mode.title()}: skipping {skipped} rows with missing date or route.")
        df = df.loc[~incomplete].copy()
    if df.empty:
        raise ValueError(f"No usable {mode} rows remain after filtering missing dates/routes")

    # Keep errors='raise': malformed non-empty dates must fail validation.
    df["report_date"] = pd.to_datetime(df["report_date"], errors="raise").dt.date
    df["time"] = df["time"].map(parse_time)

    for column in ("min_delay", "min_gap"):
        # In 2025+ exports, some missing numeric values are the literal "None".
        # Keep these as SQL NULL, not as the number 0.
        cleaned = df[column].astype("string").str.strip()
        missing = cleaned.str.lower().isin(["", "none", "null", "nan", "n/a"])
        cleaned = cleaned.mask(missing)
        numeric = pd.to_numeric(cleaned, errors="coerce")
        invalid = numeric.isna() & cleaned.notna()
        if invalid.any():
            raise ValueError(f"Invalid numeric value in {column} for {mode}")
        df[column] = numeric.astype("Int64").astype(object).where(numeric.notna(), None)

    for column in ("day", "route", "location", "incident", "direction", "vehicle"):
        values = df[column].astype("string").str.strip()
        if column == "day":
            missing_day = values.isna() | (values == "")
            values = values.mask(missing_day, pd.Series(df["report_date"]).map(
                lambda value: value.strftime("%A")
            ).astype("string"))
        df[column] = values.astype(object).where(values.notna() & (values != ""), None)

    if df["report_date"].isna().any() or df["route"].isna().any():
        raise ValueError(f"Report date or route cannot be empty for {mode}")
    return df[list(SURFACE_COLUMNS)]

def normalize_codes(resource, response):
    raw = parse_resource(resource, response)
    raw.columns = [str(column).strip().lower() for column in raw.columns]
    if "code" not in raw or "description" not in raw:
        raise ValueError("Code resource lacks code or description")
    codes = {}
    for row in raw[["code", "description"]].itertuples(index=False):
        code = str(row.code).strip()
        if code and len(code) <= 10:
            codes[code] = str(row.description).strip()
    if not codes:
        raise ValueError("Code resource had no usable codes")
    return [{"code": code, "description": desc} for code, desc in codes.items()]

def save_mode_codes(resources, mode, database_engine):
    """Upsert the published delay-code glossary for one TTC transit mode."""
    resource = find_resource(resources, "codes", formats=("CSV", "JSON"))
    entries = normalize_codes(resource, download_resource(resource))
    rows = [{"mode": mode, **entry} for entry in entries]
    with database_engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO mode_delay_codes (mode, code, description)
            VALUES (:mode, :code, :description)
            ON CONFLICT (mode, code) DO UPDATE
            SET description = EXCLUDED.description
        """), rows)
    print(f"{mode.title()} code descriptions: {len(rows)} imported.")


def first_month_to_keep(latest_date, months_to_keep):
    if months_to_keep < 1:
        raise ValueError("ROLLING_MONTHS must be at least 1")
    index = latest_date.year * 12 + (latest_date.month - 1) - (months_to_keep - 1)
    return date(index // 12, index % 12 + 1, 1)

def refresh_database(df, table_name, date_column, months_to_keep, database_engine, codes=None):
    latest_date = max(df[date_column])
    cutoff = first_month_to_keep(latest_date, months_to_keep)
    selected = df[df[date_column] >= cutoff].copy()
    if selected.empty:
        raise ValueError(f"No records were found inside the rolling window for {table_name}")
        
    with database_engine.begin() as conn:
        # Update delay codes if provided (Subway only)
        if codes:
            conn.execute(text("""
                INSERT INTO delay_codes (code, description)
                VALUES (:code, :description)
                ON CONFLICT (code) DO UPDATE
                SET description = EXCLUDED.description
            """), codes)

        # Full snapshot replacement for the mode
        conn.execute(text(f"DELETE FROM {table_name}"))
        selected.to_sql(table_name, conn, if_exists="append", index=False, chunksize=400, method="multi")
        
        # Maintain legacy route_summary specifically for subway backward compatibility
        if table_name == "subway_delays":
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
    from db import engine

    # Validate schema presence
    with engine.connect() as conn:
        views = conn.execute(text("SELECT to_regclass('public.all_delays')")).scalar()
        if not views:
            raise RuntimeError("Database view 'all_delays' is missing. Please have the DB owner run the latest migrations.")

    # 1. Subway Data Refresh
    print("Fetching Subway data...")
    subway_res = list_resources("ttc-subway-delay-data")
    delay_resource = find_resource(subway_res, "subway_delays")
    delays = normalize_delays(parse_resource(delay_resource, download_resource(delay_resource)))
    
    code_resource = find_resource(subway_res, "codes", formats=("JSON", "CSV"))
    codes = normalize_codes(code_resource, download_resource(code_resource))
    
    cutoff, latest, count = refresh_database(delays, "subway_delays", "date", months_to_keep, engine, codes)
    print(f"Subway Success: {count} rows retained ({cutoff} through {latest}).")
    save_mode_codes(subway_res, "subway", engine)

    # 2. Bus Data Refresh
    print("\nFetching Bus data...")
    bus_res = list_resources("ttc-bus-delay-data")
    bus_resource = find_resource(bus_res, "bus_delays")
    bus_data = normalize_surface(parse_resource(bus_resource, download_resource(bus_resource)), "bus")
    cutoff, latest, count = refresh_database(bus_data, "bus_delays", "report_date", months_to_keep, engine)
    print(f"Bus Success: {count} rows retained ({cutoff} through {latest}).")
    save_mode_codes(bus_res, "bus", engine)

    # 3. Streetcar Data Refresh
    print("\nFetching Streetcar data...")
    streetcar_res = list_resources("ttc-streetcar-delay-data")
    streetcar_resource = find_resource(streetcar_res, "streetcar_delays")
    streetcar_data = normalize_surface(parse_resource(streetcar_resource, download_resource(streetcar_resource)), "streetcar")
    cutoff, latest, count = refresh_database(streetcar_data, "streetcar_delays", "report_date", months_to_keep, engine)
    print(f"Streetcar Success: {count} rows retained ({cutoff} through {latest}).")
    save_mode_codes(streetcar_res, "streetcar", engine)

if __name__ == "__main__":
    run()

