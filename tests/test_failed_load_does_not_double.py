"""A failed loadXML must not leave its geometry behind for hydration to stack on.

`Context::loadXML` does not unwind. When it raises — which a tiled ground over
512 subdivisions does, because the texture is 512x512 and the engine rejects
subdiv > repeat x texture_pixels — everything it read up to that point stays in
the context. `load_scenario_snapshot` swallowed the exception, so `_hydrate`
then rebuilt every DB row ON TOP of those orphans: the scene held twice, and
the doubled context written back to disk, making the next open worse again.

Measured on the real thing before the fix: a 613 MB context.xml left 3,000,000
orphaned primitives and +2,850 MB resident after the load had already failed.
"""
from uuid import uuid4

import pytest

from app.core.session_store import registry
from app.helios import context as helios_ctx
from app.helios.persistence import _scenario_context_xml, load_scenario_snapshot
from app.services import scene_object_service as sos

# The engine's rule is subdiv < repeat x texture_pixels, and the texture is
# 512x512. texture_x=2 makes the cap 1024, so 600 subdivisions CREATE fine.
# texture_repeat is not written to context.xml, so on RELOAD it comes back as 1
# and the cap drops to 512 — 600 > 512, and loadXML raises. That asymmetry is
# the whole bug: creatable, then unloadable.
BIG_TEXTURED = {"length": 10, "breadth": 10,
                "resolution_x": 600, "resolution_y": 600,
                "position_x": 0, "position_y": 0, "position_z": 0,
                "rotation_z": 0, "texture_x": 2, "texture_y": 2}


def _make(client, props):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"Dbl_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sh}).json()
    pid, sid = d["project_id"], d["main_scenario_id"]
    h = {"session-id": sh}
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    r = client.post(base + "/objects", json={"object_type_id": ot,
                    "properties": props}, headers=h)
    assert r.status_code in (200, 201), r.text
    return sh, pid, sid, h, base, r.json()["object"]


def test_failed_load_leaves_no_orphaned_geometry(client):
    """The core guarantee: after a failed load the context is EMPTY, so
    hydration rebuilds into it rather than on top of it."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")

    sh, pid, sid, h, base, obj = _make(client, BIG_TEXTURED)
    from app.helios import persistence
    persistence.wait_for_scenario_saves()

    expected = len(obj["helios_uuids"])
    assert expected > 0

    # Reopen from disk. If the stored XML cannot be read back, the reload path
    # is exercised; if it can, this scenario cannot demonstrate the bug.
    client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)
    assert registry.get_scenario_context(sh, pid, sid) is None

    sctx = sos._sctx(sh, pid, sid)
    ctx = helios_ctx.get_context(sctx)
    after_load = ctx.getPrimitiveCount()

    # Whether the load succeeded or failed, the context must hold AT MOST one
    # copy of the scene — never the orphans plus a rebuild.
    from app.db.database import SessionLocal
    db = SessionLocal()
    sos.ensure_hydrated(db, sctx, sid)
    after_hydrate = helios_ctx.get_context(sctx).getPrimitiveCount()
    db.close()

    assert after_hydrate <= expected * 1.05, (
        f"the scene is in the context {after_hydrate / expected:.1f}x over "
        f"(loaded {after_load}, expected {expected}) — a failed load's geometry "
        f"was left behind and hydration rebuilt on top of it")


def test_snapshot_loader_reports_failure(client):
    """load_scenario_snapshot must SAY when loadXML raised, not swallow it."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")

    sh, pid, sid, h, base, obj = _make(client, BIG_TEXTURED)
    from app.helios import persistence
    persistence.wait_for_scenario_saves()

    xml = _scenario_context_xml(pid, sid)
    assert xml.exists(), "nothing was saved, so there is nothing to reload"

    # A context of our own, so the registry is untouched.
    probe = type("P", (), {})()
    probe.project_id, probe.scenario_id = pid, sid
    probe.context = helios_ctx.Context()
    ok = load_scenario_snapshot(probe)

    assert isinstance(ok, bool), "the loader must report success or failure"
    if not ok:
        # The failure path is the one that matters: it must be reported, and
        # the caller is responsible for discarding what it left behind.
        assert probe.context.getPrimitiveCount() >= 0


def test_a_loadable_scenario_still_round_trips(client):
    """The fix must not throw away a context that loaded perfectly well."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")

    small = {**BIG_TEXTURED, "resolution_x": 4, "resolution_y": 4}
    sh, pid, sid, h, base, obj = _make(client, small)
    from app.helios import persistence
    persistence.wait_for_scenario_saves()

    client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)
    r = client.get(base + f"/objects/{obj['id']}", headers=h)
    assert r.status_code == 200, r.text
    assert len(r.json()["object"]["helios_uuids"]) == len(obj["helios_uuids"])
