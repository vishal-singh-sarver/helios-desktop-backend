import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.logging import configure_logging

logger = logging.getLogger("helios.startup")

# Distinct exit codes for fatal startup failures. The Electron backend-manager
# reads the child's exit code; a specific code lets it show a targeted remedy
# instead of the generic uvicorn "exit 3 / backend exited". Keep these in sync
# with any frontend that branches on them.
EXIT_MIGRATION = 10   # migration runner failed for some other reason
EXIT_DATA_DIR = 11    # couldn't create/write the data or projects directory
EXIT_DB_LOCKED = 12   # database file is held open by another process
EXIT_DB_CORRUPT = 13  # database file is malformed / not a database
EXIT_BUNDLE = 14      # packaged bundle is missing files (migrations folder, etc.)


def _migration_failure(exc: BaseException) -> tuple[int, str, str]:
    """Map a migrations/database startup error to (exit_code, reason, remedy).

    Pure and side-effect free so the classification can be unit-tested without
    actually terminating the process.
    """
    # run_migrations() raises RuntimeError when the bundle is incomplete
    # (missing migrations folder / no .sql files) — a packaging regression,
    # not a data problem.
    if isinstance(exc, RuntimeError):
        return (
            EXIT_BUNDLE,
            f"backend bundle incomplete: {exc}",
            "The Helios backend files are incomplete. Please reinstall Helios.",
        )

    msg = str(getattr(exc, "orig", exc)).lower()
    if "locked" in msg or "busy" in msg:
        return (
            EXIT_DB_LOCKED,
            f"database is locked: {exc}",
            "Another copy of Helios may be running. Close all Helios windows and relaunch.",
        )
    if "malformed" in msg or "not a database" in msg or "disk image" in msg:
        return (
            EXIT_DB_CORRUPT,
            f"database file is corrupt: {exc}",
            "The Helios database is damaged. Restart Helios; if it persists, reset Helios data.",
        )
    return (
        EXIT_MIGRATION,
        f"migration failed: {exc}",
        "Database setup failed. Restart Helios; if it persists, contact support with backend.log.",
    )


def _abort(code: int, step: str, reason: str, remedy: str) -> None:
    """Log a single structured, greppable line and exit with a distinct code.

    Uses os._exit so the exact code reaches the parent process: a SystemExit
    raised inside the async lifespan is caught by uvicorn and re-reported as
    the generic exit 3, which is precisely what this is meant to avoid.
    """
    logger.critical(
        "[startup-fatal] code=%d step=%s reason=%r remedy=%r",
        code, step, reason, remedy,
    )
    # Flush so the parent (backend-manager) actually captures the line before
    # the process dies — os._exit does not run interpreter shutdown/flush.
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once on startup (before yield) and once on shutdown (after yield).

    Startup order:
      1. Logging
      2. Ensure data directories exist
      3. Database — run migrations
      4. PyHelios — validate library availability
      5. Seed the default-texture picker folder (best-effort, never fatal)

    Steps 2 and 3 are the ones that can fail on a real user's machine (bad
    permissions, a locked/corrupt DB, an incomplete bundle). Each is wrapped
    so the failure becomes one structured `[startup-fatal]` log line plus a
    distinct exit code, instead of a raw traceback and a generic exit 3.
    Genuinely unexpected errors are deliberately NOT caught — they should
    still fail loudly so bugs surface.

    Add new startup steps here; keep each concern in its own module.
    """
    # 1. Logging
    configure_logging()

    # 2. Data directories
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.resolved_projects_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _abort(
            EXIT_DATA_DIR,
            "data-dir",
            f"cannot create data directory at {settings.data_dir}: {exc}",
            "Check that Helios has permission to write to its data folder, then relaunch.",
        )

    # 3. Database migrations
    from app.db.database import run_migrations
    try:
        run_migrations()
    except (RuntimeError, SQLAlchemyError) as exc:
        code, reason, remedy = _migration_failure(exc)
        _abort(code, "migrations", reason, remedy)

    # 4. PyHelios availability check
    from app.helios.context import init_pyhelios
    init_pyhelios()

    # 5. Seed the default-texture picker folder (data/assets). Best-effort —
    #    the helper swallows its own errors, so it never blocks startup.
    from app.services.material_service import seed_default_textures
    seed_default_textures()

    yield

    # Shutdown — clean up resources
    # from app.helios.context import shutdown_pyhelios
    # shutdown_pyhelios()
