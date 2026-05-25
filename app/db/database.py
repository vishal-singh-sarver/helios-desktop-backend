"""
SQLite database connection, session management, and migrations.
"""
from pathlib import Path
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings


class Base(DeclarativeBase):
    pass


_db_url = f"sqlite:///{settings.resolved_db_path}"

engine = create_engine(
    _db_url,
    echo=settings.db_echo,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency — yields a DB session, closes on exit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """
    Apply all .sql migration files in db/migrations/ in version order.
    Skips migrations already recorded in schema_migrations.
    Called once at startup from lifespan.py.

    Fails fast if the migrations folder is missing or empty — historically a
    packaging regression (PyInstaller bundle without `--add-data
    app/db/migrations`) silently produced an empty `schema_migrations` table
    and no real schema, so the first query crashed with `no such table:
    projects`. Loud at startup is much easier to debug than a stale binary
    that "looks healthy" until the first request.
    """
    migrations_dir = Path(__file__).parent / "migrations"
    if not migrations_dir.is_dir():
        raise RuntimeError(
            f"Migrations folder not found at {migrations_dir}. "
            "If running from a packaged binary, the build is missing "
            "`--add-data app/db/migrations:app/db/migrations` (see "
            "scripts/build_binary.sh)."
        )
    sql_files = sorted(migrations_dir.glob("*.sql"))
    if not sql_files:
        raise RuntimeError(
            f"No .sql migration files found in {migrations_dir}. "
            "Bundle is incomplete — rebuild the backend."
        )

    with engine.begin() as conn:
        # Ensure tracking table exists before querying it
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """))

        applied = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))
        }

        for sql_file in sql_files:
            # Extract version number from filename prefix, e.g. 001_initial.sql → 1
            try:
                version = int(sql_file.stem.split("_")[0])
            except ValueError:
                continue

            if version in applied:
                continue

            raw_sql = sql_file.read_text(encoding="utf-8")
            # Strip -- line comments before splitting on ; to avoid
            # false splits on semicolons inside comment text
            lines = [
                line for line in raw_sql.splitlines()
                if not line.strip().startswith("--")
            ]
            sql = "\n".join(lines)
            for statement in sql.split(";"):
                stmt = statement.strip()
                if stmt:
                    conn.execute(text(stmt))

            print(f"[db] applied migration {sql_file.name}")
