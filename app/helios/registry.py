"""
In-memory object registry and material helpers.

All mutable state lives in the session dict.
Functions receive the session dict explicitly — no global lookups.
"""
import time
import threading

_object_lock = threading.Lock()

DEFAULT_MATERIAL_COLOR = (0.2, 0.4, 0.8, 1.0)


# ── Registry accessors ────────────────────────────────────────────────────────

def _unique_object_name(session: dict, base_name: str) -> str:
    """Return a unique display name, appending ' 1', ' 2', ... if needed.
    Caller must hold _object_lock."""
    existing = [obj["name"] for obj in session["registry"].values()]
    if base_name not in existing:
        return base_name
    n = 1
    while f"{base_name} {n}" in existing:
        n += 1
    return f"{base_name} {n}"


def register_object(session: dict, name: str, obj_type: str, primitive_uuids: list,
                    unique_name: bool = False, **extra) -> int:
    with _object_lock:
        display_name = _unique_object_name(session, name) if unique_name else name

        if session["next_object_id"] is None:
            session["next_object_id"] = int(time.time() * 1000) % 1_000_000

        obj_id = session["next_object_id"]
        session["next_object_id"] += 1

        session["registry"][obj_id] = {
            "name": display_name,
            "type": obj_type,
            "primitive_uuids": primitive_uuids,
            **extra,
        }

    return obj_id


def get_object(session: dict, object_id: int) -> dict:
    return session["registry"][object_id]


def get_all_objects(session: dict) -> dict:
    return session["registry"]


def delete_object(session: dict, object_id: int) -> None:
    del session["registry"][object_id]


def reset_registry(session: dict) -> None:
    session["registry"] = {}
    session["next_object_id"] = int(time.time() * 1000) % 1_000_000
    session["default_material_label"] = None
    session["geometry_cache"] = {}
    session["gpu_geometry_cache"] = {}
    session["gpu_children_cache"] = {}
    session["script_object_counter"] = 0


# ── Material helpers ──────────────────────────────────────────────────────────

def next_material_name(ctx) -> str:
    """Return the next available 'Material.XXX' label."""
    counter = 1
    name = f"Material.{counter:03d}"
    while ctx.doesMaterialExist(name):
        counter += 1
        name = f"Material.{counter:03d}"
    return name


def ensure_default_material(session: dict, ctx, uuids: list) -> None:
    """Create the default material if absent, then assign it to uuids."""
    if (session["default_material_label"] is None
            or not ctx.doesMaterialExist(session["default_material_label"])):
        from pyhelios.types import RGBAcolor
        session["default_material_label"] = next_material_name(ctx)
        ctx.addMaterial(session["default_material_label"])
        ctx.setMaterialColor(session["default_material_label"], RGBAcolor(*DEFAULT_MATERIAL_COLOR))

    if uuids:
        ctx.assignMaterialToPrimitive(uuids, session["default_material_label"])


def cleanup_orphaned_materials(session: dict, ctx, material_labels: set) -> None:
    """Delete any material in the set that no longer has primitives using it."""
    for label in material_labels:
        if label in ("__default__", ""):
            continue
        try:
            if ctx.doesMaterialExist(label) and not ctx.getPrimitivesUsingMaterial(label):
                ctx.deleteMaterial(label)
                if label == session["default_material_label"]:
                    session["default_material_label"] = None
        except Exception:
            pass
