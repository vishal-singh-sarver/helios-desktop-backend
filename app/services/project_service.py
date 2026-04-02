from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException

from app.db.models import Project
from app.core.timezone import utc_offset_from_coords
from app.helios import context as helios_ctx
from app.helios import registry as reg
from app.helios import persistence


def create_project(name: str, latitude: float, longitude: float, db: Session) -> dict:
    clean_name = name.strip()

    existing = db.query(Project).filter(
        func.lower(Project.name) == clean_name.lower()
    ).first()
    if existing:
        raise HTTPException(409, "A project with this name already exists")

    if helios_ctx.PYHELIOS_AVAILABLE:
        helios_ctx.reset_context()
        reg.reset_registry()

    utc_offset = utc_offset_from_coords(latitude, longitude)

    project = Project(
        name=clean_name,
        latitude=latitude,
        longitude=longitude,
        utc_offset=utc_offset,
    )
    try:
        db.add(project)
        db.commit()
        db.refresh(project)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to create project")

    if helios_ctx.PYHELIOS_AVAILABLE:
        helios_ctx.get_context()

    return {
        "success": True,
        "project_id": project.id,
        "name": clean_name,
        "latitude": latitude,
        "longitude": longitude,
        "utc_offset": utc_offset,
        "session_id": helios_ctx.session_id,
    }


def save_project(project_id, label: str, db: Session) -> dict:
    project = db.query(Project).order_by(Project.updated_at.desc()).first()
    if not project:
        raise HTTPException(400, "No active project. Create a project first.")
    if not helios_ctx.PYHELIOS_AVAILABLE:
        raise HTTPException(503, "PyHelios not available")

    ctx = helios_ctx.get_context()
    metadata = {"name": project.name, "latitude": project.latitude, "longitude": project.longitude}
    registry = {str(k): v for k, v in reg.get_all_objects().items()}

    persistence.save_snapshot(project.id, ctx, registry, metadata)
    version_id = persistence.save_version(project.id, label, ctx, registry, metadata, db)

    return {"success": True, "project_id": project.id, "version_id": version_id}


def load_project(project_id: str, db: Session) -> dict:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    if not helios_ctx.PYHELIOS_AVAILABLE:
        raise HTTPException(503, "PyHelios not available")

    helios_ctx.reset_context()
    reg.reset_registry()

    ctx = helios_ctx.get_context()
    data = persistence.load_snapshot(project_id, ctx)

    for obj_id_str, obj in data.get("objects", {}).items():
        reg._object_registry[int(obj_id_str)] = obj

    return {
        "success": True,
        "project": data.get("metadata", {}),
        "session_id": helios_ctx.session_id,
    }


def list_versions(project_id: str, db: Session) -> dict:
    return {"versions": persistence.list_versions(project_id, db)}


def restore_version(project_id: str, version_id: str, db: Session) -> dict:
    if not helios_ctx.PYHELIOS_AVAILABLE:
        raise HTTPException(503, "PyHelios not available")

    helios_ctx.reset_context()
    reg.reset_registry()

    ctx = helios_ctx.get_context()
    data = persistence.restore_version(project_id, version_id, ctx, db)

    for obj_id_str, obj in data.get("objects", {}).items():
        reg._object_registry[int(obj_id_str)] = obj

    return {
        "success": True,
        "project": data.get("metadata", {}),
        "session_id": helios_ctx.session_id,
    }
