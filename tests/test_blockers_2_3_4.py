"""The three concurrency defects found reviewing the cancellation work.

2. `_abandon()` checked `hydrated` WITHOUT the lock. That flag is False for the
   whole of another request's hydration, so it was a race window, not a
   liveness test: the eviction landed under a running hydrate and the next
   lookup built a SECOND context — the very leak _abandon exists to prevent.

3. `discard_scenario` ran writeXML with NO lock. Removing the registry entry
   stops anyone new being handed the context, but not requests that already
   hold it, nor a queued save for the same sctx.

4. Weather mutates sctx.context and took no lock at all. Survivable while its
   save ran inline on the same thread; once the save moved to the queue, an
   unlocked mutation could rewrite the context while writeXML walked it.
   Measured with .read() held 1.5s: geometry PATCH waited 1.51s, weather
   clear_data went through in 0.01s.
"""
import threading
import time
from uuid import uuid4

import pytest

from app.core.session_store import registry
from app.helios import context as helios_ctx
from app.helios import persistence

GROUND = {"length": 10, "breadth": 10, "resolution_x": 4, "resolution_y": 4,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}
HELD = 1.2


def _project(client):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"Bk_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sh}).json()
    pid, sid = d["project_id"], d["main_scenario_id"]
    h = {"session-id": sh}
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    r = client.post(base + "/objects", json={"object_type_id": ot,
                    "properties": GROUND}, headers=h)
    assert r.status_code in (200, 201), r.text
    persistence.wait_for_scenario_saves()
    return sh, pid, sid, h, base


def test_blocker4_weather_mutation_is_excluded_by_a_running_save(client):
    """A weather mutation must WAIT while the context is being serialised."""
    sh, pid, sid, h, base = _project(client)
    wbase = f"/api/weather/project/{pid}/scenario/{sid}"

    started = threading.Event()

    def _hold_read():
        with registry._scenario_lock.read():
            started.set()
            time.sleep(HELD)

    t = threading.Thread(target=_hold_read)
    t.start()
    assert started.wait(5)
    time.sleep(0.05)

    t0 = time.monotonic()
    r = client.delete(wbase + "/clear_data", headers=h)
    elapsed = time.monotonic() - t0
    t.join(timeout=10)

    assert r.status_code in (200, 204), r.text
    assert elapsed >= HELD * 0.5, (
        f"weather clear_data went through in {elapsed:.2f}s while the context "
        f"was being serialised — it is not excluded, so a mutation can rewrite "
        f"the context mid-writeXML")


def test_blocker3_discard_holds_a_lock_while_it_writes(client):
    """discard's writeXML must exclude mutations, not run bare."""
    sh, pid, sid, h, base = _project(client)

    seen = {}
    real = persistence.trigger_scenario_autosave

    def _observe(sctx):
        # Inside the save: a mutation must not be able to take .write() now.
        got = registry._scenario_lock._owner is not None or registry._scenario_lock._readers > 0
        seen["guarded"] = got
        return real(sctx)

    import app.services.scenario_service as svc
    orig = svc.__dict__.get("trigger_scenario_autosave")
    persistence.trigger_scenario_autosave = _observe
    try:
        r = client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)
    finally:
        persistence.trigger_scenario_autosave = real

    assert r.status_code == 200, r.text
    assert seen.get("guarded") is True, (
        "discard ran writeXML with no lock held — a concurrent mutation could "
        "rewrite the context while it was being serialised")


def test_blocker2_abandon_does_not_evict_a_context_being_hydrated(client):
    """_abandon must not drop a context another request is hydrating."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")

    sh, pid, sid, h, base = _project(client)
    from app.db.database import SessionLocal
    from app.services import scene_object_service as sos
    from app.services import scenario_service

    client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)

    db = SessionLocal()
    sctx = sos._sctx(sh, pid, sid)
    sos.ensure_hydrated(db, sctx, sid)          # fully hydrated, in use
    assert sctx.hydrated is True
    db.close()

    # A cancelled init for the SAME scenario must leave it alone.
    cancelled = threading.Event()
    cancelled.set()
    db2 = SessionLocal()
    emitted = []
    scenario_service.init_scenario(sh, pid, sid, db2, emitted.append, cancelled)
    db2.close()

    still = registry.get_scenario_context(sh, pid, sid)
    assert still is sctx, (
        "a cancelled init evicted a hydrated context another request was using "
        "— the next lookup builds a second one")
