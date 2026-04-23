from fastapi import Header, HTTPException
from app.core.session_store import registry
from app.core.project_context import ProjectContext


def get_session_id(
    session_id: str | None = Header(default=None, alias="session-id"),
) -> str:
    """
    Validate and return the session_id string.
    Ensures the session exists in the store (creates if new).

    Use for: create project, list projects, delete project.
    """
    if not session_id or not session_id.strip():
        raise HTTPException(400, "session_id header is required")
    sid = session_id.strip()
    registry.get_or_create_session(sid)
    return sid


def get_scenario_id(
    scenario_id: str | None = Header(default=None, alias="scenario-id"),
) -> str:
    """
    Validate and return the scenario_id string from the `scenario-id` header.

    Use for: any endpoint that operates on a specific scenario within a
    project (e.g. weather endpoints). The project_id still comes from the
    URL path; this is the extra header that pins us to one scenario.
    """
    if not scenario_id or not scenario_id.strip():
        raise HTTPException(400, "scenario_id header is required")
    return scenario_id.strip()


def get_project_context(
    session_id: str | None = Header(default=None, alias="session-id"),
    project_id: str | None = Header(default=None, alias="project-id"),
) -> ProjectContext:
    """
    Look up and return the live ProjectContext.

    Use for: any endpoint that touches the PyHelios context
    (geometry, materials, transforms, save, etc.)
    """
    if not session_id or not session_id.strip():
        raise HTTPException(400, "session_id header is required")
    if not project_id or not project_id.strip():
        raise HTTPException(400, "project_id header is required")

    sid = session_id.strip()
    pid = project_id.strip()

    pctx = registry.get_context(sid, pid)
    if pctx is None:
        raise HTTPException(404, f"Project {pid} is not loaded")
    return pctx
