"""
Shared pytest fixtures.
"""
import os
import tempfile

# Tell PyHelios not to attempt a source build during tests.
os.environ.setdefault("PYHELIOS_USE_PIP", "0")
# Isolate the whole test session from the real data dir — a fresh temp dir means
# the DB (and project files) start empty every run. Must be set BEFORE app import
# so the engine binds here.
os.environ["HELIOS_DATA_DIR"] = tempfile.mkdtemp(prefix="helios_test_")

import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(autouse=True)
def _reset_db():
    """Reset to a fresh schema + seeds before each test.

    Materials are a GLOBAL namespace (migration 019), so a DB shared across tests
    would leak material/object names between otherwise-independent tests. We drop
    every table and re-run migrations, but keep the SAME engine/SessionLocal
    objects (only the data is reset) so test modules that import SessionLocal
    directly still hit the right database.
    """
    from app.db import database

    raw = database.engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        for (table,) in cur.fetchall():
            cur.execute(f'DROP TABLE IF EXISTS "{table}"')
        raw.commit()
    finally:
        raw.close()
    # Discard pooled connections: the one we just used has foreign_keys=OFF, and
    # PRAGMA persists per-connection — reusing it would silently disable FK
    # cascades for app requests. Fresh connections re-run the connect listener
    # (foreign_keys=ON).
    database.engine.dispose()
    database.run_migrations()
    yield


@pytest.fixture
def client(_reset_db):
    with TestClient(app) as c:
        yield c
