from typing import Dict

_session_store: Dict[str, dict] = {}


def get_session(session_id: str) -> dict | None:
    return _session_store.get(session_id)


def create_session(session_id: str) -> dict:
    _session_store[session_id] = {
        "session_id": session_id,
        "projects": {},              # project_id -> project state dict
    }
    return _session_store[session_id]


def session_exists(session_id: str) -> bool:
    return session_id in _session_store


def create_project_state(session: dict, project_id: str) -> dict:
    """Initialize and return a fresh per-project state dict inside the session."""
    session["projects"][project_id] = {
        "project_id": project_id,
        "initialized": False,
        "context": None,
        "wpt": None,
        "plantarch": None,
        "registry": {},
        "next_object_id": None,
        "default_material_label": None,
        "geometry_cache": {},
        "gpu_geometry_cache": {},
        "gpu_children_cache": {},
        "script_object_counter": 0,
    }
    return session["projects"][project_id]


def get_project_state(session: dict, project_id: str) -> dict | None:
    return session["projects"].get(project_id)


def project_state_exists(session: dict, project_id: str) -> bool:
    return project_id in session["projects"]
