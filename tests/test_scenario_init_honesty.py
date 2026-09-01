"""init reports "ready" only when the geometry is actually live.

`_hydrate` swallows per-object build failures on purpose so one bad row cannot
make a scenario unopenable. That left init emitting {"objects": 0, "message":
"Scenario ready"} over a context where nothing loaded, while the object list
still returned every row — so the client drew a geometry with no primitives.
"""
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.session_store import registry
from app.services import scene_object_service as sos

GROUND = {"length": 10, "breadth": 10, "resolution_x": 2, "resolution_y": 2,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}


def _setup(client):
    sid_hdr = f"session_{uuid4().hex[:8]}"
    r = client.post("/api/project/create", json={
        "name": f"Honest_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sid_hdr})
    assert r.status_code == 201, r.text
    d = r.json()
    return sid_hdr, d["project_id"], d["main_scenario_id"]


def _events(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines()
            if line.startswith("data: ")]


def _add_ground(client, session_id, pid, sid):
    h = {"session-id": session_id}
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    r = client.post(f"/api/geometry/project/{pid}/scenario/{sid}/objects",
                    json={"object_type_id": ot, "properties": GROUND}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["object"]


def test_init_on_scenario_with_geometry_reports_ready(client):
    """The guard must not false-positive: a scenario whose geometry loads fine
    still ends with done. This is the path every real reopen takes."""
    session_id, pid, sid = _setup(client)
    _add_ground(client, session_id, pid, sid)

    # Release the context so init has to genuinely rebuild/reload it.
    client.post(f"/api/project/{pid}/scenarios/{sid}/discard",
                headers={"session-id": session_id})
    assert registry.get_scenario_context(session_id, pid, sid) is None

    events = _events(client.get(
        f"/api/project/{pid}/scenarios/{sid}/init?session_id={session_id}"))
    last = events[-1]
    assert "error" not in last, f"false error on a healthy scenario: {last}"
    assert last["stage"] == "done"
    assert last["objects"] == 1, last


def test_init_reports_error_when_geometry_cannot_be_brought_live(client,
                                                                monkeypatch):
    """DB says 1 geometry, context has 0 -> error, not "Scenario ready"."""
    session_id, pid, sid = _setup(client)
    _add_ground(client, session_id, pid, sid)
    client.post(f"/api/project/{pid}/scenarios/{sid}/discard",
                headers={"session-id": session_id})

    # Make the rebuild during hydration fail the way a real build failure does.
    def _boom(*a, **kw):
        raise HTTPException(422, "BUILD_FAILED")
    monkeypatch.setattr(sos, "_build", _boom)
    # Nothing survives in the XML either, so hydration has to rebuild.
    monkeypatch.setattr(sos.helios_ctx, "get_context",
                        lambda sctx: _EmptyCtx())

    events = _events(client.get(
        f"/api/project/{pid}/scenarios/{sid}/init?session_id={session_id}"))
    last = events[-1]
    assert "error" in last, f"init claimed ready over an empty context: {last}"
    assert last["objects"] == 0 and last["expected"] == 1
    assert last["stage"] != "done" if "stage" in last else True


class _EmptyCtx:
    """A context that loaded nothing — forces hydration down the rebuild path."""
    def getAllObjectIDs(self):
        return []


def test_object_list_and_init_agree(client):
    """The bug's real signature: the object list returned rows that init had
    already declared ready, with nothing behind them. They must not disagree."""
    session_id, pid, sid = _setup(client)
    _add_ground(client, session_id, pid, sid)
    client.post(f"/api/project/{pid}/scenarios/{sid}/discard",
                headers={"session-id": session_id})

    last = _events(client.get(
        f"/api/project/{pid}/scenarios/{sid}/init?session_id={session_id}"))[-1]
    listed = client.get(f"/api/geometry/project/{pid}/scenario/{sid}/objects",
                        headers={"session-id": session_id}).json()["objects"]

    if "error" in last:
        pytest.fail(f"init errored on a healthy scenario: {last}")
    assert last["objects"] == len(listed), (
        f"init said {last['objects']} live, list returned {len(listed)}")
