"""
Tests for the resilient migration runner in app/db/database.py.

The runner must roll an older/drifted database forward without crashing —
the state left by an app update or a reinstall that kept the existing
backend-data/ DB, where the schema is ahead of the recorded versions.
"""
import pytest
from sqlalchemy import create_engine, text

import app.db.database as database


@pytest.fixture
def temp_engine(tmp_path, monkeypatch):
    """Point run_migrations() at a throwaway SQLite file for this test."""
    db_file = tmp_path / "test.db"
    eng = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(database, "engine", eng)
    yield eng
    eng.dispose()


def _versions(eng):
    with eng.begin() as conn:
        return {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}


def _columns(eng, table):
    with eng.begin() as conn:
        return {r[1] for r in conn.execute(text(f'PRAGMA table_info("{table}")'))}


def test_clean_install_applies_every_migration(temp_engine):
    """A fresh DB runs the full contiguous sequence."""
    database.run_migrations()
    assert {9, 10, 14} <= _versions(temp_engine)
    assert "to_base_factor" in _columns(temp_engine, "data_units")


def test_rerun_tolerates_already_applied_column(temp_engine):
    """
    Reproduces the reported crash: the schema already has to_base_factor but
    version 9 is unrecorded (history drift). The old runner raised
    `duplicate column name: to_base_factor`; the new one treats it as a no-op
    and re-stamps the version.
    """
    database.run_migrations()  # build a full, current DB
    with temp_engine.begin() as conn:  # simulate the drift
        conn.execute(text("DELETE FROM schema_migrations WHERE version = 9"))

    database.run_migrations()  # must NOT raise

    assert 9 in _versions(temp_engine)
    assert "to_base_factor" in _columns(temp_engine, "data_units")


def test_rerun_does_not_rebuild_projects_when_010_unrecorded(temp_engine):
    """
    Migration 010 rebuilds `projects` to convert utc_offset REAL -> TEXT and
    is NOT a safe no-op. If a drifted DB already stores TEXT offsets but never
    recorded version 10, the runner must detect the finished state and skip
    the rebuild — re-running it would mangle half-hour offsets.
    """
    database.run_migrations()
    with temp_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO projects (id, session_id, name, utc_offset) "
            "VALUES ('p1', 's1', 'Proj', '+05:30')"
        ))
        conn.execute(text("DELETE FROM schema_migrations WHERE version = 10"))

    database.run_migrations()  # must skip the destructive rebuild

    with temp_engine.begin() as conn:
        offset = conn.execute(
            text("SELECT utc_offset FROM projects WHERE id = 'p1'")
        ).scalar()
    assert offset == "+05:30"          # offset preserved, not mangled to +05:00
    assert 10 in _versions(temp_engine)  # and stamped applied
