"""A property shared by several material types is ONE value per group.

STR: add a material group, pick a model type, set the Heat Transfer Flag, save;
then pick another type in the same group and change it. The two disagree.

`two_sided_heat_transfer` is defined on FOUR material types — Radiation, Energy
Balance, Photosynthesis and Boundary Layer Conductance — and stored as one
material_data row per member. So a group can hold two contradictory answers to
"is this surface exchanging heat from one side or two", which is a physical fact
about the surface and cannot differ between the models looking at it.

The set is derived from the catalog by a self-join (see _shared_editable_properties)
rather than hardcoded: shared AND editable. Today that is exactly
two_sided_heat_transfer and stomatal_sidedness; a property added later is picked
up automatically instead of silently staying broken.
"""
from uuid import uuid4

import pytest

FLAG = "two_sided_heat_transfer"
# Two types that both carry the flag, so one group can hold both.
TYPE_A, TYPE_B = "Radiation", "Energy Balance"


def _ids(client):
    session_id = f"session_{uuid4().hex[:8]}"
    types = client.get("/api/catalog/material-types").json()["material_types"]
    by_name = {t["materialtype"]: t["id"] for t in types}
    return session_id, by_name


def _member(group, type_name):
    return next(m for m in group["materials"] if m["material_type"] == type_name)


