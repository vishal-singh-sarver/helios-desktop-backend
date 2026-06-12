"""
Applies persisted material visualisation values (color_r/g/b, texture_file,
two_sided_heat_transfer) to the PyHelios viewport via material labels.

Label scheme (spec §12.2):
    pm_{material_id}                      shared label — all SYNCED
                                          assignments of one library
                                          material point here, so a single
                                          library edit recolors every
                                          synced geometry at once.
    pm_{material_id}_so_{object_id}       dedicated label for a FROZEN
                                          assignment — its values may
                                          diverge from the library.

Model parameters never touch the viewport. All PyHelios access degrades to
a no-op when the native library is unavailable (headless / CI).
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Datatype,
    MaterialData,
    MaterialType,
    ObjectMaterial,
    ObjectPropertyData,
    PropertyType,
)
from app.helios import context as helios_ctx
from app.helios import registry as reg

VIS_PROPS = ("color_r", "color_g", "color_b", "texture_file", "two_sided_heat_transfer")

# Viewport precedence among a geometry's assigned materials (spec §12.2 /
# open question #6): the Radiation-type material wins; otherwise the most
# recently assigned one.
_PRECEDENCE_TYPE = "Radiation"


def pm_label(material_id: int) -> str:
    return f"pm_{material_id}"


def frozen_label(material_id: int, scenario_object_id: int) -> str:
    return f"pm_{material_id}_so_{scenario_object_id}"


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


def _vis_rows_to_native(rows: list[tuple[str, str | None, str]]) -> dict:
    """[(property, canonical_value, datatype)] → native dict of vis props."""
    from app.services.eav_validation import decode_value
    out = {}
    for prop, value, dt in rows:
        if prop in VIS_PROPS:
            out[prop] = decode_value(value, dt)
    return out


def library_vis_values(db: Session, material_id: int) -> dict:
    """Visualisation values straight from material_data (the library)."""
    rows = (
        db.query(PropertyType.property, MaterialData.value, Datatype.name)
        .join(MaterialData, MaterialData.property_type_id == PropertyType.id)
        .join(Datatype, Datatype.id == PropertyType.datatype_id)
        .filter(MaterialData.project_material_id == material_id)
        .all()
    )
    return _vis_rows_to_native(rows)


def frozen_vis_values(db: Session, scenario_object_id: int, material_id: int) -> dict:
    """Visualisation values from the assignment's frozen rows."""
    rows = (
        db.query(PropertyType.property, ObjectPropertyData.value, Datatype.name)
        .join(ObjectPropertyData, ObjectPropertyData.property_type_id == PropertyType.id)
        .join(Datatype, Datatype.id == PropertyType.datatype_id)
        .filter(
            ObjectPropertyData.scenario_object_id == scenario_object_id,
            ObjectPropertyData.project_material_id == material_id,
        )
        .all()
    )
    return _vis_rows_to_native(rows)


def apply_vis_to_label(pctx, label: str, vis: dict) -> None:
    """Create/refresh one PyHelios material label from native vis values.
    No-op when PyHelios is unavailable."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        return
    ctx = helios_ctx.get_context(pctx)
    from pyhelios.types import RGBAcolor
    if not ctx.doesMaterialExist(label):
        ctx.addMaterial(label)
    r = vis.get("color_r")
    g = vis.get("color_g")
    b = vis.get("color_b")
    if r is not None and g is not None and b is not None:
        ctx.setMaterialColor(label, RGBAcolor(r / 255.0, g / 255.0, b / 255.0, 1.0))
    tex = resolve_texture_path(vis.get("texture_file"))
    # Labels are reused across refreshes — an empty string CLEARS a
    # previously set texture, otherwise removal would never take effect.
    ctx.setMaterialTexture(label, tex or "")
    two_sided = vis.get("two_sided_heat_transfer")
    if two_sided is not None:
        ctx.setMaterialTwosidedFlag(label, 1 if two_sided else 0)


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


def apply_object_appearance(db: Session, pctx, scenario_object) -> None:
    """Re-point a geometry's primitives at the precedence-winning material
    label (shared pm_{id} when synced, dedicated frozen label when not),
    falling back to the default material when nothing is assigned.

    Safe no-op when PyHelios is unavailable, the object has no live
    primitives, or the object was not built in THIS session (stored UUIDs
    are session-scoped — touching them in a fresh context would raise or
    recolor unrelated primitives; hydration repaints it correctly later).
    """
    if not helios_ctx.PYHELIOS_AVAILABLE:
        return
    if scenario_object.id not in pctx.persisted_objects:
        return
    uuids = json.loads(scenario_object.helios_uuids or "[]")
    if not uuids:
        return
    ctx = helios_ctx.get_context(pctx)

    assignments = (
        db.query(ObjectMaterial)
        .filter(ObjectMaterial.scenario_object_id == scenario_object.id)
        .all()
    )
    winner = _winning_assignment(db, assignments)
    if winner is None:
        reg.ensure_default_material(pctx, ctx, uuids)
    elif winner.sync:
        label = pm_label(winner.project_material_id)
        apply_vis_to_label(pctx, label, library_vis_values(db, winner.project_material_id))
        ctx.assignMaterialToPrimitive(uuids, label)
    else:
        label = frozen_label(winner.project_material_id, scenario_object.id)
        apply_vis_to_label(
            pctx, label,
            frozen_vis_values(db, scenario_object.id, winner.project_material_id),
        )
        ctx.assignMaterialToPrimitive(uuids, label)

    invalidate_geometry_caches(pctx)


def cleanup_label(pctx, label: str) -> None:
    """Delete a label if no primitives still use it (e.g. after unfreeze)."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        return
    ctx = helios_ctx.get_context(pctx)
    reg.cleanup_orphaned_materials(pctx, ctx, {label})


def invalidate_geometry_caches(pctx) -> None:
    """The viewport's next binary fetch must see the change."""
    pctx.geometry_cache = {}
    pctx.gpu_geometry_cache = {}
    pctx.gpu_children_cache = {}
