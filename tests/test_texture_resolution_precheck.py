"""The texture cap, answered before the engine refuses the build.

`addTileObject` rejects `subdiv >= snapped_repeat * texture_pixels`
(Context_object.cpp:377-380). Today that surfaces as a bare toast when a
material is applied, and as a GREEN SUCCESS TOAST when a texture is changed
under a material already applied — the reconcile path turns the 422 into a 200.
"""
from uuid import uuid4

import pytest

from app.helios import context as helios_ctx
from app.services import material_apply as ma


def _px(path):
    return ma._texture_pixels(path)


def _accepts(subdiv, repeat, path, px):
    """True when check_resolution lets this through."""
    try:
        ma.check_resolution(subdiv, repeat, path, "g")
        return True
    except Exception:
        return False


def test_matches_the_engine():
    """The predicate must agree with addTileObject, including the case a naive
    reading gets wrong: 521 at repeat 2 reads as legal (521 < 1024), but 521 is
    odd so the engine walks the repeat down to 1 and refuses it."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    from pyhelios.Context import Context
    from pyhelios.types import SphericalCoord, int2, vec2, vec3

    tex = ma._DEFAULT_GROUND_TEXTURE
    assert _px(tex) == (512, 512), "the stock soil texture moved"

    def engine_accepts(sx, sy, rx, ry):
        try:
            Context().addTileObject(center=vec3(0, 0, 0), size=vec2(10, 10),
                                    rotation=SphericalCoord(1, 0, 0),
                                    subdiv=int2(sx, sy), texturefile=tex,
                                    texture_repeat=int2(rx, ry))
            return True
        except Exception:
            return False

    for sx, sy, rx, ry in [
        (511, 1, 1, 1),    # just under
        (512, 1, 1, 1),    # the comparison is >=, not >
        (1, 512, 1, 1),    # y fails independently
        (521, 1, 2, 1),    # odd subdiv -> repeat snaps to 1
        (600, 10, 1, 1),
        (600, 10, 2, 1),   # a bigger repeat lifts the cap
        (1024, 1, 3, 1),   # 3 does not divide 1024 -> snaps to 2 -> exactly at the cap
        (2559, 10, 5, 5),  # 5 does not divide 2559 -> snaps to 3
        (100, 100, 4, 4),
    ]:
        assert _accepts((sx, sy), (rx, ry), tex, None) == engine_accepts(sx, sy, rx, ry), \
            f"disagreed on subdiv {sx}x{sy} repeat {rx}x{ry}"


def test_the_valid_set_has_gaps():
    """Raising the subdivision can turn a failure back into a pass, because the
    snap depends on the candidate: 42 passes, 43 and 44 fail, 45 passes again.
    Pinned because it is the reason the predicate cannot be simplified to a
    single threshold."""
    ok = [s < ma._snap(s, 3) * 16 for s in (42, 43, 44, 45, 46)]
    assert ok == [True, False, False, True, False]


def test_silent_when_it_cannot_answer():
    """No texture, or a file that cannot be read: allow the write. We would
    rather let the engine refuse a build than block one it would accept."""
    for path in (None, "", "/nowhere/missing.jpg", "run.sh"):
        ma.check_resolution((9000, 9000), (1, 1), path, "g")   # must not raise


def test_reading_the_size_does_not_touch_the_engine():
    """The size comes from the file's header, so no Context is created and
    nothing is inserted into the engine's texture map — which is never erased,
    and would otherwise keep a stale size for a re-uploaded path."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    from pyhelios.Context import Context
    ctx = Context()
    before = ctx.getPrimitiveCount()
    for _ in range(5):
        assert ma._texture_pixels(ma._DEFAULT_GROUND_TEXTURE) == (512, 512)
    assert ctx.getPrimitiveCount() == before


# ── assignment replaces, in one transaction ──────────────────────────────────
#
# Assigning used to 409 when another group held the same material type, forcing
# the client to DELETE first — and when the POST was then refused for an
# impossible texture, the DELETE had already committed and the geometry was left
# bare. The assignment now displaces the old material itself, after the texture
# check, so a refusal changes nothing at all.

GROUND = {
    "length": 10, "breadth": 10,
    "resolution_x": 100, "resolution_y": 100,
    "position_x": 0, "position_y": 0, "position_z": 0,
    "rotation_z": 0,
    "texture_x": 1, "texture_y": 1,
}

# The stock soil texture is 512x512, so repeat 1 caps the subdivision at 511.
TEXTURED = {"texture_toggle": True, "texture_file": ma._DEFAULT_GROUND_TEXTURE}
COLOUR = {"texture_toggle": False, "color_r": 10, "color_g": 200, "color_b": 10,
          "opacity": 100}


def _setup(client):
    """One project, one scenario. Returns (headers, pid, sid)."""
    h = {"session-id": f"session_{uuid4().hex[:8]}"}
    r = client.post("/api/project/create", json={
        "name": f"Precheck_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2,
    }, headers=h)
    assert r.status_code == 201, r.text
    return h, r.json()["project_id"], r.json()["main_scenario_id"]


