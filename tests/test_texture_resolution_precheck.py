"""The texture cap, answered before the engine refuses the build.

`addTileObject` rejects `subdiv >= snapped_repeat * texture_pixels`
(Context_object.cpp:377-380). Today that surfaces as a bare toast when a
material is applied, and as a GREEN SUCCESS TOAST when a texture is changed
under a material already applied — the reconcile path turns the 422 into a 200.
"""
import pytest

from app.helios import context as helios_ctx
from app.services import material_apply as ma


def _px(path):
    from pyhelios.Context import Context
    return ma._texture_pixels(Context(), path)


def _accepts(subdiv, repeat, path, px):
    """True when check_resolution lets this through."""
    try:
        from pyhelios.Context import Context
        ma.check_resolution(Context(), subdiv, repeat, path, "g")
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
    """No texture, an unreadable file, or no context: allow the write. We would
    rather let the engine refuse a build than block one it would accept."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    from pyhelios.Context import Context
    ctx = Context()
    for path in (None, "", "/nowhere/missing.jpg", "run.sh"):
        ma.check_resolution(ctx, (9000, 9000), (1, 1), path, "g")   # must not raise
    ma.check_resolution(None, (9000, 9000), (1, 1), "x.jpg", "g")


def test_probe_leaves_no_geometry():
    """The size read adds a primitive and deletes it."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    from pyhelios.Context import Context
    ctx = Context()
    before = ctx.getPrimitiveCount()
    for _ in range(5):
        assert ma._texture_pixels(ctx, ma._DEFAULT_GROUND_TEXTURE) == (512, 512)
    assert ctx.getPrimitiveCount() == before
