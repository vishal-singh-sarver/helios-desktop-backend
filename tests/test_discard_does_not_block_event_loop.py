"""/discard must not run its autosave on the event loop.

It is an `async def` route calling straight into blocking PyHelios work —
writeXML plus a gzip of the previous snapshot, ~18s on a 600x600 ground. On the
loop that freezes the whole backend, not just this request.

/init is the visible casualty. Its work runs in an executor thread and keeps
going, but the coroutine that DELIVERS its progress events (`await
asyncio.sleep(0.05)` polling a queue) is on the loop. Frozen, the browser gets
nothing; unfrozen, the whole queue drains in one pass ending in "Scenario
ready" — so init looks instant and the client believes a context that is still
being fetched is ready.
"""
import threading
import time
from uuid import uuid4

from app.helios import persistence
from app.services import scenario_service

GROUND = {"length": 10, "breadth": 10, "resolution_x": 2, "resolution_y": 2,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}

SAVE_SECONDS = 1.5


def _setup(client):
    sid_hdr = f"session_{uuid4().hex[:8]}"
    r = client.post("/api/project/create", json={
        "name": f"Block_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sid_hdr})
    d = r.json()
    return sid_hdr, d["project_id"], d["main_scenario_id"]


def test_a_slow_discard_does_not_stall_other_requests(client, monkeypatch):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    client.post(f"/api/geometry/project/{pid}/scenario/{sid}/objects",
                json={"object_type_id": ot, "properties": GROUND}, headers=h)

    real_save = persistence.trigger_scenario_autosave

    def _slow_save(sctx):
        time.sleep(SAVE_SECONDS)          # stand in for an 18s writeXML
        return real_save(sctx)

    monkeypatch.setattr(scenario_service, "trigger_scenario_autosave", _slow_save,
                        raising=False)
    monkeypatch.setattr(persistence, "trigger_scenario_autosave", _slow_save)

    # QUEUE the slow save rather than just marking the scene dirty. discard
    # drains the queue before deciding whether to write, so this guarantees the
    # slow save runs inside the request. Relying on the scene still being dirty
    # was ordering-dependent: the ground's own save usually lands first, leaving
    # it clean, and the test then passed on a 4ms discard that proved nothing.
    from app.core.session_store import registry
    live = registry.get_scenario_context(session_id, pid, sid)
    assert live is not None, "no live scenario to discard"
    persistence.queue_scenario_autosave(live)

    timings = {}

    def _discard():
        t = time.monotonic()
        resp = client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)
        timings["discard"] = time.monotonic() - t
        # Recorded so a fast discard cannot pass as "not slow" when it was
        # actually an error or a no-op release.
        timings["status"] = resp.status_code
        timings["body"] = resp.json()

    d = threading.Thread(target=_discard)
    d.start()
    time.sleep(0.3)                       # let the save get under way

    # An unrelated route that touches neither the context nor the lock. On the
    # event loop it cannot be served until the save finishes.
    t = time.monotonic()
    r = client.get("/api/catalog/object-types")
    timings["catalog"] = time.monotonic() - t

    d.join(timeout=30)

    assert r.status_code == 200
    assert timings["discard"] >= SAVE_SECONDS, (
        f"the slow save never ran: {timings}")
    assert timings["catalog"] < SAVE_SECONDS * 0.5, (
        f"the event loop was frozen by discard — an unrelated request waited "
        f"{timings['catalog']:.2f}s behind a {SAVE_SECONDS}s save: {timings}")
