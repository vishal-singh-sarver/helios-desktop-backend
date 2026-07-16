"""Milestone-2 persisted scene objects, groups and assignment (spec §5/§6/§8)."""
from uuid import uuid4

import pytest

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


def _mk_group(client, h, type_specs, name=None):
    """Create a material group; type_specs = [(type_name, properties_or_None)]."""
    r = client.post("/api/materials/library/groups", json={
        **({"name": name} if name else {}),
        "materials": [
            {"material_type_id": _mt_id(client, tn), "properties": props or {}}
            for tn, props in type_specs
        ],
    }, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["group"]


def _grp_member(assignment_or_group, type_name):
    return next(m for m in assignment_or_group["materials"]
                if m["material_type"] == type_name)


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
    assert obj["material_groups"] == []
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

    # length/breadth floor is 0.01 m inclusive (migration 021; max 1,000,000);
    # 0 is rejected. The message shows plain integers (1000000, not 1e+06).
    bad = dict(GROUND_PROPS, length=0)
    r = client.post(_base(pid, sid) + "/objects",
                    json={"object_type_id": ot, "properties": bad}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"] == {"error": "Values should be between (0.01 - 1000000)",
                                  "code": "VALUE_OUT_OF_RANGE"}

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


def test_ground_size_and_texture_resolution_bounds(client):
    """Story 'create a ground': size (length/breadth) is the exclusive range
    (0, 1,000,000] — 0 is rejected but sub-1 values like 0.5 are accepted;
    position (x/y/z) is the inclusive range [-1,000,000, +1,000,000]; and the
    texture repeat count may not exceed the resolution (enforced on create and
    update; the update rule sees the merged values)."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ot = _ot_id(client)
    url = _base(pid, sid) + "/objects"

    # Accepted: a sub-1 value (> 0) and the max boundary.
    for ok_size in (0.5, 1, 1_000_000):
        r = client.post(url, json={"object_type_id": ot,
                        "properties": dict(GROUND_PROPS, length=ok_size)}, headers=h)
        assert r.status_code == 201, r.text

    # Rejected: 0, negative, and just past the max.
    for bad_size in (0, -5, 1_000_001):
        r = client.post(url, json={"object_type_id": ot,
                        "properties": dict(GROUND_PROPS, length=bad_size)}, headers=h)
        assert r.status_code == 400, f"length={bad_size} should be rejected"
        assert r.json()["detail"]["code"] == "VALUE_OUT_OF_RANGE"

    # Position is the inclusive range [-1,000,000, +1,000,000].
    for ok_pos in (0, -1_000_000, 1_000_000):
        r = client.post(url, json={"object_type_id": ot,
                        "properties": dict(GROUND_PROPS, position_x=ok_pos)}, headers=h)
        assert r.status_code == 201, r.text
    for bad_pos in (1_000_001, -1_000_001):
        r = client.post(url, json={"object_type_id": ot,
                        "properties": dict(GROUND_PROPS, position_x=bad_pos)}, headers=h)
        assert r.status_code == 400, f"position_x={bad_pos} should be rejected"
        assert r.json()["detail"]["code"] == "VALUE_OUT_OF_RANGE"

    # texture_x must not exceed resolution_x on create (resolution is 100); the
    # message reports the resolution as a plain integer.
    r = client.post(url, json={"object_type_id": ot,
                    "properties": dict(GROUND_PROPS, texture_x=200)}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"] == {"error": "Values should be between (1 - 100)",
                                  "code": "VALUE_OUT_OF_RANGE"}

    # texture == resolution is allowed (inclusive upper bound).
    r = client.post(url, json={"object_type_id": ot, "name": "EdgeTex",
                    "properties": dict(GROUND_PROPS, texture_x=100, texture_y=100)},
                    headers=h)
    assert r.status_code == 201, r.text

    # On update the rule sees the MERGED values: raising texture above the
    # stored resolution is rejected...
    oid = client.post(url, json={"object_type_id": ot, "name": "UpdMe",
                      "properties": GROUND_PROPS}, headers=h).json()["object"]["id"]
    r = client.patch(f"{url}/{oid}", json={"properties": {"texture_x": 200}}, headers=h)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "VALUE_OUT_OF_RANGE"
    # ...and so is lowering the resolution below the already-stored texture.
    r = client.patch(f"{url}/{oid}", json={"properties": {"resolution_x": 2}}, headers=h)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "VALUE_OUT_OF_RANGE"


def test_all_ground_params_required(client):
    """Story: every Ground parameter is populated with a default and clearing any
    one (on create OR edit) must fail "Field is required". position_x/y/z and
    rotation_z were previously optional — this locks them in alongside the rest."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ot = _ot_id(client)
    url = _base(pid, sid) + "/objects"

    # CREATE: each newly-required param is rejected when omitted or cleared (null).
    for field in ("position_x", "position_y", "position_z", "rotation_z"):
        missing = {k: v for k, v in GROUND_PROPS.items() if k != field}
        r = client.post(url, json={"object_type_id": ot, "properties": missing}, headers=h)
        assert r.status_code == 400, f"{field} omitted should fail"
        assert r.json()["detail"] == {"error": f"{field} is required",
                                      "code": "MISSING_REQUIRED_PROPERTY"}
        r = client.post(url, json={"object_type_id": ot,
                        "properties": dict(GROUND_PROPS, **{field: None})}, headers=h)
        assert r.status_code == 400, f"{field} cleared should fail"
        assert r.json()["detail"]["code"] == "MISSING_REQUIRED_PROPERTY"

    # EDIT: clearing a required param is rejected and the geometry stays unchanged.
    oid = client.post(url, json={"object_type_id": ot,
                      "properties": GROUND_PROPS}, headers=h).json()["object"]["id"]
    for field in ("position_y", "rotation_z"):
        r = client.patch(f"{url}/{oid}", json={"properties": {field: None}}, headers=h)
        assert r.status_code == 400, f"clearing {field} on edit should fail"
        assert r.json()["detail"]["code"] == "MISSING_REQUIRED_PROPERTY"
    o = client.get(f"{url}/{oid}", headers=h).json()["object"]
    assert o["properties"]["position_y"] == GROUND_PROPS["position_y"]
    assert o["properties"]["rotation_z"] == GROUND_PROPS["rotation_z"]


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
    # Every Ground param is required now (incl. rotation_z) — none can be nulled.
    r = client.patch(_base(pid, sid) + f"/objects/{obj['id']}",
                     json={"properties": {"rotation_z": None}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "MISSING_REQUIRED_PROPERTY"


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
    # render = OR(models): one model off, others on ⇒ render stays on.
    assert obj["visibility"]["render"] is True

    oid = obj["id"]

    def _vis():
        return client.get(_base(pid, sid) + f"/objects/{oid}",
                          headers=h).json()["object"]["visibility"]

    # render=False is the master switch → ALL models off, render off.
    r = client.patch(_base(pid, sid) + f"/objects/{oid}",
                     json={"visibility": {"render": False}}, headers=h)
    vis = r.json()["object"]["visibility"]
    assert vis["render"] is False
    assert all(v is False for v in vis["models"].values())

    # Enabling any one model flips render back on (render = OR of models).
    r = client.patch(_base(pid, sid) + f"/objects/{oid}",
                     json={"visibility": {"models": {rad: True}}}, headers=h)
    vis = r.json()["object"]["visibility"]
    assert vis["models"][rad] is True and vis["models"][photo] is False
    assert vis["render"] is True

    # render=True is the master switch → ALL models on.
    r = client.patch(_base(pid, sid) + f"/objects/{oid}",
                     json={"visibility": {"render": True}}, headers=h)
    vis = r.json()["object"]["visibility"]
    assert vis["render"] is True
    assert all(v is True for v in vis["models"].values())

    # Unknown model id → 404 with the catalog code (no state change).
    r = client.patch(_base(pid, sid) + f"/objects/{oid}",
                     json={"visibility": {"models": {"99999": False}}}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MODEL_TYPE_NOT_FOUND"

    # State survives a simulated restart (flags come from the DB, not registry).
    from app.core.session_store import registry as session_registry
    session_registry.get_or_create_context(session_id, pid).reset()
    vis = _vis()
    assert vis["render"] is True and all(v is True for v in vis["models"].values())


def test_visibility_render_and_models_in_one_payload(client):
    """Regression: render + models in the SAME payload must not double-insert
    scenario_object_model rows (the master switch and the explicit map overlap
    on the top-level ids → previously a UNIQUE-constraint crash)."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ids = _model_ids(client)
    all_ids = [str(v) for v in ids.values()]
    one = all_ids[0]

    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
    }, headers=h).json()["object"]
    url = _base(pid, sid) + f"/objects/{obj['id']}"

    # render + the full models map together (the failing staging payload shape).
    r = client.patch(url, json={"visibility": {
        "viewport": True, "render": False,
        "models": {mid: False for mid in all_ids},
    }}, headers=h)
    assert r.status_code == 200, r.text
    vis = r.json()["object"]["visibility"]
    assert vis["render"] is False
    assert all(v is False for v in vis["models"].values())

    # render=True master + one model overridden off → that one off, rest on,
    # render = OR = True.
    r = client.patch(url, json={"visibility": {
        "render": True, "models": {one: False},
    }}, headers=h)
    assert r.status_code == 200, r.text
    vis = r.json()["object"]["visibility"]
    assert vis["models"][one] is False
    assert vis["render"] is True
    assert sum(1 for v in vis["models"].values() if v is True) == len(all_ids) - 1


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
    """Each scenario owns its own PyHelios context (Phase 2), so geometry never
    leaks across scenarios and BOTH can be live at once — no teardown-on-switch."""
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
        # Geometry lives in each scenario's OWN context — both stay live,
        # each holding only its own object (no cross-scenario leakage).
        sctx_a = session_registry.get_or_create_scenario_context(session_id, pid, sid_a)
        sctx_b = session_registry.get_or_create_scenario_context(session_id, pid, sid_b)
        assert a["id"] in sctx_a.persisted_objects and b["id"] not in sctx_a.persisted_objects
        assert b["id"] in sctx_b.persisted_objects and a["id"] not in sctx_b.persisted_objects


# ── Groups ───────────────────────────────────────────────────────────────────


def test_groups_lifecycle(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ot = _ot_id(client)
    o1 = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": ot, "properties": GROUND_PROPS}, headers=h).json()["object"]
    o2 = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": ot, "properties": GROUND_PROPS}, headers=h).json()["object"]

    # A group needs at least 2 distinct geometries: one member is rejected,
    # and the same id listed twice still counts as one.
    r = client.post(_base(pid, sid) + "/groups",
                    json={"member_ids": [o1["id"]]}, headers=h)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "GROUP_MIN_MEMBERS"
    r = client.post(_base(pid, sid) + "/groups",
                    json={"member_ids": [o1["id"], o1["id"]]}, headers=h)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "GROUP_MIN_MEMBERS"

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


def test_group_bulk_visibility_and_delete(client):
    """Group bulk ops: PATCH visibility sets viewport/render for all members;
    DELETE /objects purges every member geometry AND the group itself."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ot = _ot_id(client)
    o1 = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": ot, "properties": GROUND_PROPS}, headers=h).json()["object"]
    o2 = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": ot, "properties": GROUND_PROPS}, headers=h).json()["object"]
    grp = client.post(_base(pid, sid) + "/groups",
                      json={"member_ids": [o1["id"], o2["id"]]}, headers=h).json()["group"]
    gid = grp["id"]

    rad = str(_model_ids(client)["Radiation"])

    def _vis(oid):
        return client.get(_base(pid, sid) + f"/objects/{oid}", headers=h).json()["object"]["visibility"]

    # Bulk viewport off for the whole group (render untouched).
    r = client.patch(_base(pid, sid) + f"/groups/{gid}/visibility",
                     json={"visibility": {"viewport": False}}, headers=h)
    assert r.status_code == 200, r.text
    assert set(r.json()["member_ids"]) == {o1["id"], o2["id"]}
    assert _vis(o1["id"])["viewport"] is False and _vis(o2["id"])["viewport"] is False
    assert _vis(o1["id"])["render"] is True

    # Bulk render off → master switch: every member render off + all models off.
    r = client.patch(_base(pid, sid) + f"/groups/{gid}/visibility",
                     json={"visibility": {"render": False}}, headers=h)
    assert r.status_code == 200
    for oid in (o1["id"], o2["id"]):
        v = _vis(oid)
        assert v["render"] is False
        assert all(x is False for x in v["models"].values())

    # Enabling one model for the group flips render back on for every member.
    r = client.patch(_base(pid, sid) + f"/groups/{gid}/visibility",
                     json={"visibility": {"models": {rad: True}}}, headers=h)
    assert r.status_code == 200
    for oid in (o1["id"], o2["id"]):
        v = _vis(oid)
        assert v["models"][rad] is True and v["render"] is True

    # render + models in ONE payload (regression: must not double-insert).
    r = client.patch(_base(pid, sid) + f"/groups/{gid}/visibility",
                     json={"visibility": {"render": True, "models": {rad: False}}}, headers=h)
    assert r.status_code == 200, r.text
    for oid in (o1["id"], o2["id"]):
        v = _vis(oid)
        assert v["models"][rad] is False and v["render"] is True

    # Empty visibility object rejected.
    r = client.patch(_base(pid, sid) + f"/groups/{gid}/visibility",
                     json={"visibility": {}}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "NO_VISIBILITY_FIELDS"

    # Purge: every member geometry AND the group are deleted.
    r = client.delete(_base(pid, sid) + f"/groups/{gid}/objects", headers=h)
    assert r.status_code == 200
    assert set(r.json()["deleted_object_ids"]) == {o1["id"], o2["id"]}
    assert client.get(_base(pid, sid) + f"/objects/{o1['id']}", headers=h).status_code == 404
    assert client.get(_base(pid, sid) + f"/objects/{o2['id']}", headers=h).status_code == 404
    assert client.get(_base(pid, sid) + "/groups", headers=h).json()["groups"] == []


# ── Material-group assignment + sync/frozen (migration 022) ──────────────────


def test_group_assignment_sync_freeze_lifecycle(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
    }, headers=h).json()["object"]
    grass = _mk_group(client, h, [
        ("Radiation", {"reflectivity": 0.2}),
    ], name="Grass Set")
    soil = _mk_group(client, h, [
        ("Energy Balance", {"wind_speed": 3.5, "air_temperature": 298}),
    ], name="Soil Set")
    rad_two = _mk_group(client, h, [("Radiation", None)], name="Rad Two")

    obj_url = _base(pid, sid) + f"/objects/{obj['id']}"

    # Assign synced group
    r = client.post(obj_url + "/material-groups", json={"group_id": grass["id"]}, headers=h)
    assert r.status_code == 201, r.text
    a = r.json()["assignment"]
    assert a["name"] == "Grass Set"
    assert a["sync"] is True and a["source"] == "library"
    assert _grp_member(a, "Radiation")["properties"]["reflectivity"] == 0.2

    # Same group twice → its own 409
    r = client.post(obj_url + "/material-groups", json={"group_id": grass["id"]}, headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_ALREADY_ASSIGNED"

    # No duplicate material type ACROSS the geometry's groups; the 409 names
    # the blocking group.
    r = client.post(obj_url + "/material-groups", json={"group_id": rad_two["id"]}, headers=h)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "DUPLICATE_MATERIAL_TYPE_ASSIGNMENT"
    assert d["conflicts"][0]["group_id"] == grass["id"]
    assert d["conflicts"][0]["group_name"] == "Grass Set"
    assert d["conflicts"][0]["material_type"] == "Radiation"
    assert "stale" not in d["conflicts"][0]

    # A disjoint TYPE set is fine — assign frozen Energy Balance group
    r = client.post(obj_url + "/material-groups",
                    json={"group_id": soil["id"], "sync": False}, headers=h)
    assert r.status_code == 201
    a = r.json()["assignment"]
    assert a["sync"] is False and a["source"] == "frozen"
    assert _grp_member(a, "Energy Balance")["properties"]["wind_speed"] == 3.5

    # Editing the library (no eager scenario) does NOT touch the frozen copy
    # (and flags drift on it).
    r = client.put(f"/api/materials/library/groups/{soil['id']}", json={
        "materials": [{"material_type_id": _mt_id(client, "Energy Balance"),
                       "properties": {"wind_speed": 9}}],
    }, headers=h)
    assert r.status_code == 200, r.text
    r = client.get(obj_url + "/material-groups", headers=h)
    groups = r.json()["material_groups"]
    frozen = next(g for g in groups if g["group_id"] == soil["id"])
    m = _grp_member(frozen, "Energy Balance")
    assert m["properties"]["wind_speed"] == 3.5
    assert m.get("library_drift") is True
    synced = next(g for g in groups if g["group_id"] == grass["id"])
    assert synced["source"] == "library"

    # Editing a synced assignment's values is rejected
    r = client.patch(obj_url + f"/material-groups/{grass['id']}", json={
        "materials": [{"material_type_id": _mt_id(client, "Radiation"),
                       "properties": {"reflectivity": 0.5}}]}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "CANNOT_EDIT_SYNCED"

    # Edit frozen per-member values (validated against the catalog; addressed
    # by material type).
    eb_type = _mt_id(client, "Energy Balance")
    r = client.patch(obj_url + f"/material-groups/{soil['id']}", json={
        "materials": [{"material_type_id": eb_type,
                       "properties": {"wind_speed": 4.2}}]}, headers=h)
    assert r.status_code == 200, r.text
    assert _grp_member(r.json()["assignment"], "Energy Balance")["properties"]["wind_speed"] == 4.2
    r = client.patch(obj_url + f"/material-groups/{soil['id']}", json={
        "materials": [{"material_type_id": eb_type,
                       "properties": {"wind_speed": 999}}]}, headers=h)
    assert r.status_code == 400        # range 0-60
    # A type this group does not apply here → 404
    r = client.patch(obj_url + f"/material-groups/{soil['id']}", json={
        "materials": [{"material_type_id": _mt_id(client, "Radiation"),
                       "properties": {"reflectivity": 0.1}}]}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_TYPE_NOT_IN_GROUP"

    # Library is unaffected by frozen edits
    r = client.get(f"/api/materials/library/groups/{soil['id']}", headers=h)
    assert _grp_member(r.json()["group"], "Energy Balance")["properties"]["wind_speed"] == 9

    # Unfreeze → re-snapshots and follows the library again
    r = client.patch(obj_url + f"/material-groups/{soil['id']}", json={"sync": True}, headers=h)
    assert r.status_code == 200
    a = r.json()["assignment"]
    assert a["source"] == "library"
    assert _grp_member(a, "Energy Balance")["properties"]["wind_speed"] == 9

    # Freeze-and-edit in one call
    r = client.patch(obj_url + f"/material-groups/{soil['id']}", json={
        "sync": False,
        "materials": [{"material_type_id": eb_type, "properties": {"wind_speed": 1.5}}],
    }, headers=h)
    assert r.status_code == 200
    assert _grp_member(r.json()["assignment"], "Energy Balance")["properties"]["wind_speed"] == 1.5

    # Unassign drops the group's applied rows
    r = client.delete(obj_url + f"/material-groups/{soil['id']}", headers=h)
    assert r.json() == {"success": True, "object_id": obj["id"], "group_id": soil["id"]}
    r = client.get(obj_url + "/material-groups", headers=h)
    assert [g["group_id"] for g in r.json()["material_groups"]] == [grass["id"]]

    # Unknown assignment
    r = client.delete(obj_url + f"/material-groups/{soil['id']}", headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "ASSIGNMENT_NOT_FOUND"


def test_group_delete_keeps_applied_state_until_sync(client):
    """The migration-022 break point: deleting a group WITHOUT the eager
    scenario hook leaves the geometry's applied rows + snapshots in place,
    flagged stale — cleanup happens via PUT material-sync."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
    }, headers=h).json()["object"]
    grp = _mk_group(client, h, [("Radiation", {"reflectivity": 0.2})], name="Doomed")
    obj_url = _base(pid, sid) + f"/objects/{obj['id']}"
    client.post(obj_url + "/material-groups",
                json={"group_id": grp["id"], "sync": False}, headers=h)

    r = client.delete(f"/api/materials/library/groups/{grp['id']}", headers=h)
    assert r.status_code == 200
    assert r.json()["unassigned_from"] == 0   # nothing eagerly cleaned (no scenario_id)

    # The assignment survives as STALE, painted from its snapshots.
    r = client.get(obj_url + "/material-groups", headers=h)
    groups = r.json()["material_groups"]
    assert len(groups) == 1
    assert groups[0]["stale"] is True and groups[0]["name"] is None
    m = _grp_member(groups[0], "Radiation")
    assert m["stale"] is True
    assert m["properties"]["reflectivity"] == 0.2   # snapshot values

    # PUT material-sync cleans it up.
    r = client.put(_base(pid, sid) + "/material-sync", json={}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["removed_groups"] == 1
    r = client.get(obj_url + "/material-groups", headers=h)
    assert r.json()["material_groups"] == []


def test_group_delete_eager_scenario_cleans_immediately(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
    }, headers=h).json()["object"]
    grp = _mk_group(client, h, [("Radiation", None)], name="EagerDoom")
    obj_url = _base(pid, sid) + f"/objects/{obj['id']}"
    client.post(obj_url + "/material-groups", json={"group_id": grp["id"]}, headers=h)

    r = client.delete(f"/api/materials/library/groups/{grp['id']}?scenario_id={sid}",
                      headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["unassigned_from"] == 1
    r = client.get(obj_url + "/material-groups", headers=h)
    assert r.json()["material_groups"] == []


def test_group_assignment_in_create_call(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    grass = _mk_group(client, h, [
        ("Radiation", None),
        ("Visualiser", {"texture_toggle": False, "color_r": 90, "color_g": 200,
                        "color_b": 90, "opacity": 100}),
    ], name="Grass Set")
    r = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client),
        "properties": GROUND_PROPS,
        "materials": [{"group_id": grass["id"], "sync": True}],
    }, headers=h)
    assert r.status_code == 201, r.text
    groups = r.json()["object"]["material_groups"]
    assert len(groups) == 1 and groups[0]["group_id"] == grass["id"]
    assert _grp_member(groups[0], "Visualiser")["properties"]["color_r"] == 90

    # Duplicate TYPE across the requested groups → 409, nothing created.
    rad_two = _mk_group(client, h, [("Radiation", None)], name="Rad Two")
    r = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
        "materials": [{"group_id": grass["id"]}, {"group_id": rad_two["id"]}],
    }, headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "DUPLICATE_MATERIAL_TYPE_ASSIGNMENT"

    # Unknown group id → 404.
    r = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
        "materials": [{"group_id": 999999}],
    }, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_NOT_FOUND"


def test_create_materials_field_takes_group_assignments(client):
    """`materials` on object create carries GROUP assignments since migration
    022 (elements {group_id, sync}); the pre-022 per-material shape is gone.
    The frontend's stub `materials: []` still creates cleanly; a pre-022
    element shape is now a plain 422 (typed field, no silent tolerance)."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    r = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client),
        "properties": GROUND_PROPS,
        "materials": [],
    }, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["object"]["material_groups"] == []

    r = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client),
        "properties": GROUND_PROPS,
        "materials": [{"material_id": 12345, "sync": True}],   # pre-022 shape
    }, headers=h)
    assert r.status_code == 422


def test_assignment_conflict_names_stale_blocker(client):
    """A stale leftover (deleted group, not yet synced) still owns its type
    slot: assigning another group of that type 409s with stale: true, and
    succeeds after a sync."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
    }, headers=h).json()["object"]
    obj_url = _base(pid, sid) + f"/objects/{obj['id']}"

    old = _mk_group(client, h, [("Radiation", None)], name="Old Rad")
    client.post(obj_url + "/material-groups", json={"group_id": old["id"]}, headers=h)
    client.delete(f"/api/materials/library/groups/{old['id']}", headers=h)   # no eager

    new = _mk_group(client, h, [("Radiation", None)], name="New Rad")
    r = client.post(obj_url + "/material-groups", json={"group_id": new["id"]}, headers=h)
    assert r.status_code == 409
    c = r.json()["detail"]["conflicts"][0]
    assert c["group_id"] == old["id"] and c["group_name"] is None
    assert c["stale"] is True

    client.put(_base(pid, sid) + "/material-sync", json={}, headers=h)
    r = client.post(obj_url + "/material-groups", json={"group_id": new["id"]}, headers=h)
    assert r.status_code == 201, r.text


def test_inplace_geometry_edits(client):
    """Item #6: geometry is a TileObject (ctx_object_id captured); resize with no
    rotation mutates it in place (UUIDs + object id stable, no rebuild);
    resolution regenerates primitives; texture-repeat recreates the object."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable — in-place ops are DB-only no-ops")
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    props = {**GROUND_PROPS, "rotation_z": 0,
             "resolution_x": 2, "resolution_y": 2, "texture_x": 1, "texture_y": 1}
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": props,
    }, headers=h).json()["object"]
    obj_url = _base(pid, sid) + f"/objects/{obj['id']}"

    uuids0 = obj["helios_uuids"]
    ctx0 = obj["viewport"]["ctx_object_id"]
    assert ctx0 is not None          # always a TileObject now
    assert len(uuids0) == 4          # 2x2 subdivisions

    # Resize with rotation_z == 0 → in-place scaleObject: same primitives + id.
    o = client.patch(obj_url, json={"properties": {"length": 30, "breadth": 30}},
                     headers=h).json()["object"]
    assert o["properties"]["length"] == 30
    assert o["helios_uuids"] == uuids0
    assert o["viewport"]["ctx_object_id"] == ctx0

    # Resolution change → setTileObjectSubdivisionCount regenerates primitives.
    o = client.patch(obj_url, json={"properties": {"resolution_x": 5, "resolution_y": 4}},
                     headers=h).json()["object"]
    assert len(o["helios_uuids"]) == 20
    assert o["helios_uuids"] != uuids0

    # Texture-repeat change → recreate this one object (fresh id + primitives).
    prev_uuids, prev_ctx = o["helios_uuids"], o["viewport"]["ctx_object_id"]
    o = client.patch(obj_url, json={"properties": {"texture_x": 3, "texture_y": 3}},
                     headers=h).json()["object"]
    assert o["helios_uuids"] != prev_uuids
    assert o["viewport"]["ctx_object_id"] != prev_ctx


def _reopen(session_id, pid, sid):
    """Simulate a restart of one scenario: drop its in-memory context so the
    next request re-creates it, loadXML's context.xml, and re-hydrates."""
    from app.core.session_store import registry as session_registry
    session_registry.remove_scenario(session_id, pid, sid)


def test_geometry_persists_to_context_xml_and_reloads(client):
    """Phase 2: a geometry update writes context.xml; reopening the scenario
    loads the geometry back from it and re-derives the session-scoped ids."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    from app.core.config import settings
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    props = {**GROUND_PROPS, "rotation_z": 0, "resolution_x": 2, "resolution_y": 2,
             "texture_x": 1, "texture_y": 1}
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": props}, headers=h).json()["object"]
    oid = obj["id"]
    assert len(obj["helios_uuids"]) == 4

    xml = settings.scenario_context_file_dir(pid, sid) / "context.xml"
    assert xml.exists() and xml.stat().st_size > 0    # written on create

    _reopen(session_id, pid, sid)

    o = client.get(_base(pid, sid) + f"/objects/{oid}", headers=h).json()["object"]
    assert len(o["helios_uuids"]) == 4                # re-mapped from the loaded object
    assert o["viewport"]["ctx_object_id"] is not None


def test_color_survives_reload_via_material_label(client):
    """Color is applied through a Helios material label, so it round-trips in
    context.xml and is restored on reopen with NO repaint from the DB."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    from app.core.session_store import registry as session_registry
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    grp = _mk_group(client, h, [
        ("Visualiser", {"texture_toggle": False, "color_r": 200, "color_g": 50,
                        "color_b": 50, "opacity": 100}),
    ], name="Reload Color")
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client),
        "properties": {**GROUND_PROPS, "resolution_x": 2, "resolution_y": 2,
                       "texture_x": 1, "texture_y": 1},
        "materials": [{"group_id": grp["id"], "sync": True}],
    }, headers=h).json()["object"]
    oid = obj["id"]

    _reopen(session_id, pid, sid)
    client.get(_base(pid, sid) + f"/objects/{oid}", headers=h)   # triggers reopen+hydrate

    sctx = session_registry.get_or_create_scenario_context(session_id, pid, sid)
    # The per-object color material label was serialized and restored — proving
    # color persisted without a DB repaint.
    assert sctx.context.doesMaterialExist(f"so_{oid}")


