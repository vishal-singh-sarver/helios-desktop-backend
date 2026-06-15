"""
Per-primitive material application (snapshot model).

A material assignment applies the material's CURRENT property values directly
onto the geometry's primitives (decision: snapshot-on-assign — there is no live
library sync). Two channels are handled here:

    color_r/g/b   -> ctx.setPrimitiveColor(uuids, RGBcolor)          (single-valued)
    everything    -> ctx.setPrimitiveData<typed>(uuids, name, value) (model data,
    else                                                              one label each)

The TEXTURE channel (texture_file) is NOT handled here: PyHelios can only tile a
texture (texture_repeat) when the texture is baked into the TileObject at build
time, so scene_object_service._build owns texture, and a texture change forces a
rebuild of that one object. reapply_all_materials therefore never touches the
primitive texture.

Snapshot source: object_property_data rows for the (object, material) pair —
written on assign by scene_object_service._snapshot_frozen for EVERY assignment.
reapply_all_materials re-applies from these rows, so hydration and in-place
regeneration repaint without re-reading the (possibly edited) library.

The single-valued color channel is owned by the precedence-winning assignment
(Radiation wins, else most-recently created). Model-data labels are additive —
each assigned material contributes its own labels.

All PyHelios access degrades to a no-op when the native library is unavailable
(headless / CI) or when the object was not built in THIS session.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Datatype,
    MaterialType,
    ObjectMaterial,
    ObjectPropertyData,
    PropertyType,
)
from app.helios import context as helios_ctx
from app.helios import registry as reg
from app.services.eav_validation import (
    VISUALISATION_PROPERTIES,
    decode_value,
    load_type_properties,
)

# Viewport precedence among a geometry's assigned materials (spec §12.2 / open
# question #6): the Radiation-type material owns the single-valued color/texture
# channel; otherwise the most recently assigned one does.
_PRECEDENCE_TYPE = "Radiation"

# datatype -> the Context.setPrimitiveData<typed> method that stores it.
# boolean has no native bool setter, so it rides UInt as 0/1 (matching Helios'
# own twosided-flag convention). date/time/file/enum/string all store as text.
_DATATYPE_SETTER = {
    "float": "setPrimitiveDataFloat",
    "integer": "setPrimitiveDataInt",
    "boolean": "setPrimitiveDataUInt",
    "string": "setPrimitiveDataString",
    "enum": "setPrimitiveDataString",
    "date": "setPrimitiveDataString",
    "time": "setPrimitiveDataString",
    "file": "setPrimitiveDataString",
}


def resolve_texture_path(value: str | None) -> str | None:
    """Resolve a stored texture_file value to an absolute path.

    plugin:<plugin>/<file> → PyHelios texture library dirs
    uploads/<...>          → settings.data_dir / uploads / <...>
    anything else          → returned as-is (already a path)
    """
    if not value:
        return None
    if value.startswith("plugin:"):
        spec = value[len("plugin:"):]
        plugin, _, filename = spec.partition("/")
        from app.services.material_service import get_texture_dirs
        for name, tex_dir in get_texture_dirs():
            if name == plugin and (tex_dir / filename).exists():
                return str(tex_dir / filename)
        return None
    if value.startswith("uploads/"):
        path = settings.data_dir / value
        return str(path) if Path(path).exists() else None
    return value


# ── Snapshot reads ───────────────────────────────────────────────────────────


def _assignment_snapshot_native(db: Session, so_id: int, material_id: int) -> dict:
    """Snapshot (object_property_data) values for one assignment, as
    {property_name: native value}."""
    rows = (
        db.query(PropertyType.property, ObjectPropertyData.value, Datatype.name)
        .join(ObjectPropertyData, ObjectPropertyData.property_type_id == PropertyType.id)
        .join(Datatype, Datatype.id == PropertyType.datatype_id)
        .filter(
            ObjectPropertyData.scenario_object_id == so_id,
            ObjectPropertyData.project_material_id == material_id,
        )
        .all()
    )
    return {prop: decode_value(value, dt) for prop, value, dt in rows}


def _winning_assignment(db: Session, assignments: list[ObjectMaterial]) -> ObjectMaterial | None:
    if not assignments:
        return None
    type_names = dict(
        db.query(MaterialType.id, MaterialType.materialtype)
        .filter(MaterialType.id.in_({a.material_type_id for a in assignments}))
        .all()
    )
    for a in assignments:
        if type_names.get(a.material_type_id) == _PRECEDENCE_TYPE:
            return a
    return max(assignments, key=lambda a: a.created_at or "")


# ── Per-primitive writers ────────────────────────────────────────────────────


def _reset_color(ctx, uuids: list[int]) -> None:
    """Reset the color channel to the default (no material). The precedence
    winner overrides afterward. Texture is owned by the build path, not here."""
    from pyhelios.types import RGBcolor
    ctx.setPrimitiveColor(uuids, RGBcolor(*reg.DEFAULT_MATERIAL_COLOR[:3]))


def _apply_color(ctx, uuids: list[int], values: dict) -> None:
    """Apply one material's color (0..255 ints → RGBcolor)."""
    r, g, b = values.get("color_r"), values.get("color_g"), values.get("color_b")
    if r is not None or g is not None or b is not None:
        from pyhelios.types import RGBcolor
        ctx.setPrimitiveColor(
            uuids, RGBcolor((r or 0) / 255.0, (g or 0) / 255.0, (b or 0) / 255.0)
        )


