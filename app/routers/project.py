from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.project import ProjectCreateRequest
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


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
):
    return project_service.delete_project(session_id, project_id, db)