def test_group_delete_does_not_repaint_live_geometry(client):
    """Library delete without the eager hook is DB-only (break point): the live
    object keeps its geometry + ids AND its stale applied state."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    grp = _mk_group(client, h, [
        ("Visualiser", {"texture_toggle": False, "color_r": 10, "color_g": 250,
                        "color_b": 10, "opacity": 100}),
    ], name="ToDelete")
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
        "materials": [{"group_id": grp["id"], "sync": True}]}, headers=h).json()["object"]
    oid = obj["id"]
    before = client.get(_base(pid, sid) + f"/objects/{oid}", headers=h).json()["object"]

    r = client.delete(f"/api/materials/library/groups/{grp['id']}", headers=h)
    assert r.status_code == 200 and r.json()["unassigned_from"] == 0

    after = client.get(_base(pid, sid) + f"/objects/{oid}", headers=h).json()["object"]
    assert after["viewport"]["ctx_object_id"] == before["viewport"]["ctx_object_id"]
    assert after["helios_uuids"] == before["helios_uuids"]   # no rebuild
    # The applied state survives as stale until a sync (break point).
    assert len(after["material_groups"]) == 1
    assert after["material_groups"][0]["stale"] is True

def test_assign_empty_group(client):
    """An EMPTY group is assignable: the assignment exists with zero members,
    the geometry keeps its default paint, and the scenario stays in sync."""
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    obj = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": _ot_id(client), "properties": GROUND_PROPS,
    }, headers=h).json()["object"]
    grp = _mk_group(client, h, [], name="Empty Set")

    obj_url = _base(pid, sid) + f"/objects/{obj['id']}"
    r = client.post(obj_url + "/material-groups", json={"group_id": grp["id"]}, headers=h)
    assert r.status_code == 201, r.text
    a = r.json()["assignment"]
    assert a["name"] == "Empty Set" and a["materials"] == []

    r = client.get(_base(pid, sid) + "/material-sync", headers=h)
    assert r.json()["in_sync"] is True

    r = client.delete(obj_url + f"/material-groups/{grp['id']}", headers=h)
    assert r.status_code == 200


def test_colour_mode_escapes_resolution_cap(client):
    """Edge #3: a colour-mode Visualiser ground builds an UNTEXTURED tile, so it
    has no texture-pixel cap — a resolution that a textured/soil ground rejects
    with RESOLUTION_TOO_HIGH is accepted here."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    url = _base(pid, sid) + "/objects"
    low = {**GROUND_PROPS, "resolution_x": 2, "resolution_y": 2,
           "texture_x": 1, "texture_y": 1}
    high = {"properties": {"resolution_x": 1000, "resolution_y": 1000}}

    # Unstyled ground = soil texture (dirt.jpg, 512px) -> resolution is capped.
    soil = client.post(url, json={"object_type_id": _ot_id(client),
                                  "properties": low}, headers=h).json()["object"]
    r = client.patch(f"{url}/{soil['id']}", json=high, headers=h)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "RESOLUTION_TOO_HIGH"

    # Colour-mode Visualiser ground = untextured -> the same resolution is fine.
    grp = _mk_group(client, h, [("Visualiser", {
        "texture_toggle": False, "color_r": 100, "color_g": 100,
        "color_b": 100, "opacity": 100})], name="Plain Colour")
    colour = client.post(url, json={
        "object_type_id": _ot_id(client), "properties": low,
        "materials": [{"group_id": grp["id"], "sync": True}]}, headers=h).json()["object"]
    r = client.patch(f"{url}/{colour['id']}", json=high, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["object"]["properties"]["resolution_x"] == 1000
