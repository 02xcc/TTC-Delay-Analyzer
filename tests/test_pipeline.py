from datetime import date
import pandas as pd
import pytest

from fetch_data import find_resource
from pipeline import (
    first_month_to_keep,
    normalize_delays,
    normalize_codes,
)


def test_cutoff_crosses_year():
    assert first_month_to_keep(date(2026, 10, 25), 12) == date(2025, 11, 1)
    assert first_month_to_keep(date(2026, 1, 15), 3) == date(2025, 11, 1)

    with pytest.raises(ValueError):
        first_month_to_keep(date(2026, 1, 15), 0)


def test_find_correct_resource_not_first_csv():
    resources = [
        {
            "name": "Code Descriptions.csv",
            "format": "CSV",
            "url": "wrong.csv",
        },
        {
            "name": "TTC Subway Delay Data since 2025.csv",
            "format": "CSV",
            "url": "right.csv",
        },
    ]

    assert find_resource(resources, "delays")["url"] == "right.csv"
    assert find_resource(resources, "codes")["url"] == "wrong.csv"


def test_normalize_csv_columns_and_types():
    raw = pd.DataFrame([{
        "_id": "9",
        "Date": "2025-01-01",
        "Time": "02:10",
        "Day": "Wednesday",
        "Station": "BATHURST STATION",
        "Code": "MUSAN",
        "Min Delay": "5",
        "Min Gap": "9",
        "Bound": "E",
        "Line": "BD",
        "Vehicle": "05227",
    }])

    result = normalize_delays(raw)

    assert "_id" not in result.columns
    assert result.iloc[0]["date"] == date(2025, 1, 1)
    assert result.iloc[0]["min_delay"] == 5
    assert result.iloc[0]["vehicle"] == "05227"


def test_empty_dataset_is_rejected():
    with pytest.raises(ValueError):
        normalize_delays(
            pd.DataFrame(columns=[
                "Date", "Time", "Station",
                "Code", "Min Delay", "Line"
            ])
        )


def test_transactional_rolling_refresh_idempotent():
    from sqlalchemy import create_engine, text
    from pipeline import refresh_database

    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE subway_delays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                time TIME,
                day VARCHAR(15),
                station VARCHAR(100),
                code VARCHAR(10),
                min_delay INTEGER,
                min_gap INTEGER,
                bound VARCHAR(5),
                line VARCHAR(10),
                vehicle VARCHAR(20)
            )
        """))

        conn.execute(text("""
            CREATE TABLE delay_codes (
                code VARCHAR(10) PRIMARY KEY,
                description TEXT
            )
        """))

        conn.execute(text("""
            CREATE TABLE route_summary (
                line VARCHAR(10) PRIMARY KEY,
                mean_delay REAL,
                max_delay INTEGER,
                total_incidents INTEGER
            )
        """))

    raw = pd.DataFrame([
        {
            "Date": "2025-01-01",
            "Time": "01:00",
            "Station": "A",
            "Code": "X",
            "Min Delay": "5",
            "Min Gap": "10",
            "Line": "BD",
        },
        {
            "Date": "2025-02-01",
            "Time": "01:00",
            "Station": "B",
            "Code": "X",
            "Min Delay": "7",
            "Min Gap": "9",
            "Line": "YU",
        },
    ])

    delays = normalize_delays(raw)
    codes = [{"code": "X", "description": "TEST"}]

    refresh_database(delays, codes, 1, engine)
    refresh_database(delays, codes, 1, engine)

    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT COUNT(*) FROM subway_delays")
        ).scalar() == 1

        assert conn.execute(
            text("SELECT date FROM subway_delays")
        ).scalar() == "2025-02-01"

        assert conn.execute(
            text("SELECT total_incidents FROM route_summary")
        ).scalar() == 1

        assert conn.execute(
            text("SELECT COUNT(*) FROM delay_codes")
        ).scalar() == 1


def test_failed_refresh_keeps_previous_data(monkeypatch):
    from sqlalchemy import create_engine, text
    from pipeline import refresh_database

    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE subway_delays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                time TIME,
                day VARCHAR(15),
                station VARCHAR(100),
                code VARCHAR(10),
                min_delay INTEGER,
                min_gap INTEGER,
                bound VARCHAR(5),
                line VARCHAR(10),
                vehicle VARCHAR(20)
            )
        """))

        conn.execute(text("""
            CREATE TABLE delay_codes (
                code VARCHAR(10) PRIMARY KEY,
                description TEXT
            )
        """))

        conn.execute(text("""
            CREATE TABLE route_summary (
                line VARCHAR(10) PRIMARY KEY,
                mean_delay REAL,
                max_delay INTEGER,
                total_incidents INTEGER
            )
        """))

        conn.execute(text("""
            INSERT INTO subway_delays (date, line, min_delay)
            VALUES ('2025-01-01', 'BD', 2)
        """))

    rows = normalize_delays(pd.DataFrame([{
        "Date": "2025-02-01",
        "Time": "01:30",
        "Station": "A",
        "Code": "Y",
        "Min Delay": "9",
        "Min Gap": "9",
        "Line": "YU",
    }]))

    def raise_during_import(*args, **kwargs):
        raise RuntimeError("Simulated interruption during import")

    monkeypatch.setattr(
        pd.DataFrame,
        "to_sql",
        raise_during_import
    )

    with pytest.raises(
        RuntimeError,
        match="Simulated interruption"
    ):
        refresh_database(
            rows,
            [{"code": "Y", "description": "TEST"}],
            12,
            engine
        )

    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT line FROM subway_delays")
        ).scalar() == "BD"

        assert conn.execute(
            text("SELECT COUNT(*) FROM delay_codes")
        ).scalar() == 0