"""
SQLite database connection, session management, and migrations.
"""
from pathlib import Path
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
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


# Errors that mean "this statement's effect is already present in the DB".
# They fire when a not-yet-recorded migration re-runs structural DDL against
# a database that already has the change — e.g. after an app update, or a
# reinstall that kept the existing backend-data/ DB. (See migration 014's
# header for how the recorded versions drifted from the schema.) Treating
# them as no-ops lets an older DB roll forward instead of crashing at startup.
_ALREADY_APPLIED_ERRORS = (
    "duplicate column name",   # ALTER TABLE ... ADD COLUMN  (009, 014)
    "already exists",          # CREATE TABLE / INDEX / TRIGGER  (009's unique index)
)


def _split_statements(raw_sql: str) -> list[str]:
    """Strip -- comment lines, then split into individual statements on ;."""
    lines = [
        line for line in raw_sql.splitlines()
        if not line.strip().startswith("--")
    ]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def _is_already_applied(exc: OperationalError) -> bool:
    msg = str(getattr(exc, "orig", exc)).lower()
    return any(token in msg for token in _ALREADY_APPLIED_ERRORS)


def _column_type(conn, table: str, column: str) -> str | None:
    """Declared type of a column (upper-cased), or None if table/column absent."""
    # PRAGMA can't bind the table name; `table` here is always a literal.
    rows = conn.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
    for row in rows:  # cid, name, type, notnull, dflt_value, pk
        if row[1] == column:
            return (row[2] or "").upper()
    return None


def _column_is_nullable(conn, table: str, column: str) -> bool | None:
    """True/False if the column allows NULL, or None if table/column absent."""
    rows = conn.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
    for row in rows:  # cid, name, type, notnull, dflt_value, pk
        if row[1] == column:
            return row[3] == 0
    return None


def run_migrations() -> None:
    """
    Apply all .sql migration files in db/migrations/ in version order.
    Skips migrations already recorded in schema_migrations.
    Called once at startup from lifespan.py.

    Resilient to a DB whose schema is ahead of its recorded versions — the
    state left by an app update or a reinstall over an existing backend-data/
    DB. Structural DDL that is already satisfied ("duplicate column name",
    "already exists") is treated as a no-op and the version is still stamped,
    so the next launch skips it instead of crashing (exit code 3).

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

    # 1. Ensure the tracking table exists, then read what's been applied.
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """))
        applied = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))
        }

        # Reconcile the one migration that is NOT a safe no-op to re-run.
        # 010 rebuilds `projects` to change utc_offset REAL -> TEXT (create
        # _new, copy+convert, drop, rename). If a drifted DB already stores
        # utc_offset as TEXT but never recorded version 10, re-running the
        # rebuild would mangle half-hour offsets (+05:30 -> +05:00). Detect
        # the finished state and stamp it applied so the rebuild is skipped.
        if 10 not in applied and _column_type(conn, "projects", "utc_offset") == "TEXT":
            conn.execute(text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (10)"))
            applied.add(10)
            print("[db] migration 010 already satisfied (utc_offset is TEXT) — marking applied")

        # 019 rebuilds `project_material` to make project_id nullable (global
        # materials). The rebuild DROPs the table, whose ON DELETE CASCADE would
        # fire on a drifted DB that already has the finished shape — re-running it
        # is destructive. If project_material.project_id is already nullable the
        # migration is done; stamp it so the rebuild is skipped.
        # Post-022 the column is GONE (materials live in groups), so the nullable
        # probe returns None — detect that era by the 022 marker column instead,
        # or 019 would re-run and crash-loop at startup on the missing column.
        _pm_has_group = _column_type(conn, "project_material", "material_group_id") is not None
        if 19 not in applied and (
            _column_is_nullable(conn, "project_material", "project_id") is True
            or _pm_has_group
        ):
            conn.execute(text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (19)"))
            applied.add(19)
            print("[db] migration 019 already satisfied (project_material globalised or grouped) — marking applied")

        # 022 rebuilds `project_material`/`object_material` around material
        # groups. Re-running it on a finished DB would crash at the first
        # `SELECT ... project_id FROM project_material` (column gone; not an
        # _ALREADY_APPLIED_ERRORS token). material_group_id is the marker.
        if 22 not in applied and _pm_has_group:
            conn.execute(text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (22)"))
            applied.add(22)
            print("[db] migration 022 already satisfied (project_material.material_group_id present) — marking applied")

    # 2. Apply each pending migration in its OWN transaction, so one
    #    migration committing is independent of the next.
    for sql_file in sql_files:
        # Extract version number from filename prefix, e.g. 001_initial.sql → 1
        try:
            version = int(sql_file.stem.split("_")[0])
        except ValueError:
            continue
        if version in applied:
            continue

        with engine.begin() as conn:
            for stmt in _split_statements(sql_file.read_text(encoding="utf-8")):
                try:
                    conn.execute(text(stmt))
                except OperationalError as exc:
                    if _is_already_applied(exc):
                        print(f"[db] {sql_file.name}: already-applied statement skipped "
                              f"({getattr(exc, 'orig', exc)})")
                        continue
                    raise
            # Record centrally so a fully- or partially-already-applied
            # migration is stamped done and never retried next launch.
            conn.execute(
                text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (:v)"),
                {"v": version},
            )
        print(f"[db] applied migration {sql_file.name}")
