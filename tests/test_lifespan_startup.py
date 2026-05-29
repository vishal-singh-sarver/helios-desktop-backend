"""
Tests for the startup-failure classification in app/core/lifespan.py.

The actual abort path calls os._exit (untestable in-process), so the testable
seam is _migration_failure: exception -> (exit_code, reason, remedy).
"""
from sqlalchemy.exc import OperationalError

from app.core.lifespan import (
    EXIT_BUNDLE,
    EXIT_DB_CORRUPT,
    EXIT_DB_LOCKED,
    EXIT_MIGRATION,
    _migration_failure,
)


def _op_error(message: str) -> OperationalError:
    # SQLAlchemy wraps the driver error in .orig; the classifier reads that.
    return OperationalError("SELECT 1", {}, Exception(message))


def test_bundle_incomplete_runtimeerror():
    code, reason, remedy = _migration_failure(
        RuntimeError("Migrations folder not found at /x")
    )
    assert code == EXIT_BUNDLE
    assert "reinstall" in remedy.lower()


def test_locked_database():
    code, _, remedy = _migration_failure(_op_error("database is locked"))
    assert code == EXIT_DB_LOCKED
    assert "another copy" in remedy.lower()


def test_busy_database():
    code, _, _ = _migration_failure(_op_error("database is busy"))
    assert code == EXIT_DB_LOCKED


def test_corrupt_database():
    code, _, remedy = _migration_failure(
        _op_error("database disk image is malformed")
    )
    assert code == EXIT_DB_CORRUPT
    assert "damaged" in remedy.lower()


def test_not_a_database():
    code, _, _ = _migration_failure(_op_error("file is not a database"))
    assert code == EXIT_DB_CORRUPT


def test_other_migration_error_falls_through():
    # e.g. the historical duplicate-column crash, if it ever resurfaced.
    code, reason, _ = _migration_failure(
        _op_error("duplicate column name: to_base_factor")
    )
    assert code == EXIT_MIGRATION
    assert "to_base_factor" in reason
