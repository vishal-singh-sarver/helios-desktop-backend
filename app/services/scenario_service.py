"""
Scenario service.

Each scenario belongs to a project and owns its own runtime context
(ScenarioContext) + its own weather CSV on disk. No PyHelios state is
serialized anywhere — the context lives in RAM only, same lifetime as a
ProjectContext, and gets rebuilt from the weather CSV on first access
after a server restart.

Disk layout per scenario:
    data/<project_id>/<scenario_id>/weather.csv
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.scenario_context import ScenarioContext
from app.core.session_store import registry
from app.db.models import Project, Scenario, ScenarioObject
from app.helios import context as helios_ctx
logger = logging.getLogger(__name__)

from app.helios.persistence import (
    _ensure_scenario_structure,
    load_scenario_snapshot,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _scenario_dir(project_id: str, scenario_id: str) -> Path:
    """Per-scenario folder, nested under its parent project."""
    return settings.scenario_dir(project_id, scenario_id)


def _assert_project_owned(
    db: Session, session_id: str, project_id: str
) -> Project:
    """Look up the project and verify this session owns it."""
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.session_id == session_id)
        .first()
    )
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return project


def _resolve_scenario(
    session_id: str, project_id: str, scenario_id: str, db: Session
) -> ScenarioContext:
    """Auth + lazy hydration for any endpoint scoped to a scenario.

    1. Validate IDs are non-empty.
    2. Confirm the project exists in the DB and belongs to this session (→ 404).
    3. Confirm the scenario exists in the DB and belongs to the project (→ 404).
    4. Get-or-create the in-memory ScenarioContext (fresh after restart).
    5. If PyHelios is available and the scenario has no live Context yet,
       create an empty one — no file hydration (weather is session-only).

    An already-live scenario is returned without taking the lock, exactly as
    `_sctx` does for geometry. The lock guards CREATION, which happens once;
    taking it on every later call meant every weather request — upload, header,
    time series — queued behind whatever held it, including a context.xml save
    (18s on a 600x600 ground). Geometry had this fast-path and weather did not,
    so the same scenario felt instant in the viewport and frozen in Weather.
    """
    pid = project_id.strip()
    sid = scenario_id.strip()
    if not pid:
        raise HTTPException(400, "project_id is required")
    if not sid:
        raise HTTPException(400, "scenario_id is required")

    _assert_project_owned(db, session_id, pid)

    scenario = (
        db.query(Scenario)
        .filter(Scenario.id == sid, Scenario.project_id == pid)
        .first()
    )
    if scenario is None:
        raise HTTPException(404, f"Scenario {sid} not found in this project")

    # After the auth checks, so an unauthorised caller still 404s.
    sctx = registry.get_scenario_context(session_id, pid, sid)
    if sctx is not None and (sctx.initialized or not helios_ctx.PYHELIOS_AVAILABLE):
        return sctx

    with registry._scenario_lock.write():
        sctx = registry.get_or_create_scenario_context(session_id, pid, sid)
        if helios_ctx.PYHELIOS_AVAILABLE and sctx.context is None:
            sctx.context = helios_ctx.Context()
            # Restore weather data from scenario-specific XML if it exists
            if not load_scenario_snapshot(sctx):
                # loadXML raised. It does not unwind, so everything it read
                # before failing is still in there; hydration would rebuild the
                # DB rows on top and save the doubled result. Start clean and
                # let hydration rebuild from the DB, which is the source of
                # truth for the object set anyway.
                sctx.context = helios_ctx.Context()
            # Only now is the context worth reading — `_sctx` lets callers skip
            # the lock on this flag, so it must not be set before loadXML ends.
            sctx.initialized = True
    return sctx


# ─── Endpoint handlers ───────────────────────────────────────────────────────


def create_scenario(
    session_id: str,
    project_id: str,
    name: str,
    source_scenario_id: str | None,
    db: Session,
) -> dict:
    """Create a new scenario.

    - Validates project ownership.
    - Rejects duplicate names within the project.
    - If source_scenario_id is given, copies its weather CSV to the new
      scenario's folder (byte-for-byte). Otherwise the new scenario starts
      with no weather file.
    - Registers an empty ScenarioContext in memory so the first weather
      call on the new scenario is instant.
    """
    _assert_project_owned(db, session_id, project_id)

    # Reject duplicate names (handled at DB level too, but fail fast)
    existing = (
        db.query(Scenario)
        .filter(Scenario.project_id == project_id, Scenario.name == name)
        .first()
    )
    if existing:
        raise HTTPException(400, f"Scenario '{name}' already exists in this project")

    # Resolve the source (if given)
    source: Scenario | None = None
    if source_scenario_id:
        source = (
            db.query(Scenario)
            .filter(
                Scenario.id == source_scenario_id,
                Scenario.project_id == project_id,
            )
            .first()
        )
        if source is None:
            raise HTTPException(
                404,
                f"Source scenario {source_scenario_id} not found in this project",
            )

    # Insert the new row (UUID auto-generated by model default)
    scenario = Scenario(project_id=project_id, name=name)
    try:
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to create scenario")

    # Scaffold the canonical per-scenario folder shape:
    #   <new_dir>/
    #     context_file/  (+ archives/)
    #     weather/
    #     metadata/
    #     export_files/
    new_dir = _ensure_scenario_structure(project_id, scenario.id)

    # Fork weather CSV from the source, if any → land it under weather/.
    if source and source.weather_file_path:
        src_path = Path(source.weather_file_path)
        if src_path.exists():
            dst_path = new_dir / "weather" / "weather.csv"
            shutil.copyfile(src_path, dst_path)
            scenario.weather_file_path = str(dst_path)
            try:
                db.commit()
                db.refresh(scenario)
            except Exception:
                db.rollback()
                raise HTTPException(500, "Failed to persist scenario weather path")

    # Register an empty ScenarioContext in memory
    registry.get_or_create_scenario_context(session_id, project_id, scenario.id)

    logger.info("[scenario] created   id=%s project=%s name=%r%s",
                scenario.id[:8], project_id[:8], scenario.name,
                f" forked from {source_scenario_id[:8]}" if source_scenario_id else "")
    return {
        "success": True,
        "scenario_id": scenario.id,
        "name": scenario.name,
    }


def list_scenarios(session_id: str, project_id: str, db: Session) -> dict:
    """List all scenarios for a project."""
    _assert_project_owned(db, session_id, project_id)

    rows = (
        db.query(Scenario)
        .filter(Scenario.project_id == project_id)
        .order_by(Scenario.created_at.asc())
        .all()
    )

    return {
        "scenarios": [
            {
                "id": r.id,
                "name": r.name,
                "has_weather": bool(r.weather_file_path),
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ]
    }


def delete_scenario(
    session_id: str, project_id: str, scenario_id: str, db: Session
) -> dict:
    """Delete a scenario: DB row, in-memory context, and on-disk folder."""
    _assert_project_owned(db, session_id, project_id)

    scenario = (
        db.query(Scenario)
        .filter(Scenario.id == scenario_id, Scenario.project_id == project_id)
        .first()
    )
    if scenario is None:
        raise HTTPException(404, f"Scenario {scenario_id} not found")

    deleted_name = scenario.name      # read before the row goes

    try:
        db.delete(scenario)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to delete scenario")

    registry.remove_scenario(session_id, project_id, scenario_id)
    helios_ctx.release_memory()
    shutil.rmtree(_scenario_dir(project_id, scenario_id), ignore_errors=True)

    logger.info("[scenario] deleted   id=%s project=%s name=%r",
                scenario_id[:8], project_id[:8], deleted_name)
    return {"success": True, "scenario_id": scenario_id}


# ─── Context lifecycle (explicit init + discard) ─────────────────────────────
#
# Without these, a scenario's PyHelios context is created implicitly by whichever
# request touches it first (_resolve_scenario above, or scene_object_service.
# _sctx), which silently absorbs the whole loadXML + rebuild cost with no
# progress, and is never released until the scenario or project is deleted.


def init_scenario(session_id: str, project_id: str, scenario_id: str,
                  db: Session, emit, cancelled=None) -> None:
    """Create + hydrate a scenario's context, reporting progress through `emit`.

    Runs the work the lazy paths would otherwise do on a random request:
    Context() + load_scenario_snapshot (via _resolve_scenario) and then
    ensure_hydrated, which re-maps loaded objects and builds any that are
    missing. Once this returns, `sctx.hydrated` is True and every other
    scenario-scoped endpoint is safe to call.

    Blocking (PyHelios is synchronous) — the router runs it off the event loop.
    `emit(dict)` is called with progress events; exceptions are reported through
    it as {"error": ...} rather than raised, so the stream always terminates.
    """
    from app.services import scene_object_service as sos   # avoid import cycle

    def _abandon() -> None:
        """Drop a context whose client walked away before it finished loading.

        loadXML cannot be interrupted, so a cancelled open still pays for the
        whole read — but nothing has to KEEP the result. Left in the registry it
        stays resident until the scenario or project is deleted, so opening a
        second project loaded on top of the first: two 1000x1000 contexts is
        ~21 GB and the kernel SIGKILLs the server (observed twice).

        The lock is REQUIRED, not decoration. `hydrated` is False for the whole
        of another request's hydration, so checking it unlocked is a race, not a
        liveness test: a concurrent hydrate would still be running, this would
        evict the context out from under it, and the next lookup would build a
        SECOND one — two live Contexts, the exact leak this exists to prevent.
        Taking .write() serialises against `_hydrate` (which holds it for the
        duration), so by the time the check runs the other request has either
        finished — `hydrated` True, and this leaves it alone — or has not
        started.

        Nothing is saved: the context is partial, and everything in it is
        already in the DB.
        """
        nonlocal sctx
        dropped = False
        with registry._scenario_lock.write():
            live = registry.get_scenario_context(session_id, project_id, scenario_id)
            if live is not None and not live.hydrated:
                registry.remove_scenario(session_id, project_id, scenario_id)
                dropped = True
                logger.info("[context] released  scenario=%s (abandoned)",
                            scenario_id[:8])
        # BOTH names, and `sctx` is the one that matters. `_abandon` only ever
        # runs after `sctx = _resolve_scenario(...)` below, and _resolve_scenario
        # returns the very object the registry held — so `live is sctx`, and
        # clearing `live` alone leaves init_scenario's own frame pinning the
        # context. The trim then runs against a fully live 1.4 GB allocation and
        # reclaims nothing, on the one path this was written for. Both callers
        # return immediately after, so dropping `sctx` here is safe.
        live = None
        sctx = None
        if dropped:
            helios_ctx.release_memory()

    _t0 = time.monotonic()
    logger.info("[init]    started   scenario=%s project=%s",
                scenario_id[:8], project_id[:8])
    try:
        emit({"stage": "context", "progress": 0.1,
              "message": "Loading scenario context"})
        sctx = _resolve_scenario(session_id, project_id, scenario_id, db)

        # Checked BEFORE hydrating: the load above is the expensive half, and
        # the next scenario's init is already queued behind the lock it holds.
        # Releasing here is what keeps the peak at one context instead of two.
        if cancelled is not None and cancelled.is_set():
            logger.info("[init]    cancelled scenario=%s during load, %.1fs",
                        scenario_id[:8], time.monotonic() - _t0)
            _abandon()
            emit({"error": "Scenario load cancelled", "cancelled": True})
            return

        emit({"stage": "hydrate", "progress": 0.5,
              "message": "Preparing geometry"})
        sos.ensure_hydrated(db, sctx, scenario_id, cancelled)

        if cancelled is not None and cancelled.is_set():
            logger.info("[init]    cancelled scenario=%s during hydrate, %.1fs",
                        scenario_id[:8], time.monotonic() - _t0)
            _abandon()
            emit({"error": "Scenario load cancelled", "cancelled": True})
            return

        # Hydration only QUEUES its context.xml save. Wait for it here so
        # "ready" means the write is done too — this is the request that is
        # still reporting progress, and the client's next call is not.
        emit({"stage": "persist", "progress": 0.9,
              "message": "Saving scenario"})
        sos.wait_for_saves()

        # "Ready" means every geometry the DB says exists is LIVE in the context,
        # not merely that hydration returned. It swallows per-object build
        # failures on purpose (`except HTTPException: continue`) so one bad row
        # cannot make a scenario unopenable — but that left init reporting
        # {"objects": 0, "message": "Scenario ready"} for a scenario where
        # nothing loaded, while the object list still returned every row. The
        # client then drew a geometry that has no primitives behind it.
        expected = (
            db.query(ScenarioObject)
            .filter(ScenarioObject.scenario_id == scenario_id)
            .count()
        )
        objects = len(sctx.persisted_objects) if sctx.persisted_objects else 0
        if objects < expected:
            logger.error("[init]    incomplete scenario=%s only %d of %d "
                         "geometries are live", scenario_id[:8], objects, expected)
            emit({"error": f"{expected - objects} of {expected} geometries could not "
                            f"be loaded into the scenario",
                  "objects": objects, "expected": expected})
            return

        logger.info("[init]    ready     scenario=%s objects=%d in %.1fs",
                    scenario_id[:8], objects, time.monotonic() - _t0)
        emit({"stage": "done", "progress": 1.0, "objects": objects,
              "message": "Scenario ready"})
    except HTTPException as exc:
        logger.warning("[init]    failed    scenario=%s %s (%.1fs)",
                       scenario_id[:8], exc.detail, time.monotonic() - _t0)
        emit({"error": exc.detail, "status": exc.status_code})
    except Exception as exc:   # noqa: BLE001 — the stream must always end
        logger.exception("[init]    failed    scenario=%s after %.1fs",
                         scenario_id[:8], time.monotonic() - _t0)
        emit({"error": str(exc) or exc.__class__.__name__})


def discard_scenario(session_id: str, project_id: str, scenario_id: str,
                     save: bool = True) -> dict:
    """Autosave a scenario's context to context.xml, then drop it from memory.

    `save=False` releases WITHOUT writing — the cancel path. A load the user
    walked away from leaves a half-hydrated context, and saving that would
    overwrite the scenario's real context.xml while rotating the good copy into
    archives: cancelling a load would corrupt the saved scene. So the client
    could not release a cancelled load at all, and its memory stayed resident
    until the scenario or project was deleted. Nothing is lost by skipping the
    write — the geometry is in the DB, and a half-hydrated context has nothing
    the DB does not already have.

    Called when the user switches away. The registry entry is dropped under
    .write(); the save then runs under .read(), which excludes mutations
    without stalling other readers. A scenario with no live context is a
    no-op success — discard is idempotent and safe to call on navigation.

    The entry is dropped BEFORE the save, not after: _sctx returns an already-live
    scenario without taking the lock, so an entry left in place for the duration
    of the write would be handed to a request as if it were current. Removing
    first makes that lookup miss, and the request then waits on the lock exactly
    as it used to. `sctx` is a local reference, so the save is unaffected.
    """
    from app.helios.persistence import (
        trigger_scenario_autosave,
        wait_for_scenario_saves,
    )

    with registry._scenario_lock.write():
        sctx = registry.get_scenario_context(session_id, project_id, scenario_id)
        if sctx is None:
            return {"success": True, "scenario_id": scenario_id, "discarded": False}
        registry.remove_scenario(session_id, project_id, scenario_id)

    # Under .read(), NOT .write(). Removing the registry entry stops anyone NEW
    # from being handed this context, but it does nothing about requests that
    # resolved it before the discard — one of those re-entering a mutation
    # would rewrite the context while this writeXML walks it, and a queued save
    # for the same sctx could run its own write concurrently and double-rotate
    # the archive. .read() excludes both (mutations take .write(), and the
    # queued save takes .read() the same way) without holding every other
    # scenario off for the length of the write, which is what taking .write()
    # here did.
    #
    # Still SYNCHRONOUS, unlike every other save: discard is the one path where
    # the context is about to be released, so a queued write would serialise a
    # context that may be gone by the time the worker reaches it.
    # DRAIN FIRST. Every mutation already queues a save, so by the time the
    # user navigates away the write is usually done or in flight. Waiting for
    # it costs nothing that was not already going to be spent, and it is what
    # makes the dirty check below meaningful — an undrained queue would leave
    # the scene looking dirty and we would serialise it a second time.
    wait_for_scenario_saves()

    # SKIP THE WRITE WHEN THE FILE ALREADY MATCHES. This was an unconditional
    # writeXML, and on a high-resolution textured ground it is ~16s of
    # re-serialising a scene byte-identical to what is already on disk — paid
    # while the user waits to get back to the project list. Persistence is the
    # mutation's job; discard only has to cover a change that raced the drain.
    saved = False
    dirty = sctx.mutation_seq != sctx.saved_seq
    if save and dirty:
        try:
            with registry._scenario_lock.read():
                trigger_scenario_autosave(sctx)
            saved = True
        except Exception:
            pass    # never block the release on a save failure

    # `del sctx` before the trim: held to the end of the function it would keep
    # the whole context alive and malloc_trim would find nothing to give back.
    # The queued-save closure held a reference too, which is the other reason
    # the drain above has to come first. Outside the lock, so a 62 ms reclaim
    # never stalls another scenario.
    del sctx
    helios_ctx.release_memory()

    # Logged AFTER the trim, not before: the line claims the context was
    # released, and until release_memory() has run that is not yet true.
    # `saved` distinguishes a real write from a skip — without it a fast
    # discard and a broken one look identical in the log.
    logger.info("[context] released  scenario=%s (discard, saved=%s, dirty=%s)",
                scenario_id[:8], saved, dirty)
    return {"success": True, "scenario_id": scenario_id,
            "discarded": True, "saved": saved}
