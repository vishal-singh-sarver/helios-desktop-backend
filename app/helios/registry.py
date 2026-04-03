"""
In-memory object registry and material helpers.

All mutable state lives here. Routers import the accessor functions;
they never touch the module-level variables directly.
"""
import time
import threading
from app.core import session_store

# ── Object registry ───────────────────────────────────────────────────────────
#_object_registry: dict = {}
#_next_object_id: int = int(time.time() * 1000) % 1_000_000
_object_lock = threading.Lock()

# ── Geometry caches (consumed on first read, pre-packed by canopy/stream) ─────
#_geometry_cache: dict = {}       # object_id -> bytes  (binary wire format)
#_gpu_geometry_cache: dict = {}   # object_id -> bytes  (GPU-ready buffer)
#_gpu_children_cache: dict = {}   # object_id -> bytes  (per-child GPU buffers)

# ── Material helpers ──────────────────────────────────────────────────────────
DEFAULT_MATERIAL_COLOR = (0.2, 0.4, 0.8, 1.0)
#_default_material_label: str | None = None

#_script_object_counter: int = 0


# ── Registry accessors ────────────────────────────────────────────────────────
def _get_active_registry_state() -> dict:
    session = session_store.get_active_session()
    if session is None:
        raise RuntimeError("No active session")
    return session
def _unique_object_name(base_name: str) -> str:
    """Return a unique display name, appending ' 1', ' 2', ... if needed.
    Caller must hold _object_lock."""
    state = _get_active_registry_state()
    existing = [obj["name"] for obj in state["registry"].values()]
    if base_name not in existing:
        return base_name
    n = 1
    while f"{base_name} {n}" in existing:
        n += 1
    return f"{base_name} {n}"


def register_object(name: str, obj_type: str, primitive_uuids: list,
                    unique_name: bool = False, **extra) -> int:
    state = _get_active_registry_state()

    with _object_lock:
        display_name = _unique_object_name(name) if unique_name else name

        if state["next_object_id"] is None:
            state["next_object_id"] = int(time.time() * 1000) % 1_000_000

        obj_id = state["next_object_id"]
        state["next_object_id"] += 1

        state["registry"][obj_id] = {
            "name": display_name,
            "type": obj_type,
            "primitive_uuids": primitive_uuids,
            **extra,
        }

    return obj_id


def get_object(object_id: int) -> dict:
    state = _get_active_registry_state()
    return state["registry"][object_id]


def get_all_objects() -> dict:
    state = _get_active_registry_state()
    return state["registry"]


def delete_object(object_id: int) -> None:
    state = _get_active_registry_state()
    del state["registry"][object_id]


def reset_registry() -> None:
    state = _get_active_registry_state()
    state["registry"] = {}
    state["next_object_id"] = int(time.time() * 1000) % 1_000_000
    state["default_material_label"] = None
    state["geometry_cache"] = {}
    state["gpu_geometry_cache"] = {}
    state["gpu_children_cache"] = {}
    state["script_object_counter"] = 0


# ── Material helpers ──────────────────────────────────────────────────────────

def next_material_name(ctx) -> str:
    """Return the next available 'Material.XXX' label."""
    counter = 1
    name = f"Material.{counter:03d}"
    while ctx.doesMaterialExist(name):
        counter += 1
        name = f"Material.{counter:03d}"
    return name


def ensure_default_material(ctx, uuids: list) -> None:
    """Create the default material if absent, then assign it to uuids."""
    state = _get_active_registry_state()

    if state["default_material_label"] is None or not ctx.doesMaterialExist(state["default_material_label"]):
        from pyhelios.types import RGBAcolor
        state["default_material_label"] = next_material_name(ctx)
        ctx.addMaterial(state["default_material_label"])
        ctx.setMaterialColor(state["default_material_label"], RGBAcolor(*DEFAULT_MATERIAL_COLOR))

    if uuids:
        ctx.assignMaterialToPrimitive(uuids, state["default_material_label"])


def cleanup_orphaned_materials(ctx, material_labels: set) -> None:
    """Delete any material in the set that no longer has primitives using it."""
    state = _get_active_registry_state()

    for label in material_labels:
        if label in ("__default__", ""):
            continue
        try:
            if ctx.doesMaterialExist(label) and not ctx.getPrimitivesUsingMaterial(label):
                ctx.deleteMaterial(label)
                if label == state["default_material_label"]:
                    state["default_material_label"] = None
        except Exception:
            pass