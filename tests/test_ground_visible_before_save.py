"""A new ground must be drawable before its context.xml write finishes.

Deferring the save fixed the POST latency but not what the user sees: the
geometry read took the same exclusive lock as writeXML, so the ground did not
appear until the write was done. The wait moved from the response to the
render. Neither operation mutates the context, so they take a shared lock and
now overlap; mutations still exclude both.
"""
import time
from uuid import uuid4

from app.helios import persistence
from app.services import scene_object_service as sos

GROUND = {"length": 10, "breadth": 10, "resolution_x": 2, "resolution_y": 2,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}
SAVE_SECONDS = 2.0


def _setup(client):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={"name": f"Vis_{uuid4().hex[:6]}",
        "latitude": 28.6, "longitude": 77.2}, headers={"session-id": sh}).json()
    return sh, d["project_id"], d["main_scenario_id"]


def test_ground_is_drawable_while_the_save_is_still_running(client, monkeypatch):
    sh, pid, sid = _setup(client)
    h = {"session-id": sh}
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")

    real = persistence.trigger_scenario_autosave
    started, finished = [], []

    def _slow(sctx):
        started.append(time.monotonic())
        time.sleep(SAVE_SECONDS)
        finished.append(time.monotonic())
        return real(sctx)

    monkeypatch.setattr(persistence, "trigger_scenario_autosave", _slow)

    t = time.monotonic()
    r = client.post(base + "/objects", json={"object_type_id": ot,
                    "properties": GROUND}, headers=h)
    create = time.monotonic() - t
    assert r.status_code in (200, 201), r.text

    t = time.monotonic()
    b = client.get(base + "/geometry/binary", headers=h)
    draw = time.monotonic() - t

    assert b.status_code == 200 and len(b.content) > 0
    assert started, "the save never ran — the measurement is meaningless"
    # The read completed while the write was still in flight.
    assert not finished, "the save finished before the geometry read even started"
    assert create < SAVE_SECONDS * 0.5, f"POST waited on the save: {create:.2f}s"
    assert draw < SAVE_SECONDS * 0.5, (
        f"geometry/binary waited {draw:.2f}s behind a {SAVE_SECONDS}s save — "
        f"the ground still cannot be drawn until the write finishes")


def test_a_mutation_still_excludes_a_running_save(client, monkeypatch):
    """The shared lock must not let a build overlap a writeXML."""
    sh, pid, sid = _setup(client)
    h = {"session-id": sh}
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    client.post(base + "/objects", json={"object_type_id": ot,
                "properties": GROUND}, headers=h)

    real = persistence.trigger_scenario_autosave
    overlap = []
    saving = threading_flag = {"in": False}

    def _slow(sctx):
        saving["in"] = True
        time.sleep(SAVE_SECONDS)
        saving["in"] = False
        return real(sctx)

    real_build = sos._build

    def _watch_build(*a, **kw):
        if saving["in"]:
            overlap.append(True)
        return real_build(*a, **kw)

    monkeypatch.setattr(persistence, "trigger_scenario_autosave", _slow)
    monkeypatch.setattr(sos, "_build", _watch_build)

    # A second ground: its build must wait for the in-flight save.
    r = client.post(base + "/objects", json={"object_type_id": ot,
                    "properties": {**GROUND, "position_x": 30}}, headers=h)
    assert r.status_code in (200, 201), r.text
    assert not overlap, "a _build ran while writeXML was serializing the context"
