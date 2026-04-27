"""Weather endpoints — session-only state (no persistence).

All endpoints live under:
    /api/weather/project/{project_id}/scenario/{scenario_id}/<verb>

Required header:
    session-id    — whose session this belongs to

project_id and scenario_id are URL path parameters. Every request
routes through _resolve_scenario for auth + context lookup.
"""
from fastapi import APIRouter, Body, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import get_session_id
from app.db.database import get_db
from app.schemas.weather import AddRequest, DeleteRequest, UpdateRequest
from app.services import weather_service
from app.services.scenario_service import _resolve_scenario

router = APIRouter()


# ─── Read endpoints ──────────────────────────────────────────────────────────


@router.get("/project/{project_id}/scenario/{scenario_id}/inspect")
def inspect(
    project_id: str,
    scenario_id: str,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Lightweight debug probe — first 3 rows + availability metadata."""
    sctx = _resolve_scenario(session_id, project_id, scenario_id, db)
    return weather_service.inspect(sctx)


@router.get("/project/{project_id}/scenario/{scenario_id}/getAllTimeSeriesData")
def get_all_timeseries_data(
    project_id: str,
    scenario_id: str,
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Full table read with optional paging."""
    sctx = _resolve_scenario(session_id, project_id, scenario_id, db)
    return weather_service.get_all_timeseries_data(sctx, limit=limit, offset=offset)


# ─── Write endpoints ─────────────────────────────────────────────────────────


@router.post("/project/{project_id}/scenario/{scenario_id}/uploadfile")
async def upload_file(
    project_id: str,
    scenario_id: str,
    file: UploadFile = File(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Bulk-load a CSV into PyHelios via loadTabularTimeseriesData."""
    sctx = _resolve_scenario(session_id, project_id, scenario_id, db)
    content = await file.read()
    return weather_service.upload_file(sctx, content)


@router.post("/project/{project_id}/scenario/{scenario_id}/add")
def add_weather(
    project_id: str,
    scenario_id: str,
    body: AddRequest = Body(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Add one column and/or any number of rows."""
    sctx = _resolve_scenario(session_id, project_id, scenario_id, db)
    return weather_service.add(sctx, body)


@router.post("/project/{project_id}/scenario/{scenario_id}/update")
def update_weather(
    project_id: str,
    scenario_id: str,
    body: UpdateRequest = Body(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Update one existing cell."""
    sctx = _resolve_scenario(session_id, project_id, scenario_id, db)
    return weather_service.update_cell(sctx, body)


@router.post("/project/{project_id}/scenario/{scenario_id}/delete")
def delete_weather(
    project_id: str,
    scenario_id: str,
    body: DeleteRequest = Body(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Delete a row, a column, or wipe everything."""
    sctx = _resolve_scenario(session_id, project_id, scenario_id, db)
    return weather_service.delete(sctx, body)
