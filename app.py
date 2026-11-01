"""TTC Delay Analyzer: historical impact summaries, station search, code glossary."""
import os
from datetime import datetime
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask import Flask, render_template, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from db import engine

app = Flask(__name__)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["120 per minute"],
    storage_uri="memory://"
)
# Friendly labels for recognized TTC source codes; unknown categories stay unchanged.
LINE_LABELS = {
    "YU": "Line 1 · Yonge–University",
    "BD": "Line 2 · Bloor–Danforth",
    "SRT": "Line 3 · Scarborough RT (historical)",
    "SHP": "Line 4 · Sheppard",
}
RESULT_LIMIT = 50
MIN_LINE_INCIDENTS = 20  # Avoid one-off line values dominating the overview.


@app.route("/")
def home():
    mode_filter = request.args.get("mode", "all").strip().lower()
    try:
        with engine.connect() as conn:
            overview = conn.execute(text("""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE min_delay > 0) AS delayed_incidents,
                       COALESCE(SUM(min_delay) FILTER (WHERE min_delay > 0), 0) AS delay_minutes,
                       MIN(report_date) AS earliest, MAX(report_date) AS latest
                FROM all_delays
                WHERE (:mode = 'all' OR mode = :mode)
            """), {"mode": mode_filter}).mappings().one()
            
            routes = conn.execute(text("""
                SELECT mode, route, COUNT(*) AS incidents,
                       COALESCE(SUM(min_delay), 0) AS total_delay_minutes,
                       ROUND(AVG(min_delay), 1) AS mean_delay
                FROM all_delays
                WHERE min_delay > 0 AND route IS NOT NULL AND TRIM(route) <> ''
                  AND (:mode = 'all' OR mode = :mode)
                GROUP BY mode, route
                HAVING COUNT(*) >= :minimum
                ORDER BY total_delay_minutes DESC, incidents DESC
            """), {"minimum": MIN_LINE_INCIDENTS, "mode": mode_filter}).mappings().all()
            
        max_minutes = max((r["total_delay_minutes"] for r in routes), default=0)
        return render_template(
            "home.html", routes=routes, stats=overview,
            max_minutes=max_minutes, minimum=MIN_LINE_INCIDENTS,
            line_labels=LINE_LABELS, error=None, current_mode=mode_filter
        )
    except SQLAlchemyError:
        app.logger.exception("Unable to load TTC overview")
        return render_template(
            "home.html", routes=[], stats={}, max_minutes=0,
            minimum=MIN_LINE_INCIDENTS, line_labels=LINE_LABELS, current_mode=mode_filter,
            error="The database isn't available right now. Please try again.",
        ), 503


def _search_options(conn, mode):
    # Fetch locations and routes specifically for the form datalists based on the view
    locations = conn.execute(text("""
        SELECT DISTINCT location FROM all_delays
        WHERE location IS NOT NULL AND TRIM(location) <> ''
          AND (:mode = 'all' OR mode = :mode)
        ORDER BY location LIMIT 500
    """), {"mode": mode}).scalars().all()
    
    routes = conn.execute(text("""
        SELECT route FROM all_delays
        WHERE min_delay > 0 AND route IS NOT NULL AND TRIM(route) <> ''
          AND (:mode = 'all' OR mode = :mode)
        GROUP BY route HAVING COUNT(*) >= :minimum
        ORDER BY COUNT(*) DESC, route
    """), {"minimum": MIN_LINE_INCIDENTS, "mode": mode}).scalars().all()
    return locations, routes


@app.route("/search")
def search():
    mode = request.args.get("mode", "all").strip().lower()
    location = request.args.get("location", "").strip()
    route = request.args.get("route", "").strip()
    
    # Backward compatibility with previously shared /search?query=BD or /search?line=BD links.
    legacy_query = request.args.get("query", "").strip()
    legacy_station = request.args.get("station", "").strip()
    legacy_line = request.args.get("line", "").strip()
    
    if not location and not route:
        location = legacy_station
        route = legacy_line
        if legacy_query:
            if legacy_query.upper() in LINE_LABELS:
                route = legacy_query.upper()
            else:
                location = legacy_query

    state = {"location": location, "route": route, "mode": mode, "line_labels": LINE_LABELS,
             "rows": [], "matches": 0, "locations": [], "routes": [], "error": None,
             "limit": RESULT_LIMIT}
             
    if len(location) > 100 or len(route) > 100:
        state["error"] = "Search text is too long. Please use shorter names."
        return render_template("search.html", **state), 400

    try:
        with engine.connect() as conn:
            state["locations"], state["routes"] = _search_options(conn, mode)
            params = {"location_pattern": f"%{location}%", "route": route, "mode": mode, "limit": RESULT_LIMIT}
            
            filters = """
                WHERE min_delay > 0
                  AND (:mode = 'all' OR mode = :mode)
                  AND (:location_pattern = '%%' OR LOWER(location) LIKE LOWER(:location_pattern))
                  AND (:route = '' OR route = :route)
            """
            
            state["matches"] = conn.execute(
                text("SELECT COUNT(*) FROM all_delays " + filters), params
            ).scalar_one()
            
            state["rows"] = conn.execute(text("""
                SELECT report_date AS date, time, location AS station, route AS line, mode,
                       min_delay, code, incident AS code_description
                FROM all_delays
                """ + filters + """
                ORDER BY report_date DESC, time DESC
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

