import time
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db.database import get_db
from app.db.models import Project
from app.core.timezone import utc_offset_from_coords
from app.helios import context as helios_ctx
from app.helios import registry as reg
from app.helios import persistence
from app.schemas.project import (
    ProjectCreateRequest, ProjectSaveRequest,
    ProjectLoadRequest, ProjectRestoreVersionRequest,
)

router = APIRouter()


@router.post("/create", status_code=201)
async def create_project(req: ProjectCreateRequest, db: Session = Depends(get_db)):
    clean_name = req.name.strip()

    # ---- Duplicate check (case-insensitive) ----
    existing = db.query(Project).filter(
        func.lower(Project.name) == clean_name.lower()
    ).first()

    if existing:
        raise HTTPException(
            status_code=409,
            detail="A project with this name already exists"
        )

    # ---- Reset active runtime state ----
    if helios_ctx.PYHELIOS_AVAILABLE:
        helios_ctx.reset_context()
        reg.reset_registry()

    # ---- Compute timezone offset ----
    utc_offset = utc_offset_from_coords(req.latitude, req.longitude)

    # ---- Create project row ----
    project = Project(
        name=clean_name,
        latitude=req.latitude,
        longitude=req.longitude,
        utc_offset=utc_offset,
    )

    try:
        db.add(project)
        db.commit()
        db.refresh(project)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create project")

    # ---- Initialize fresh Helios context ----
    if helios_ctx.PYHELIOS_AVAILABLE:
        helios_ctx.get_context()

    return {
        "success": True,
        "project_id": project.id,
        "name": clean_name,
        "latitude": req.latitude,
        "longitude": req.longitude,
        "utc_offset": utc_offset,
        "session_id": helios_ctx.session_id,
    }


@router.get("/info")
async def project_info():
    return {"project": reg._object_registry and "active" or None}


@router.post("/save")
async def save_project(req: ProjectSaveRequest, db: Session = Depends(get_db)):
    """Save current context snapshot + archive a version."""
    project = db.query(Project).order_by(Project.updated_at.desc()).first()
    if not project:
        raise HTTPException(400, "No active project. Create a project first.")
    if not helios_ctx.PYHELIOS_AVAILABLE:
        raise HTTPException(503, "PyHelios not available")

    ctx = helios_ctx.get_context()
    metadata = {"name": project.name, "latitude": project.latitude, "longitude": project.longitude}
    registry = {str(k): v for k, v in reg.get_all_objects().items()}

    persistence.save_snapshot(project.id, ctx, registry, metadata)
    version_id = persistence.save_version(project.id, req.label, ctx, registry, metadata, db)

    return {"success": True, "project_id": project.id, "version_id": version_id}


@router.post("/load")
async def load_project(req: ProjectLoadRequest, db: Session = Depends(get_db)):
    """Restore context from the latest snapshot on disk."""
    project = db.query(Project).filter(Project.id == req.project_id).first()
    if not project:
        raise HTTPException(404, f"Project {req.project_id} not found")
    if not helios_ctx.PYHELIOS_AVAILABLE:
        raise HTTPException(503, "PyHelios not available")

    helios_ctx.reset_context()
    reg.reset_registry()

    ctx = helios_ctx.get_context()
    data = persistence.load_snapshot(req.project_id, ctx)

    # Restore registry
    for obj_id_str, obj in data.get("objects", {}).items():
        reg._object_registry[int(obj_id_str)] = obj

    return {
        "success": True,
        "project": data.get("metadata", {}),
        "session_id": helios_ctx.session_id,
    }


@router.get("/{project_id}/versions")
async def list_versions(project_id: str, db: Session = Depends(get_db)):
    return {"versions": persistence.list_versions(project_id, db)}


@router.post("/{project_id}/restore")
async def restore_version(project_id: str, req: ProjectRestoreVersionRequest,
                          db: Session = Depends(get_db)):
    if not helios_ctx.PYHELIOS_AVAILABLE:
        raise HTTPException(503, "PyHelios not available")

    if helios_ctx.PYHELIOS_AVAILABLE:
        helios_ctx.reset_context()
        reg.reset_registry()

    ctx = helios_ctx.get_context()
    data = persistence.restore_version(project_id, req.version_id, ctx, db)

    for obj_id_str, obj in data.get("objects", {}).items():
        reg._object_registry[int(obj_id_str)] = obj

    return {
        "success": True,
        "project": data.get("metadata", {}),
        "session_id": helios_ctx.session_id,
    }
