"""
Shared pytest fixtures.
"""
import os
import pytest

# Tell PyHelios not to attempt a source build during tests
os.environ.setdefault("PYHELIOS_USE_PIP", "0")

from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
