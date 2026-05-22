from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.project import ProjectCreateRequest, ProjectUpdateRequest
from app.services import project_service
from app.core.dependencies import get_session_id

router = APIRouter()


@router.post("/create", status_code=201)
async def create_project(
    req: ProjectCreateRequest,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
):
    return project_service.create_project(
        session_id, req.name, req.latitude, req.longitude, db
    )


@router.get("/recent")
async def list_recent_projects(
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
):
    return project_service.list_recent_projects(session_id, db)


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
):
    """Project + its scenarios + each scenario's weather_data_headers."""
    return project_service.get_project_with_scenarios(session_id, project_id, db)


@router.patch("/{project_id}")
async def update_project(
    project_id: str,
    req: ProjectUpdateRequest,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
):
    """Partial update of a project. Editable: name, latitude, longitude.
    When latitude or longitude changes, utc_offset is recomputed."""
    return project_service.update_project(
        session_id, project_id, req.name, req.latitude, req.longitude, db
    )


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
):
    return project_service.delete_project(session_id, project_id, db)
