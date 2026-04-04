# TODO: implement tests in Step 3
import pytest
from uuid import uuid4


def test_create_project_success(client):
    payload = {
        "name": f"TestProject_{uuid4().hex[:8]}",
        "latitude": 28.6,
        "longitude": 77.2
    }

    r = client.post("/api/project/create", json=payload)

    assert r.status_code == 201
    data = r.json()

    assert data["success"] is True
    assert data["name"] == payload["name"]
    assert data["latitude"] == 28.6
    assert data["longitude"] == 77.2
    assert "project_id" in data
    assert "utc_offset" in data


def test_project_name_required(client):
    payload = {
        "name": "   ",
        "latitude": 28.6,
        "longitude": 77.2
    }

    r = client.post("/api/project/create", json=payload)

    assert r.status_code == 422
    assert "Project name is required" in str(r.json())


def test_project_name_max_length(client):
    payload = {
        "name": "a" * 31,
        "latitude": 28.6,
        "longitude": 77.2
    }

    r = client.post("/api/project/create", json=payload)

    assert r.status_code == 422
    assert "This field supports up to 30 characters only" in str(r.json())


def test_duplicate_project_case_insensitive(client):
    unique_name = f"DuplicateTest_{uuid4().hex[:8]}"

    payload = {
        "name": unique_name,
        "latitude": 28.6,
        "longitude": 77.2
    }

    r1 = client.post("/api/project/create", json=payload)
    assert r1.status_code == 201

    payload2 = {
        "name": unique_name.lower(),
        "latitude": 28.6,
        "longitude": 77.2
    }

    r2 = client.post("/api/project/create", json=payload2)

    assert r2.status_code == 409
    assert "already exists" in r2.text


def test_latitude_out_of_range(client):
    payload = {
        "name": f"LatTest_{uuid4().hex[:8]}",
        "latitude": 100,
        "longitude": 77.2
    }

    r = client.post("/api/project/create", json=payload)

    assert r.status_code == 422
    assert "Invalid" in str(r.json())


def test_longitude_out_of_range(client):
    payload = {
        "name": f"LngTest_{uuid4().hex[:8]}",
        "latitude": 28.6,
        "longitude": 200
    }

    r = client.post("/api/project/create", json=payload)

    assert r.status_code == 422
    assert "Invalid" in str(r.json())


def test_invalid_numeric_input(client):
    payload = {
        "name": f"InvalidInput_{uuid4().hex[:8]}",
        "latitude": "abc",
        "longitude": "5:30"
    }

    r = client.post("/api/project/create", json=payload)

    assert r.status_code == 422
    assert "Invalid input" in str(r.json())