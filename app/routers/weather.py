"""Weather endpoints — file-on-disk + PyHelios reload approach.

Endpoints:

    POST /api/weather/{project_id}/uploadfile   multipart CSV upload
    POST /api/weather/{project_id}/add          JSON: add rows/column/both
    POST /api/weather/{project_id}/update       JSON: update a single cell
    POST /api/weather/{project_id}/delete       JSON: delete row/column/both
    GET  /api/weather/{project_id}/inspect      debug: file + PyHelios state

All endpoints require a session-id header. project_id is a path
parameter. The CSV lives at backend-api/data/<project_id>/weather.csv.
Every write reloads PyHelios from the file via clearTimeseriesData() +
loadTabularTimeseriesData(path).
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import get_session_id
from app.core.session_store import registry
from app.core.project_context import ProjectContext
from app.db.database import get_db
from app.db.models import Project
from app.helios import context as helios_ctx
from app.schemas.weather import AddRequest, DeleteRequest, UpdateRequest
from app.services import weather_service

router = APIRouter()


def _resolve_project(
    session_id: str, project_id: str, db: Session
) -> ProjectContext:
    """Auth + lazy rehydration.

    1. Verify the project exists in the DB and belongs to this session.
       If not → 404 (real "doesn't exist or not yours").
    2. Get-or-create the in-memory ProjectContext. If the server was
       restarted, this rebuilds an empty context — weather operations
       still work (file is source of truth); PyHelios reload becomes a
       no-op until the project is fully hydrated by other means.
    """
    pid = project_id.strip()
    if not pid:
        raise HTTPException(400, "project_id is required")

    project = (
        db.query(Project)
        .filter(Project.id == pid, Project.session_id == session_id)
        .first()
    )
    if project is None:
        raise HTTPException(404, f"Project {pid} not found")

    pctx = registry.get_or_create_context(session_id, pid)
    # Lazy-init the PyHelios Context if it's missing (happens after restart).
    # Without this, _reload_pyhelios short-circuits and weather data never
    # reaches PyHelios memory.
    if helios_ctx.PYHELIOS_AVAILABLE and pctx.context is None:
        pctx.context = helios_ctx.Context()
    return pctx


@router.get("/{project_id}/inspect")
async def inspect(
    project_id: str,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Show both layers of state: what's on disk and what's in PyHelios memory."""
    pctx = _resolve_project(session_id, project_id, db)
    return weather_service.inspect(pctx)


@router.post("/{project_id}/uploadfile")
async def upload_file(
    project_id: str,
    file: UploadFile = File(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Upload a CSV file. Saves to disk and reloads PyHelios."""
    pctx = _resolve_project(session_id, project_id, db)
    content = await file.read()
    return weather_service.upload_file(pctx, content)


@router.post("/{project_id}/add")
async def add(
    project_id: str,
    req: AddRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Add a row, a column, or both to the existing weather CSV."""
    pctx = _resolve_project(session_id, project_id, db)
    return weather_service.add(pctx, req)


@router.post("/{project_id}/update")
async def update(
    project_id: str,
    req: UpdateRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Update a single cell, identified by (date, time, col). Value must
    be numeric or empty (to clear the cell)."""
    pctx = _resolve_project(session_id, project_id, db)
    return weather_service.update_cell(pctx, req)


@router.post("/{project_id}/delete")
async def delete(
    project_id: str,
    req: DeleteRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Delete a row, a column, or both from the existing weather CSV."""
    pctx = _resolve_project(session_id, project_id, db)
    return weather_service.delete(pctx, req)
