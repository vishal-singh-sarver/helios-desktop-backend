"""A cancelled hydration must never write context.xml.

The cancel branch used to `queue_scenario_autosave(sctx)` when it had built
anything. That closure holds sctx and runs LATER — after `_abandon()` has
dropped the registry entry, and after the client has called
`/discard?save=false` precisely to avoid a write. So the flag was not honoured:
the half-built context overwrote the scenario's real context.xml AND rotated
the good copy into the single archive slot, destroying both copies of the good
scene.

Measured before the fix, 5-object scenario cancelled after 2 builds:
    good context.xml   1550 bytes, 1 <tile>
    after the release  2877 bytes, 2 <tile>, archive rotated

Nothing is lost by not saving: every object is already in the DB, and the next
open rebuilds from it.
"""
import threading
from uuid import uuid4

import pytest

from app.core.session_store import registry
from app.helios import context as helios_ctx
from app.helios import persistence
from app.helios.persistence import _scenario_archives_dir, _scenario_context_xml
from app.services import scene_object_service as sos

GROUND = {"length": 10, "breadth": 10, "resolution_x": 2, "resolution_y": 2,
          "position_x": 0, "position_y": 0, "position_z": 0, "rotation_z": 0,
          "texture_x": 1, "texture_y": 1}


def _seeded(client, n=5):
    """A scenario with n grounds, saved, then released with its snapshot gone
    so a reopen must REBUILD — the path that used to queue the bad save."""
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"Cx_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sh}).json()
    pid, sid = d["project_id"], d["main_scenario_id"]
    h = {"session-id": sh}
    base = f"/api/geometry/project/{pid}/scenario/{sid}"
    ot = next(o["id"] for o in client.get("/api/catalog/object-types").json()
              ["object_types"] if o["object"] == "Ground")
    for i in range(n):
        r = client.post(base + "/objects", json={"object_type_id": ot,
                        "properties": {**GROUND, "position_x": i * 30}}, headers=h)
        assert r.status_code in (200, 201), r.text
    persistence.wait_for_scenario_saves()
    client.post(f"/api/project/{pid}/scenarios/{sid}/discard", headers=h)
    return sh, pid, sid, h


def _cancel_after(n_builds):
    """A token that trips once n_builds objects have been built."""
    token = threading.Event()
    real_build = sos._build
    count = {"n": 0}

    def _watch(db, sctx, so, **kw):
        out = real_build(db, sctx, so, **kw)
        count["n"] += 1
        if count["n"] >= n_builds:
            token.set()
        return out

    return token, _watch, count


def test_cancelled_hydration_writes_nothing(client, monkeypatch):
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")

    sh, pid, sid, h = _seeded(client, n=5)
    xml = _scenario_context_xml(pid, sid)
    good_bytes = xml.read_bytes()
    good_mtime = xml.stat().st_mtime_ns
    archives = _scenario_archives_dir(pid, sid)
    archives_before = sorted(p.name for p in archives.glob("autosave_*.xml.gz")) \
        if archives.exists() else []

    # Force the rebuild path: drop the snapshot so hydration must build.
    xml.unlink()

    token, watch, count = _cancel_after(2)
    monkeypatch.setattr(sos, "_build", watch)

    from app.db.database import SessionLocal
    db = SessionLocal()
    sctx = sos._sctx(sh, pid, sid)
    sos.ensure_hydrated(db, sctx, sid, token)
    db.close()

    assert 0 < count["n"] < 5, f"expected a partial build, got {count['n']}"
    assert sctx.hydrated is False, "a cancelled hydration marked itself done"

    # THE ASSERTION: the queued save must not exist. Draining the pool would
    # run it if it did.
    persistence.wait_for_scenario_saves()

    assert not xml.exists(), (
        f"a cancelled hydration wrote context.xml ({xml.stat().st_size} bytes) "
        f"— this overwrites the user's real scene")


def test_cancelled_hydration_does_not_rotate_the_good_snapshot(client, monkeypatch):
    """The second half of the damage: the good copy being pushed into the
    single archive slot, so neither copy survives."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")

    sh, pid, sid, h = _seeded(client, n=5)
    xml = _scenario_context_xml(pid, sid)
    good_bytes = xml.read_bytes()
    archives = _scenario_archives_dir(pid, sid)
    before = sorted(p.name for p in archives.glob("autosave_*.xml.gz")) \
        if archives.exists() else []

    token, watch, count = _cancel_after(2)
    monkeypatch.setattr(sos, "_build", watch)

    from app.db.database import SessionLocal
    db = SessionLocal()
    sctx = sos._sctx(sh, pid, sid)
    sos.ensure_hydrated(db, sctx, sid, token)
    db.close()
    persistence.wait_for_scenario_saves()

    after = sorted(p.name for p in archives.glob("autosave_*.xml.gz")) \
        if archives.exists() else []
    assert after == before, f"a cancelled hydration rotated the archive: {before} -> {after}"
    assert xml.read_bytes() == good_bytes, (
        "a cancelled hydration overwrote the good context.xml")


def test_an_uncancelled_hydration_still_saves(client):
    """The fix must not stop a completed hydration from persisting."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        pytest.skip("native PyHelios unavailable")

    sh, pid, sid, h = _seeded(client, n=3)
    xml = _scenario_context_xml(pid, sid)
    xml.unlink()                       # force the rebuild path

    from app.db.database import SessionLocal
    db = SessionLocal()
    sctx = sos._sctx(sh, pid, sid)
    sos.ensure_hydrated(db, sctx, sid, None)
    db.close()
    persistence.wait_for_scenario_saves()

    assert sctx.hydrated is True
    assert xml.exists() and xml.stat().st_size > 0, (
        "a completed hydration stopped saving")
