from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException
import shutil

from app.db.models import Project
from app.core.timezone import utc_offset_from_coords
from app.helios import context as helios_ctx
from app.helios import registry as reg
from app.helios import persistence
from app.core.config import settings
from app.core import session_store


def create_project(session: dict, name: str, latitude: float, longitude: float, db: Session) -> dict:
    project_session_id = session["session_id"]
    project_clean_name = name.strip()

    existing_project = db.query(Project).filter(
        func.lower(Project.name) == project_clean_name.lower(),
        Project.session_id == project_session_id,
    ).first()
    if existing_project:
        raise HTTPException(409, "A project with this name already exists")

    project_utc_offset = utc_offset_from_coords(latitude, longitude)

    project = Project(
        session_id=project_session_id,
        name=project_clean_name,
        latitude=latitude,
        longitude=longitude,
        utc_offset=project_utc_offset,
    )
    try:
        db.add(project)
        db.commit()
        db.refresh(project)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to create project")

    # Initialize per-project state (context, registry, caches) in the session
    project_state = session_store.create_project_state(session, project.id)

    if helios_ctx.PYHELIOS_AVAILABLE:
        helios_ctx.reset_context(project_state)
        reg.reset_registry(project_state)
        helios_ctx.get_context(project_state)

    project_state["initialized"] = True

    return {
        "success": True,
        "project_id": project.id,
        "name": project_clean_name,
        "latitude": latitude,
        "longitude": longitude,
        "utc_offset": project_utc_offset,
        "session_id": project_session_id,
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

    session_objects = reg.get_all_objects()
    for obj_id_str, obj in data.get("objects", {}).items():
        session_objects[int(obj_id_str)] = obj

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

    session_objects = reg.get_all_objects()
    for obj_id_str, obj in data.get("objects", {}).items():
        session_objects[int(obj_id_str)] = obj

    return {
        "success": True,
        "project": data.get("metadata", {}),
        "session_id": helios_ctx.session_id,
    }


def list_recent_projects(session: dict, db: Session) -> dict:
    project_session_id = session["session_id"]

    projects = (
        db.query(Project)
        .filter(Project.session_id == project_session_id)
        .order_by(Project.updated_at.desc())
        .all()
    )

    recent = []
    for project in projects:
        snapshot_path = settings.resolved_projects_dir / project.id / "current.xml.gz"
        project_size = snapshot_path.stat().st_size if snapshot_path.exists() else 0
        recent.append(
            {
                "id": project.id,
                "name": project.name,
                "last_updated": project.updated_at,
                "size": project_size,
            }
        )

    return {"projects": recent}


def delete_project(session: dict, project_id: str, db: Session) -> dict:
    project_session_id = session["session_id"]

    project = (
        db.query(Project)
        .filter(
            Project.id == project_id,
            Project.session_id == project_session_id,
        )
        .first()
    )
    if not project:
        raise HTTPException(404, "Project not found")

    try:
        db.delete(project)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to delete project")

    # Clean up in-memory project state if it is loaded
    if project_id in session["projects"]:
        del session["projects"][project_id]

    # Clean up project snapshot files from disk
    project_dir = settings.resolved_projects_dir / project_id
    shutil.rmtree(project_dir, ignore_errors=True)

    return {"success": True, "project_id": project_id}
