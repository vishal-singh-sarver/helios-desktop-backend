"""EVERY mutating weather endpoint returns before its context.xml is written.

Spot-checking two endpoints proved nothing about the other ten. This exercises
each one in turn against a REAL slow writeXML and asserts none of them pays for
the write on the request thread — the same guarantee geometry already has.

Uses a genuinely slow save, not a stand-in for one: an earlier version of this
suite faked it with time.sleep() on a binding weather never imports, and passed
against code that blocked for three seconds.
"""
import time
from uuid import uuid4

import pytest

from app.helios import persistence

CSV = (b"date,time,temperature,humidity\n"
       b"2023-07-13,10:00:00,22.5,65\n"
       b"2023-07-13,11:00:00,23.8,62\n"
       b"2023-07-13,12:00:00,25.3,58\n")

GROUND = {"length": 10, "breadth": 10, "resolution_x": 300, "resolution_y": 300,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}

# No stand-in for the write: the real one is measured at runtime and every
# endpoint is compared against it. Monkeypatching persistence.trigger_scenario_
# autosave does NOT reach these modules — they bind the name at import — which
# is exactly how an earlier version of this test passed against blocking code.
GROUND_RES = 300    # big enough that a real writeXML is clearly measurable


@pytest.fixture
def scenario(client):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"AW_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sh}).json()
    pid, sid = d["project_id"], d["main_scenario_id"]
    h = {"session-id": sh}
    gbase = f"/api/geometry/project/{pid}/scenario/{sid}"
    wbase = f"/api/weather/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    # A scene big enough that a real writeXML is not free.
    client.post(gbase + "/objects", json={"object_type_id": ot,
                "properties": GROUND}, headers=h)
    # Seed weather so the edit endpoints have something to act on.
    client.post(wbase + "/uploadfile", headers=h,
                files={"file": ("t.csv", CSV, "text/csv")})
    persistence.wait_for_scenario_saves()
    return sh, pid, sid, h, wbase


def test_every_mutating_weather_endpoint_returns_before_the_write(
        scenario, client):
    sh, pid, sid, h, wbase = scenario

    # Time the REAL write for this scene. Every endpoint below must be a small
    # fraction of it; anything close to it paid for a writeXML inline.
    from app.core.session_store import registry
    persistence.wait_for_scenario_saves()
    sctx = registry.get_scenario_context(sh, pid, sid)
    t = time.monotonic()
    persistence.trigger_scenario_autosave(sctx)
    save_secs = time.monotonic() - t
    limit = max(save_secs * 0.25, 0.05)
    assert save_secs > 0.2, (
        f"this scene writes in {save_secs:.2f}s — too fast to prove anything; "
        f"raise GROUND_RES")

    def call(label, fn):
        t = time.monotonic()
        r = fn()
        dt = time.monotonic() - t
        return label, dt, getattr(r, "status_code", "?")

    ops = []
    ops.append(call("POST   /uploadfile", lambda: client.post(
        wbase + "/uploadfile", headers=h,
        files={"file": ("t.csv", CSV, "text/csv")})))

    # A REGISTERED column. CSV columns live in PyHelios but not in
    # weather_data_headers, and /addRow and /delete both key off the registered
    # set — using a CSV name gets a 404 before the save is ever reached.
    col = f"c_{uuid4().hex[:4]}"
    ops.append(call("POST   /addCol", lambda: client.post(
        wbase + "/addCol", headers=h,
        json={"column": [{"name": col, "values": []}]})))

    hdrs = client.get(wbase + "/weather_data_header", headers=h).json()
    ids = [str(x["id"]) for x in hdrs.get("headers", [])]
    assert ids, "no registered headers — /addRow below would prove nothing"

    ops.append(call("POST   /addRow", lambda: client.post(
        wbase + "/addRow", headers=h, json={"rows": [
            {"date": "2023-07-14", "time": "10:00:00",
             **{i: "1" for i in ids}}]})))
    ops.append(call("PATCH  /update", lambda: client.patch(
        wbase + "/update", headers=h, json={"updates": [
            {"col": "temperature",
             "row": {"date": "2023-07-13", "time": "10:00:00"},
             "value": "30"}]})))
    ops.append(call("POST   /deleteRow", lambda: client.post(
        wbase + "/deleteRow", headers=h,
        json=[{"date": "2023-07-13", "time": "11:00:00"}])))
    ops.append(call("POST   /delete", lambda: client.post(
        wbase + "/delete", headers=h,
        json={"column": {"columnname": col}})))
    ops.append(call("PUT    /weather_data_header", lambda: client.put(
        wbase + "/weather_data_header", headers=h, json={"headers": []})))
    ops.append(call("DELETE /weather_data_header", lambda: client.delete(
        wbase + "/weather_data_header", headers=h)))
    ops.append(call("DELETE /clear_data", lambda: client.delete(
        wbase + "/clear_data", headers=h)))

    print(f"\n  REAL writeXML of this scene: {save_secs:.2f}s   limit: {limit:.2f}s\n")
    blocked, failed = [], []
    for label, dt, code in ops:
        flag = ""
        if code not in (200, 201, 204):
            flag = "  <-- DID NOT REACH THE SAVE"
            failed.append((label, code))
        elif dt >= limit:
            flag = "  <-- WAITED ON THE WRITE"
            blocked.append((label, dt, code))
        print(f"  {label:<32} {dt:5.2f}s  ({code}){flag}")

    assert not failed, (
        "these never reached the save path, so they prove nothing:\n" +
        "\n".join(f"    {l}  ({c})" for l, c in failed))
    assert not blocked, (
        "these weather operations still block on context.xml:\n" +
        "\n".join(f"    {l}  {d:.2f}s  ({c})" for l, d, c in blocked))
