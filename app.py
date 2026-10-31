"""TTC Delay Analyzer: historical impact summaries, station search, code glossary."""
import os
from datetime import datetime

from flask import Flask, render_template, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from db import engine

app = Flask(__name__)

# Friendly labels for recognized TTC source codes; unknown categories stay unchanged.
LINE_LABELS = {
    "YU": "Line 1 · Yonge–University",
    "BD": "Line 2 · Bloor–Danforth",
    "SRT": "Line 3 · Scarborough RT (historical)",
    "SHP": "Line 4 · Sheppard",
}
RESULT_LIMIT = 50


def _month_label(value):
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%b %Y")
    return datetime.fromisoformat(str(value)[:10]).strftime("%b %Y")

MIN_LINE_INCIDENTS = 20  # Avoid one-off line values dominating the overview.


@app.route("/")
def home():
    try:
        with engine.connect() as conn:
            overview = conn.execute(text("""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE min_delay > 0) AS delayed_incidents,
                       COALESCE(SUM(min_delay) FILTER (WHERE min_delay > 0), 0) AS delay_minutes,
                       MIN(date) AS earliest, MAX(date) AS latest
                FROM subway_delays
            """)).mappings().one()
            routes = conn.execute(text("""
                SELECT line, COUNT(*) AS incidents,
                       COALESCE(SUM(min_delay), 0) AS total_delay_minutes,
                       ROUND(AVG(min_delay), 1) AS mean_delay
                FROM subway_delays
                WHERE min_delay > 0 AND line IS NOT NULL AND TRIM(line) <> ''
                GROUP BY line
                HAVING COUNT(*) >= :minimum
                ORDER BY total_delay_minutes DESC, incidents DESC, line
            """), {"minimum": MIN_LINE_INCIDENTS}).mappings().all()
        max_minutes = max((r["total_delay_minutes"] for r in routes), default=0)
        return render_template(
            "home.html", routes=routes, stats=overview,
            max_minutes=max_minutes, earliest_label=_month_label(overview["earliest"]),
            latest_label=_month_label(overview["latest"]), minimum=MIN_LINE_INCIDENTS,
            line_labels=LINE_LABELS, error=None,
        )
    except SQLAlchemyError:
        app.logger.exception("Unable to load TTC overview")
        return render_template(
            "home.html", routes=[], stats={}, max_minutes=0,
            earliest_label=None, latest_label=None,
            minimum=MIN_LINE_INCIDENTS, line_labels=LINE_LABELS,
            error="The database isn't available right now. Please try again.",
        ), 503


def _search_options(conn):
    # Use real TTC values, not hard-coded station names or guessed route codes.
    stations = conn.execute(text("""
        SELECT DISTINCT station FROM subway_delays
        WHERE station IS NOT NULL AND TRIM(station) <> ''
        ORDER BY station LIMIT 500
    """)).scalars().all()
    lines = conn.execute(text("""
        SELECT line FROM subway_delays
        WHERE min_delay > 0 AND line IS NOT NULL AND TRIM(line) <> ''
        GROUP BY line HAVING COUNT(*) >= :minimum
        ORDER BY COUNT(*) DESC, line
    """), {"minimum": MIN_LINE_INCIDENTS}).scalars().all()
    return stations, lines


@app.route("/search")
def search():
    station = request.args.get("station", "").strip()
    line = request.args.get("line", "").strip()
    # Compatibility with previously shared /search?query=BD links.
    legacy = request.args.get("query", "").strip()
    if legacy and not station and not line:
        if legacy.upper() in LINE_LABELS:
            line = legacy.upper()
        else:
            station = legacy

    state = {"station": station, "line": line, "line_labels": LINE_LABELS,
             "rows": [], "matches": 0, "stations": [], "lines": [], "error": None,
             "result_limit": RESULT_LIMIT}
    if len(station) > 100 or len(line) > 100:
        state["error"] = "Search text is too long. Please use shorter names."
        return render_template("search.html", **state), 400

    try:
        with engine.connect() as conn:
            state["stations"], state["lines"] = _search_options(conn)
            # Never interpolate user input into the SQL itself.
            params = {"station_pattern": f"%{station}%", "line": line,
                      "limit": RESULT_LIMIT}
            filters = """
                WHERE s.min_delay > 0
                  AND (:station_pattern = '%%' OR LOWER(s.station) LIKE LOWER(:station_pattern))
                  AND (:line = '' OR s.line = :line)
            """
            state["matches"] = conn.execute(
                text("SELECT COUNT(*) FROM subway_delays AS s " + filters), params
            ).scalar_one()
            state["rows"] = conn.execute(text("""
                SELECT s.date, s.time, s.station, s.line, s.min_delay, s.code,
                       c.description AS code_description
                FROM subway_delays AS s
                LEFT JOIN delay_codes AS c ON UPPER(s.code) = UPPER(c.code)
            """ + filters + """
                ORDER BY s.date DESC, s.time DESC
                LIMIT :limit
            """), params).mappings().all()
        return render_template("search.html", **state)
    except SQLAlchemyError:
        app.logger.exception("Unable to search TTC delays")
        state["error"] = "Couldn't load search results. Please try again."
        return render_template("search.html", **state), 503


@app.route("/codes")
def codes():
    q = request.args.get("q", "").strip()
    if len(q) > 70:
        return render_template("codes.html", q=q, rows=[], error="Search is too long."), 400
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT code, description FROM delay_codes
                WHERE (:pattern = '%%' OR LOWER(code) LIKE LOWER(:pattern)
                       OR LOWER(description) LIKE LOWER(:pattern))
                ORDER BY code LIMIT 300
            """), {"pattern": f"%{q}%"}).mappings().all()
        return render_template("codes.html", q=q, rows=rows, error=None)
    except SQLAlchemyError:
        app.logger.exception("Unable to load TTC code descriptions")
        return render_template("codes.html", q=q, rows=[],
                               error="Code descriptions are unavailable right now."), 503


@app.route("/story")
def story():
    return render_template("story.html")


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("PORT", "5000")))
