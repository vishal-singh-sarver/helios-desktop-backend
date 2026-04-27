"""
Tests for FK cascade and RESTRICT behaviour on the metadata catalog.

Cascade rules (per doc Section 2):
    Project    -> Scenario           CASCADE
    Scenario   -> WeatherDataHeader  CASCADE
    DataType   -> DataUnit           CASCADE
    DataType   -> WeatherDataHeader  RESTRICT
    DataUnit   -> WeatherDataHeader  RESTRICT
"""
from uuid import uuid4


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_project_and_scenario(client) -> tuple[str, str, str]:
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


def _put_one_header(client, sid: str, pid: str, scn: str, dt: int, u: int) -> None:
    """Install exactly one header for the scenario."""
    r = client.put(
        f"/api/weather/project/{pid}/scenario/{scn}/weather_data_header",
        headers={"session-id": sid},
        json={"headers": [
            {"name": f"h_{uuid4().hex[:6]}", "helios_data_type_id": dt, "unit_id": u, "display_order": 0}
        ]},
    )
    assert r.status_code == 200, r.text


def _header_count(client, sid: str, pid: str, scn: str) -> int:
    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/weather_data_header",
        headers={"session-id": sid},
    )
    assert r.status_code == 200, r.text
    return r.json()["count"]


# ─── Cascades ────────────────────────────────────────────────────────────────


def test_deleting_parent_scenario_cascades_headers(client):
    """Project -> Scenario -> WeatherDataHeader is CASCADE end-to-end.
    Deleting the project should remove every header referencing its scenarios."""
    sid, pid, scn = _make_project_and_scenario(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)
    _put_one_header(client, sid, pid, scn, dt, u)

    # Sanity: header exists
    assert _header_count(client, sid, pid, scn) == 1

    # Delete the project (which CASCADEs to scenario which CASCADEs to header)
    r = client.delete(f"/api/project/{pid}", headers={"session-id": sid})
    assert r.status_code == 200

    # Scenario gone -> reading header endpoint now 404s (not 200 with empty list)
    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/weather_data_header",
        headers={"session-id": sid},
    )
    assert r.status_code == 404


def test_delete_data_type_with_units_but_no_headers_cascades_units(client):
    """DataType -> DataUnit is CASCADE. With no headers blocking, deleting the
    type should also remove its units."""
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    # Sanity: unit exists
    r = client.get(f"/api/data-units/{u}")
    assert r.status_code == 200

    # Delete the parent type
    r = client.delete(f"/api/data-types/{dt}")
    assert r.status_code == 200

    # Unit gone via CASCADE
    r = client.get(f"/api/data-units/{u}")
    assert r.status_code == 404


# ─── RESTRICTs ───────────────────────────────────────────────────────────────


def test_delete_data_type_referenced_by_header_returns_409_and_keeps_both(client):
    """DataType -> WeatherDataHeader is RESTRICT. With at least one header
    pointing at the type, the delete must be blocked and the type AND its
    units must remain."""
    sid, pid, scn = _make_project_and_scenario(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)
    _put_one_header(client, sid, pid, scn, dt, u)

    # DELETE blocked
    r = client.delete(f"/api/data-types/{dt}")
    assert r.status_code == 409
    assert "in use" in r.json()["detail"].lower()

    # Both rows survive
    r = client.get(f"/api/data-types/{dt}")
    assert r.status_code == 200
    r = client.get(f"/api/data-units/{u}")
    assert r.status_code == 200


def test_delete_data_unit_referenced_by_header_returns_409_and_keeps_unit(client):
    """DataUnit -> WeatherDataHeader is RESTRICT."""
    sid, pid, scn = _make_project_and_scenario(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)
    _put_one_header(client, sid, pid, scn, dt, u)

    r = client.delete(f"/api/data-units/{u}")
    assert r.status_code == 409
    assert "in use" in r.json()["detail"].lower()

    # Unit survives
    r = client.get(f"/api/data-units/{u}")
    assert r.status_code == 200
