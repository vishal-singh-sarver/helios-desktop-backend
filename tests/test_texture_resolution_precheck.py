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


# ── the pre-check endpoint ───────────────────────────────────────────────────
#
# The client must DELETE the ground's current material before it can POST a new
# one, and those are two requests: if the POST fails the DELETE has already
# committed and the ground is left bare. This endpoint is asked first, so the
# DELETE is never issued when the answer is no.

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


def _check(client, h, pid, sid, oid, gid):
    r = client.get(f"/api/geometry/project/{pid}/scenario/{sid}"
                   f"/objects/{oid}/material-groups/{gid}/check", headers=h)
    assert r.status_code == 200, r.text     # always 200 — the verdict is the body
    return r.json()


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


def test_precheck_says_yes_when_the_texture_fits(client):
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    h, pid, sid = _setup(client)
    tex = _mk(client, h, TEXTURED)

    # repeat 2 lifts the cap to 1024, so 600 subdivisions fit
    oid = _ground(client, h, pid, sid, resolution_x=600, resolution_y=2,
                  texture_x=2)
    assert _check(client, h, pid, sid, oid, tex["id"]) == {"ok": True}


def test_precheck_says_no_without_touching_anything(client):
    """The whole point: a no must leave the ground exactly as it was."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    h, pid, sid = _setup(client)
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    colour = _mk(client, h, COLOUR)
    tex = _mk(client, h, TEXTURED)
    oid = _ground_past_the_cap(client, h, pid, sid, colour)

    verdict = _check(client, h, pid, sid, oid, tex["id"])
    assert verdict["ok"] is False
    assert verdict["code"] == "RESOLUTION_TOO_HIGH"
    assert "512x512" in verdict["error"] and "900 x 2" in verdict["error"]

    # unchanged: still carrying exactly the colour group
    after = client.get(base + f"/objects/{oid}", headers=h).json()["object"]
    assert [g["group_id"] for g in after["material_groups"]] == [colour["id"]]


def test_precheck_ignores_the_duplicate_type_rule(client):
    """THE TRAP. At pre-check time the material being replaced still occupies the
    Visualiser slot, so running the full assign validation would 409 on a
    conflict the caller is about to resolve by displacing it. Reusing that
    validation passes every other test here and fails only this one."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    h, pid, sid = _setup(client)
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    a = _mk(client, h, COLOUR)
    b = _mk(client, h, TEXTURED)

    oid = _ground(client, h, pid, sid, resolution_x=100, resolution_y=2)
    r = client.post(base + f"/objects/{oid}/material-groups",
                    json={"group_id": a["id"], "sync": True}, headers=h)
    assert r.status_code == 201, r.text

    # The conflict is real: assigning B right now really would be refused...
    r = client.post(base + f"/objects/{oid}/material-groups",
                    json={"group_id": b["id"], "sync": True}, headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "DUPLICATE_MATERIAL_TYPE_ASSIGNMENT"

    # ...but the caller is about to resolve it by displacing A, so the answer
    # the pre-check owes is yes.
    assert _check(client, h, pid, sid, oid, b["id"]) == {"ok": True}


def test_precheck_allows_what_it_cannot_judge(client):
    """Colour mode has no cap, and an unreadable file is simply unknown. Neither
    is a reason to say no, and a false no would strand a legal assignment."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    h, pid, sid = _setup(client)
    colour = _mk(client, h, COLOUR)
    oid = _ground_past_the_cap(client, h, pid, sid, colour)

    other_colour = _mk(client, h, {**COLOUR, "color_r": 200, "color_g": 10})
    assert _check(client, h, pid, sid, oid, other_colour["id"]) == {"ok": True}

    missing = _mk(client, h, {"texture_toggle": True,
                              "texture_file": "/nowhere/missing.png"})
    assert _check(client, h, pid, sid, oid, missing["id"]) == {"ok": True}


def test_precheck_404s_on_an_unknown_group(client):
    h, pid, sid = _setup(client)
    oid = _ground(client, h, pid, sid)
    r = client.get(f"/api/geometry/project/{pid}/scenario/{sid}"
                   f"/objects/{oid}/material-groups/999999/check", headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MATERIAL_GROUP_NOT_FOUND"
