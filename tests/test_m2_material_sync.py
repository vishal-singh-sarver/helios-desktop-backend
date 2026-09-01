"""Scenario material-sync (migration 022): library changes propagate eagerly
only to the active scenario (?scenario_id= on group PUT/DELETE/upload); every
other scenario keeps its applied snapshot state (the break point) and settles
drift through GET/PUT /project/{pid}/scenario/{sid}/material-sync — both paths
run the same reconcile engine."""
from uuid import uuid4

GROUND_PROPS = {
    "length": 10, "breadth": 20,
    "resolution_x": 100, "resolution_y": 100,
    "position_x": 0, "position_y": 0, "position_z": 0,
    "rotation_z": 0,
    "texture_x": 4, "texture_y": 4,
}

LIB = "/api/materials/library"


def _setup(client):
    """One project with TWO scenarios (A = main, B = fork) and one geometry in
    each. Returns (headers, pid, sid_a, sid_b, obj_a, obj_b)."""
    session_id = f"session_{uuid4().hex[:8]}"
    h = {"session-id": session_id}
    r = client.post("/api/project/create", json={
        "name": f"M2Sync_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2,
    }, headers=h)
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    sid_a = r.json()["main_scenario_id"]
    r = client.post(f"/api/project/{pid}/scenarios/create",
                    json={"name": "Fork"}, headers=h)
    assert r.status_code == 201, r.text
    sid_b = r.json()["scenario_id"]
    obj_a = _mk_object(client, h, pid, sid_a)
    obj_b = _mk_object(client, h, pid, sid_b)
    return h, pid, sid_a, sid_b, obj_a, obj_b


def _base(pid, sid):
    return f"/api/geometry/project/{pid}/scenario/{sid}"


