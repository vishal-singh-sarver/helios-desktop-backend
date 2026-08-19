"""Weather requests must not queue behind a context.xml save.

Geometry reaches its context through `_sctx`, which returns an already-live
scenario without taking the lock. Weather reaches the SAME context through
`_resolve_scenario`, which took the write lock on every call — so while a save
held the read lock, every weather request (upload, header, time series) waited
for the whole writeXML. Same scenario, instant in the viewport, frozen in
Weather.
"""
import threading
import time
from uuid import uuid4

from app.services import scene_object_service as sos

GROUND = {"length": 10, "breadth": 10, "resolution_x": 2, "resolution_y": 2,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}
SAVE_SECONDS = 2.0


def test_weather_request_is_served_while_a_save_runs(client, monkeypatch):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"W_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sh}).json()
    pid, sid = d["project_id"], d["main_scenario_id"]
    h = {"session-id": sh}
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")

    real = sos.trigger_scenario_autosave
    running = {"in": False}

    def _slow(sctx):
        running["in"] = True
        time.sleep(SAVE_SECONDS)
        running["in"] = False
        return real(sctx)

    monkeypatch.setattr(sos, "trigger_scenario_autosave", _slow)

    # Create a ground → queues a slow save.
    r = client.post(base + "/objects", json={"object_type_id": ot,
                    "properties": GROUND}, headers=h)
    assert r.status_code in (200, 201), r.text

    # Give the queued save a moment to pick up the lock.
    for _ in range(40):
        if running["in"]:
            break
        time.sleep(0.02)
    assert running["in"], "the save never started — the measurement is meaningless"

    t = time.monotonic()
    w = client.get(f"/api/weather/project/{pid}/scenario/{sid}/weather_data_header",
                   headers=h)
    weather_secs = time.monotonic() - t

    assert w.status_code in (200, 404), w.text     # 404 = no weather uploaded yet
    assert weather_secs < SAVE_SECONDS * 0.5, (
        f"a weather request waited {weather_secs:.2f}s behind a {SAVE_SECONDS}s "
        f"context.xml save")