def _apply_model_data(ctx, uuids: list[int], defs: dict, values: dict) -> None:
    """Apply every non-visualisation property as typed primitive data."""
    for name, prop in defs.items():
        if name in VISUALISATION_PROPERTIES:
            continue
        value = values.get(name)
        if value is None:
            continue
        setter_name = _DATATYPE_SETTER.get(prop.datatype)
        if setter_name is None:
            continue
        setter = getattr(ctx, setter_name)
        if prop.datatype == "boolean":
            setter(uuids, name, 1 if value else 0)
        elif prop.datatype == "float":
            setter(uuids, name, float(value))
        elif prop.datatype == "integer":
            setter(uuids, name, int(value))
        else:
            setter(uuids, name, str(value))


# ── Public entry points ──────────────────────────────────────────────────────


def clear_material_from_primitives(db: Session, pctx, so, material_type_id: int) -> None:
    """Remove a material type's model-data labels from the object's primitives
    (the color/texture channel is reset by reapply_all_materials). Call BEFORE
    deleting an assignment, with the OLD material type, so stale primitive data
    never lingers. No-op when headless or the object isn't built this session."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        return
    if so.id not in pctx.persisted_objects:
        return
    uuids = json.loads(so.helios_uuids or "[]")
    if not uuids:
        return
    ctx = helios_ctx.get_context(pctx)
    defs = load_type_properties(db, material_type_id=material_type_id)
    for name in defs:
        if name in VISUALISATION_PROPERTIES:
            continue
        try:
            ctx.clearPrimitiveData(uuids, name)
        except Exception:
            pass    # label may not be present on these primitives
    invalidate_geometry_caches(pctx)


def reapply_all_materials(db: Session, pctx, so) -> None:
    """Re-apply every current assignment's snapshot onto the object's primitives.

    Resets the single-valued color/texture channel, applies every assignment's
    model-data labels, then lets the precedence winner own color/texture. Falls
    back to the default color when nothing is assigned. Safe no-op when headless,
    the object isn't built this session, or it has no live primitives."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        return
    if so.id not in pctx.persisted_objects:
        return
    uuids = json.loads(so.helios_uuids or "[]")
    if not uuids:
        return
    ctx = helios_ctx.get_context(pctx)

    assignments = (
        db.query(ObjectMaterial)
        .filter(ObjectMaterial.scenario_object_id == so.id)
        .all()
    )

    _reset_color(ctx, uuids)

    for om in assignments:
        defs = load_type_properties(db, material_type_id=om.material_type_id)
        values = _assignment_snapshot_native(db, so.id, om.project_material_id)
        _apply_model_data(ctx, uuids, defs, values)

    winner = _winning_assignment(db, assignments)
    if winner is not None:
        _apply_color(
            ctx, uuids, _assignment_snapshot_native(db, so.id, winner.project_material_id)
        )

    invalidate_geometry_caches(pctx)


def invalidate_geometry_caches(pctx) -> None:
    """The viewport's next binary fetch must see the change."""
    pctx.geometry_cache = {}
    pctx.gpu_geometry_cache = {}
    pctx.gpu_children_cache = {}
