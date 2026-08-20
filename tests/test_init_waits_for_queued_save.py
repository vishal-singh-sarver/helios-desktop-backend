"""init must not report ready while a context.xml save is still queued.

Saves are deferred to a single-worker pool, so `_build` returns before its
writeXML runs. Hydration builds, init sees it return and announces "Scenario
ready" — then the queued save runs and the client's very next call lands while
the write is in flight. The wait did not go away; it moved off init, which
reports progress, onto the request after it, which does not.
"""
import json
import time
from uuid import uuid4

from app.helios.persistence import _scenario_context_xml
from app.helios import persistence
from app.services import scene_object_service as sos

GROUND = {"length": 10, "breadth": 10, "resolution_x": 2, "resolution_y": 2,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}

SAVE_SECONDS = 1.5


def _setup(client):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"Queue_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sh}).json()
    return sh, d["project_id"], d["main_scenario_id"]


def _seed(client, sh, pid, sid, count=1):
    h = {"session-id": sh}
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    for i in range(count):
        r = client.post(base + "/objects", json={"object_type_id": ot,
                        "properties": {**GROUND, "position_x": i * 30}}, headers=h)
        assert r.status_code in (200, 201), r.text
    client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)
    # Drop the snapshot so hydration must REBUILD from the DB — the path that
    # queues a save.
    _scenario_context_xml(pid, sid).unlink(missing_ok=True)
    return base


def test_init_does_not_report_ready_while_a_save_is_queued(client, monkeypatch):
    sh, pid, sid = _setup(client)
    _seed(client, sh, pid, sid)

    real_save = persistence.trigger_scenario_autosave

    def _slow_save(sctx):
        time.sleep(SAVE_SECONDS)          # stand in for an 18s writeXML
        return real_save(sctx)

    monkeypatch.setattr(persistence, "trigger_scenario_autosave", _slow_save)

    t = time.monotonic()
    resp = client.get(
        f"/api/project/{pid}/scenarios/{sid}/init?session_id={sh}")
    init_secs = time.monotonic() - t

    events = [json.loads(l[6:]) for l in resp.text.splitlines()
              if l.startswith("data: ")]
    assert "error" not in events[-1], events[-1]
    assert events[-1]["stage"] == "done", events[-1]
    assert init_secs >= SAVE_SECONDS, (
        f"init returned in {init_secs:.2f}s without waiting for the "
        f"{SAVE_SECONDS}s save — it is reporting ready too early")


def test_hydration_queues_one_save_not_one_per_object(client, monkeypatch):
    """Each _build queues a save of the WHOLE scene; hydration calls it per row."""
    sh, pid, sid = _setup(client)
    _seed(client, sh, pid, sid, count=3)

    calls = []
    real_save = persistence.trigger_scenario_autosave

    def _counting_save(sctx):
        calls.append(sctx.scenario_id)
        return real_save(sctx)

    monkeypatch.setattr(persistence, "trigger_scenario_autosave", _counting_save)
    client.get(f"/api/project/{pid}/scenarios/{sid}/init?session_id={sh}")

    assert len(calls) == 1, (
        f"hydration of 3 objects serialized the whole scene {len(calls)} times")
