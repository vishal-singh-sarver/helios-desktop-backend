"""A cancelled open must not leave its context resident.

`loadXML` cannot be interrupted, so cancelling an open still pays for the whole
read. What it must not do is KEEP the result: the registry holds contexts until
something explicitly removes them, and nothing did. Opening a second project
therefore loaded on top of the first — two 1000x1000 contexts is ~21 GB, and
the kernel SIGKILLed the server (observed twice, anon-rss 21.5 GB).

Because `_resolve_scenario` takes the write lock, the next scenario's load is
already queued behind this one. Releasing the abandoned context here is what
keeps the peak at ONE context rather than two.
"""
import threading
from uuid import uuid4

from app.core.session_store import registry
from app.helios.persistence import _scenario_context_xml
from app.services import scenario_service

GROUND = {"length": 10, "breadth": 10, "resolution_x": 2, "resolution_y": 2,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}


def _setup(client, n=3):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"Ab_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sh}).json()
    pid, sid = d["project_id"], d["main_scenario_id"]
    h = {"session-id": sh}
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    for i in range(n):
        client.post(base + "/objects", json={"object_type_id": ot,
                    "properties": {**GROUND, "position_x": i * 30}}, headers=h)
    client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)
    _scenario_context_xml(pid, sid).unlink(missing_ok=True)
    return sh, pid, sid


def _events(emitted):
    return emitted[-1] if emitted else {}


def test_cancelled_open_releases_its_context(client):
    sh, pid, sid = _setup(client)
    from app.db.database import SessionLocal
    db = SessionLocal()

    cancelled = threading.Event()
    cancelled.set()                       # client already gone
    emitted = []
    scenario_service.init_scenario(sh, pid, sid, db, emitted.append, cancelled)

    assert "error" in _events(emitted), emitted
    assert registry.get_scenario_context(sh, pid, sid) is None, (
        "an abandoned context stayed resident — this is what OOM-killed the "
        "server when a second project was opened")
    db.close()


def test_cancelled_open_does_not_evict_a_live_context(client):
    """A fully loaded scenario is shared. A cancelled init must not drop it."""
    sh, pid, sid = _setup(client)
    from app.db.database import SessionLocal
    db = SessionLocal()

    # First open completes normally.
    emitted = []
    scenario_service.init_scenario(sh, pid, sid, db, emitted.append, None)
    assert _events(emitted).get("stage") == "done", emitted
    live = registry.get_scenario_context(sh, pid, sid)
    assert live is not None and live.hydrated

    # A second, cancelled open must leave the live one alone.
    cancelled = threading.Event()
    cancelled.set()
    emitted2 = []
    scenario_service.init_scenario(sh, pid, sid, db, emitted2.append, cancelled)

    still = registry.get_scenario_context(sh, pid, sid)
    assert still is live, "a cancelled init evicted a context that was in use"
    assert still.hydrated
    db.close()


def test_uncancelled_open_keeps_its_context(client):
    """The release path must not fire on the ordinary open."""
    sh, pid, sid = _setup(client)
    r = client.get(f"/api/project/{pid}/scenarios/{sid}/init?session_id={sh}")
    assert '"stage": "done"' in r.text, r.text[-300:]
    sctx = registry.get_scenario_context(sh, pid, sid)
    assert sctx is not None and sctx.hydrated
    assert len(sctx.persisted_objects) == 3
