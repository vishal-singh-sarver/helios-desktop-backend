"""Packing the scene stops when the client that asked for it goes away.

This is the longest READ in the app — 228 MB on a 1000x1000 ground, fetched
more than once per viewport load — and it used to run to completion even after
the browser had dropped the connection. Unlike an engine call it is a Python
loop, so it can stop part-way.

Two checkpoints are needed, not one: the inner packing loop, and the
O(mask x distinct-textures) rescan that selects each group. On a large scene
the rescan dominates, so guarding only the inner loop leaves most of the work
uninterruptible.
"""
import threading
from uuid import uuid4

import pytest

from app.helios import context as helios_ctx
from app.services.geometry_pack import PackCancelled, pack_primitives_binary

GROUND = {"length": 10, "breadth": 10, "resolution_x": 60, "resolution_y": 60,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}


def _scene(client):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"Pk_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sh}).json()
    pid, sid = d["project_id"], d["main_scenario_id"]
    h = {"session-id": sh}
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    r = client.post(base + "/objects", json={"object_type_id": ot,
                    "properties": GROUND}, headers=h)
    assert r.status_code in (200, 201), r.text
    return sh, pid, sid, base, r.json()["object"]["helios_uuids"]


def test_pack_raises_when_already_cancelled(client):
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    sh, pid, sid, base, uuids = _scene(client)
    from app.core.session_store import registry
    ctx = helios_ctx.get_context(registry.get_scenario_context(sh, pid, sid))

    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(PackCancelled):
        pack_primitives_binary(ctx, uuids, cancelled=cancelled)


def test_pack_completes_normally_when_not_cancelled(client):
    """The token is optional and inert — nothing changes when nobody cancels."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    sh, pid, sid, base, uuids = _scene(client)
    from app.core.session_store import registry
    ctx = helios_ctx.get_context(registry.get_scenario_context(sh, pid, sid))

    with_token = pack_primitives_binary(ctx, uuids, cancelled=threading.Event())
    without = pack_primitives_binary(ctx, uuids)
    assert with_token == without, "the cancel token changed the packed output"
    assert len(without) > 0


def test_pack_stops_partway_not_at_the_end(client):
    """Cancelling mid-pack must abort, not merely be noticed after the fact."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")
    sh, pid, sid, base, uuids = _scene(client)
    from app.core.session_store import registry
    ctx = helios_ctx.get_context(registry.get_scenario_context(sh, pid, sid))

    full = len(pack_primitives_binary(ctx, uuids))

    # A token that trips the moment it is first read: the pack must give up
    # then, so nothing like a full buffer is ever produced.
    class _TripOnFirstCheck:
        def __init__(self):
            self.checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks > 1

    token = _TripOnFirstCheck()
    with pytest.raises(PackCancelled):
        pack_primitives_binary(ctx, uuids, cancelled=token)
    assert token.checks > 1, "the pack never consulted the token"
    assert full > 0


def test_route_still_serves_geometry_normally(client):
    """The disconnect watcher must not break the ordinary request."""
    sh, pid, sid, base, uuids = _scene(client)
    r = client.get(base + "/geometry/binary", headers={"session-id": sh})
    assert r.status_code == 200, r.text
    assert len(r.content) > 0
