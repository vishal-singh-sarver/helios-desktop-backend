"""Init stops hydrating when the client goes away.

Loading a scenario is three phases with different cancellability: Context() +
loadXML is ONE C++ call and cannot be interrupted, but the hydration that
follows rebuilds objects one at a time in Python and can stop between them.
SSE is the one route shape where the server actually learns the browser left —
the generator gets a real CancelledError — so /init can act on it.

Bailing mid-hydration is safe and resumable: objects already built stay in
persisted_objects and are skipped next time, and `hydrated` stays False so a
later init finishes the job.
"""
import threading
from uuid import uuid4

from app.core.session_store import registry
from app.helios.persistence import _scenario_context_xml
from app.services import scene_object_service as sos
from app.services import scenario_service

GROUND = {"length": 10, "breadth": 10, "resolution_x": 2, "resolution_y": 2,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}


def _setup(client, n_objects):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"Cx_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sh}).json()
    pid, sid = d["project_id"], d["main_scenario_id"]
    h = {"session-id": sh}
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    for i in range(n_objects):
        client.post(base + "/objects", json={"object_type_id": ot,
                    "properties": {**GROUND, "position_x": i * 30}}, headers=h)
    client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)
    # Drop the snapshot so hydration must REBUILD each object one at a time.
    _scenario_context_xml(pid, sid).unlink(missing_ok=True)
    return sh, pid, sid


def test_hydration_stops_between_objects_when_cancelled(client):
    sh, pid, sid = _setup(client, n_objects=5)

    from app.db.database import SessionLocal
    db = SessionLocal()
    sctx = sos._sctx(sh, pid, sid)

    cancelled = threading.Event()
    built = []
    real_build = sos._build

    def _watch(db_, sctx_, so, **kw):
        built.append(so.id)
        if len(built) == 2:        # client leaves after the 2nd object
            cancelled.set()
        return real_build(db_, sctx_, so, **kw)

    sos._build = _watch
    try:
        sos.ensure_hydrated(db, sctx, sid, cancelled)
    finally:
        sos._build = real_build

    assert len(built) < 5, f"hydration ignored the cancel and built all {len(built)}"
    assert sctx.hydrated is False, "cancelled hydration must not mark itself done"
    db.close()


def test_a_cancelled_load_resumes_on_the_next_init(client):
    """Whatever the cancelled pass built is kept; the rest is finished later."""
    sh, pid, sid = _setup(client, n_objects=5)

    from app.db.database import SessionLocal
    db = SessionLocal()
    sctx = sos._sctx(sh, pid, sid)

    cancelled = threading.Event()
    built = []
    real_build = sos._build

    def _watch(db_, sctx_, so, **kw):
        built.append(so.id)
        if len(built) == 2:
            cancelled.set()
        return real_build(db_, sctx_, so, **kw)

    sos._build = _watch
    try:
        sos.ensure_hydrated(db, sctx, sid, cancelled)
    finally:
        sos._build = real_build

    partial = len(sctx.persisted_objects)
    assert 0 < partial < 5, f"expected a partial load, got {partial}"

    # Second init, no cancellation — must finish the job.
    sos.ensure_hydrated(db, sctx, sid, None)
    assert sctx.hydrated is True
    assert len(sctx.persisted_objects) == 5, (
        f"resume left {len(sctx.persisted_objects)}/5 objects live")
    db.close()


def test_uncancelled_init_still_completes(client):
    """The token is optional — nothing changes when nobody cancels."""
    sh, pid, sid = _setup(client, n_objects=3)
    events = client.get(
        f"/api/project/{pid}/scenarios/{sid}/init?session_id={sh}").text
    assert '"stage": "done"' in events, events[-400:]
    sctx = registry.get_scenario_context(sh, pid, sid)
    assert sctx.hydrated is True
    assert len(sctx.persisted_objects) == 3
