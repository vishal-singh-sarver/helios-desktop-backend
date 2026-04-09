"""
In-memory object registry and material helpers.

All mutable state lives in the ProjectContext object.
Functions receive the ProjectContext explicitly — no global lookups.
"""
import time

DEFAULT_MATERIAL_COLOR = (0.2, 0.4, 0.8, 1.0)


# ── Registry accessors ────────────────────────────────────────────────────────

def _unique_object_name(pctx, base_name: str) -> str:
    """Return a unique display name, appending ' 1', ' 2', ... if needed."""
    existing = [obj["name"] for obj in pctx.registry.values()]
    if base_name not in existing:
        return base_name
    n = 1
    while f"{base_name} {n}" in existing:
        n += 1
    return f"{base_name} {n}"


def register_object(pctx, name: str, obj_type: str, primitive_uuids: list,
                    unique_name: bool = False, **extra) -> int:
    display_name = _unique_object_name(pctx, name) if unique_name else name

    obj_id = pctx.next_object_id
    pctx.next_object_id += 1

    pctx.registry[obj_id] = {
        "name": display_name,
        "type": obj_type,
        "primitive_uuids": primitive_uuids,
        **extra,
    }

    return obj_id


def get_object(pctx, object_id: int) -> dict:
    return pctx.registry[object_id]


def get_all_objects(pctx) -> dict:
    return pctx.registry


def delete_object(pctx, object_id: int) -> None:
    del pctx.registry[object_id]


def reset_registry(pctx) -> None:
    pctx.registry = {}
    pctx.next_object_id = int(time.time() * 1000) % 1_000_000
    pctx.default_material_label = None
    pctx.geometry_cache = {}
    pctx.gpu_geometry_cache = {}
    pctx.gpu_children_cache = {}
    pctx.script_object_counter = 0


# ── Material helpers ──────────────────────────────────────────────────────────

def next_material_name(ctx) -> str:
    """Return the next available 'Material.XXX' label."""
    counter = 1
    name = f"Material.{counter:03d}"
    while ctx.doesMaterialExist(name):
        counter += 1
        name = f"Material.{counter:03d}"
    return name


def ensure_default_material(pctx, ctx, uuids: list) -> None:
    """Create the default material if absent, then assign it to uuids."""
    if (pctx.default_material_label is None
            or not ctx.doesMaterialExist(pctx.default_material_label)):
        from pyhelios.types import RGBAcolor
        pctx.default_material_label = next_material_name(ctx)
        ctx.addMaterial(pctx.default_material_label)
        ctx.setMaterialColor(pctx.default_material_label, RGBAcolor(*DEFAULT_MATERIAL_COLOR))

    if uuids:
        ctx.assignMaterialToPrimitive(uuids, pctx.default_material_label)


def cleanup_orphaned_materials(pctx, ctx, material_labels: set) -> None:
    """Delete any material in the set that no longer has primitives using it."""
    for label in material_labels:
        if label in ("__default__", ""):
            continue
        try:
            if ctx.doesMaterialExist(label) and not ctx.getPrimitivesUsingMaterial(label):
                ctx.deleteMaterial(label)
                if label == pctx.default_material_label:
                    pctx.default_material_label = None
        except Exception:
            pass
