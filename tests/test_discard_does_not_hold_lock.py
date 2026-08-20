"""discard must not hold the global lock across its write.

The save itself is correct and stays SYNCHRONOUS — discard is the one path
where the context is about to be released, so a queued write could serialise a
context that is already gone. But it was being done while still holding the
GLOBAL write lock, so tearing one scenario down stalled every other scenario in
the process, other projects included, for the length of the write.
"""
import threading
import time
from uuid import uuid4

from app.helios import persistence

GROUND = {"length": 10, "breadth": 10, "resolution_x": 120, "resolution_y": 120,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}
SAVE = 1.5


def _project(client, res=120):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"Mx_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sh}).json()
    pid, sid = d["project_id"], d["main_scenario_id"]
    h = {"session-id": sh}
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    client.post(base + "/objects", json={"object_type_id": ot,
                "properties": {**GROUND, "resolution_x": res,
                               "resolution_y": res}}, headers=h)
    persistence.wait_for_scenario_saves()
    return sh, pid, sid, h, base, ot


def _slow_save(monkeypatch):
    real = persistence.trigger_scenario_autosave

    def _slow(sctx):
        time.sleep(SAVE)
        return real(sctx)

    monkeypatch.setattr(persistence, "trigger_scenario_autosave", _slow)


def test_discard_does_not_hold_the_lock_across_its_write(client, monkeypatch):
    """The probe must be something that ACTUALLY takes the scenario lock —
    a geometry write on a DIFFERENT scenario does; a catalog read does not."""
    sh_a, pid_a, sid_a, h_a, base_a, ot = _project(client)
    sh_b, pid_b, sid_b, h_b, base_b, _ = _project(client, res=2)

    _slow_save(monkeypatch)

    timings = {}

    def _discard():
        t = time.monotonic()
        client.post(f"/api/project/{pid_a}/scenarios/{sid_a}/discard", headers=h_a)
        timings["discard"] = time.monotonic() - t

    d = threading.Thread(target=_discard)
    d.start()
    time.sleep(0.3)                    # let the teardown write get under way

    # Scenario B is unrelated. Creating geometry there takes the write lock.
    t = time.monotonic()
    r = client.post(base_b + "/objects", json={
        "object_type_id": ot,
        "properties": {**GROUND, "resolution_x": 2, "resolution_y": 2,
                       "position_x": 40}}, headers=h_b)
    other_scenario = time.monotonic() - t

    d.join(timeout=30)

    assert r.status_code in (200, 201), r.text
    assert timings["discard"] >= SAVE, f"the slow save never ran: {timings}"
    assert other_scenario < SAVE * 0.5, (
        f"an unrelated scenario waited {other_scenario:.2f}s behind another "
        f"scenario's {SAVE}s discard write — the lock is held across it")
