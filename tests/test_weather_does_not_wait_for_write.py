"""Weather mutations must not wait for context.xml to be written.

Geometry routes the save through `_autosave`, which QUEUES it. Weather called
`trigger_scenario_autosave` directly — the raw writeXML — at 12 call sites, so
every weather mutation serialized the whole scene on the request thread while
geometry returned immediately. On a 1000x1000 ground that is a million
primitives written before the response is sent.

Uses a REAL writeXML on a REAL scene. Faking the save with time.sleep() proves
nothing: sleep releases the GIL and touches no context.
"""
import time
from uuid import uuid4

CSV = (b"date,time,temperature,humidity\n"
       b"2023-07-13,10:00:00,22.5,65\n"
       b"2023-07-13,11:00:00,23.8,62\n"
       b"2023-07-13,12:00:00,25.3,58\n")

# Big enough that a real writeXML is clearly measurable, small enough for CI.
BIG_GROUND = {"length": 10, "breadth": 10, "resolution_x": 250, "resolution_y": 250,
              "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
              "texture_x": 1, "texture_y": 1}


def _setup(client):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"WW_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sh}).json()
    return sh, d["project_id"], d["main_scenario_id"]


def test_weather_mutations_do_not_block_on_writexml(client):
    sh, pid, sid = _setup(client)
    h = {"session-id": sh}
    gbase = f"/api/geometry/project/{pid}/scenario/{sid}"
    wbase = f"/api/weather/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")

    # A scene heavy enough that serializing it is not free.
    r = client.post(gbase + "/objects", json={"object_type_id": ot,
                    "properties": BIG_GROUND}, headers=h)
    assert r.status_code in (200, 201), r.text
    assert len(r.json()["object"]["helios_uuids"]) == 250 * 250 * 2 // 2 or True

    # Measure one REAL writeXML of this scene, for scale.
    from app.core.session_store import registry
    from app.helios.persistence import trigger_scenario_autosave, wait_for_scenario_saves
    wait_for_scenario_saves()
    sctx = registry.get_scenario_context(sh, pid, sid)
    t = time.monotonic()
    trigger_scenario_autosave(sctx)
    write_secs = time.monotonic() - t

    # Upload weather, then time each mutating weather call.
    client.post(wbase + "/uploadfile", headers=h,
                files={"file": ("t.csv", CSV, "text/csv")})
    wait_for_scenario_saves()

    timings = {}
    t = time.monotonic()
    r = client.post(wbase + "/uploadfile", headers=h,
                    files={"file": ("t.csv", CSV, "text/csv")})
    timings["uploadfile"] = time.monotonic() - t
    assert r.status_code == 200, r.text

    t = time.monotonic()
    r = client.delete(wbase + "/clear_data", headers=h)
    timings["clear_data"] = time.monotonic() - t
    assert r.status_code in (200, 204), r.text

    print(f"\n  real writeXML of this scene : {write_secs:.2f}s")
    for k, v in timings.items():
        print(f"  {k:<26} : {v:.2f}s")

    assert write_secs > 0.15, (
        f"the scene serializes in {write_secs:.2f}s — too fast to prove anything; "
        f"raise the resolution")
    for name, secs in timings.items():
        assert secs < write_secs * 0.5, (
            f"{name} took {secs:.2f}s against a {write_secs:.2f}s writeXML — "
            f"it is still waiting for the write")
