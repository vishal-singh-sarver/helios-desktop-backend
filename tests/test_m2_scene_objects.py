"""Milestone-2 persisted scene objects, groups and assignment (spec §5/§6/§8)."""
from uuid import uuid4

from app.helios import context as helios_ctx

GROUND_PROPS = {
    "length": 10, "breadth": 20,
    "resolution_x": 100, "resolution_y": 100,
    "position_x": 0, "position_y": 0, "position_z": 0,
    "rotation_z": 45,
    "texture_x": 4, "texture_y": 4,
}


def _setup(client):
    session_id = f"session_{uuid4().hex[:8]}"
    r = client.post("/api/project/create", json={
        "name": f"M2Geo_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": session_id})
    assert r.status_code == 201, r.text
    data = r.json()
    return session_id, data["project_id"], data["main_scenario_id"]


def _base(pid, sid):
    return f"/api/geometry/project/{pid}/scenario/{sid}"


def _ot_id(client, name="Ground"):
    r = client.get("/api/catalog/object-types")
    return next(ot["id"] for ot in r.json()["object_types"] if ot["object"] == name)


def _mt_id(client, name):
    r = client.get("/api/catalog/material-types")
    return next(mt["id"] for mt in r.json()["material_types"] if mt["materialtype"] == name)


def _mk_material(client, h, pid, type_name, name=None, properties=None):
    r = client.post(f"/api/materials/project/{pid}/library", json={
        "material_type_id": _mt_id(client, type_name),
        **({"name": name} if name else {}),
        "properties": properties or {},
    }, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["material"]


# ── Geometry CRUD ────────────────────────────────────────────────────────────


def test_create_ground_auto_name_and_shape(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}

    r = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client),
        "properties": GROUND_PROPS,
    }, headers=h)
    assert r.status_code == 201, r.text
    obj = r.json()["object"]
    assert obj["name"] == "Ground.001"
    assert obj["object_type"] == "Ground"
    assert obj["group_id"] is None
    assert obj["properties"]["length"] == 10
    assert obj["properties"]["rotation_z"] == 45
    # Full DB-backed map: all top-level models enabled by default
    assert obj["visibility"]["viewport"] is True
    assert obj["visibility"]["render"] is True
    assert len(obj["visibility"]["models"]) == 6
    assert all(v is True for v in obj["visibility"]["models"].values())
    assert obj["materials"] == []
    assert isinstance(obj["helios_uuids"], list)
    if helios_ctx.PYHELIOS_AVAILABLE:
        assert obj["helios_uuids"], "build should produce primitives"
        assert obj["viewport"]["object_id"] is not None

    r2 = client.get(_base(pid, sid) + "/objects/next-name?object_type=Ground", headers=h)
    assert r2.json()["name"] == "Ground.002"


