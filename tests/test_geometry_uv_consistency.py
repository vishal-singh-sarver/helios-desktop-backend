"""The geometry wire format must never declare a texture it cannot back with UVs.

HELIO-339: opening a previously created project showed
    "Geometry data is truncated at primitive 0 (texture coordinates):
     needed 32 more byte(s) at offset 233, buffer is 233 byte(s)."
and the whole ground failed to draw — the parse is all-or-nothing, so one bad
primitive loses the entire object.

The format makes "has a texture path" imply "has vertexCount*8 bytes of UV".
The engine does not guarantee that: getTextureFile reads the MATERIAL while
getTextureUV reads the primitive's own uv field, and assigning a textured
material touches only the first. A colour-mode ground is built by addTileObject
with no texturefile, so it has NO UVs; the default soil texture then arrives
through its material label. Helios treats empty UV as a valid state meaning
"stretch the whole image across this shape", so nothing is corrupt — the format
simply could not express it, and the writer emitted a buffer no reader accepts.
"""
import struct

import pytest

from app.helios import context as helios_ctx
from app.services.geometry_pack import _default_uvs, pack_primitives_binary

pytestmark = pytest.mark.skipif(
    not helios_ctx.PYHELIOS_AVAILABLE, reason="native PyHelios unavailable")


def _walk(blob: bytes):
    """Parse exactly as the frontend's geometry.ts does, and fail the same way."""
    view = memoryview(blob)
    off = 0
    count = struct.unpack_from("<I", view, off)[0]
    off += 4
    out = []
    for i in range(count):
        off += 4                                             # uuid
        nv = struct.unpack_from("<I", view, off)[0]
        off += 4
        off += nv * 12                                       # vertices
        off += 12                                            # colour
        tex_len = struct.unpack_from("<H", view, off)[0]
        off += 2
        off += tex_len
        if tex_len > 0:
            need = nv * 8
            if off + need > len(blob):
                raise AssertionError(
                    f"Geometry data is truncated at primitive {i} (texture "
                    f"coordinates): needed {need} more byte(s) at offset {off}, "
                    f"buffer is {len(blob)} byte(s).")
            off += need
        out.append((nv, tex_len))
    assert off == len(blob), f"{len(blob) - off} trailing byte(s) after the walk"
    return out


def _textured_material_on_untextured_patch():
    """The exact shape from the bug report: no UVs, texture via the material."""
    from pyhelios.types import RGBAcolor
    from app.helios.context import vec2, vec3
    from app.services import material_apply

    ctx = helios_ctx.Context()
    uuids = [ctx.addPatch(center=vec3(0.0, 0.0, 0.0), size=vec2(1.0, 1.0))]
    ctx.addMaterial("m")
    ctx.setMaterialColor("m", RGBAcolor(1.0, 1.0, 1.0, 1.0))
    ctx.setMaterialTexture("m", str(material_apply._DEFAULT_GROUND_TEXTURE))
    ctx.assignMaterialToPrimitive(uuids, "m")
    return ctx, uuids


def test_a_textured_primitive_with_no_uvs_still_parses():
    """THE regression test. Before the fix this walk raised at primitive 0."""
    ctx, uuids = _textured_material_on_untextured_patch()

    _, uv_offsets = ctx.getPrimitiveTextureUV(uuids)
    assert int(uv_offsets[1]) - int(uv_offsets[0]) == 0, \
        "fixture no longer reproduces the UV-less case this test exists for"

    parsed = _walk(pack_primitives_binary(ctx, uuids))
    assert len(parsed) == 1


def test_the_texture_is_kept_not_dropped():
    """Padding, not discarding. The ground must still LOOK like soil — dropping
    the texture would trade an invisible object for a plain grey one."""
    ctx, uuids = _textured_material_on_untextured_patch()
    (nv, tex_len), = _walk(pack_primitives_binary(ctx, uuids))
    assert nv == 4
    assert tex_len > 0, "the texture was dropped instead of given default UVs"


def test_a_primitive_with_real_uvs_is_untouched():
    """The control: genuine UVs must survive exactly as before, V-flipped."""
    from app.helios.context import vec2, vec3
    from app.services import material_apply

    ctx = helios_ctx.Context()
    uuids = [ctx.addPatchTextured(vec3(0.0, 0.0, 0.0), vec2(1.0, 1.0),
                                  str(material_apply._DEFAULT_GROUND_TEXTURE))]
    _, uv_offsets = ctx.getPrimitiveTextureUV(uuids)
    assert int(uv_offsets[1]) - int(uv_offsets[0]) == 8, "fixture has no real UVs"

    (nv, tex_len), = _walk(pack_primitives_binary(ctx, uuids))
    assert nv == 4 and tex_len > 0


