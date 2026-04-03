from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.helios import registry as reg
from app.schemas.project import (
    ProjectCreateRequest, ProjectSaveRequest,
    ProjectLoadRequest, ProjectRestoreVersionRequest,
)
from app.services import project_service

router = APIRouter()


@router.post("/create", status_code=201)
async def create_project(
    req: ProjectCreateRequest,
    db: Session = Depends(get_db),
    session_id: str | None = Header(default=None, alias="session_id"),
):
    try:
        if session_id is None or not session_id.strip():
            raise HTTPException(400, "Session ID is required")
        return project_service.create_project(
            session_id,
            req.name,
            req.latitude,
            req.longitude,
            db
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/info")
async def project_info():
    return {"project": reg._object_registry and "active" or None}


@router.post("/save")
async def save_project(req: ProjectSaveRequest, db: Session = Depends(get_db)):
    try:
        return project_service.save_project(None, req.label, db)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/load")
async def load_project(req: ProjectLoadRequest, db: Session = Depends(get_db)):
    try:
        return project_service.load_project(req.project_id, db)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/{project_id}/versions")
async def list_versions(project_id: str, db: Session = Depends(get_db)):
    return project_service.list_versions(project_id, db)


@router.post("/{project_id}/restore")
async def restore_version(project_id: str, req: ProjectRestoreVersionRequest,
                          db: Session = Depends(get_db)):
    try:
        return project_service.restore_version(project_id, req.version_id, db)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
