"""Weather endpoints — file-on-disk + PyHelios reload approach.

Endpoints:

    POST /api/weather/{project_id}/uploadfile   multipart CSV upload

Every request is scoped to a scenario. Required headers:
    session-id    — who you are
    scenario-id   — which scenario inside the project to operate on

project_id is a path parameter. The CSV lives at
backend-api/data/<project_id>/<scenario_id>/weather.csv. Every write
reloads PyHelios from the file via clearTimeseriesData() +
loadTabularTimeseriesData(path).
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import get_session_id, get_scenario_id
from app.core.session_store import registry
from app.core.scenario_context import ScenarioContext
from app.db.database import get_db
from app.db.models import Project, Scenario
from app.helios import context as helios_ctx
from app.services import weather_service

router = APIRouter()


def _resolve_scenario(
    session_id: str, project_id: str, scenario_id: str, db: Session
) -> ScenarioContext:
    """Auth + lazy rehydration for weather operations.

    1. Verify the project exists in the DB and belongs to this session.
       If not → 404.
    2. Verify the scenario exists in the DB and belongs to the project.
       If not → 404.
    3. Get-or-create the in-memory ScenarioContext. If the server was
       restarted, this rebuilds an empty context; the weather service
       lazy-loads the CSV into PyHelios on first use.
    4. Lazy-init the PyHelios Context if PyHelios is available and the
       scenario context has no live handle yet.
    """
    pid = project_id.strip()
    sid = scenario_id.strip()
    if not pid:
        raise HTTPException(400, "project_id is required")
    if not sid:
        raise HTTPException(400, "scenario_id is required")

    project = (
        db.query(Project)
        .filter(Project.id == pid, Project.session_id == session_id)
        .first()
    )
    if project is None:
        raise HTTPException(404, f"Project {pid} not found")

    scenario = (
        db.query(Scenario)
        .filter(Scenario.id == sid, Scenario.project_id == pid)
        .first()
    )
    if scenario is None:
        raise HTTPException(404, f"Scenario {sid} not found in this project")

    sctx = registry.get_or_create_scenario_context(session_id, pid, sid)
    if helios_ctx.PYHELIOS_AVAILABLE and sctx.context is None:
        sctx.context = helios_ctx.Context()
    return sctx


@router.post("/{project_id}/uploadfile")
async def upload_file(
    project_id: str,
    file: UploadFile = File(...),
    session_id: str = Depends(get_session_id),
    scenario_id: str = Depends(get_scenario_id),
    db: Session = Depends(get_db),
):
    """Upload a CSV file. Saves to disk and reloads PyHelios.

    Updates the scenario's weather_file_path column on first upload so the
    scenario knows where its weather CSV lives."""
    sctx = _resolve_scenario(session_id, project_id, scenario_id, db)
    content = await file.read()
    return weather_service.upload_file(sctx, content, db)
