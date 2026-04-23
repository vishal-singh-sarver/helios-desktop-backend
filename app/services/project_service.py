from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException
import shutil

from app.db.models import Project, Scenario
from app.core.timezone import utc_offset_from_coords
from app.core.session_store import registry
from app.core.config import settings
from app.helios import context as helios_ctx


def create_project(session_id: str, name: str, latitude: float,
                   longitude: float, db: Session) -> dict:
    clean_name = name.strip()

    # Pre-compute — DB work before touching the store
    existing = db.query(Project).filter(
        func.lower(Project.name) == clean_name.lower(),
        Project.session_id == session_id,
    ).first()
    if existing:
        raise HTTPException(409, "A project with this name already exists")

    utc_offset = utc_offset_from_coords(latitude, longitude)

    project = Project(
        session_id=session_id,
        name=clean_name,
        latitude=latitude,
        longitude=longitude,
        utc_offset=utc_offset,
    )
    try:
        db.add(project)
        db.flush()  # get project.id without committing yet

        # Auto-create the "main" scenario — every project must have >=1
        main_scenario = Scenario(project_id=project.id, name="main")
        db.add(main_scenario)
        db.commit()
        db.refresh(project)
        db.refresh(main_scenario)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to create project")

    # Mutate store — fast, in-place
    pctx = registry.get_or_create_context(session_id, project.id)

    if helios_ctx.PYHELIOS_AVAILABLE:
        pctx.reset()
        pctx.context = helios_ctx.Context()

    pctx.initialized = True

    # Register the main scenario's ScenarioContext so the first request
    # that targets it is instant.
    registry.get_or_create_scenario_context(session_id, project.id, main_scenario.id)

    return {
        "success": True,
        "project_id": project.id,
        "main_scenario_id": main_scenario.id,
        "name": clean_name,
        "latitude": latitude,
        "longitude": longitude,
        "utc_offset": utc_offset,
        "session_id": session_id,
    }


def list_recent_projects(session_id: str, db: Session) -> dict:
    projects = (
        db.query(Project)
        .filter(Project.session_id == session_id)
        .order_by(Project.updated_at.desc())
        .all()
    )

    recent = []
    for project in projects:
        snapshot_path = settings.resolved_projects_dir / project.id / "current.xml.gz"
        project_size = snapshot_path.stat().st_size if snapshot_path.exists() else 0
        recent.append({
            "id": project.id,
            "name": project.name,
            "last_updated": project.updated_at,
            "size": project_size,
        })

    return {"projects": recent}


def delete_project(session_id: str, project_id: str, db: Session) -> dict:
    # Pre-compute — DB work
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.session_id == session_id)
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

    # Mutate store — remove reference, GC handles cleanup.
    # remove_project also wipes all scenarios for this project from memory.
    registry.remove_project(session_id, project_id)

    # Disk cleanup — legacy project snapshot tree + scenario data tree.
    shutil.rmtree(settings.resolved_projects_dir / project_id, ignore_errors=True)
    shutil.rmtree(settings.data_dir / project_id, ignore_errors=True)

    return {"success": True, "project_id": project_id}
