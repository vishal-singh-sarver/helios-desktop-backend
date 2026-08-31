"""POST /discard?save=false releases a scenario WITHOUT writing context.xml.

This is the cancel path the client was blocked on. Releasing a load the user
walked away from means releasing a HALF-HYDRATED context, and the only release
available always saved first — which would overwrite the scenario's real
context.xml and rotate the good copy into archives. Cancelling a load would
have corrupted the saved scene, so the client (correctly) never called it, and
a cancelled load's memory stayed resident until the scenario was deleted.

Nothing is lost by skipping the write: geometry lives in the DB, so a
half-hydrated context holds nothing the DB does not already have.
"""
import time
from uuid import uuid4

from app.core.session_store import registry
from app.helios import persistence
from app.helios.persistence import _scenario_context_xml

GROUND = {"length": 10, "breadth": 10, "resolution_x": 60, "resolution_y": 60,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}


def _project(client):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"Ds_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
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


def test_save_false_releases_without_touching_context_xml(client):
    sh, pid, sid, h, base = _project(client)
    xml = _scenario_context_xml(pid, sid)
    before_bytes = xml.read_bytes()
    before_mtime = xml.stat().st_mtime_ns

    r = client.post(f"/api/project/{pid}/scenarios/{sid}/discard?save=false",
                    headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["discarded"] is True
    assert body["saved"] is False, "save=false still wrote the context"

    # Released from memory...
    assert registry.get_scenario_context(sh, pid, sid) is None

    # ...and the file on disk is byte-for-byte untouched.
    assert xml.stat().st_mtime_ns == before_mtime, "context.xml was rewritten"
    assert xml.read_bytes() == before_bytes, "context.xml contents changed"


def _dirty(session_id, pid, sid):
    """Mark the live scenario as changed since its last save.

    Done on the counter rather than through a real mutation because a mutation
    queues a save that may drain before discard runs, putting the scene back to
    clean and quietly testing nothing.
    """
    from app.core.session_store import registry
    sctx = registry.get_scenario_context(session_id, pid, sid)
    assert sctx is not None, "no live scenario to dirty"
    sctx.mutation_seq += 1
    return sctx


def test_default_saves_when_there_is_something_to_save(client):
    """The default persists a change that has not reached disk.

    `_project` drains the queue, so the scene it hands back is already on disk
    and discard is right to skip it — see the companion test below. Dirtying it
    here is what exercises the write path.
    """
    sh, pid, sid, h, base = _project(client)
    xml = _scenario_context_xml(pid, sid)
    before_mtime = xml.stat().st_mtime_ns
    time.sleep(0.01)                      # so a rewrite is detectable
    _dirty(sh, pid, sid)

    r = client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["saved"] is True, "the default stopped saving"
    assert xml.stat().st_mtime_ns != before_mtime, "the default did not write"


def test_default_skips_the_write_when_the_file_already_matches(client):
    """The optimisation. Every mutation already queues a save, so on the way
    back to the project list the file is usually current — and re-serialising
    it cost ~16s on a high-resolution ground for byte-identical output.

    The scene must still be intact afterwards; skipping a write is only safe
    because the file was already right."""
    sh, pid, sid, h, base = _project(client)
    xml = _scenario_context_xml(pid, sid)
    before = xml.read_bytes()

    r = client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["saved"] is False, "re-wrote a scene already on disk"
    assert r.json()["discarded"] is True
    assert xml.read_bytes() == before, "the scene on disk changed"


def test_save_false_does_not_rotate_the_good_snapshot(client):
    """The corruption the client was avoiding: a cancel must not push the good
    context.xml into archives and replace it with a half-built one."""
    sh, pid, sid, h, base = _project(client)
    from app.helios.persistence import _scenario_archives_dir
    archives = _scenario_archives_dir(pid, sid)
    before = sorted(p.name for p in archives.glob("autosave_*.xml.gz")) \
        if archives.exists() else []

    client.post(f"/api/project/{pid}/scenarios/{sid}/discard?save=false", headers=h)

    after = sorted(p.name for p in archives.glob("autosave_*.xml.gz")) \
        if archives.exists() else []
    assert after == before, (
        f"save=false rotated the snapshot: {before} -> {after}")


def test_geometry_survives_a_save_false_release(client):
    """Releasing without saving loses nothing — the DB is the source of truth."""
    sh, pid, sid, h, base = _project(client)
    before = client.get(base + "/objects", headers=h).json()["objects"]
    assert len(before) == 1

    client.post(f"/api/project/{pid}/scenarios/{sid}/discard?save=false", headers=h)
    after = client.get(base + "/objects", headers=h).json()["objects"]
    assert len(after) == len(before)
    assert after[0]["name"] == before[0]["name"]