def test_create_ground_validation(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ot = _ot_id(client)

    # Missing required property
    incomplete = {k: v for k, v in GROUND_PROPS.items() if k != "length"}
    r = client.post(_base(pid, sid) + "/objects",
                    json={"object_type_id": ot, "properties": incomplete}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"] == {"error": "length is required",
                                  "code": "MISSING_REQUIRED_PROPERTY"}

    # Range violation
    bad = dict(GROUND_PROPS, rotation_z=400)
    r = client.post(_base(pid, sid) + "/objects",
                    json={"object_type_id": ot, "properties": bad}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "VALUE_OUT_OF_RANGE"

    # length must be strictly positive (exclusive lower bound)
    bad = dict(GROUND_PROPS, length=0)
    r = client.post(_base(pid, sid) + "/objects",
                    json={"object_type_id": ot, "properties": bad}, headers=h)
    assert r.status_code == 400

    # Unknown property
    bad = dict(GROUND_PROPS, wingspan=3)
    r = client.post(_base(pid, sid) + "/objects",
                    json={"object_type_id": ot, "properties": bad}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "UNKNOWN_PROPERTY"

    # Duplicate name, case-insensitive, per project
    ok = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": ot, "name": "South Field", "properties": GROUND_PROPS}, headers=h)
    assert ok.status_code == 201
    dup = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": ot, "name": "SOUTH field", "properties": GROUND_PROPS}, headers=h)
    assert dup.status_code == 409
    assert dup.json()["detail"]["error"] == "Geometry name already exists"

    # Crop has no catalog yet → rejected
    r = client.post(_base(pid, sid) + "/objects",
                    json={"object_type_id": _ot_id(client, "Crop"), "properties": {}},
                    headers=h)
    assert r.status_code == 400


def test_list_get_update_rename_delete(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ot = _ot_id(client)

    o1 = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": ot, "properties": GROUND_PROPS}, headers=h).json()["object"]
    o2 = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": ot, "properties": GROUND_PROPS}, headers=h).json()["object"]

    # Ascending created_at — newest at the bottom
    r = client.get(_base(pid, sid) + "/objects", headers=h)
    names = [o["name"] for o in r.json()["objects"]]
    assert names == ["Ground.001", "Ground.002"]

    # Case-insensitive search
    r = client.get(_base(pid, sid) + "/objects?search=GROUND.002", headers=h)
    assert [o["id"] for o in r.json()["objects"]] == [o2["id"]]
    r = client.get(_base(pid, sid) + "/objects?search=nomatch", headers=h)
    assert r.json()["objects"] == []

    # Get one
    r = client.get(_base(pid, sid) + f"/objects/{o1['id']}", headers=h)
    assert r.status_code == 200
    assert r.json()["object"]["properties"]["texture_x"] == 4

    # Update properties + visibility
    r = client.patch(_base(pid, sid) + f"/objects/{o1['id']}", json={
        "properties": {"rotation_z": 90, "texture_x": 8},
        "visibility": {"viewport": False},
    }, headers=h)
    assert r.status_code == 200, r.text
    obj = r.json()["object"]
    assert obj["properties"]["rotation_z"] == 90
    assert obj["properties"]["texture_x"] == 8
    assert obj["properties"]["length"] == 10          # untouched
    assert obj["visibility"]["viewport"] is False

    # Rename
    r = client.patch(_base(pid, sid) + f"/objects/{o1['id']}/rename",
                     json={"name": "South Field"}, headers=h)
    assert r.status_code == 200 and r.json()["object"]["name"] == "South Field"
    r = client.patch(_base(pid, sid) + f"/objects/{o2['id']}/rename",
                     json={"name": "south field"}, headers=h)
    assert r.status_code == 409

    # Delete
    r = client.delete(_base(pid, sid) + f"/objects/{o1['id']}", headers=h)
    assert r.json() == {"success": True, "object_id": o1["id"]}
    assert client.get(_base(pid, sid) + f"/objects/{o1['id']}", headers=h).status_code == 404


def test_get_object_geometry_binary(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
    }, headers=h).json()["object"]

    r = client.get(_base(pid, sid) + f"/objects/{obj['id']}/geometry/binary", headers=h)
    if helios_ctx.PYHELIOS_AVAILABLE:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/octet-stream")
        assert len(r.content) >= 4
    else:
        assert r.status_code == 503

    # Whole-scene fetch for the scenario
    r = client.get(_base(pid, sid) + "/geometry/binary", headers=h)
    if helios_ctx.PYHELIOS_AVAILABLE:
        assert r.status_code == 200
        assert len(r.content) >= 4
    else:
        assert r.status_code == 503


def test_patch_cannot_null_required_property(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
    }, headers=h).json()["object"]

    r = client.patch(_base(pid, sid) + f"/objects/{obj['id']}",
                     json={"properties": {"length": None}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "MISSING_REQUIRED_PROPERTY"
    # Optional properties may still be cleared
    r = client.patch(_base(pid, sid) + f"/objects/{obj['id']}",
                     json={"properties": {"rotation_z": None}}, headers=h)
    assert r.status_code == 200
    assert r.json()["object"]["properties"]["rotation_z"] is None


def _model_ids(client):
    r = client.get("/api/catalog/model-types")
    return {mt["model"]: mt["id"] for mt in r.json()["model_types"]}


def test_per_geometry_model_visibility(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ids = _model_ids(client)
    rad, photo = str(ids["Radiation"]), str(ids["Photosynthesis"])

    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client),
        "properties": GROUND_PROPS,
        "visibility": {"models": {rad: False}},
    }, headers=h).json()["object"]
    assert obj["visibility"]["models"][rad] is False
    assert obj["visibility"]["models"][photo] is True

    # Partial merge: disabling another model keeps the first setting
    r = client.patch(_base(pid, sid) + f"/objects/{obj['id']}",
                     json={"visibility": {"render": False, "models": {photo: False}}},
                     headers=h)
    vis = r.json()["object"]["visibility"]
    assert vis["render"] is False
    assert vis["models"][rad] is False and vis["models"][photo] is False

    # Re-enable → exception row removed, reads enabled again
    r = client.patch(_base(pid, sid) + f"/objects/{obj['id']}",
                     json={"visibility": {"models": {rad: True}}}, headers=h)
    assert r.json()["object"]["visibility"]["models"][rad] is True

    # Unknown model id → 404 with the catalog code
    r = client.patch(_base(pid, sid) + f"/objects/{obj['id']}",
                     json={"visibility": {"models": {"99999": False}}}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MODEL_TYPE_NOT_FOUND"

    # Visibility survives a simulated restart (fresh in-memory context):
    # flags come from the DB, not the registry.
    from app.core.session_store import registry as session_registry
    session_registry.get_or_create_context(session_id, pid).reset()
    r = client.get(_base(pid, sid) + f"/objects/{obj['id']}", headers=h)
    vis = r.json()["object"]["visibility"]
    assert vis["render"] is False and vis["models"][photo] is False


def test_scenario_run_configuration(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ids = _model_ids(client)

    # Defaults: all six enabled
    r = client.get(_base(pid, sid) + "/models", headers=h)
    assert r.status_code == 200
    models = r.json()["models"]
    assert len(models) == 6 and all(m["enabled"] for m in models)

    # Disable one model for the run
    rad = ids["Radiation"]
    r = client.patch(_base(pid, sid) + "/models",
                     json={"models": {str(rad): False}}, headers=h)
    assert r.status_code == 200
    by_id = {m["model_type_id"]: m["enabled"] for m in r.json()["models"]}
    assert by_id[rad] is False
    assert sum(1 for v in by_id.values() if v) == 5

    # Another scenario is unaffected
    r = client.post(f"/api/project/{pid}/scenarios/create",
                    json={"name": f"S2_{uuid4().hex[:6]}"}, headers=h)
    sid2 = r.json()["scenario_id"]
    r = client.get(_base(pid, sid2) + "/models", headers=h)
    assert all(m["enabled"] for m in r.json()["models"])

    # Re-enable removes the exception row
    r = client.patch(_base(pid, sid) + "/models",
                     json={"models": {str(rad): True}}, headers=h)
    assert all(m["enabled"] for m in r.json()["models"])

    # Unknown id → 404
    r = client.patch(_base(pid, sid) + "/models",
                     json={"models": {"99999": False}}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MODEL_TYPE_NOT_FOUND"


def test_scenario_isolation_on_switch(client):
    """Hydrating one scenario must not leak another scenario's objects
    into whole-context reads (spec §12.3 scenario switch)."""
    session_id, pid, sid_a = _setup(client)
    h = {"session-id": session_id}
    r = client.post(f"/api/project/{pid}/scenarios/create",
                    json={"name": f"B_{uuid4().hex[:6]}"}, headers=h)
    assert r.status_code in (200, 201), r.text
    sid_b = r.json()["scenario_id"]

    a = client.post(_base(pid, sid_a) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
    }, headers=h).json()["object"]
    b = client.post(_base(pid, sid_b) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
    }, headers=h).json()["object"]

    # Each scenario lists only its own object
    r = client.get(_base(pid, sid_a) + "/objects", headers=h)
    assert [o["id"] for o in r.json()["objects"]] == [a["id"]]
    r = client.get(_base(pid, sid_b) + "/objects", headers=h)
    assert [o["id"] for o in r.json()["objects"]] == [b["id"]]

    if helios_ctx.PYHELIOS_AVAILABLE:
        from app.core.session_store import registry as session_registry
        pctx = session_registry.get_or_create_context(session_id, pid)
        # After touching B last, only B's object may be live in the context
        assert b["id"] in pctx.persisted_objects
        assert a["id"] not in pctx.persisted_objects
        # Touching A again swaps the active scenario back
        client.get(_base(pid, sid_a) + "/objects", headers=h)
        assert a["id"] in pctx.persisted_objects
        assert b["id"] not in pctx.persisted_objects


# ── Groups ───────────────────────────────────────────────────────────────────


def test_groups_lifecycle(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ot = _ot_id(client)
    o1 = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": ot, "properties": GROUND_PROPS}, headers=h).json()["object"]
    o2 = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": ot, "properties": GROUND_PROPS}, headers=h).json()["object"]

    r = client.post(_base(pid, sid) + "/groups",
                    json={"member_ids": [o1["id"], o2["id"]]}, headers=h)
    assert r.status_code == 201, r.text
    grp = r.json()["group"]
    assert grp["name"] == "Group.001"
    assert set(grp["member_ids"]) == {o1["id"], o2["id"]}

    # Members carry group_id
    r = client.get(_base(pid, sid) + f"/objects/{o1['id']}", headers=h)
    assert r.json()["object"]["group_id"] == grp["id"]

    # Drag out via PATCH object
    r = client.patch(_base(pid, sid) + f"/objects/{o1['id']}",
                     json={"group_id": None}, headers=h)
    assert r.json()["object"]["group_id"] is None
    # And back in
    r = client.patch(_base(pid, sid) + f"/objects/{o1['id']}",
                     json={"group_id": grp["id"]}, headers=h)
    assert r.json()["object"]["group_id"] == grp["id"]

    # Rename + duplicate
    r = client.patch(_base(pid, sid) + f"/groups/{grp['id']}/rename",
                     json={"name": "Field blocks"}, headers=h)
    assert r.status_code == 200 and r.json()["group"]["name"] == "Field blocks"

    # Delete ungroups, never deletes geometries
    r = client.delete(_base(pid, sid) + f"/groups/{grp['id']}", headers=h)
    assert r.status_code == 200
    assert set(r.json()["ungrouped"]) == {o1["id"], o2["id"]}
    r = client.get(_base(pid, sid) + f"/objects/{o1['id']}", headers=h)
    assert r.status_code == 200 and r.json()["object"]["group_id"] is None


# ── Material assignment + sync/frozen ────────────────────────────────────────


def test_assignment_sync_freeze_lifecycle(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
    }, headers=h).json()["object"]
    rad = _mk_material(client, h, pid, "Radiation", "Grass Rad",
                       {"color_r": 90, "color_g": 200, "color_b": 90, "reflectivity": 0.2})
    eb = _mk_material(client, h, pid, "Energy Balance", "Soil EB",
                      {"wind_speed": 3.5, "air_temperature": 298})
    rad2 = _mk_material(client, h, pid, "Radiation", "Rad Two")

    obj_url = _base(pid, sid) + f"/objects/{obj['id']}"

    # Assign synced Radiation
    r = client.post(obj_url + "/materials", json={"material_id": rad["id"]}, headers=h)
    assert r.status_code == 201, r.text
    a = r.json()["assignment"]
    assert a["sync"] is True and a["source"] == "library"
    assert a["properties"]["reflectivity"] == 0.2

    # One material per type per geometry
    r = client.post(obj_url + "/materials", json={"material_id": rad2["id"]}, headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "DUPLICATE_MATERIAL_TYPE_ASSIGNMENT"

    # A second TYPE is fine — assign frozen Energy Balance
    r = client.post(obj_url + "/materials",
                    json={"material_id": eb["id"], "sync": False}, headers=h)
    assert r.status_code == 201
    a = r.json()["assignment"]
    assert a["sync"] is False and a["source"] == "frozen"
    assert a["properties"]["wind_speed"] == 3.5      # snapshot of library values

    # Editing the library does NOT touch the frozen copy (and flags drift)
    r = client.patch(f"/api/materials/project/{pid}/library/{eb['id']}",
                     json={"properties": {"wind_speed": 9}}, headers=h)
    assert r.status_code == 200
    r = client.get(obj_url + "/materials", headers=h)
    frozen = next(m for m in r.json()["materials"] if m["material_id"] == eb["id"])
    assert frozen["properties"]["wind_speed"] == 3.5
    assert frozen.get("library_drift") is True
    synced = next(m for m in r.json()["materials"] if m["material_id"] == rad["id"])
    assert synced["source"] == "library"

    # Editing a synced assignment's values is rejected
    r = client.patch(obj_url + f"/materials/{rad['id']}",
                     json={"properties": {"reflectivity": 0.5}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "CANNOT_EDIT_SYNCED"

    # Edit frozen per-geometry values (validated against the catalog)
    r = client.patch(obj_url + f"/materials/{eb['id']}",
                     json={"properties": {"wind_speed": 4.2}}, headers=h)
    assert r.status_code == 200
    assert r.json()["assignment"]["properties"]["wind_speed"] == 4.2
    r = client.patch(obj_url + f"/materials/{eb['id']}",
                     json={"properties": {"wind_speed": 999}}, headers=h)
    assert r.status_code == 400        # range 0-60

    # Library is unaffected by frozen edits
    r = client.get(f"/api/materials/project/{pid}/library/{eb['id']}", headers=h)
    assert r.json()["material"]["properties"]["wind_speed"] == 9

    # Unfreeze → follows the library again
    r = client.patch(obj_url + f"/materials/{eb['id']}", json={"sync": True}, headers=h)
    assert r.status_code == 200
    a = r.json()["assignment"]
    assert a["source"] == "library" and a["properties"]["wind_speed"] == 9

    # Freeze-and-edit in one call
    r = client.patch(obj_url + f"/materials/{eb['id']}",
                     json={"sync": False, "properties": {"wind_speed": 1.5}}, headers=h)
    assert r.status_code == 200
    assert r.json()["assignment"]["properties"]["wind_speed"] == 1.5

    # Unassign drops frozen values
    r = client.delete(obj_url + f"/materials/{eb['id']}", headers=h)
    assert r.json() == {"success": True, "object_id": obj["id"], "material_id": eb["id"]}
    r = client.get(obj_url + "/materials", headers=h)
    assert [m["material_id"] for m in r.json()["materials"]] == [rad["id"]]

    # Unknown assignment
    r = client.delete(obj_url + f"/materials/{eb['id']}", headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "ASSIGNMENT_NOT_FOUND"


def test_material_delete_cascades_assignments(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
    }, headers=h).json()["object"]
    rad = _mk_material(client, h, pid, "Radiation")
    obj_url = _base(pid, sid) + f"/objects/{obj['id']}"
    client.post(obj_url + "/materials",
                json={"material_id": rad["id"], "sync": False}, headers=h)

    r = client.delete(f"/api/materials/project/{pid}/library/{rad['id']}", headers=h)
    assert r.status_code == 200
    assert r.json()["unassigned_from"] == 1

    # Geometry survives with no assignments
    r = client.get(obj_url, headers=h)
    assert r.status_code == 200
    assert r.json()["object"]["materials"] == []


def test_assignment_in_create_call(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    rad = _mk_material(client, h, pid, "Radiation", "Grass Rad",
                       {"color_r": 90, "color_g": 200, "color_b": 90})
    r = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client),
        "properties": GROUND_PROPS,
        "materials": [{"material_id": rad["id"], "sync": True}],
    }, headers=h)
    assert r.status_code == 201, r.text
    mats = r.json()["object"]["materials"]
    assert len(mats) == 1 and mats[0]["material_id"] == rad["id"]
    assert mats[0]["properties"]["color_r"] == 90
