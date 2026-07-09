"""Material-group library (migration 022): global groups of nameless members
(one per material type), CRUD on /api/materials/library/groups."""
import io
from uuid import uuid4

BASE = "/api/materials/library"

# Every fresh DB carries the 6 wrapped mig-019 defaults as global groups.
DEFAULT_GROUP_COUNT = 6


def _setup(client):
    session_id = f"session_{uuid4().hex[:8]}"
    r = client.post("/api/project/create", json={
        "name": f"M2Mat_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": session_id})
    assert r.status_code == 201, r.text
    data = r.json()
    return session_id, data["project_id"], data["main_scenario_id"]


def _mt_id(client, name):
    r = client.get("/api/catalog/material-types")
    return next(mt["id"] for mt in r.json()["material_types"] if mt["materialtype"] == name)


def _member(group, type_name):
    return next(m for m in group["materials"] if m["material_type"] == type_name)


def _mk_group(client, h, materials, name=None, project_id=None, scenario_id=None):
    body = {"materials": materials}
    if name is not None:
        body["name"] = name
    if project_id is not None:
        body["project_id"] = project_id
    if scenario_id is not None:
        body["scenario_id"] = scenario_id
    r = client.post(BASE + "/groups", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["group"]


def test_create_group_with_values_and_auto_name(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")

    r = client.post(BASE + "/groups", json={
        "scenario_id": sid,
        "materials": [
            {"material_type_id": rad, "properties": {
                "color_r": 90, "color_g": 200, "color_b": 90,
                "surface_temperature": 300, "reflectivity": 0.2,
                "two_sided_heat_transfer": False,
            }},
            {"material_type_id": eb, "properties": {"wind_speed": 3.5}},
        ],
    }, headers=h)
    assert r.status_code == 201, r.text
    grp = r.json()["group"]
    assert grp["name"] == "Material.001"
    # scenario provenance derives the project.
    assert grp["project_id"] == pid and grp["scenario_id"] == sid
    assert len(grp["materials"]) == 2
    m = _member(grp, "Radiation")
    assert m["properties"]["reflectivity"] == 0.2
    assert m["properties"]["two_sided_heat_transfer"] is False
    assert m["properties"]["spectral_data"] is None   # untouched prop present as null
    assert "name" not in m                            # members are nameless
    assert _member(grp, "Energy Balance")["properties"]["wind_speed"] == 3.5

    r2 = client.get(BASE + "/groups/next-name", headers=h)
    assert r2.json()["name"] == "Material.002"


def test_create_group_validation_errors(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    blc = _mt_id(client, "Boundary Layer Conductance")

    # At least one material type is required
    r = client.post(BASE + "/groups", json={"materials": []}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_EMPTY"

    # No duplicate material type within one group
    r = client.post(BASE + "/groups", json={"materials": [
        {"material_type_id": rad}, {"material_type_id": rad},
    ]}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "DUPLICATE_MATERIAL_TYPE_IN_GROUP"

    # Unknown material type
    r = client.post(BASE + "/groups", json={"materials": [
        {"material_type_id": 99999}]}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_NOT_FOUND"

    # Per-member property validation (same eav codes as before)
    r = client.post(BASE + "/groups", json={"materials": [
        {"material_type_id": rad, "properties": {"reflectivity": 2}}]}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "VALUE_OUT_OF_RANGE"

    r = client.post(BASE + "/groups", json={"materials": [
        {"material_type_id": rad, "properties": {"reflectivity": 0.123456789}}]}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "TOO_MANY_DECIMALS"

    r = client.post(BASE + "/groups", json={"materials": [
        {"material_type_id": rad, "properties": {"wind_speed": 3}}]}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_MISMATCH"
    assert r.json()["detail"]["error"] == "wind_speed is not a property of Radiation"

    r = client.post(BASE + "/groups", json={"materials": [
        {"material_type_id": blc,
         "properties": {"boundary_layer_model": "Cylinder"}}]}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "ENUM_INVALID_OPTION"

    # Name too long (21 chars)
    r = client.post(BASE + "/groups", json={
        "name": "x" * 21, "materials": [{"material_type_id": rad}]}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "Character limit exceeded"

    # Unknown provenance scenario / mismatched project+scenario pair
    r = client.post(BASE + "/groups", json={
        "scenario_id": "nope", "materials": [{"material_type_id": rad}]}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SCENARIO_NOT_FOUND"
    r = client.post(BASE + "/groups", json={
        "project_id": "other-project", "scenario_id": sid,
        "materials": [{"material_type_id": rad}]}, headers=h)
    assert r.status_code == 404


def test_group_names_globally_unique_case_insensitive(client):
    s1, p1, _ = _setup(client)
    s2, p2, _ = _setup(client)
    rad = _mt_id(client, "Radiation")
    _mk_group(client, {"session-id": s1}, [{"material_type_id": rad}],
              name="Grass Set", project_id=p1)
    # The namespace is GLOBAL — another session/project cannot reuse the name.
    r = client.post(BASE + "/groups", json={
        "name": "GRASS set", "materials": [{"material_type_id": rad}],
    }, headers={"session-id": s2})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_NAME_EXISTS"
    # But any session can SEE the global group.
    r = client.get(BASE + "/groups", headers={"session-id": s2})
    assert "Grass Set" in [g["name"] for g in r.json()["groups"]]


def test_list_groups_preview_filter_search(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")

    _mk_group(client, h, [
        {"material_type_id": rad, "properties": {"color_r": 90, "color_g": 200, "color_b": 90}},
        {"material_type_id": eb, "properties": {"wind_speed": 3.5}},
    ], name="Grass Set")
    _mk_group(client, h, [{"material_type_id": eb}], name="Soil EB")

    r = client.get(BASE + "/groups", headers=h)
    rows = r.json()["groups"]
    # Newest first; the 6 wrapped defaults trail the created groups.
    assert [g["name"] for g in rows[:2]] == ["Soil EB", "Grass Set"]
    assert len(rows) == 2 + DEFAULT_GROUP_COUNT
    grass = rows[1]
    assert set(grass["material_type_ids"]) == {rad, eb}
    assert set(grass["material_types"]) == {"Radiation", "Energy Balance"}
    # Preview mirrors viewport precedence: the Radiation member wins.
    assert grass["preview"]["color_r"] == 90
    assert set(grass["preview"].keys()) == {"color_r", "color_g", "color_b", "texture_file"}

    # Filter: groups containing the type
    r = client.get(BASE + f"/groups?material_type_id={rad}", headers=h)
    names = [g["name"] for g in r.json()["groups"]]
    assert "Grass Set" in names and "Soil EB" not in names

    # Search on the group name
    r = client.get(BASE + "/groups?search=soil", headers=h)
    assert [g["name"] for g in r.json()["groups"]] == ["Soil EB"]


def test_put_group_diff_semantics(client):
    """PUT = full member set: kept types update in place (same material_id),
    absent types are removed, new types are added; rename rides along."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")
    sp = _mt_id(client, "Solar Position")

    grp = _mk_group(client, h, [
        {"material_type_id": rad, "properties": {"reflectivity": 0.2, "color_r": 90}},
        {"material_type_id": eb, "properties": {"wind_speed": 3.5}},
    ], name="Grass Set")
    rad_member_id = _member(grp, "Radiation")["material_id"]
    url = BASE + f"/groups/{grp['id']}"

    # Rename-only PUT: same membership, empty per-member properties → nothing lost.
    r = client.put(url, json={"name": "Dry Grass", "materials": [
        {"material_type_id": rad}, {"material_type_id": eb},
    ]}, headers=h)
    assert r.status_code == 200, r.text
    g = r.json()["group"]
    assert g["name"] == "Dry Grass"
    m = _member(g, "Radiation")
    assert m["material_id"] == rad_member_id           # updated in place
    assert m["properties"]["reflectivity"] == 0.2      # absent keys untouched

    # Update + remove + add in one PUT: keep Radiation (new value, explicit null
    # clears color_r), drop Energy Balance, add Solar Position.
    r = client.put(url, json={"materials": [
        {"material_type_id": rad, "properties": {"reflectivity": 0.4, "color_r": None}},
        {"material_type_id": sp, "properties": {"atmospheric_pressure": 101325}},
    ]}, headers=h)
    assert r.status_code == 200, r.text
    g = r.json()["group"]
    assert {m["material_type"] for m in g["materials"]} == {"Radiation", "Solar Position"}
    m = _member(g, "Radiation")
    assert m["material_id"] == rad_member_id
    assert m["properties"]["reflectivity"] == 0.4
    assert m["properties"]["color_r"] is None          # explicit null cleared it

    # Idempotent: the same PUT again changes nothing.
    r = client.put(url, json={"materials": [
        {"material_type_id": rad, "properties": {"reflectivity": 0.4}},
        {"material_type_id": sp},
    ]}, headers=h)
    assert r.status_code == 200
    g2 = r.json()["group"]
    assert _member(g2, "Radiation")["material_id"] == rad_member_id

    # PUT must keep >= 1 member; duplicate types rejected; rename collision 409.
    r = client.put(url, json={"materials": []}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_EMPTY"
    r = client.put(url, json={"materials": [
        {"material_type_id": rad}, {"material_type_id": rad}]}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "DUPLICATE_MATERIAL_TYPE_IN_GROUP"
    other = _mk_group(client, h, [{"material_type_id": eb}], name="Taken")
    r = client.put(url, json={"name": "taken",
                              "materials": [{"material_type_id": rad}]}, headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_NAME_EXISTS"

    # Unknown group
    r = client.put(BASE + "/groups/999999",
                   json={"materials": [{"material_type_id": rad}]}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_NOT_FOUND"


def test_delete_group_cascades_library_side(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    grp = _mk_group(client, h, [
        {"material_type_id": rad, "properties": {"reflectivity": 0.2}}], name="Doomed")

    r = client.delete(BASE + f"/groups/{grp['id']}", headers=h)
    assert r.status_code == 200
    assert r.json() == {"success": True, "group_id": grp["id"], "unassigned_from": 0}
    assert client.get(BASE + f"/groups/{grp['id']}", headers=h).status_code == 404

    # Members + values are gone from the library tables.
    from app.db.database import SessionLocal
    from app.db.models import MaterialData, ProjectMaterial
    db = SessionLocal()
    try:
        member_id = grp["materials"][0]["material_id"]
        assert db.get(ProjectMaterial, member_id) is None
        assert db.query(MaterialData).filter(
            MaterialData.project_material_id == member_id).count() == 0
    finally:
        db.close()


def test_group_survives_project_deletion(client):
    """project_id is SET NULL provenance — deleting the home project must not
    delete the group (groups are global)."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    grp = _mk_group(client, h, [{"material_type_id": rad}],
                    name="Orphan Set", project_id=pid)
    assert grp["project_id"] == pid

    r = client.delete(f"/api/project/{pid}", headers=h)
    assert r.status_code == 200, r.text

    r = client.get(BASE + f"/groups/{grp['id']}", headers=h)
    assert r.status_code == 200, r.text
    g = r.json()["group"]
    assert g["name"] == "Orphan Set"
    assert g["project_id"] is None and g["scenario_id"] is None


def test_file_upload_by_group_and_type(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")
    grp = _mk_group(client, h, [{"material_type_id": rad}], name="Textured")
    member_id = grp["materials"][0]["material_id"]

    url = BASE + f"/groups/{grp['id']}/materials/{rad}/files/texture_file"
    r = client.post(url, files={"file": ("grass.png", io.BytesIO(b"png-bytes"), "image/png")},
                    headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True and body["property"] == "texture_file"
    # Project-free storage path (groups are global).
    assert body["value"] == f"uploads/materials/{member_id}/grass.png"
    assert _member(body["group"], "Radiation")["properties"]["texture_file"] == body["value"]

    # A type that is not in the group → 404 MATERIAL_TYPE_NOT_IN_GROUP.
    r = client.post(BASE + f"/groups/{grp['id']}/materials/{eb}/files/texture_file",
                    files={"file": ("x.png", io.BytesIO(b"z"), "image/png")}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_NOT_IN_GROUP"

    # Not a file property / unsupported texture extension.
    r = client.post(BASE + f"/groups/{grp['id']}/materials/{rad}/files/reflectivity",
                    files={"file": ("x.png", io.BytesIO(b"z"), "image/png")}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "UNKNOWN_PROPERTY"
    r = client.post(url, files={"file": ("x.gif", io.BytesIO(b"z"), "image/gif")}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_FILE_FORMAT"
