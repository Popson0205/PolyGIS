import logging
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import settings

log = logging.getLogger("db")
MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

# PolyGIS's own schema first, then public, then wherever PostGIS lives (some hosts install it in its own schema,
# e.g. "postgis" or "extensions"). Its tables never mix with Geohub's.
def options(extra: str = "") -> str:
    path = ",".join(dict.fromkeys(x for x in (settings.db_schema, "public", extra) if x))
    return f"-c search_path={path} -c statement_timeout=30000"


OPTIONS = options()
pool = AsyncConnectionPool(
    settings.database_url, open=False, min_size=1, max_size=settings.db_pool_max,
    kwargs={"row_factory": dict_row, "options": OPTIONS, "prepare_threshold": None})


def postgis_schema(conn) -> str:
    """The schema PostGIS is installed in (installing it in public when the database does not have it yet)."""
    row = conn.execute("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
                       "WHERE e.extname = 'postgis'").fetchone()
    if row:
        return row[0]
    conn.execute("CREATE EXTENSION IF NOT EXISTS postgis SCHEMA public")
    return "public"


def migrate() -> list[str]:
    """Apply any migrations/*.sql not yet recorded. Safe to run from several processes at once."""
    applied: list[str] = []
    with psycopg.connect(settings.database_url, autocommit=True, options=OPTIONS) as conn:
        conn.execute("SELECT pg_advisory_lock(747474)")
        try:
            gis = postgis_schema(conn)
            if gis != "public":
                # PostGIS's types must be found by the migrations and by every request
                pool.kwargs["options"] = options(gis)
                conn.execute(f'SET search_path = "{settings.db_schema}", public, "{gis}"')
                log.info("PostGIS is in schema %s", gis)
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{settings.db_schema}"')
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                         "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
            for f in sorted(MIGRATIONS.glob("*.sql")):
                if f.name in done:
                    continue
                with conn.transaction():
                    conn.execute(f.read_text())
                    conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
                applied.append(f.name)
                log.info("applied migration %s", f.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(747474)")
    return applied


async def get_conn():
    """One transaction per request: commits on success, rolls back on any error."""
    async with pool.connection() as conn:
        yield conn
