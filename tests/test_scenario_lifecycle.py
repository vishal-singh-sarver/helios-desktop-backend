"""Explicit scenario-context lifecycle: SSE init (create + hydrate) and discard
(autosave + release). Without these the context is created implicitly by
whichever request lands first, and is never freed until the scenario is deleted.
"""
import json
from uuid import uuid4

from app.core.session_store import registry

GROUND = {"length": 10, "breadth": 10, "resolution_x": 2, "resolution_y": 2,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}


def _setup(client):
    sid_hdr = f"session_{uuid4().hex[:8]}"
    r = client.post("/api/project/create", json={
        "name": f"Life_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sid_hdr})
    assert r.status_code == 201, r.text
    d = r.json()
    return sid_hdr, d["project_id"], d["main_scenario_id"]


def _events(resp):
    """Parse an SSE body into a list of dicts."""
    return [json.loads(line[6:]) for line in resp.text.splitlines()
            if line.startswith("data: ")]


def test_init_streams_progress_and_ends_with_done(client):
    session_id, pid, sid = _setup(client)
    r = client.get(f"/api/project/{pid}/scenarios/{sid}/init?session_id={session_id}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _events(r)
    assert events, r.text
    assert [e["stage"] for e in events][-1] == "done"
    assert events[-1]["progress"] == 1.0
    # Progress is monotonic and never exceeds 1.
    progress = [e["progress"] for e in events]
    assert progress == sorted(progress) and progress[-1] <= 1.0
    # done means hydrated — every other scenario endpoint is now safe.
    sctx = registry.get_scenario_context(session_id, pid, sid)
    assert sctx is not None and sctx.hydrated is True


def test_init_requires_session_id_and_rejects_unknown_scenario(client):
    session_id, pid, sid = _setup(client)
    assert client.get(f"/api/project/{pid}/scenarios/{sid}/init").status_code == 422
    assert client.get(
        f"/api/project/{pid}/scenarios/{sid}/init?session_id=%20").status_code == 400

    # A bad scenario is reported as an SSE error event, not a crash — the
    # stream must always terminate so the client is never left hanging.
    r = client.get(f"/api/project/{pid}/scenarios/nope/init?session_id={session_id}")
    assert r.status_code == 200
    assert "error" in _events(r)[-1]


def test_init_is_idempotent(client):
    session_id, pid, sid = _setup(client)
    url = f"/api/project/{pid}/scenarios/{sid}/init?session_id={session_id}"
    assert _events(client.get(url))[-1]["stage"] == "done"
    assert _events(client.get(url))[-1]["stage"] == "done"   # already hydrated


def test_discard_saves_and_releases_then_reinit_restores(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    obj = client.post(base + "/objects", json={"object_type_id": ot,
                      "properties": GROUND}, headers=h).json()["object"]

    assert registry.get_scenario_context(session_id, pid, sid) is not None

    r = client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["discarded"] is True
    # The context is gone from memory — this is the release the registry
    # otherwise never performs outside scenario/project delete.
    assert registry.get_scenario_context(session_id, pid, sid) is None

    # The geometry survives, because discard autosaved before releasing.
    again = client.get(base + f"/objects/{obj['id']}", headers=h)
    assert again.status_code == 200, again.text
    assert again.json()["object"]["name"] == obj["name"]


def test_discard_is_idempotent(client):
    session_id, pid, sid = _setup(client)
    h = {"session-id": session_id}
    url = f"/api/project/{pid}/scenarios/{sid}/discard"
    client.get(f"/api/project/{pid}/scenarios/{sid}/init?session_id={session_id}")
    assert client.post(url, headers=h).json()["discarded"] is True
    second = client.post(url, headers=h).json()          # nothing live now
    assert second["success"] is True and second["discarded"] is False