def _mk_object(client, h, pid, sid):
    r = client.get("/api/catalog/object-types")
    ot = next(o["id"] for o in r.json()["object_types"] if o["object"] == "Ground")
    r = client.post(_base(pid, sid) + "/objects", json={
        "object_type_id": ot, "properties": GROUND_PROPS}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["object"]


def _mt_id(client, name):
    r = client.get("/api/catalog/material-types")
    return next(mt["id"] for mt in r.json()["material_types"] if mt["materialtype"] == name)


def _mk_group(client, h, type_specs, name=None):
    r = client.post(LIB + "/groups", json={
        **({"name": name} if name else {}),
        "materials": [
            {"material_type_id": _mt_id(client, tn), "properties": props or {}}
            for tn, props in type_specs
        ],
    }, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["group"]


def _assign(client, h, pid, sid, obj, group, sync=True):
    r = client.post(_base(pid, sid) + f"/objects/{obj['id']}/material-groups",
                    json={"group_id": group["id"], "sync": sync}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["assignment"]


def _status(client, h, pid, sid):
    r = client.get(_base(pid, sid) + "/material-sync", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _sync(client, h, pid, sid, body=None):
    r = client.put(_base(pid, sid) + "/material-sync", json=body or {}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _issues(status, kind):
    return [i for o in status["objects"] for i in o["issues"] if i["kind"] == kind]


def _assignments(client, h, pid, sid, obj):
    r = client.get(_base(pid, sid) + f"/objects/{obj['id']}/material-groups", headers=h)
    return r.json()["material_groups"]


def test_eager_scenario_full_update_other_scenario_lazy(client):
    """PUT group with scenario_id=A: A is reconciled + snapshots refreshed;
    B keeps its applied rows AND frozen snapshot rows (the break point), then
    settles via GET/PUT material-sync."""
    h, pid, sid_a, sid_b, obj_a, obj_b = _setup(client)
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")
    grp = _mk_group(client, h, [
        ("Radiation", {"reflectivity": 0.2}),
        ("Energy Balance", {"wind_speed": 3.5}),
    ], name="Shared Set")
    _assign(client, h, pid, sid_a, obj_a, grp)
    _assign(client, h, pid, sid_b, obj_b, grp)
    member_ids = {m["material_type_id"]: m["material_id"]
                  for m in _assignments(client, h, pid, sid_a, obj_a)[0]["materials"]}

    # Active scenario A: update Radiation value + REMOVE Energy Balance.
    r = client.put(LIB + f"/groups/{grp['id']}?scenario_id={sid_a}", json={
        "materials": [{"material_type_id": rad, "properties": {"reflectivity": 0.4}}],
    }, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["sync"]["applied"]["removed_members"] == 1
    assert r.json()["sync"]["applied"]["refreshed_values"] == 1
    assert r.json()["sync"]["conflicts"] == []

    # A is fully reconciled.
    a_groups = _assignments(client, h, pid, sid_a, obj_a)
    assert [m["material_type"] for m in a_groups[0]["materials"]] == ["Radiation"]
    assert _status(client, h, pid, sid_a)["in_sync"] is True

    # B is untouched — the removed member's row + snapshots survive.
    from app.db.database import SessionLocal
    from app.db.models import ObjectMaterial, ObjectPropertyData
    db = SessionLocal()
    try:
        b_rows = db.query(ObjectMaterial).filter(
            ObjectMaterial.scenario_object_id == obj_b["id"]).all()
        assert {r.material_type_id for r in b_rows} == {rad, eb}
        eb_member = member_ids[eb]
        assert db.query(ObjectPropertyData).filter(
            ObjectPropertyData.scenario_object_id == obj_b["id"],
            ObjectPropertyData.project_material_id == eb_member,
        ).count() > 0
    finally:
        db.close()

    # B reports the drift...
    status = _status(client, h, pid, sid_b)
    assert status["in_sync"] is False
    removed = _issues(status, "member_removed")
    assert len(removed) == 1 and removed[0]["material_type_id"] == eb
    assert removed[0]["group_id"] == grp["id"] and removed[0]["group_name"] == "Shared Set"
    stale = _issues(status, "values_stale")
    assert len(stale) == 1 and stale[0]["material_type_id"] == rad
    assert "reflectivity" in stale[0]["changed_properties"]

    # ...and PUT material-sync applies it (idempotently).
    result = _sync(client, h, pid, sid_b)
    assert result["success"] is True
    assert result["applied"] == {"removed_groups": 0, "removed_members": 1,
                                 "added_members": 0, "refreshed_values": 1}
    assert result["conflicts"] == []
    assert _status(client, h, pid, sid_b)["in_sync"] is True
    again = _sync(client, h, pid, sid_b)
    assert again["applied"] == {"removed_groups": 0, "removed_members": 0,
                                "added_members": 0, "refreshed_values": 0}


def test_group_deleted_lazy_cleanup(client):
    h, pid, sid_a, sid_b, obj_a, obj_b = _setup(client)
    grp = _mk_group(client, h, [("Radiation", {"reflectivity": 0.3})], name="Doomed Set")
    _assign(client, h, pid, sid_a, obj_a, grp)
    _assign(client, h, pid, sid_b, obj_b, grp)

    r = client.delete(LIB + f"/groups/{grp['id']}?scenario_id={sid_a}", headers=h)
    assert r.status_code == 200 and r.json()["unassigned_from"] == 1

    assert _assignments(client, h, pid, sid_a, obj_a) == []
    b_groups = _assignments(client, h, pid, sid_b, obj_b)
    assert len(b_groups) == 1 and b_groups[0]["stale"] is True

    status = _status(client, h, pid, sid_b)
    deleted = _issues(status, "group_deleted")
    assert len(deleted) == 1
    assert deleted[0]["group_id"] == grp["id"] and deleted[0]["group_name"] is None

    result = _sync(client, h, pid, sid_b)
    assert result["applied"]["removed_groups"] == 1
    assert result["applied"]["removed_members"] == 1
    assert _assignments(client, h, pid, sid_b, obj_b) == []
    assert _status(client, h, pid, sid_b)["in_sync"] is True


def test_member_added_lazy_and_eager_conflict_precheck(client):
    """A PUT that adds a type materializes eagerly on the active scenario and
    lazily elsewhere; the eager path pre-checks conflicts (409, nothing
    written), the lazy path skips + reports them."""
    h, pid, sid_a, sid_b, obj_a, obj_b = _setup(client)
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")
    g_rad = _mk_group(client, h, [("Radiation", None)], name="Rad Only")
    g_eb = _mk_group(client, h, [("Energy Balance", None)], name="EB Only")
    _assign(client, h, pid, sid_a, obj_a, g_rad)
    _assign(client, h, pid, sid_a, obj_a, g_eb)
    _assign(client, h, pid, sid_b, obj_b, g_eb)

    # EAGER pre-check: adding Radiation to g_eb collides with g_rad on obj_a.
    body = {"materials": [{"material_type_id": eb}, {"material_type_id": rad}]}
    r = client.put(LIB + f"/groups/{g_eb['id']}?scenario_id={sid_a}", json=body, headers=h)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "DUPLICATE_MATERIAL_TYPE_ASSIGNMENT"
    assert d["conflicts"][0]["group_id"] == g_rad["id"]
    assert d["conflicts"][0]["object_id"] == obj_a["id"]
    # Nothing was written to the library.
    r = client.get(LIB + f"/groups/{g_eb['id']}", headers=h)
    assert [m["material_type"] for m in r.json()["group"]["materials"]] == ["Energy Balance"]

    # LAZY: the same PUT without a scenario succeeds (library truth changes);
    # scenario B (no Radiation group) syncs the new member in cleanly.
    r = client.put(LIB + f"/groups/{g_eb['id']}", json=body, headers=h)
    assert r.status_code == 200, r.text
    status = _status(client, h, pid, sid_b)
    added = _issues(status, "member_added")
    assert len(added) == 1 and added[0]["material_type_id"] == rad
    result = _sync(client, h, pid, sid_b)
    assert result["applied"]["added_members"] == 1 and result["conflicts"] == []

    # Scenario A's sync hits the conflict: skipped + reported, never a 409.
    status = _status(client, h, pid, sid_a)
    added = _issues(status, "member_added")
    assert len(added) == 1 and added[0].get("conflict") is not None
    result = _sync(client, h, pid, sid_a)
    assert result["applied"]["added_members"] == 0
    assert len(result["conflicts"]) == 1
    c = result["conflicts"][0]
    assert c["blocking_group_id"] == g_rad["id"] and c["blocking_stale"] is False
    assert c["group_id"] == g_eb["id"] and c["object_id"] == obj_a["id"]


def test_remove_then_readd_type_converges_in_one_sync(client):
    """Deletion-before-add ordering: a type removed and re-added (new member id)
    must not conflict with its own stale row — one sync pass converges."""
    h, pid, sid_a, sid_b, obj_a, obj_b = _setup(client)
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")
    grp = _mk_group(client, h, [("Radiation", {"reflectivity": 0.2})], name="Churn Set")
    _assign(client, h, pid, sid_b, obj_b, grp)

    # Two lazy PUTs: Radiation → Energy Balance, then back to Radiation + EB.
    r = client.put(LIB + f"/groups/{grp['id']}", json={
        "materials": [{"material_type_id": eb}]}, headers=h)
    assert r.status_code == 200, r.text
    r = client.put(LIB + f"/groups/{grp['id']}", json={
        "materials": [{"material_type_id": rad, "properties": {"reflectivity": 0.5}},
                      {"material_type_id": eb}]}, headers=h)
    assert r.status_code == 200, r.text

    result = _sync(client, h, pid, sid_b)
    assert result["conflicts"] == []            # its own stale row was deleted first
    assert result["applied"]["removed_members"] == 1
    assert result["applied"]["added_members"] == 2
    groups = _assignments(client, h, pid, sid_b, obj_b)
    m = next(m for m in groups[0]["materials"] if m["material_type"] == "Radiation")
    assert m["properties"]["reflectivity"] == 0.5
    again = _sync(client, h, pid, sid_b)
    assert again["applied"] == {"removed_groups": 0, "removed_members": 0,
                                "added_members": 0, "refreshed_values": 0}


def test_sync_scoping_by_group(client):
    h, pid, sid_a, sid_b, obj_a, obj_b = _setup(client)
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")
    g1 = _mk_group(client, h, [("Radiation", {"reflectivity": 0.2})], name="Scope G1")
    g2 = _mk_group(client, h, [("Energy Balance", {"wind_speed": 3})], name="Scope G2")
    _assign(client, h, pid, sid_b, obj_b, g1)
    _assign(client, h, pid, sid_b, obj_b, g2)

    # Drift both groups (lazy library PUTs).
    client.put(LIB + f"/groups/{g1['id']}", json={"materials": [
        {"material_type_id": rad, "properties": {"reflectivity": 0.9}}]}, headers=h)
    client.put(LIB + f"/groups/{g2['id']}", json={"materials": [
        {"material_type_id": eb, "properties": {"wind_speed": 9}}]}, headers=h)

    result = _sync(client, h, pid, sid_b, {"group_ids": [g1["id"]]})
    assert result["applied"]["refreshed_values"] == 1
    status = _status(client, h, pid, sid_b)
    stale = _issues(status, "values_stale")
    assert len(stale) == 1 and stale[0]["group_id"] == g2["id"]   # g2 still pending


def test_frozen_assignment_membership_applies_values_do_not(client):
    h, pid, sid_a, sid_b, obj_a, obj_b = _setup(client)
    rad = _mt_id(client, "Radiation")
    eb = _mt_id(client, "Energy Balance")
    grp = _mk_group(client, h, [("Radiation", {"reflectivity": 0.2})], name="Frozen Set")
    _assign(client, h, pid, sid_b, obj_b, grp, sync=False)

    # Lazy library PUT: change the Radiation value AND add Energy Balance.
    r = client.put(LIB + f"/groups/{grp['id']}", json={"materials": [
        {"material_type_id": rad, "properties": {"reflectivity": 0.8}},
        {"material_type_id": eb, "properties": {"wind_speed": 5}},
    ]}, headers=h)
    assert r.status_code == 200, r.text

    # Frozen: membership drift is reported, value drift is NOT.
    status = _status(client, h, pid, sid_b)
    assert len(_issues(status, "member_added")) == 1
    assert _issues(status, "values_stale") == []

    result = _sync(client, h, pid, sid_b)
    assert result["applied"]["added_members"] == 1
    assert result["applied"]["refreshed_values"] == 0

    groups = _assignments(client, h, pid, sid_b, obj_b)
    assert groups[0]["sync"] is False
    m = next(m for m in groups[0]["materials"] if m["material_type"] == "Radiation")
    assert m["properties"]["reflectivity"] == 0.2      # frozen value kept
    assert m.get("library_drift") is True
    m = next(m for m in groups[0]["materials"] if m["material_type"] == "Energy Balance")
    assert m["properties"]["wind_speed"] == 5          # new member snapshotted


def _valid_png(size=256):
    """A real PNG (uniform grey, `size`×`size`): the eager repaint path rebuilds
    the tile with the uploaded texture, so PyHelios must be able to load it —
    and it must be large enough for the subdiv-vs-texture-resolution cap."""
    import struct
    import zlib

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)   # 8-bit RGB
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_upload_file_eager_refreshes_active_scenario(client):
    import io
    h, pid, sid_a, sid_b, obj_a, obj_b = _setup(client)
    vis = _mt_id(client, "Visualiser")
    # Start in colour mode (a Visualiser member is always a complete mode); the
    # texture upload below switches it into texture mode.
    grp = _mk_group(client, h, [("Visualiser", {
        "texture_toggle": False, "color_r": 128, "color_g": 128,
        "color_b": 128, "opacity": 100})], name="Tex Set")
    _assign(client, h, pid, sid_a, obj_a, grp)
    _assign(client, h, pid, sid_b, obj_b, grp)

    # Upload only stores the file and returns its path — no member write, so no
    # scenario reconcile happens here.
    r = client.post(LIB + f"/groups/{grp['id']}/files/texture_file",
                    files={"file": ("dirt.png", io.BytesIO(_valid_png()), "image/png")},
                    headers=h)
    assert r.status_code == 200, r.text
    path = r.json()["path"]
    assert "sync" not in r.json()

    # SAVING that path onto the member is what switches the mode and eagerly
    # refreshes the active scenario.
    r = client.put(LIB + f"/groups/{grp['id']}/materials/{vis}?scenario_id={sid_a}",
                   json={"properties": {"texture_toggle": True, "texture_file": path}},
                   headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["sync"]["applied"]["refreshed_values"] == 1   # A refreshed eagerly

    # B drifted; its sync refreshes the snapshot.
    status = _status(client, h, pid, sid_b)
    stale = _issues(status, "values_stale")
    assert len(stale) == 1 and "texture_file" in stale[0]["changed_properties"]
    result = _sync(client, h, pid, sid_b)
    assert result["applied"]["refreshed_values"] == 1

def test_last_member_removed_syncs_to_empty_assigned_group(client):
    """DELETE of a group's last member (no eager hook) leaves the other
    scenario painted from its snapshot; its sync removes the row and lands in
    the empty-assigned steady state — assignment kept, in_sync true."""
    h, pid, sid_a, sid_b, obj_a, obj_b = _setup(client)
    rad = _mt_id(client, "Radiation")
    grp = _mk_group(client, h, [("Radiation", {"reflectivity": 0.3})], name="Shrinking")
    _assign(client, h, pid, sid_b, obj_b, grp)

    r = client.delete(LIB + f"/groups/{grp['id']}/materials/{rad}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["group"]["materials"] == []          # group now empty

    status = _status(client, h, pid, sid_b)
    removed = _issues(status, "member_removed")
    assert len(removed) == 1 and removed[0]["group_name"] == "Shrinking"

    result = _sync(client, h, pid, sid_b)
    assert result["applied"]["removed_members"] == 1
    assert result["applied"]["removed_groups"] == 0      # assignment survives

    groups = _assignments(client, h, pid, sid_b, obj_b)
    assert len(groups) == 1 and groups[0]["materials"] == []
    assert _status(client, h, pid, sid_b)["in_sync"] is True
