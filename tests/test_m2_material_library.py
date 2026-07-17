"""Material-group library (migration 022): global groups of nameless members
(one per material type), CRUD on /api/materials/library/groups."""
import io
from uuid import uuid4

BASE = "/api/materials/library"

# Every fresh DB carries 7 default groups: the 6 wrapped mig-019 defaults plus
# the mig-024 "Default Visualiser" group.
DEFAULT_GROUP_COUNT = 7


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

    # An EMPTY group is legal — created with zero members.
    r = client.post(BASE + "/groups", json={"materials": []}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["group"]["materials"] == []

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
    vis = _mt_id(client, "Visualiser")
    eb = _mt_id(client, "Energy Balance")

    _mk_group(client, h, [
        {"material_type_id": vis, "properties": {
            "texture_toggle": False, "color_r": 90, "color_g": 200,
            "color_b": 90, "opacity": 100}},
        {"material_type_id": eb, "properties": {"wind_speed": 3.5}},
    ], name="Grass Set")
    _mk_group(client, h, [{"material_type_id": eb}], name="Soil EB")

    r = client.get(BASE + "/groups", headers=h)
    rows = r.json()["groups"]
    # Newest first; the default groups trail the created groups.
    assert [g["name"] for g in rows[:2]] == ["Soil EB", "Grass Set"]
    assert len(rows) == 2 + DEFAULT_GROUP_COUNT
    grass = rows[1]
    assert set(grass["material_type_ids"]) == {vis, eb}
    assert set(grass["material_types"]) == {"Visualiser", "Energy Balance"}
    # Preview mirrors viewport precedence: the Visualiser member wins.
    assert grass["preview"]["color_r"] == 90
    assert set(grass["preview"].keys()) == {"color_r", "color_g", "color_b",
                                             "opacity", "texture_file", "texture_toggle"}

    # Filter: groups containing the type
    r = client.get(BASE + f"/groups?material_type_id={vis}", headers=h)
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
        {"material_type_id": rad, "properties": {"reflectivity": 0.2, "transmissivity": 0.5}},
        {"material_type_id": eb, "properties": {"wind_speed": 3.5}},
    ], name="Grass Set")
    rad_member_id = _member(grp, "Radiation")["material_id"]
    url = BASE + f"/groups/{grp['id']}"

    # PUT is now FULL-REPLACEMENT per member: to preserve a member's properties
    # across a rename you must re-send them (empty props would clear them).
    r = client.put(url, json={"name": "Dry Grass", "materials": [
        {"material_type_id": rad, "properties": {"reflectivity": 0.2, "transmissivity": 0.5}},
        {"material_type_id": eb, "properties": {"wind_speed": 3.5}},
    ]}, headers=h)
    assert r.status_code == 200, r.text
    g = r.json()["group"]
    assert g["name"] == "Dry Grass"
    m = _member(g, "Radiation")
    assert m["material_id"] == rad_member_id           # updated in place
    assert m["properties"]["reflectivity"] == 0.2      # re-sent, so preserved

    # Update + remove + add in one PUT: keep Radiation (new value, explicit null
    # clears transmissivity), drop Energy Balance, add Solar Position.
    r = client.put(url, json={"materials": [
        {"material_type_id": rad, "properties": {"reflectivity": 0.4, "transmissivity": None}},
        {"material_type_id": sp, "properties": {"atmospheric_pressure": 101325}},
    ]}, headers=h)
    assert r.status_code == 200, r.text
    g = r.json()["group"]
    assert {m["material_type"] for m in g["materials"]} == {"Radiation", "Solar Position"}
    m = _member(g, "Radiation")
    assert m["material_id"] == rad_member_id
    assert m["properties"]["reflectivity"] == 0.4
    assert m["properties"]["transmissivity"] is None    # explicit null cleared it

    # Idempotent: the same PUT again changes nothing.
    r = client.put(url, json={"materials": [
        {"material_type_id": rad, "properties": {"reflectivity": 0.4}},
        {"material_type_id": sp},
    ]}, headers=h)
    assert r.status_code == 200
    g2 = r.json()["group"]
    assert _member(g2, "Radiation")["material_id"] == rad_member_id

    # PUT with an empty member set removes every member — the group survives.
    r = client.put(url, json={"materials": []}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["group"]["materials"] == []
    # restore a member so the rename-collision check below still exercises PUT
    r = client.put(url, json={"materials": [
        {"material_type_id": rad, "properties": {"reflectivity": 0.4}}]}, headers=h)
    assert r.status_code == 200
    # Duplicate types in one payload rejected; rename collision 409.
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
    vis = _mt_id(client, "Visualiser")
    eb = _mt_id(client, "Energy Balance")
    # Start the Visualiser member in colour mode (a member is always a complete
    # mode); uploading a texture then switches it into texture mode.
    grp = _mk_group(client, h, [{"material_type_id": vis, "properties": {
        "texture_toggle": False, "color_r": 128, "color_g": 128,
        "color_b": 128, "opacity": 100}}], name="Textured")
    member_id = grp["materials"][0]["material_id"]

    url = BASE + f"/groups/{grp['id']}/materials/{vis}/files/texture_file"
    r = client.post(url, files={"file": ("grass.png", io.BytesIO(b"png-bytes"), "image/png")},
                    headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True and body["property"] == "texture_file"
    # Project-free storage path (groups are global).
    assert body["value"] == f"uploads/materials/{member_id}/grass.png"
    vis_member = _member(body["group"], "Visualiser")
    assert vis_member["properties"]["texture_file"] == body["value"]
    # Uploading a texture switches the member INTO texture mode: toggle on,
    # colour cleared (the atomic mode switch — a member is exactly one mode).
    assert vis_member["properties"]["texture_toggle"] is True
    assert vis_member["properties"]["color_r"] is None

    # A type that is not in the group → 404 MATERIAL_TYPE_NOT_IN_GROUP.
    r = client.post(BASE + f"/groups/{grp['id']}/materials/{eb}/files/texture_file",
                    files={"file": ("x.png", io.BytesIO(b"z"), "image/png")}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_NOT_IN_GROUP"

    # Not a file property / unsupported texture extension. color_r IS on the
    # Visualiser member but is not a file property -> UNKNOWN_PROPERTY.
    r = client.post(BASE + f"/groups/{grp['id']}/materials/{vis}/files/color_r",
                    files={"file": ("x.png", io.BytesIO(b"z"), "image/png")}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "UNKNOWN_PROPERTY"
    r = client.post(url, files={"file": ("x.gif", io.BytesIO(b"z"), "image/gif")}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_FILE_FORMAT"


def test_texture_upload_autocreates_visualiser(client):
    """Uploading a texture to a group with NO Visualiser member creates it on the
    spot, born directly in texture mode (no colour version -> no grey flash)."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    vis = _mt_id(client, "Visualiser")
    eb = _mt_id(client, "Energy Balance")

    grp = _mk_group(client, h, [], name="Auto Tex")   # empty group, no members
    url = BASE + f"/groups/{grp['id']}/materials/{vis}/files/texture_file"
    r = client.post(url, files={"file": ("dirt.png", io.BytesIO(b"png-bytes"), "image/png")},
                    headers=h)
    assert r.status_code == 200, r.text
    m = _member(r.json()["group"], "Visualiser")      # the member now exists
    assert m["properties"]["texture_toggle"] is True
    assert m["properties"]["texture_file"] == r.json()["value"]
    assert m["properties"]["color_r"] is None         # born as texture, never colour

    # A missing NON-Visualiser member is NOT auto-created -> still 404.
    r = client.post(BASE + f"/groups/{grp['id']}/materials/{eb}/files/texture_file",
                    files={"file": ("x.png", io.BytesIO(b"z"), "image/png")}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_NOT_IN_GROUP"

    # A bogus material type id -> 404 MATERIAL_TYPE_NOT_FOUND.
    r = client.post(BASE + f"/groups/{grp['id']}/materials/99999/files/texture_file",
                    files={"file": ("x.png", io.BytesIO(b"z"), "image/png")}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_NOT_FOUND"


def test_member_crud_one_by_one(client):
    """Granular member management: start EMPTY, add types one at a time, patch
    one standalone, remove one — the group may end (and stay) empty."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")

    grp = _mk_group(client, h, [], name="Built Up")   # empty create
    assert grp["materials"] == []
    url = BASE + f"/groups/{grp['id']}"

    # Add two members one-by-one.
    r = client.post(url + "/materials", json={
        "material_type_id": rad, "properties": {"reflectivity": 0.2, "transmissivity": 0.5}},
        headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["success"] is True
    assert [m["material_type"] for m in r.json()["group"]["materials"]] == ["Radiation"]
    r = client.post(url + "/materials", json={
        "material_type_id": eb, "properties": {"wind_speed": 3.5}}, headers=h)
    assert r.status_code == 201, r.text
    assert len(r.json()["group"]["materials"]) == 2

    # Adding a type that is already in the group → 409.
    r = client.post(url + "/materials", json={"material_type_id": rad}, headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "DUPLICATE_MATERIAL_TYPE_IN_GROUP"

    # Unknown type / unknown group / eav validation on add.
    r = client.post(url + "/materials", json={"material_type_id": 99999}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_NOT_FOUND"
    r = client.post(BASE + "/groups/999999/materials",
                    json={"material_type_id": rad}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_NOT_FOUND"
    sp = _mt_id(client, "Solar Position")
    r = client.post(url + "/materials", json={
        "material_type_id": sp, "properties": {"reflectivity": 0.1}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_MISMATCH"

    # PUT one member standalone: FULL REPLACEMENT (omitted props are cleared).
    r = client.put(url + f"/materials/{rad}", json={
        "properties": {"reflectivity": 0.5}}, headers=h)
    assert r.status_code == 200, r.text
    m = _member(r.json()["group"], "Radiation")
    assert m["properties"]["reflectivity"] == 0.5
    assert m["properties"]["transmissivity"] is None     # not re-sent -> cleared
    # eav validation + non-member type on PUT.
    r = client.put(url + f"/materials/{rad}",
                   json={"properties": {"reflectivity": 2}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "VALUE_OUT_OF_RANGE"
    r = client.put(url + f"/materials/{sp}",
                   json={"properties": {"latitude": 10}}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_NOT_IN_GROUP"

    # DELETE one member; repeat → 404; removing the LAST member is legal.
    r = client.delete(url + f"/materials/{eb}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["material_type_id"] == eb
    assert [m["material_type"] for m in r.json()["group"]["materials"]] == ["Radiation"]
    r = client.delete(url + f"/materials/{eb}", headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_NOT_IN_GROUP"
    r = client.delete(url + f"/materials/{rad}", headers=h)
    assert r.status_code == 200
    assert r.json()["group"]["materials"] == []          # empty group survives
    assert client.get(url, headers=h).status_code == 200


def test_member_crud_eager_scenario(client):
    """The member endpoints run the same eager reconcile as PUT: add
    materializes onto the active scenario's geometries (with the advisory
    conflict pre-check), patch refreshes sync=1 snapshots, remove cleans the
    applied rows."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")

    # A geometry with the group assigned (synced).
    r = client.get("/api/catalog/object-types")
    ot = next(o["id"] for o in r.json()["object_types"] if o["object"] == "Ground")
    obj = client.post(f"/api/geometry/project/{pid}/scenario/{sid}/objects", json={
        "object_type_id": ot,
        "properties": {"length": 10, "breadth": 10, "resolution_x": 1, "resolution_y": 1,
                       "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
                       "texture_x": 1, "texture_y": 1},
    }, headers=h).json()["object"]
    grp = _mk_group(client, h, [{"material_type_id": rad,
                                 "properties": {"reflectivity": 0.2}}], name="Eager Grp")
    obj_url = f"/api/geometry/project/{pid}/scenario/{sid}/objects/{obj['id']}"
    r = client.post(obj_url + "/material-groups", json={"group_id": grp["id"]}, headers=h)
    assert r.status_code == 201, r.text
    url = BASE + f"/groups/{grp['id']}"

    # Eager ADD: the new member is materialized + snapshotted immediately.
    r = client.post(url + f"/materials?scenario_id={sid}", json={
        "material_type_id": eb, "properties": {"wind_speed": 3.5}}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["sync"]["applied"]["added_members"] == 1
    r = client.get(obj_url + "/material-groups", headers=h)
    assert len(r.json()["material_groups"][0]["materials"]) == 2

    # Eager ADD conflict pre-check: another assigned group owns the type → 409
    # with conflicts, nothing written to the library.
    blocker = _mk_group(client, h, [{"material_type_id": _mt_id(client, "Photosynthesis")}],
                        name="Blocker Grp")
    client.post(obj_url + "/material-groups", json={"group_id": blocker["id"]}, headers=h)
    r = client.post(url + f"/materials?scenario_id={sid}", json={
        "material_type_id": _mt_id(client, "Photosynthesis")}, headers=h)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "DUPLICATE_MATERIAL_TYPE_ASSIGNMENT"
    assert d["conflicts"][0]["group_id"] == blocker["id"]
    r = client.get(url, headers=h)
    assert len(r.json()["group"]["materials"]) == 2      # library unchanged

    # Eager PUT: sync=1 snapshot refreshed.
    r = client.put(url + f"/materials/{rad}?scenario_id={sid}", json={
        "properties": {"reflectivity": 0.9}}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["sync"]["applied"]["refreshed_values"] == 1

    # Eager REMOVE: applied row + snapshots cleaned on the active scenario.
    r = client.delete(url + f"/materials/{eb}?scenario_id={sid}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["sync"]["applied"]["removed_members"] == 1
    r = client.get(obj_url + "/material-groups", headers=h)
    assert [m["material_type"] for m in r.json()["material_groups"][0]["materials"]] == ["Radiation"]

    # Bad scenario id fails fast with nothing written.
    r = client.post(url + "/materials?scenario_id=nope",
                    json={"material_type_id": eb}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SCENARIO_NOT_FOUND"


def test_visualiser_required_by_mode(client):
    """Visualiser is required-by-mode (migration 025): texture_toggle picks
    colour|texture, that mode's fields are required and the other's forbidden.
    Viz props on a model type are still rejected."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    vis = _mt_id(client, "Visualiser")

    grp = _mk_group(client, h, [], name="Viz Rules")
    add = BASE + f"/groups/{grp['id']}/materials"

    # color_r on a Radiation member -> MATERIAL_TYPE_MISMATCH (viz is Visualiser-only).
    r = client.post(add, json={"material_type_id": rad,
                               "properties": {"color_r": 90}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_MISMATCH"

    # A Visualiser member with no texture_toggle -> MISSING_REQUIRED_PROPERTY.
    r = client.post(add, json={"material_type_id": vis,
                               "properties": {"color_r": 90}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "MISSING_REQUIRED_PROPERTY"

    # Colour mode requires ALL of color_r/g/b + opacity (no partial).
    r = client.post(add, json={"material_type_id": vis, "properties": {
        "texture_toggle": False, "color_r": 90, "color_g": 200}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "MISSING_REQUIRED_PROPERTY"

    # A stray texture_file in colour mode is a mode conflict.
    r = client.post(add, json={"material_type_id": vis, "properties": {
        "texture_toggle": False, "color_r": 90, "color_g": 200, "color_b": 90,
        "opacity": 40, "texture_file": "x.png"}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "VISUALISER_MODE_CONFLICT"

    # A complete colour-mode member is accepted.
    r = client.post(add, json={"material_type_id": vis, "properties": {
        "texture_toggle": False, "color_r": 90, "color_g": 200, "color_b": 90,
        "opacity": 40}}, headers=h)
    assert r.status_code == 201, r.text
    m = _member(r.json()["group"], "Visualiser")
    assert m["properties"]["color_r"] == 90 and m["properties"]["opacity"] == 40
    assert m["properties"]["texture_toggle"] is False
    assert m["properties"]["texture_file"] is None

    # opacity is a 0..100 percent: out of range rejected (full colour-mode PUT).
    r = client.put(add + f"/{vis}", json={"properties": {
        "texture_toggle": False, "color_r": 1, "color_g": 2, "color_b": 3,
        "opacity": 150}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "VALUE_OUT_OF_RANGE"

    # PUT into texture mode requires a texture_file.
    r = client.put(add + f"/{vis}", json={
        "properties": {"texture_toggle": True}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "MISSING_REQUIRED_PROPERTY"

    # PUT full-replacement into texture mode (path supplied) clears the colour.
    r = client.put(add + f"/{vis}", json={"properties": {
        "texture_toggle": True, "texture_file": "uploads/materials/x/t.png"}}, headers=h)
    assert r.status_code == 200, r.text
    m = _member(r.json()["group"], "Visualiser")
    assert m["properties"]["texture_toggle"] is True
    assert m["properties"]["texture_file"] == "uploads/materials/x/t.png"
    assert m["properties"]["color_r"] is None and m["properties"]["opacity"] is None


def test_preview_winner_visualiser_and_none_fallback(client):
    """List-preview swatch mirrors the viewport precedence flip: the Visualiser
    member owns the colour; a group with no Visualiser member previews empty
    (winner None -> soil/default, not an arbitrary member)."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    vis = _mt_id(client, "Visualiser")
    eb = _mt_id(client, "Energy Balance")

    _mk_group(client, h, [
        {"material_type_id": vis, "properties": {
            "texture_toggle": False, "color_r": 10, "color_g": 20,
            "color_b": 30, "opacity": 100}},
        {"material_type_id": eb, "properties": {"wind_speed": 2.0}},
    ], name="Has Viz")
    _mk_group(client, h, [{"material_type_id": eb, "properties": {"wind_speed": 2.0}}],
              name="No Viz")

    rows = {g["name"]: g for g in client.get(BASE + "/groups", headers=h).json()["groups"]}

    # Visualiser member wins the preview colour.
    assert rows["Has Viz"]["preview"]["color_r"] == 10
    # No Visualiser member -> empty preview (all viz keys null).
    assert all(v is None for v in rows["No Viz"]["preview"].values())

    # Seeded defaults: Default Visualiser carries grey 128 + opacity 100; the
    # model defaults preview empty — their orphaned mig-019 colour is NOT
    # surfaced because colour is owned solely by Visualiser.
    assert rows["Default Visualiser"]["preview"]["color_r"] == 128
    assert rows["Default Visualiser"]["preview"]["opacity"] == 100
    assert all(v is None for v in rows["Default Radiation"]["preview"].values())
# ── Group rename: PATCH /groups/{id}/rename ──────────────────────────────────
# Name-only; members untouched (which the full-replacement PUT cannot promise).


def _rename(client, h, gid, name):
    return client.patch(BASE + f"/groups/{gid}/rename", json={"name": name}, headers=h)


def test_rename_group_happy_path_members_untouched(client):
    """Renames ONLY the name: id, provenance and created_at survive, and every
    member keeps its material_id and values — the promise PUT cannot make."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")
    grp = _mk_group(client, h, [
        {"material_type_id": rad, "properties": {"reflectivity": 0.2, "transmissivity": 0.5}},
        {"material_type_id": eb, "properties": {"wind_speed": 3.5}},
    ], name="Grass Set", scenario_id=sid)
    before = client.get(BASE + f"/groups/{grp['id']}", headers=h).json()["group"]

    r = _rename(client, h, grp["id"], "Meadow Set")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["group"]["id"] == grp["id"]
    assert body["group"]["name"] == "Meadow Set"
    assert body["group"]["updated_at"] != before["updated_at"]

    after = client.get(BASE + f"/groups/{grp['id']}", headers=h).json()["group"]
    assert after["name"] == "Meadow Set"
    assert after["created_at"] == before["created_at"]     # renamed, not re-created
    assert after["project_id"] == before["project_id"]     # provenance intact
    assert after["scenario_id"] == before["scenario_id"]
    assert after["materials"] == before["materials"]       # members byte-identical
    assert _member(after, "Radiation")["properties"]["reflectivity"] == 0.2
    assert _member(after, "Energy Balance")["properties"]["wind_speed"] == 3.5

    # The PATCH body is the FULL group — byte-identical to GET on this resource.
    # Pinned deliberately: a slim body under the same "group" key would let a
    # client that swaps its PUT refresh for rename silently drop `materials`.
    assert body["group"] == after


def test_rename_group_duplicate_name_global_and_case_insensitive(client):
    """The namespace is GLOBAL: colliding with another SESSION's group — or with
    a seeded mig-019 default — is 409 in any casing, and writes nothing."""
    s1, p1, _ = _setup(client)
    s2, p2, _ = _setup(client)
    rad = _mt_id(client, "Radiation")
    _mk_group(client, {"session-id": s1}, [{"material_type_id": rad}], name="Grass Set")
    mine = _mk_group(client, {"session-id": s2}, [{"material_type_id": rad}], name="Soil Set")
    h2 = {"session-id": s2}

    for taken in ("Grass Set", "GRASS set", "grass set", "default radiation"):
        r = _rename(client, h2, mine["id"], taken)
        assert r.status_code == 409, taken
        assert r.json()["detail"]["code"] == "MATERIAL_GROUP_NAME_EXISTS"

    # A rejected rename writes nothing.
    assert client.get(BASE + f"/groups/{mine['id']}",
                      headers=h2).json()["group"]["name"] == "Soil Set"


def test_rename_group_to_own_name_is_allowed(client):
    """A group never collides with itself: its exact name is accepted, and a
    case-only rename is applied (the `!= grp.name.lower()` guard)."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    grp = _mk_group(client, h, [{"material_type_id": rad}], name="Grass Set")

    r = _rename(client, h, grp["id"], "Grass Set")        # identical
    assert r.status_code == 200, r.text
    assert r.json()["group"]["name"] == "Grass Set"

    r = _rename(client, h, grp["id"], "grass set")        # own name, new casing
    assert r.status_code == 200, r.text
    assert r.json()["group"]["name"] == "grass set"
    assert client.get(BASE + f"/groups/{grp['id']}",
                      headers=h).json()["group"]["name"] == "grass set"


def test_rename_group_name_validation(client):
    """validate_name: stripped, non-empty, <=20 chars (internal spaces count)."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    gid = _mk_group(client, h, [{"material_type_id": rad}], name="Grass Set")["id"]

    for bad in ("x" * 21, "a" * 10 + " " + "b" * 10):     # 21, incl. one with a space
        r = _rename(client, h, gid, bad)
        assert r.status_code == 400, bad
        assert r.json()["detail"]["code"] == "NAME_TOO_LONG"
        assert r.json()["detail"]["error"] == "Character limit exceeded"

    for bad in ("", "   "):
        r = _rename(client, h, gid, bad)
        assert r.status_code == 400, repr(bad)
        assert r.json()["detail"]["code"] == "NAME_REQUIRED"

    # Control characters must die HERE. Python len() counts a NUL but SQLite's
    # length() stops at it, so "\x00abc" would pass validation and then trip
    # CHECK(length(name) BETWEEN 1 AND 20) at commit — surfacing to the user as a
    # bogus "name already exists" that no other name could fix.
    for bad in ("\x00abc", "ab\tcd", "ab\ncd"):
        r = _rename(client, h, gid, bad)
        assert r.status_code == 400, repr(bad)
        assert r.json()["detail"]["code"] == "NAME_INVALID"

    assert _rename(client, h, gid, "y" * 20).status_code == 200      # exactly 20
    r = _rename(client, h, gid, "  Padded Name  ")                   # stored stripped
    assert r.status_code == 200
    assert r.json()["group"]["name"] == "Padded Name"
    r = _rename(client, h, gid, "z" * 20 + "  ")   # 22 raw, 20 post-strip → allowed
    assert r.status_code == 200
    assert r.json()["group"]["name"] == "z" * 20

    # Structurally-wrong bodies are pydantic 422s, not our {error, code} 400s.
    assert client.patch(BASE + f"/groups/{gid}/rename",
                        json={"name": 123}, headers=h).status_code == 422
    assert client.patch(BASE + f"/groups/{gid}/rename",
                        json={}, headers=h).status_code == 422

    # Only the last successful rename stands.
    assert client.get(BASE + f"/groups/{gid}",
                      headers=h).json()["group"]["name"] == "z" * 20


def test_rename_group_unknown_group(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    r = _rename(client, h, 999999, "Nope")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_NOT_FOUND"
    # Non-int group_id → pydantic path-param 422.
    assert client.patch(BASE + "/groups/abc/rename",
                        json={"name": "x"}, headers=h).status_code == 422


def test_rename_group_visible_in_get_and_list(client):
    """New name shows in GET / list / search; ordering (created_at) and the
    neighbouring group are untouched."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")
    grass = _mk_group(client, h, [{"material_type_id": rad},
                                  {"material_type_id": eb}], name="Grass Set")
    _mk_group(client, h, [{"material_type_id": eb}], name="Soil EB")

    assert _rename(client, h, grass["id"], "Meadow Set").status_code == 200

    rows = client.get(BASE + "/groups", headers=h).json()["groups"]
    assert len(rows) == 2 + DEFAULT_GROUP_COUNT           # renamed, not created
    # Newest-first is by created_at, which a rename does not touch.
    assert [g["name"] for g in rows[:2]] == ["Soil EB", "Meadow Set"]
    assert set(rows[1]["material_type_ids"]) == {rad, eb}  # membership intact

    hits = client.get(BASE + "/groups?search=Meadow", headers=h).json()["groups"]
    assert [g["name"] for g in hits] == ["Meadow Set"]
    assert client.get(BASE + "/groups?search=Grass", headers=h).json()["groups"] == []


def test_rename_assigned_group_keeps_applied_state(client):
    """LOAD-BEARING: a group assigned to a geometry survives a rename intact —
    the assignment keys off material_group_id and the name is resolved LIVE. This
    is the proof that rename needs no ?scenario_id= reconcile; it would fail if
    an eager reconcile were ever added."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")

    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()["object_types"]
              if o["object"] == "Ground")
    obj = client.post(f"/api/geometry/project/{pid}/scenario/{sid}/objects", json={
        "object_type_id": ot,
        "properties": {"length": 10, "breadth": 10, "resolution_x": 1, "resolution_y": 1,
                       "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
                       "texture_x": 1, "texture_y": 1},
    }, headers=h).json()["object"]
    grp = _mk_group(client, h, [{"material_type_id": rad,
                                 "properties": {"reflectivity": 0.2}}], name="Grass Set")
    obj_url = f"/api/geometry/project/{pid}/scenario/{sid}/objects/{obj['id']}"
    assert client.post(obj_url + "/material-groups",
                       json={"group_id": grp["id"]}, headers=h).status_code == 201

    before = client.get(obj_url + "/material-groups", headers=h).json()["material_groups"][0]
    assert before["name"] == "Grass Set"
    sync_url = f"/api/geometry/project/{pid}/scenario/{sid}/material-sync"
    before_sync = client.get(sync_url, headers=h).json()
    assert before_sync["in_sync"] is True

    assert _rename(client, h, grp["id"], "Meadow Set").status_code == 200

    after = client.get(obj_url + "/material-groups", headers=h).json()["material_groups"][0]
    assert after["group_id"] == before["group_id"]     # same soft reference
    assert after["name"] == "Meadow Set"               # resolved live from the library
    assert after["sync"] == before["sync"]
    assert after["materials"] == before["materials"]   # applied members/values untouched
    assert _member(after, "Radiation")["properties"]["reflectivity"] == 0.2

    # THE proof that skipping the eager ?scenario_id= hook is correct: the sync
    # engine's dry-run report is byte-identical after the rename — zero drift, so
    # a reconcile would have had nothing to do. Regression guard: this breaks the
    # moment anyone bolts an eager reconcile onto rename.
    assert client.get(sync_url, headers=h).json() == before_sync


def test_rename_frees_the_old_name(client):
    """next_default_name gap-fills from the LIVE name set (no stored counter), so
    renaming away from 'Material.001' frees it for reuse."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mt_id(client, "Radiation")
    grp = _mk_group(client, h, [{"material_type_id": rad}])       # auto-named
    assert grp["name"] == "Material.001"
    assert client.get(BASE + "/groups/next-name", headers=h).json()["name"] == "Material.002"

    assert _rename(client, h, grp["id"], "Grass Set").status_code == 200
    assert client.get(BASE + "/groups/next-name", headers=h).json()["name"] == "Material.001"
    reused = _mk_group(client, h, [{"material_type_id": rad}])
    assert reused["name"] == "Material.001"

    # ...and the renamed group's new name is now taken (case-insensitively).
    r = _rename(client, h, reused["id"], "grass SET")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_NAME_EXISTS"


def test_rename_empty_group(client):
    """A group may legally hold zero members (module docstring) — renaming one
    still works and still returns the full (empty) member list."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    grp = _mk_group(client, h, [], name="Empty Set")
    assert grp["materials"] == []

    r = _rename(client, h, grp["id"], "Still Empty")
    assert r.status_code == 200, r.text
    assert r.json()["group"]["name"] == "Still Empty"
    assert r.json()["group"]["materials"] == []


def test_rename_seeded_default_group(client):
    """The 6 mig-019 defaults are ordinary global groups — nothing protects them,
    and renaming one is not a create/delete."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rows = client.get(BASE + "/groups", headers=h).json()["groups"]
    default = next(g for g in rows if g["name"] == "Default Radiation")
    before = client.get(BASE + f"/groups/{default['id']}", headers=h).json()["group"]

    assert _rename(client, h, default["id"], "House Radiation").status_code == 200

    after = client.get(BASE + f"/groups/{default['id']}", headers=h).json()["group"]
    assert after["name"] == "House Radiation"
    assert after["materials"] == before["materials"]
    assert len(client.get(BASE + "/groups",
                          headers=h).json()["groups"]) == DEFAULT_GROUP_COUNT
