"""TTC Delay Analyzer: historical impact summaries, station search, code glossary."""
import os
import unicodedata
from datetime import datetime

from flask import Flask, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from db import engine

app = Flask(__name__)

# Render runs Flask behind its reverse proxy. Trust only the proxy hop
# for the originating client IP; do not enable this for an untrusted direct proxy.
if os.getenv("RENDER", "").lower() == "true":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# Per-process storage is suitable for a starter single-worker deployment.
# Use Redis/shared storage if deploying multiple workers or instances.
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["120 per minute"],
    storage_uri="memory://",
)

# Friendly labels for recognized TTC source codes; unknown categories stay unchanged.
LINE_LABELS = {
    "YU": "Line 1 · Yonge–University",
    "BD": "Line 2 · Bloor–Danforth",
    "SRT": "Line 3 · Scarborough RT (historical)",
    "SHP": "Line 4 · Sheppard",
}
def clean_display(value):
    """ASCII-friendly display; preserve stored TTC text unchanged."""
    if value is None:
        return ""
    text_value = str(value)
    replacements = {"â€™": "'", "â€“": "-", "â€”": "-", "Â": "", "�": ""}
    for original, replacement in replacements.items():
        text_value = text_value.replace(original, replacement)
    text_value = unicodedata.normalize("NFKD", text_value)
    return " ".join("".join(ch for ch in text_value if not unicodedata.combining(ch)).split())


app.jinja_env.filters["clean_display"] = clean_display

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


@app.route("/search")
@limiter.limit("30 per minute")
def search():
    mode = request.args.get("mode", "all").strip().lower()
    location = request.args.get("location", "").strip()
    route = request.args.get("route", "").strip()

    # Keep older links working, while the form uses location and route.
    if not location and not route:
        location = request.args.get("station", "").strip()
        route = request.args.get("line", "").strip()
        legacy = request.args.get("query", "").strip()
        if legacy:
            if legacy.upper() in LINE_LABELS:
                route = legacy.upper()
            else:
                location = legacy

    state = {"mode": mode, "location": location, "route": route,
             "rows": [], "matches": 0, "error": None,
             "limit": RESULT_LIMIT, "line_labels": LINE_LABELS}

    if mode not in ("all", "subway", "bus", "streetcar"):
        state["error"] = "Choose a valid transit mode."
        return render_template("search.html", **state), 400
    if len(location) > 100 or len(route) > 100:
        state["error"] = "Search text is too long. Please use shorter names."
        return render_template("search.html", **state), 400

    # Build only predefined SQL fragments; all user input remains bound parameters.
    filters = ["min_delay > 0"]
    params = {"limit": RESULT_LIMIT}
    if mode != "all":
        filters.append("mode = :mode")
        params["mode"] = mode
    if location:
        # Treat %, _ and backslash as text, rather than SQL LIKE wildcards.
        escaped = (location.replace("\\", "\\\\")
                           .replace("%", "\\%")
                           .replace("_", "\\_"))
        filters.append("location ILIKE :location_pattern ESCAPE '\\'")
        params["location_pattern"] = f"%{escaped}%"
    if route:
        # Accept route labels such as '25 DON MILLS' when searching for '25'.
        escaped_route = (route.replace("\\", "\\\\")
                              .replace("%", "\\%")
                              .replace("_", "\\_"))
        filters.append("(route ILIKE :route_exact OR route ILIKE :route_prefix ESCAPE '\\')")
        params["route_exact"] = route
        params["route_prefix"] = f"{escaped_route} %"

    where_clause = " WHERE " + " AND ".join(filters)
    try:
        with engine.connect() as conn:
            state["matches"] = conn.execute(
                text("SELECT COUNT(*) FROM all_delays" + where_clause), params
            ).scalar_one()
            state["rows"] = conn.execute(text("""
                SELECT report_date AS date, time, location AS station,
                       route AS line, mode, min_delay, code,
                       incident AS code_description
                FROM all_delays
            """ + where_clause + """
                ORDER BY report_date DESC, time DESC NULLS LAST
                LIMIT :limit
            """), params).mappings().all()
        return render_template("search.html", **state)
    except SQLAlchemyError:
        app.logger.exception("Unable to search TTC delays")
        state["error"] = "Couldn't load search results. Please try again."
        return render_template("search.html", **state), 503


@app.route("/codes")
@limiter.limit("60 per minute")
def codes():
    q = request.args.get("q", "").strip()
    if len(q) > 70:
        return render_template("codes.html", q=q, mode="all", rows=[], error="Search is too long."), 400
    mode = request.args.get("mode", "all").strip().lower()
    if mode not in ("all", "subway", "bus", "streetcar"):
        return render_template("codes.html", q=q, mode="all", rows=[], error="Invalid transit mode."), 400
    try:
        with engine.connect() as conn:
            # Mode-aware descriptions so matching codes do not borrow other modes' meanings.
            # Include the existing subway dictionary as a fallback for legacy records.
            rows = conn.execute(text("""
                WITH glossary AS (
                    SELECT mode, code, description FROM mode_delay_codes
                    UNION ALL
                    SELECT 'subway' AS mode, d.code, d.description FROM delay_codes d
                    WHERE NOT EXISTS (
                        SELECT 1 FROM mode_delay_codes m
                        WHERE m.mode = 'subway' AND m.code = d.code
                    )
                )
                SELECT mode, code, description FROM glossary
                WHERE (:mode = 'all' OR mode = :mode)
                  AND (:q = '' OR code ILIKE :pattern OR description ILIKE :pattern)
                ORDER BY mode, code LIMIT 300
            """), {"mode": mode, "q": q, "pattern": f"%{q}%"}).mappings().all()
        return render_template("codes.html", q=q, mode=mode, rows=rows, error=None)
    except SQLAlchemyError:
        app.logger.exception("Unable to load TTC code descriptions")
        return render_template("codes.html", q=q, mode=mode, rows=[],
                               error="Code descriptions are unavailable right now."), 503


@app.route("/story")
def story():
    return render_template("story.html")


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("PORT", "5000")))

