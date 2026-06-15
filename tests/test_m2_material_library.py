"""Milestone-2 persisted material library (spec §7)."""
from uuid import uuid4


def _setup(client):
    session_id = f"session_{uuid4().hex[:8]}"
    r = client.post("/api/project/create", json={
        "name": f"M2Mat_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": session_id})
    assert r.status_code == 201, r.text
    return session_id, r.json()["project_id"]


def _mt_id(client, name):
    r = client.get("/api/catalog/material-types")
    return next(mt["id"] for mt in r.json()["material_types"] if mt["materialtype"] == name)


def _base(project_id):
    return f"/api/materials/project/{project_id}/library"


def test_create_material_with_values_and_auto_name(client):
    session_id, pid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")

    r = client.post(_base(pid), json={
        "material_type_id": rad,
        "properties": {
            "color_r": 90, "color_g": 200, "color_b": 90,
            "surface_temperature": 300, "reflectivity": 0.2,
            "two_sided_heat_transfer": False,
        },
    }, headers=h)
    assert r.status_code == 201, r.text
    mat = r.json()["material"]
    assert mat["name"] == "Material.001"
    assert mat["material_type"] == "Radiation"
    assert mat["scenario_id"] is None
    assert mat["properties"]["reflectivity"] == 0.2
    assert mat["properties"]["two_sided_heat_transfer"] is False
    assert mat["properties"]["spectral_data"] is None   # untouched prop present as null

    # Auto-numbering continues; next-name agrees
    r2 = client.get(_base(pid) + "/next-name", headers=h)
    assert r2.json()["name"] == "Material.002"


def test_create_material_validation_errors(client):
    session_id, pid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    blc = _mt_id(client, "Boundary Layer Conductance")

    # Out of range
    r = client.post(_base(pid), json={
        "material_type_id": rad, "properties": {"reflectivity": 2}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "VALUE_OUT_OF_RANGE"
    assert "Values should be between" in r.json()["detail"]["error"]

    # >7 decimal places
    r = client.post(_base(pid), json={
        "material_type_id": rad, "properties": {"reflectivity": 0.123456789}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "TOO_MANY_DECIMALS"

    # Property of a different material type (spec §9: dedicated code)
    r = client.post(_base(pid), json={
        "material_type_id": rad, "properties": {"wind_speed": 3}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_MISMATCH"
    assert r.json()["detail"]["error"] == "wind_speed is not a property of Radiation"

    # Absurdly large number must not 500 (Decimal quantize overflow)
    r = client.post(_base(pid), json={
        "material_type_id": rad, "properties": {"reflectivity": 1e22}}, headers=h)
    assert r.status_code == 400

    # Bad enum token
    r = client.post(_base(pid), json={
        "material_type_id": blc, "properties": {"boundary_layer_model": "Cylinder"}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "ENUM_INVALID_OPTION"

    # Name too long (21 chars)
    r = client.post(_base(pid), json={
        "material_type_id": rad, "name": "x" * 21}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "Character limit exceeded"

    # Unknown material type
    r = client.post(_base(pid), json={"material_type_id": 99999}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_NOT_FOUND"


def test_duplicate_name_case_insensitive(client):
    session_id, pid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    r = client.post(_base(pid), json={
        "material_type_id": rad, "name": "Grass Rad"}, headers=h)
    assert r.status_code == 201
    r = client.post(_base(pid), json={
        "material_type_id": rad, "name": "GRASS rad"}, headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "Material name already exists"


def test_list_get_update_rename_delete(client):
    session_id, pid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")

    m1 = client.post(_base(pid), json={
        "material_type_id": rad, "name": "Grass Rad",
        "properties": {"color_r": 90, "color_g": 200, "color_b": 90, "reflectivity": 0.2},
    }, headers=h).json()["material"]
    m2 = client.post(_base(pid), json={
        "material_type_id": eb, "name": "Soil EB",
        "properties": {"wind_speed": 3.5, "air_temperature": 298},
    }, headers=h).json()["material"]

    # List: newest first (created materials precede the seeded global defaults),
    # preview carries vis props only.
    r = client.get(_base(pid), headers=h)
    rows = r.json()["materials"]
    assert [m["name"] for m in rows[:2]] == ["Soil EB", "Grass Rad"]
    assert rows[1]["preview"]["color_r"] == 90
    assert "reflectivity" not in rows[1]["preview"]

    # Filter by type + search. The Radiation list also carries the seeded
    # 'Default Radiation'; the created one is newest, so it comes first.
    r = client.get(_base(pid) + f"?material_type_id={rad}", headers=h)
    rad_names = [m["name"] for m in r.json()["materials"]]
    assert rad_names[0] == "Grass Rad"
    assert "Grass Rad" in rad_names
    r = client.get(_base(pid) + "?search=soil", headers=h)
    assert [m["name"] for m in r.json()["materials"]] == ["Soil EB"]

    # Get one
    r = client.get(_base(pid) + f"/{m2['id']}", headers=h)
    assert r.json()["material"]["properties"]["wind_speed"] == 3.5

    # Partial update
    r = client.patch(_base(pid) + f"/{m1['id']}",
                     json={"properties": {"reflectivity": 0.3, "color_r": 110}}, headers=h)
    assert r.status_code == 200
    props = r.json()["material"]["properties"]
    assert props["reflectivity"] == 0.3 and props["color_r"] == 110
    assert props["color_g"] == 200   # untouched

    # Rename (no-op to same name is fine; duplicate is 409)
    r = client.patch(_base(pid) + f"/{m1['id']}/rename", json={"name": "Dry Grass"}, headers=h)
    assert r.status_code == 200 and r.json()["material"]["name"] == "Dry Grass"
    r = client.patch(_base(pid) + f"/{m2['id']}/rename", json={"name": "dry grass"}, headers=h)
    assert r.status_code == 409

    # Delete
    r = client.delete(_base(pid) + f"/{m1['id']}", headers=h)
    assert r.status_code == 200
    assert r.json() == {"success": True, "material_id": m1["id"], "unassigned_from": 0}
    assert client.get(_base(pid) + f"/{m1['id']}", headers=h).status_code == 404


def test_materials_are_global(client):
    """Materials globalised (migration 019): a material is reachable from any
    project, and names are unique across the whole DB. Project auth still
    applies to the library endpoints."""
    s1, p1 = _setup(client)
    s2, p2 = _setup(client)
    rad = _mt_id(client, "Radiation")
    m = client.post(_base(p1), json={"material_type_id": rad, "name": "Shared Mat"},
                    headers={"session-id": s1}).json()["material"]
    # Another project CAN see/assign the global material.
    r = client.get(_base(p2) + f"/{m['id']}", headers={"session-id": s2})
    assert r.status_code == 200, r.text
    # The name is globally unique — a second project cannot reuse it.
    r = client.post(_base(p2), json={"material_type_id": rad, "name": "shared mat"},
                    headers={"session-id": s2})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "MATERIAL_NAME_EXISTS"
    # Wrong session for p1 → project not found (endpoint auth unchanged).
    r = client.get(_base(p1), headers={"session-id": s2})
    assert r.status_code == 404
