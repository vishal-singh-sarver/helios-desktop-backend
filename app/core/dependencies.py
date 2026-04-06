from fastapi import Header, HTTPException
from app.core import session_store


def require_session(
    session_id: str | None = Header(default=None, alias="session_id"),
) -> dict:
    """
    Resolves the caller's session dict from the session_id header.
    Creates a new session if this is the first request from this client.
    Use this for endpoints that only need to know WHO the user is
    (e.g. list projects, create project).
    """
    if not session_id or not session_id.strip():
        raise HTTPException(400, "session_id header is required")
    sid = session_id.strip()
    if not session_store.session_exists(sid):
        session_store.create_session(sid)
    return session_store.get_session(sid)


def require_project(
    session_id: str | None = Header(default=None, alias="session_id"),
    project_id: str | None = Header(default=None, alias="project_id"),
) -> dict:
    """
    Resolves the caller's per-project state dict from session_id + project_id headers.
    Use this for endpoints that touch PyHelios — geometry, materials, transforms, etc.
    Returns the project state dict which contains context, registry, and caches.
    """
    if not session_id or not session_id.strip():
        raise HTTPException(400, "session_id header is required")
    if not project_id or not project_id.strip():
        raise HTTPException(400, "project_id header is required")

    sid = session_id.strip()
    pid = project_id.strip()

    if not session_store.session_exists(sid):
        session_store.create_session(sid)

    session = session_store.get_session(sid)

    if not session_store.project_state_exists(session, pid):
        raise HTTPException(404, f"Project {pid} is not loaded. Call /load first.")

    return session_store.get_project_state(session, pid)