def _make_group(client, h, by_name, a_props=None, b_props=None):
    r = client.post("/api/materials/library/groups", json={
        "materials": [
            {"material_type_id": by_name[TYPE_A], "properties": a_props or {}},
            {"material_type_id": by_name[TYPE_B], "properties": b_props or {}},
        ],
    }, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["group"]


def test_the_catalog_really_shares_this_property(client):
    """Guard the premise. If the catalog stops sharing the flag across types,
    every other test here becomes vacuous rather than failing."""
    from app.services.material_library_service import _shared_editable_properties
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        shared = _shared_editable_properties(db)
    finally:
        db.close()
    assert FLAG in shared, f"{FLAG} is no longer a shared editable property"
    assert len(shared[FLAG]) >= 2, "the flag is no longer on multiple types"


def test_setting_the_flag_on_one_member_sets_it_on_the_others(client):
    """THE bug. Step 3 of the STR, observed at step 5."""
    session_id, by_name = _ids(client)
    h = {"session-id": session_id}
    grp = _make_group(client, h, by_name)

    r = client.put(
        f"/api/materials/library/groups/{grp['id']}/materials/{by_name[TYPE_A]}",
        json={"properties": {FLAG: "Two Sided"}}, headers=h)
    assert r.status_code == 200, r.text

    after = r.json()["group"]
    assert _member(after, TYPE_A)["properties"][FLAG] == "Two Sided"
    assert _member(after, TYPE_B)["properties"][FLAG] == "Two Sided", (
        "the flag is a physical fact about the surface — the other member in "
        "the same group still disagrees")


def test_changing_it_on_the_second_member_moves_the_first(client):
    """Step 4: 'now pick another and change it'. It must not diverge."""
    session_id, by_name = _ids(client)
    h = {"session-id": session_id}
    grp = _make_group(client, h, by_name)

    client.put(f"/api/materials/library/groups/{grp['id']}/materials/{by_name[TYPE_A]}",
               json={"properties": {FLAG: "Two Sided"}}, headers=h)
    r = client.put(f"/api/materials/library/groups/{grp['id']}/materials/{by_name[TYPE_B]}",
                   json={"properties": {FLAG: "One Sided"}}, headers=h)
    assert r.status_code == 200, r.text

    after = r.json()["group"]
    assert _member(after, TYPE_A)["properties"][FLAG] == "One Sided"
    assert _member(after, TYPE_B)["properties"][FLAG] == "One Sided"


def test_creating_a_group_with_conflicting_values_settles_on_one(client):
    """A group cannot be BORN divergent either."""
    session_id, by_name = _ids(client)
    h = {"session-id": session_id}
    grp = _make_group(client, h, by_name,
                      a_props={FLAG: "Two Sided"}, b_props={FLAG: "One Sided"})

    a = _member(grp, TYPE_A)["properties"].get(FLAG)
    b = _member(grp, TYPE_B)["properties"].get(FLAG)
    assert a == b, f"group created holding two answers: {TYPE_A}={a} {TYPE_B}={b}"


def test_a_non_shared_property_is_left_alone(client):
    """The control. Only SHARED editable properties are global — a property
    belonging to one type must not leak into its neighbours."""
    from app.services.eav_validation import load_type_properties
    from app.db.database import SessionLocal

    session_id, by_name = _ids(client)
    h = {"session-id": session_id}
    db = SessionLocal()
    try:
        a_defs = set(load_type_properties(db, material_type_id=by_name[TYPE_A]))
        b_defs = set(load_type_properties(db, material_type_id=by_name[TYPE_B]))
    finally:
        db.close()
    only_a = sorted(a_defs - b_defs)
    if not only_a:
        pytest.skip(f"{TYPE_A} has no property of its own to test with")

    grp = _make_group(client, h, by_name)
    r = client.put(
        f"/api/materials/library/groups/{grp['id']}/materials/{by_name[TYPE_A]}",
        json={"properties": {}}, headers=h)
    assert r.status_code == 200, r.text
    assert only_a[0] not in _member(r.json()["group"], TYPE_B)["properties"], (
        f"{only_a[0]} belongs only to {TYPE_A} but appeared on {TYPE_B}")


# ── What must NOT be treated as shared ────────────────────────────────────────

def test_computed_and_external_properties_are_not_shared(client):
    """The `editable` filter, pinned.

    Ten properties are shared across types, but only two are editable. The rest
    are `computed` (a model produces them) or `external` (the weather supplies
    them) — forcing those equal across members would overwrite one model's input
    with another's output. Dropping the filter leaves every other test passing,
    so this is the only thing standing between us and that.
    """
    from app.services.material_library_service import _shared_editable_properties
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        shared = _shared_editable_properties(db)
    finally:
        db.close()

    for prop in ("air_temperature", "air_humidity", "air_pressure",
                 "boundary_layer_conductance", "radiation_flux"):
        assert prop not in shared, (
            f"{prop} is shared but not user-editable — propagating it would "
            f"overwrite a model input or a computed result")


def test_a_property_on_only_one_type_is_not_shared(client):
    """The self-join's `!=` on material_type_id, pinned.

    Join a row to ITSELF (== instead of !=) and every editable property looks
    shared. The per-sibling type check hides that at runtime, so nothing else
    catches it — but the set would be wrong for any future caller.
    """
    from app.services.material_library_service import _shared_editable_properties
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        shared = _shared_editable_properties(db)
    finally:
        db.close()

    # Photosynthesis-only Farquhar/Ball-Berry parameters — one type each.
    for prop in ("alpha", "bbl_a1", "bbl_d0", "bbl_gs0"):
        assert prop not in shared, (
            f"{prop} belongs to a single material type; the self-join is "
            f"matching rows to themselves")


# ── The group ASSIGNED to a geometry ─────────────────────────────────────────

def test_the_flag_reaches_an_assigned_geometry_snapshot(client):
    """Library propagation is only half the job.

    Once a group is assigned to a geometry, each member gets a frozen snapshot
    in object_property_data. Fixing the library but not the snapshot would look
    fixed in the materials panel and stay wrong on the object the user actually
    renders — so this asserts the sibling's SNAPSHOT moved too, not just the
    library row.
    """
    from uuid import uuid4

    session_id, by_name = _ids(client)
    h = {"session-id": session_id}

    proj = client.post("/api/project/create", json={
        "name": f"Shared_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
    }, headers=h).json()
    pid, sid = proj["project_id"], proj["main_scenario_id"]
    base = f"/api/geometry/project/{pid}/scenario/{sid}"

    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    obj = client.post(base + "/objects", json={"object_type_id": ot, "properties": {
        "length": 5, "breadth": 5, "resolution_x": 2, "resolution_y": 2,
        "position_x": 0, "position_y": 0, "position_z": 0,
        "rotation_z": 0, "texture_x": 1, "texture_y": 1,
    }}, headers=h)
    assert obj.status_code in (200, 201), obj.text
    object_id = obj.json()["object"]["id"]

    grp = _make_group(client, h, by_name)
    r = client.post(f"{base}/objects/{object_id}/material-groups",
                    json={"group_id": grp["id"], "sync": True}, headers=h)
    if r.status_code not in (200, 201):
        pytest.skip(f"assignment endpoint unavailable here: {r.status_code}")

    r = client.put(
        f"/api/materials/library/groups/{grp['id']}/materials/{by_name[TYPE_A]}"
        f"?scenario_id={sid}",
        json={"properties": {FLAG: "Two Sided"}}, headers=h)
    assert r.status_code == 200, r.text

    # Read the applied snapshot for the SIBLING member, not the one we wrote.
    from app.db.database import SessionLocal
    from app.db.models import ObjectPropertyData, ProjectMaterial, PropertyType
    db = SessionLocal()
    try:
        pt = db.query(PropertyType).filter(PropertyType.property == FLAG).first()
        sib = (db.query(ProjectMaterial)
               .filter(ProjectMaterial.material_group_id == grp["id"],
                       ProjectMaterial.material_type_id == by_name[TYPE_B])
               .first())
        assert sib is not None
        row = (db.query(ObjectPropertyData)
               .filter(ObjectPropertyData.scenario_object_id == object_id,
                       ObjectPropertyData.project_material_id == sib.id,
                       ObjectPropertyData.property_type_id == pt.id)
               .first())
    finally:
        db.close()

    assert row is not None and row.value == "Two Sided", (
        f"the sibling's APPLIED snapshot did not follow the library "
        f"(got {row.value if row else None!r})")
