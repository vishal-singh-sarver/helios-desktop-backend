"""
Shared pytest fixtures.

Fixtures added here:
  client       — TestClient with in-memory SQLite, no PyHelios required
  db_session   — isolated DB session per test
  mock_context — stub PyHelios context for unit tests
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

# TODO: add db_session and mock_context fixtures in Step 2 / Step 3
