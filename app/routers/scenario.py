"""Scenario endpoints — CRUD for scenarios under a project.

Endpoints:

    POST   /api/project/{project_id}/scenarios/create
    GET    /api/project/{project_id}/scenarios
    DELETE /api/project/{project_id}/scenarios/{scenario_id}

All endpoints require a session-id header. project_id is a path parameter.
Each scenario owns a folder at data/<project_id>/<scenario_id>/ and (when
populated) a weather.csv inside it. PyHelios state for a scenario lives
in RAM only — in a ScenarioContext held by SessionRegistry.
"""
import asyncio
import json
import queue
import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.dependencies import get_session_id
from app.db.database import SessionLocal, get_db
from app.schemas.scenario import ScenarioCreateRequest
from app.services import scenario_service

router = APIRouter()


@router.post("/{project_id}/scenarios/create", status_code=201)
async def create_scenario(
    project_id: str,
    req: ScenarioCreateRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Create a new scenario for this project. If source_scenario_id is
    provided, the new scenario is a fork — its weather CSV is copied from
    the source. Otherwise the new scenario starts empty."""
    return scenario_service.create_scenario(
        session_id, project_id, req.name, req.source_scenario_id, db
    )


@router.get("/{project_id}/scenarios")
async def list_scenarios(
    project_id: str,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """List all scenarios for this project."""
    return scenario_service.list_scenarios(session_id, project_id, db)


@router.delete("/{project_id}/scenarios/{scenario_id}")
async def delete_scenario(
    project_id: str,
    scenario_id: str,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Delete a scenario: DB row, in-memory context, and on-disk folder."""
    return scenario_service.delete_scenario(session_id, project_id, scenario_id, db)


# ─── Context lifecycle ───────────────────────────────────────────────────────


@router.get("/{project_id}/scenarios/{scenario_id}/init")
async def init_scenario(
    project_id: str,
    scenario_id: str,
    session_id: str = Query(..., description="session id (EventSource cannot set headers)"),
    db: Session = Depends(get_db),
):
    """Create + hydrate this scenario's context, streaming progress as SSE.

    NOTE: this is the ONE endpoint that takes session_id as a QUERY PARAM rather
    than the `session-id` header every other route uses — the browser's
    EventSource API cannot set request headers. Everything else, including
    /discard below, keeps the header convention.

    Emits {"stage", "progress", "message"} events and terminates on either
    {"stage": "done"} or {"error": ...}. `done` means hydration finished, so
    every other scenario-scoped endpoint is safe to call.
    """
    if not session_id or not session_id.strip():
        raise HTTPException(400, "session_id is required")

    progress_queue: queue.Queue = queue.Queue()
    cancelled = threading.Event()

    def _run():
        # Its OWN Session. This work outlives the request: Depends(get_db)
        # closes the request's Session the moment the generator returns, and on
        # a client disconnect that happens while init is still querying it.
        #
        # Nothing may escape this function. The stream ends only on an event
        # from this queue, so a worker that dies silently leaves the client
        # hanging on an open response forever — including anything raised
        # before init_scenario's own try block is entered.
        db_bg = None
        try:
            db_bg = SessionLocal()
            scenario_service.init_scenario(
                session_id, project_id, scenario_id, db_bg,
                progress_queue.put, cancelled)
        except BaseException as exc:     # noqa: BLE001 — the stream must end
            progress_queue.put(
                {"error": f"{exc.__class__.__name__}: {exc}" or "init failed"})
        finally:
            if db_bg is not None:
                db_bg.close()

    async def _stream():
        loop = asyncio.get_event_loop()
        fut = loop.run_in_executor(None, _run)   # PyHelios is blocking — keep it off the loop
        try:
            while True:
                await asyncio.sleep(0.05)
                while not progress_queue.empty():
                    event = progress_queue.get_nowait()
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("stage") == "done" or "error" in event:
                        return
                # Belt and braces: if the worker is gone and left nothing to
                # send, end the stream rather than poll an empty queue forever.
                if fut.done() and progress_queue.empty():
                    yield ('data: {"error": "init ended without reporting a '
                           'result"}\n\n')
                    return
        finally:
            # Reached on normal completion AND on the CancelledError raised
            # when the browser drops the EventSource — closing the tab, or
            # switching scenario before this one finished loading. Hydration
            # checks this between objects and stops, so a scenario nobody is
            # waiting for no longer rebuilds itself to the end. Setting it
            # after a normal finish is a no-op: the work is already done.
            cancelled.set()

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.post("/{project_id}/scenarios/{scenario_id}/discard")
async def discard_scenario(
    project_id: str,
    scenario_id: str,
    save: bool = Query(True, description="false = release WITHOUT writing context.xml"),
    session_id: str = Depends(get_session_id),
):
    """Autosave this scenario's context, then release it from memory.

    Call when the user switches away. Idempotent — discarding a scenario with no
    live context succeeds with discarded=false.

    `?save=false` is the CANCEL path. A load the user walked away from leaves a
    half-hydrated context; saving that would overwrite the scenario's real
    context.xml and rotate the good copy into archives, so cancelling a load
    would corrupt the saved scene. Because the only release available always
    wrote, the client could not free a cancelled load at all and its memory
    stayed resident. Nothing is lost by skipping the write: geometry lives in
    the DB, and a half-hydrated context holds nothing the DB does not.

    Run off the event loop: the autosave inside serialises the whole scene via
    PyHelios and gzips the previous snapshot, both blocking. Called directly from
    an `async def` it froze the entire backend for the duration — every request,
    not just this one. /init suffered worst: its work runs in an executor thread
    and keeps going, but the coroutine that DELIVERS its progress events is on
    the loop, so nothing reached the browser until the freeze ended and then the
    whole queue drained at once — the client saw no loading, then an immediate
    "Scenario ready". Same treatment /init and the geometry routes already get.
    """
    return await asyncio.to_thread(
        scenario_service.discard_scenario, session_id, project_id, scenario_id, save
    )
