from typing import Dict

_session_store: Dict[str, dict] = {}
_active_session_id: str | None = None


def get_session(session_id: str) -> dict | None:
    return _session_store.get(session_id)


def create_session(session_id: str) -> dict:
    _session_store[session_id] = {
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
    return _session_store[session_id]


def session_exists(session_id: str) -> bool:
    return session_id in _session_store


def set_active_session(session_id: str) -> None:
    global _active_session_id
    _active_session_id = session_id


def get_active_session_id() -> str | None:
    return _active_session_id


def get_active_session() -> dict | None:
    if _active_session_id is None:
        return None
    return _session_store.get(_active_session_id)