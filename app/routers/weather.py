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
from app.schemas.weather import (
    AddColumnsRequest,
    AddRowsRequest,
    DeleteRequest,
    UpdateRequest,
)
from app.schemas.weather_header import (
    WeatherDataHeaderReplaceRequest,
    WeatherDataHeaderUpdateRequest,
)
from app.services import weather_header_service, weather_service
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


@router.post("/project/{project_id}/scenario/{scenario_id}/addCol")
def add_columns(
    project_id: str,
    scenario_id: str,
    body: AddColumnsRequest = Body(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Add one or more columns. Persists header rows + writes cells atomically."""
    sctx = _resolve_scenario(session_id, project_id, scenario_id, db)
    return weather_service.add_columns(sctx, body.column, db)


@router.post("/project/{project_id}/scenario/{scenario_id}/addRow")
def add_rows(
    project_id: str,
    scenario_id: str,
    body: AddRowsRequest = Body(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Append rows to the timeseries table — PyHelios-only, no SQL writes."""
    sctx = _resolve_scenario(session_id, project_id, scenario_id, db)
    return weather_service.add_rows(sctx, body.rows, db)


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


@router.post("/project/{project_id}/scenario/{scenario_id}/wipe")
def wipe_weather(
    project_id: str,
    scenario_id: str,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Wipe everything: SQL weather_data_headers + PyHelios timeseries data."""
    sctx = _resolve_scenario(session_id, project_id, scenario_id, db)
    return weather_service.wipe(sctx, db)


# ─── Per-scenario weather header mapping ────────────────────────────────────


@router.get("/project/{project_id}/scenario/{scenario_id}/weather_data_header")
def get_weather_data_header(
    project_id: str,
    scenario_id: str,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Return this scenario's CSV-column-to-(data_type, unit) mapping."""
    return weather_header_service.get_headers(session_id, project_id, scenario_id, db)


@router.put("/project/{project_id}/scenario/{scenario_id}/weather_data_header")
def replace_weather_data_header(
    project_id: str,
    scenario_id: str,
    body: WeatherDataHeaderReplaceRequest = Body(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Atomically replace the scenario's header set. Empty list clears it."""
    items = [h.model_dump() for h in body.headers]
    return weather_header_service.replace_headers(
        session_id, project_id, scenario_id, items, db
    )


@router.delete("/project/{project_id}/scenario/{scenario_id}/weather_data_header")
def clear_weather_data_header(
    project_id: str,
    scenario_id: str,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Remove all headers for the scenario. Returns the count removed."""
    return weather_header_service.clear_headers(session_id, project_id, scenario_id, db)


@router.patch(
    "/project/{project_id}/scenario/{scenario_id}/weather_data_header/{header_id}"
)
def update_weather_data_header(
    project_id: str,
    scenario_id: str,
    header_id: int,
    body: WeatherDataHeaderUpdateRequest = Body(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Partial update of a single header — name, datatype, unit, or order."""
    return weather_header_service.update_header(
        session_id,
        project_id,
        scenario_id,
        header_id,
        body.name,
        body.helios_data_type_id,
        body.unit_id,
        body.display_order,
        db,
    )


@router.delete(
    "/project/{project_id}/scenario/{scenario_id}/weather_data_header/{header_id}"
)
def delete_weather_data_header(
    project_id: str,
    scenario_id: str,
    header_id: int,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Delete one header row + NaN-clear its PyHelios cells (best-effort)."""
    return weather_header_service.delete_header(
        session_id, project_id, scenario_id, header_id, db
    )
