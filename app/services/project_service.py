from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException
import shutil

from app.db.models import Project, Scenario, WeatherDataHeader
from app.core.timezone import utc_offset_from_coords
from app.core.session_store import registry
from app.core.config import settings
from app.helios import context as helios_ctx
from app.services.weather_header_service import serialize as serialize_header


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
    # that targets it is instant. Scenario autosave kicks in once the
    # user does their first weather mutation.
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


def get_project_with_scenarios(
    session_id: str, project_id: str, db: Session
) -> dict:
    """Fetch a project + every scenario + each scenario's weather headers.

    Two-level deep tree for the frontend's "project overview" view. Three
    flat queries (project, scenarios, headers) grouped in Python — avoids
    the N+1 of one-query-per-scenario without paying for a JOIN that
    explodes one row per (scenario, header) pair.

    Auth follows the rest of project_service: a project that doesn't
    belong to the calling session returns 404, never 403.
    """
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.session_id == session_id)
        .first()
    )
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    scenarios = (
        db.query(Scenario)
        .filter(Scenario.project_id == project_id)
        .order_by(Scenario.created_at.asc())
        .all()
    )

    scenario_ids = [s.id for s in scenarios]
    headers_by_scenario: dict[str, list[dict]] = {}
    if scenario_ids:
        header_rows = (
            db.query(WeatherDataHeader)
            .filter(WeatherDataHeader.scenario_id.in_(scenario_ids))
            .order_by(WeatherDataHeader.display_order.asc())
            .all()
        )
        for h in header_rows:
            headers_by_scenario.setdefault(h.scenario_id, []).append(serialize_header(h))

    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "latitude": project.latitude,
            "longitude": project.longitude,
            "utc_offset": project.utc_offset,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "scenarios": [
                {
                    "id": s.id,
                    "name": s.name,
                    "has_weather": bool(s.weather_file_path),
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
                    "weather_data_headers": headers_by_scenario.get(s.id, []),
                }
                for s in scenarios
            ],
        }
    }


def _project_disk_size(project_id: str) -> int:
    """Total bytes on disk for a project: the sum of every file under its
    nested folder (scenario context XMLs, weather CSVs, autosave archives).

    Replaces the old single-file `current.xml.gz` lookup, which was dropped
    when project-level autosave was removed — so the old code always
    reported 0.

    Returns 0 when the folder doesn't exist yet (a project with nothing
    persisted). Per-file errors are skipped so a transient lock or a
    vanished file can't break the whole recent-projects listing.
    """
    proj_dir = settings.resolved_projects_dir / project_id
    if not proj_dir.exists():
        return 0
    total = 0
    for entry in proj_dir.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def list_recent_projects(session_id: str, db: Session) -> dict:
    projects = (
        db.query(Project)
        .filter(Project.session_id == session_id)
        .order_by(Project.updated_at.desc())
        .all()
    )

    recent = []
    for project in projects:
        recent.append({
            "id": project.id,
            "name": project.name,
            "last_updated": project.updated_at,
            "size": _project_disk_size(project.id),
        })

    return {"projects": recent}


def update_project(
    session_id: str,
    project_id: str,
    name: str | None,
    latitude: float | None,
    longitude: float | None,
    db: Session,
) -> dict:
    """Partial update of a project. Editable: name, latitude, longitude.

    Auth + scope: the project must belong to the calling session, else 404.

    Behavior:
      - Empty body / all-None is a 200 no-op.
      - Renaming to the project's current name is a no-op (no 409).
      - Name uniqueness is checked case-insensitive within the session,
        excluding the project being updated.
      - When latitude or longitude changes, utc_offset is recomputed from
        the resulting (lat, long) pair.
    """
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.session_id == session_id)
        .first()
    )
    if project is None:
        raise HTTPException(404, "Project not found")

    # Name uniqueness — case-insensitive, excluding self.
    clean_name = name
    if clean_name is not None and clean_name.lower() != project.name.lower():
        clash = (
            db.query(Project.id)
            .filter(
                func.lower(Project.name) == clean_name.lower(),
                Project.session_id == session_id,
                Project.id != project_id,
            )
            .first()
        )
        if clash is not None:
            raise HTTPException(
                409, "A project with this name already exists"
            )

    # Apply changes.
    coords_changed = False
    if clean_name is not None:
        project.name = clean_name
    if latitude is not None and latitude != project.latitude:
        project.latitude = latitude
        coords_changed = True
    if longitude is not None and longitude != project.longitude:
        project.longitude = longitude
        coords_changed = True

    # Recompute utc_offset on coord changes.
    if coords_changed:
        project.utc_offset = utc_offset_from_coords(
            project.latitude, project.longitude
        )

    try:
        db.commit()
        db.refresh(project)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to update project")

    return {
        "success": True,
        "project": {
            "id": project.id,
            "name": project.name,
            "latitude": project.latitude,
            "longitude": project.longitude,
            "utc_offset": project.utc_offset,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
        },
    }


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

    # Disk cleanup — one rmtree handles the entire project tree (scenes,
    # scenarios, weather, archives, everything) since scenarios live nested
    # under the project folder.
    shutil.rmtree(settings.resolved_projects_dir / project_id, ignore_errors=True)

    # Belt-and-suspenders: also remove the legacy `data/<pid>/` top-level
    # folder if it survived a partial migration. No-op when the migration
    # has already cleaned it.
    shutil.rmtree(settings.data_dir / project_id, ignore_errors=True)

    return {"success": True, "project_id": project_id}
