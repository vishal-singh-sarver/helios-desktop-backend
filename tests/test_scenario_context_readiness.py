"""A context is handed out only once its snapshot has finished loading.

`_sctx` skips the lock for an already-live scenario — that is what keeps reads
off the back of a background autosave. The flag it tests has to mean "loadXML
finished", not "a Context object exists": `sctx.context` is non-None from the
moment the empty Context is allocated, a whole loadXML before it holds any
geometry. Gating on that let a request arriving mid-`init` skip the lock and
read an empty scene — 200 with no primitives, viewer stuck on "Loading scene".
"""
import threading
import time
from uuid import uuid4

from app.core.session_store import registry
from app.services import scene_object_service as sos

GROUND = {"length": 10, "breadth": 10, "resolution_x": 2, "resolution_y": 2,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}


def _setup(client):
    sid_hdr = f"session_{uuid4().hex[:8]}"
    r = client.post("/api/project/create", json={
        "name": f"Ready_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sid_hdr})
    assert r.status_code == 201, r.text
    d = r.json()
    return sid_hdr, d["project_id"], d["main_scenario_id"]


def test_concurrent_caller_waits_for_the_snapshot_to_finish(client, monkeypatch):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    client.post(f"/api/geometry/project/{pid}/scenario/{sid}/objects",
                json={"object_type_id": ot, "properties": GROUND}, headers=h)

    # Release it so the next _sctx has to create + load from scratch.
    client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)
    assert registry.get_scenario_context(session_id, pid, sid) is None

    real_load = sos.load_scenario_snapshot
    load_done = threading.Event()

    def _slow_load(sctx):
        time.sleep(0.5)          # stand in for an 18s loadXML
        real_load(sctx)
        load_done.set()

    monkeypatch.setattr(sos, "load_scenario_snapshot", _slow_load)

    observed = {}

    def _first():
        sos._sctx(session_id, pid, sid)          # creates + loads

    def _second():
        time.sleep(0.15)                          # lands mid-load
        sctx = sos._sctx(session_id, pid, sid)
        # What the racing request actually saw at the moment it was served.
        observed["load_finished"] = load_done.is_set()
        observed["initialized"] = sctx.initialized

    a = threading.Thread(target=_first)
    b = threading.Thread(target=_second)
    a.start(); b.start()
    a.join(timeout=10); b.join(timeout=10)

    assert observed, "the racing thread never completed"
    assert observed["load_finished"], (
        "_sctx handed out a context while its snapshot was still loading — "
        "a read served here sees an empty scene")
    assert observed["initialized"] is True


def test_initialized_is_false_until_the_snapshot_lands(client, monkeypatch):
    """The flag itself: it must not go true alongside the Context allocation."""
    session_id, pid, sid = _setup(client)
    client.post(f"/api/project/{pid}/scenarios/{sid}/discard",
                headers={"session-id": session_id})

    seen = {}
    real_load = sos.load_scenario_snapshot

    def _probe(sctx):
        # Inside the window: Context exists, snapshot has not been read yet.
        seen["context_allocated"] = sctx.context is not None
        seen["initialized_during_load"] = sctx.initialized
        real_load(sctx)

    monkeypatch.setattr(sos, "load_scenario_snapshot", _probe)
    sctx = sos._sctx(session_id, pid, sid)

    assert seen["context_allocated"] is True
    assert seen["initialized_during_load"] is False, (
        "initialized went true before the snapshot was read")
    assert sctx.initialized is True
