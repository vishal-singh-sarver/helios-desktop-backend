"""
In-memory object registry and material helpers.

All mutable state lives here. Routers import the accessor functions;
they never touch the module-level variables directly.
"""
import time
import threading

# ── Object registry ───────────────────────────────────────────────────────────
_object_registry: dict = {}
_next_object_id: int = int(time.time() * 1000) % 1_000_000
_object_lock = threading.Lock()

# ── Geometry caches (consumed on first read, pre-packed by canopy/stream) ─────
_geometry_cache: dict = {}       # object_id -> bytes  (binary wire format)
_gpu_geometry_cache: dict = {}   # object_id -> bytes  (GPU-ready buffer)
_gpu_children_cache: dict = {}   # object_id -> bytes  (per-child GPU buffers)

# ── Material helpers ──────────────────────────────────────────────────────────
DEFAULT_MATERIAL_COLOR = (0.2, 0.4, 0.8, 1.0)
_default_material_label: str | None = None

_script_object_counter: int = 0


# ── Registry accessors ────────────────────────────────────────────────────────

def _unique_object_name(base_name: str) -> str:
    """Return a unique display name, appending ' 1', ' 2', ... if needed.
    Caller must hold _object_lock."""
    existing = [obj["name"] for obj in _object_registry.values()]
    if base_name not in existing:
        return base_name
    n = 1
    while f"{base_name} {n}" in existing:
        n += 1
    return f"{base_name} {n}"


def register_object(name: str, obj_type: str, primitive_uuids: list,
                    unique_name: bool = False, **extra) -> int:
    global _next_object_id
    with _object_lock:
        display_name = _unique_object_name(name) if unique_name else name
        obj_id = _next_object_id
        _next_object_id += 1
        _object_registry[obj_id] = {
            "name": display_name,
            "type": obj_type,
            "primitive_uuids": primitive_uuids,
            **extra,
        }
    return obj_id


def get_object(object_id: int) -> dict:
    return _object_registry[object_id]


def get_all_objects() -> dict:
    return _object_registry


def delete_object(object_id: int) -> None:
    del _object_registry[object_id]


def reset_registry() -> None:
    global _object_registry, _next_object_id, _default_material_label
    global _geometry_cache, _gpu_geometry_cache, _gpu_children_cache
    global _script_object_counter
    _object_registry = {}
    _next_object_id = int(time.time() * 1000) % 1_000_000
    _default_material_label = None
    _geometry_cache = {}
    _gpu_geometry_cache = {}
    _gpu_children_cache = {}
    _script_object_counter = 0


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
    global _default_material_label
    if _default_material_label is None or not ctx.doesMaterialExist(_default_material_label):
        from pyhelios.types import RGBAcolor
        _default_material_label = next_material_name(ctx)
        ctx.addMaterial(_default_material_label)
        ctx.setMaterialColor(_default_material_label, RGBAcolor(*DEFAULT_MATERIAL_COLOR))
    if uuids:
        ctx.assignMaterialToPrimitive(uuids, _default_material_label)


def cleanup_orphaned_materials(ctx, material_labels: set) -> None:
    """Delete any material in the set that no longer has primitives using it."""
    global _default_material_label
    for label in material_labels:
        if label in ("__default__", ""):
            continue
        try:
            if ctx.doesMaterialExist(label) and not ctx.getPrimitivesUsingMaterial(label):
                ctx.deleteMaterial(label)
                if label == _default_material_label:
                    _default_material_label = None
        except Exception:
            pass
