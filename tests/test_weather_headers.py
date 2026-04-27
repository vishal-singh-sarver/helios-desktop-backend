"""
Tests for /api/weather/project/{pid}/scenario/{sid}/weather_data_header.

Per-scenario CRUD on the weather header set:
    GET     -> read the mapping
    PUT     -> atomically replace
    DELETE  -> clear

Each test creates its own project + scenario + catalog entries to stay
isolated within the shared test DB.
"""
from uuid import uuid4


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_project_and_scenario(client) -> tuple[str, str, str]:
    """Returns (session_id, project_id, scenario_id)."""
    sid = f"session_{uuid4().hex[:8]}"
    r = client.post(
        "/api/project/create",
        json={"name": f"P_{uuid4().hex[:8]}", "latitude": 38.5, "longitude": -121.7},
        headers={"session-id": sid},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return sid, body["project_id"], body["main_scenario_id"]


def _make_data_type(client) -> int:
    r = client.post("/api/data-types/", json={"data_type": f"T_{uuid4().hex[:8]}"})
    assert r.status_code == 201, r.text
    return r.json()["data_type"]["id"]


def _make_data_unit(client, data_type_id: int) -> int:
    r = client.post(
        "/api/data-units/",
        json={"unit": f"u_{uuid4().hex[:8]}", "data_type_id": data_type_id},
    )
    assert r.status_code == 201, r.text
    return r.json()["data_unit"]["id"]


def _url(project_id: str, scenario_id: str) -> str:
    return f"/api/weather/project/{project_id}/scenario/{scenario_id}/weather_data_header"


# ─── PUT happy paths ─────────────────────────────────────────────────────────


def test_put_with_multiple_columns_returns_200_and_get_returns_ordered(client):
    sid, pid, scn = _make_project_and_scenario(client)
    dt = _make_data_type(client)
    u1 = _make_data_unit(client, dt)
    u2 = _make_data_unit(client, dt)
    u3 = _make_data_unit(client, dt)

    # PUT in non-sorted order to verify GET sorts by display_order
    r = client.put(
        _url(pid, scn),
        headers={"session-id": sid},
        json={"headers": [
            {"name": "third",  "helios_data_type_id": dt, "unit_id": u3, "display_order": 2},
            {"name": "first",  "helios_data_type_id": dt, "unit_id": u1, "display_order": 0},
            {"name": "second", "helios_data_type_id": dt, "unit_id": u2, "display_order": 1},
        ]},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 3

    r = client.get(_url(pid, scn), headers={"session-id": sid})
    assert r.status_code == 200
    headers = r.json()["headers"]
    assert [h["name"] for h in headers] == ["first", "second", "third"]
    assert [h["display_order"] for h in headers] == [0, 1, 2]


def test_put_then_put_replaces_no_orphans_no_duplicates(client):
    sid, pid, scn = _make_project_and_scenario(client)
    dt = _make_data_type(client)
    u1 = _make_data_unit(client, dt)
    u2 = _make_data_unit(client, dt)

    # First PUT
    client.put(
        _url(pid, scn),
        headers={"session-id": sid},
        json={"headers": [
            {"name": "old_a", "helios_data_type_id": dt, "unit_id": u1, "display_order": 0},
            {"name": "old_b", "helios_data_type_id": dt, "unit_id": u2, "display_order": 1},
        ]},
    )

    # Second PUT replaces — old rows must be gone, new rows must appear
    r = client.put(
        _url(pid, scn),
        headers={"session-id": sid},
        json={"headers": [
            {"name": "new_only", "helios_data_type_id": dt, "unit_id": u1, "display_order": 0},
        ]},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 1

    r = client.get(_url(pid, scn), headers={"session-id": sid})
    names = [h["name"] for h in r.json()["headers"]]
    assert names == ["new_only"]
    assert "old_a" not in names
    assert "old_b" not in names


def test_put_with_empty_array_clears_the_set(client):
    sid, pid, scn = _make_project_and_scenario(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    client.put(
        _url(pid, scn),
        headers={"session-id": sid},
        json={"headers": [
            {"name": "x", "helios_data_type_id": dt, "unit_id": u, "display_order": 0}
        ]},
    )

    r = client.put(_url(pid, scn), headers={"session-id": sid}, json={"headers": []})
    assert r.status_code == 200
    assert r.json()["count"] == 0
    assert r.json()["headers"] == []


# ─── PUT consistency check ──────────────────────────────────────────────────


def test_put_with_mismatched_unit_data_type_returns_400_and_existing_unchanged(client):
    """unit_id must point at a unit whose data_type_id matches the declared
    helios_data_type_id. Mismatch -> 400, and any existing headers must NOT
    be cleared."""
    sid, pid, scn = _make_project_and_scenario(client)
    dt1 = _make_data_type(client)
    dt2 = _make_data_type(client)
    u1 = _make_data_unit(client, dt1)   # belongs to dt1
    u2 = _make_data_unit(client, dt2)   # belongs to dt2

    # First, install one valid header
    client.put(
        _url(pid, scn),
        headers={"session-id": sid},
        json={"headers": [
            {"name": "valid", "helios_data_type_id": dt1, "unit_id": u1, "display_order": 0}
        ]},
    )

    # Now try a PUT where the second item declares dt1 but uses unit from dt2
    r = client.put(
        _url(pid, scn),
        headers={"session-id": sid},
        json={"headers": [
            {"name": "ok",       "helios_data_type_id": dt1, "unit_id": u1, "display_order": 0},
            {"name": "mismatch", "helios_data_type_id": dt1, "unit_id": u2, "display_order": 1},
        ]},
    )
    assert r.status_code == 400
    assert "belongs to data_type" in r.json()["detail"].lower() or "belongs to" in r.json()["detail"].lower()

    # Existing header survives the failed PUT
    r = client.get(_url(pid, scn), headers={"session-id": sid})
    names = [h["name"] for h in r.json()["headers"]]
    assert names == ["valid"]


def test_put_with_unknown_data_type_id_returns_404(client):
    sid, pid, scn = _make_project_and_scenario(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    r = client.put(
        _url(pid, scn),
        headers={"session-id": sid},
        json={"headers": [
            {"name": "x", "helios_data_type_id": 99999999, "unit_id": u, "display_order": 0}
        ]},
    )
    assert r.status_code == 404


def test_put_with_unknown_unit_id_returns_404(client):
    sid, pid, scn = _make_project_and_scenario(client)
    dt = _make_data_type(client)

    r = client.put(
        _url(pid, scn),
        headers={"session-id": sid},
        json={"headers": [
            {"name": "x", "helios_data_type_id": dt, "unit_id": 99999999, "display_order": 0}
        ]},
    )
    assert r.status_code == 404


# ─── PUT Pydantic-level validation ──────────────────────────────────────────


def test_put_with_duplicate_names_returns_422(client):
    sid, pid, scn = _make_project_and_scenario(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    r = client.put(
        _url(pid, scn),
        headers={"session-id": sid},
        json={"headers": [
            {"name": "same", "helios_data_type_id": dt, "unit_id": u, "display_order": 0},
            {"name": "same", "helios_data_type_id": dt, "unit_id": u, "display_order": 1},
        ]},
    )
    assert r.status_code == 422


def test_put_with_duplicate_display_order_returns_422(client):
    sid, pid, scn = _make_project_and_scenario(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    r = client.put(
        _url(pid, scn),
        headers={"session-id": sid},
        json={"headers": [
            {"name": "a", "helios_data_type_id": dt, "unit_id": u, "display_order": 0},
            {"name": "b", "helios_data_type_id": dt, "unit_id": u, "display_order": 0},
        ]},
    )
    assert r.status_code == 422


# ─── Auth: scenario ownership ───────────────────────────────────────────────


def test_unknown_scenario_returns_404(client):
    sid, pid, _ = _make_project_and_scenario(client)
    r = client.get(
        _url(pid, uuid4().hex),
        headers={"session-id": sid},
    )
    assert r.status_code == 404


def test_scenario_from_another_session_returns_404(client):
    """User A creates a scenario; user B (different session) can't see it."""
    sid_a, pid, scn = _make_project_and_scenario(client)
    sid_b = f"session_{uuid4().hex[:8]}"

    r = client.get(_url(pid, scn), headers={"session-id": sid_b})
    assert r.status_code == 404


# ─── DELETE ─────────────────────────────────────────────────────────────────


def test_delete_returns_count_of_rows_removed(client):
    sid, pid, scn = _make_project_and_scenario(client)
    dt = _make_data_type(client)
    u1 = _make_data_unit(client, dt)
    u2 = _make_data_unit(client, dt)

    client.put(
        _url(pid, scn),
        headers={"session-id": sid},
        json={"headers": [
            {"name": "a", "helios_data_type_id": dt, "unit_id": u1, "display_order": 0},
            {"name": "b", "helios_data_type_id": dt, "unit_id": u2, "display_order": 1},
        ]},
    )

    r = client.delete(_url(pid, scn), headers={"session-id": sid})
    assert r.status_code == 200
    assert r.json() == {"success": True, "count": 2}

    # GET after delete -> empty
    r = client.get(_url(pid, scn), headers={"session-id": sid})
    assert r.json()["count"] == 0


def test_delete_on_empty_scenario_returns_count_zero(client):
    sid, pid, scn = _make_project_and_scenario(client)
    r = client.delete(_url(pid, scn), headers={"session-id": sid})
    assert r.status_code == 200
    assert r.json() == {"success": True, "count": 0}