def _mk(client, h, props):
    """A one-member Visualiser group — the only member that decides the tile."""
    vt = next(m["id"] for m in client.get("/api/catalog/material-types").json()
              ["material_types"] if m["materialtype"] == "Visualiser")
    r = client.post("/api/materials/library/groups", json={
        "materials": [{"material_type_id": vt, "properties": props}]}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["group"]


def _ground(client, h, pid, sid, **props):
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    r = client.post(f"/api/geometry/project/{pid}/scenario/{sid}/objects", json={
        "object_type_id": ot, "properties": {**GROUND, **props}}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["object"]["id"]


def _assign(client, h, pid, sid, oid, gid):
    return client.post(f"/api/geometry/project/{pid}/scenario/{sid}"
                       f"/objects/{oid}/material-groups",
                       json={"group_id": gid, "sync": True}, headers=h)


def _assigned(client, h, pid, sid, oid):
    r = client.get(f"/api/geometry/project/{pid}/scenario/{sid}/objects/{oid}",
                   headers=h)
    assert r.status_code == 200, r.text
    return [g["group_id"] for g in r.json()["object"]["material_groups"]]


def _ground_past_the_cap(client, h, pid, sid, colour):
    """A ground carrying `colour` at a subdivision no 512px texture can serve.

    It cannot simply be created that way: a bare ground already wears the stock
    soil texture, so the create is refused by the very cap under test. Assign a
    COLOUR material first — that takes the texture off the tile — and only then
    raise the resolution.
    """
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    oid = _ground(client, h, pid, sid, resolution_x=100, resolution_y=2)
    r = client.post(base + f"/objects/{oid}/material-groups",
                    json={"group_id": colour["id"], "sync": True}, headers=h)
    assert r.status_code == 201, r.text
    r = client.patch(base + f"/objects/{oid}",
                     json={"properties": {"resolution_x": 900}}, headers=h)
    assert r.status_code == 200, r.text
    return oid


def test_assigning_replaces_the_material_already_there(client):
    """One request. B displaces A — no DELETE from the client, so the geometry
    is never momentarily bare. This used to be a 409."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    h, pid, sid = _setup(client)
    a = _mk(client, h, COLOUR)
    b = _mk(client, h, TEXTURED)
    oid = _ground(client, h, pid, sid, resolution_x=100, resolution_y=2)

    assert _assign(client, h, pid, sid, oid, a["id"]).status_code == 201
    assert _assigned(client, h, pid, sid, oid) == [a["id"]]

    assert _assign(client, h, pid, sid, oid, b["id"]).status_code == 201
    assert _assigned(client, h, pid, sid, oid) == [b["id"]]


def test_a_refused_texture_leaves_the_old_material_in_place(client):
    """THE BUG. The texture check runs BEFORE anything is displaced, so a
    refusal changes nothing — previously the client had already deleted A by
    this point and the ground was left bare."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    h, pid, sid = _setup(client)
    colour = _mk(client, h, COLOUR)
    tex = _mk(client, h, TEXTURED)
    oid = _ground_past_the_cap(client, h, pid, sid, colour)

    r = _assign(client, h, pid, sid, oid, tex["id"])
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "RESOLUTION_TOO_HIGH"
    assert "512x512" in detail["error"] and "900 x 2" in detail["error"]

    # still carrying exactly the colour group
    assert _assigned(client, h, pid, sid, oid) == [colour["id"]]


def test_replacing_works_where_the_texture_fits(client):
    """repeat 2 lifts the cap to 1024, so 600 subdivisions take the 512px
    texture — the assignment goes through and replaces."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    h, pid, sid = _setup(client)
    colour = _mk(client, h, COLOUR)
    tex = _mk(client, h, TEXTURED)
    oid = _ground(client, h, pid, sid, resolution_x=600, resolution_y=2,
                  texture_x=2)

    assert _assign(client, h, pid, sid, oid, colour["id"]).status_code == 201
    assert _assign(client, h, pid, sid, oid, tex["id"]).status_code == 201
    assert _assigned(client, h, pid, sid, oid) == [tex["id"]]


def test_colour_replaces_on_a_ground_past_the_texture_cap(client):
    """Colour mode has no resolution cap, so a ground too fine for any texture
    still takes a colour material — and one colour still replaces another.

    (Whether an UNREADABLE texture blocks the check is covered directly by
    test_silent_when_it_cannot_answer; it cannot be asserted through the
    assignment, because the engine then fails the build on the missing file.)
    """
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    h, pid, sid = _setup(client)
    colour = _mk(client, h, COLOUR)
    oid = _ground_past_the_cap(client, h, pid, sid, colour)

    other = _mk(client, h, {**COLOUR, "color_r": 200, "color_g": 10})
    assert _assign(client, h, pid, sid, oid, other["id"]).status_code == 201
    assert _assigned(client, h, pid, sid, oid) == [other["id"]]


def test_reassigning_the_same_group_is_still_a_409(client):
    """Displacing is for a DIFFERENT group holding the type. Re-posting the group
    the geometry already carries is a client bug, not a replacement."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    h, pid, sid = _setup(client)
    colour = _mk(client, h, COLOUR)
    oid = _ground(client, h, pid, sid, resolution_x=100, resolution_y=2)

    assert _assign(client, h, pid, sid, oid, colour["id"]).status_code == 201
    r = _assign(client, h, pid, sid, oid, colour["id"])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_ALREADY_ASSIGNED"
    assert _assigned(client, h, pid, sid, oid) == [colour["id"]]


def test_assigning_an_unknown_group_is_a_404(client):
    h, pid, sid = _setup(client)
    oid = _ground(client, h, pid, sid)
    r = _assign(client, h, pid, sid, oid, 999999)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_NOT_FOUND"
