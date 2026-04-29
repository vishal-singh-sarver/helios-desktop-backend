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
from fastapi import APIRouter, Depends
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