def test_default_uvs_cover_the_shapes_we_can_map():
    """Quads and triangles get the full image; anything else declines, so the
    writer never emits a texture length it cannot back."""
    assert _default_uvs(4).shape == (4, 2)
    assert _default_uvs(3).shape == (3, 2)
    assert _default_uvs(5) is None
    assert _default_uvs(0) is None
    # Full image, not a sliver.
    quad = _default_uvs(4)
    assert quad.min() == 0.0 and quad.max() == 1.0


# ── The root cause: hydration recorded what the DB wanted, not what it loaded ──

def test_loaded_signature_reports_colour_when_the_tile_has_no_uvs():
    """A UV-less tile IS a colour-mode build, whatever the DB says.

    Recording `desired` made _repaint_after_material_change compare a value
    against itself, so it never rebuilt and materials were stamped onto UV-less
    primitives instead.
    """
    from app.services.scene_object_service import _loaded_surface_signature

    ctx, uuids = _textured_material_on_untextured_patch()
    assert _loaded_surface_signature(ctx, uuids, "soil") == "colour"
    assert _loaded_surface_signature(ctx, uuids, "texture:/x/y.jpg") == "colour"


def test_loaded_signature_is_unchanged_for_a_properly_textured_tile():
    """The control — a real textured build must NOT be forced to rebuild."""
    from app.helios.context import vec2, vec3
    from app.services import material_apply
    from app.services.scene_object_service import _loaded_surface_signature

    ctx = helios_ctx.Context()
    uuids = [ctx.addPatchTextured(vec3(0.0, 0.0, 0.0), vec2(1.0, 1.0),
                                  str(material_apply._DEFAULT_GROUND_TEXTURE))]
    assert _loaded_surface_signature(ctx, uuids, "soil") == "soil"


def test_colour_mode_and_empty_objects_are_left_alone():
    """Never let this helper invent work: colour is already colour, and an
    object with no primitives has nothing to inspect."""
    from app.services.scene_object_service import _loaded_surface_signature

    ctx, uuids = _textured_material_on_untextured_patch()
    assert _loaded_surface_signature(ctx, uuids, "colour") == "colour"
    assert _loaded_surface_signature(ctx, [], "soil") == "soil"


# ── HELIO-339 end to end, through the real HTTP endpoint ─────────────────────

def test_reopening_a_saved_project_returns_parseable_geometry(client):
    """The reported STR: create a project with a ground, reopen it, draw it.

    Goes through the real endpoint the frontend calls and parses the response
    exactly as geometry.ts does, so a regression here fails with the same
    sentence the user saw rather than something that needs interpreting.
    """
    from uuid import uuid4
    from app.core.session_store import registry as session_registry
    from app.helios import persistence

    session_id = f"session_{uuid4().hex[:8]}"
    h = {"session-id": session_id}
    r = client.post("/api/project/create", json={
        "name": f"H339_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2,
    }, headers=h)
    assert r.status_code == 201, r.text
    pid, sid = r.json()["project_id"], r.json()["main_scenario_id"]
    base = f"/api/geometry/project/{pid}/scenario/{sid}"

    ot = client.get("/api/catalog/object-types").json()["object_types"]
    ot_id = next(o["id"] for o in ot if o["object"] == "Ground")
    props = {"length": 10, "breadth": 10, "resolution_x": 1, "resolution_y": 1,
             "position_x": 0, "position_y": 0, "position_z": 0,
             "rotation_z": 0, "texture_x": 1, "texture_y": 1}
    r = client.post(base + "/objects",
                    json={"object_type_id": ot_id, "properties": props}, headers=h)
    assert r.status_code in (200, 201), r.text
    oid = r.json()["object"]["id"]

    persistence.wait_for_scenario_saves()          # the save is queued
    session_registry.remove_scenario(session_id, pid, sid)   # "open previously created app"

    r = client.get(base + f"/objects/{oid}/geometry/binary", headers=h)
    assert r.status_code == 200, r.text
    parsed = _walk(r.content)                      # raises with the user's message
    assert parsed, "no primitives returned for a ground that exists"
