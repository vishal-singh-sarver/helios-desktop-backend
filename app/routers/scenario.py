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

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.dependencies import get_session_id
from app.db.database import get_db
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

    def _run():
        scenario_service.init_scenario(
            session_id, project_id, scenario_id, db, progress_queue.put)

    async def _stream():
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _run)     # PyHelios is blocking — keep it off the loop
        while True:
            await asyncio.sleep(0.05)
            while not progress_queue.empty():
                event = progress_queue.get_nowait()
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("stage") == "done" or "error" in event:
                    return

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.post("/{project_id}/scenarios/{scenario_id}/discard")
async def discard_scenario(
    project_id: str,
    scenario_id: str,
    session_id: str = Depends(get_session_id),
):
    """Autosave this scenario's context, then release it from memory.

    Call when the user switches away. Idempotent — discarding a scenario with no
    live context succeeds with discarded=false.

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
        scenario_service.discard_scenario, session_id, project_id, scenario_id
    )
