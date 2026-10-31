"""Shared database connection for local PostgreSQL and hosted Neon PostgreSQL."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

load_dotenv()


def get_database_url():
    """Prefer DATABASE_URL; support the existing DB_* local .env format too."""
    connection_string = os.getenv("DATABASE_URL")
    if connection_string:
        url = make_url(connection_string)
        if url.drivername == "postgres":  # Compatibility with old URLs.
            url = url.set(drivername="postgresql")
        if url.host and url.host.endswith(".neon.tech") and "sslmode" not in url.query:
            url = url.update_query_dict({"sslmode": "require"})
        return url

    required = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(
            "Set DATABASE_URL or these settings in .env: " + ", ".join(missing)
        )
    return URL.create(
        drivername="postgresql+psycopg2",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.environ["DB_NAME"],
    )


engine = create_engine(get_database_url(), pool_pre_ping=True)