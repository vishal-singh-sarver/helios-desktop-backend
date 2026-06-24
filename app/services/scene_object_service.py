"""
Persisted scene objects (milestone 2, spec §5/§6/§8/§12).

DB-first write-through into the per-project PyHelios context:

    POST .../objects   one DB transaction (scenario_object + intrinsic
                       object_property_data rows) → in-memory ground build
                       as a TileObject → object id + UUIDs written back to
                       scenario_object.ctx_object_id / helios_uuids.

The ground build (spec §12.1, decision #1): ALWAYS a compound TileObject
    ctx.addTileObject(center, size, rotation, subdiv,
                      texturefile?, texture_repeat | color)
so a stable ctx_object_id exists for in-place edits (translate/rotate/scale/
subdivision). The winning material's texture (if any) is baked in with the
intrinsic texture_x/texture_y repeat; a texture change rebuilds the object.
Stored ctx_object_id/UUIDs are session-scoped — `ensure_hydrated` rebuilds a
scenario's objects after a restart and rewrites the columns.

Material assignment applies color + model properties per-primitive (snapshot
model) through app/services/material_apply.py.

All PyHelios work degrades to a DB-only operation when the native library
is unavailable (helios_uuids stays []; getObjectGeometry returns 503).
"""
from __future__ import annotations

import functools
import json
import math

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.session_store import registry as session_registry
from app.db.models import (
    Datatype,
    MaterialData,
    MaterialType,
    ModelType,
    ObjectGroup,
    ObjectMaterial,
    ObjectPropertyData,
    ObjectType,
    ProjectMaterial,
    PropertyType,
    Scenario,
    ScenarioModel,
    ScenarioObject,
    ScenarioObjectModel,
    _now,
)
from app.helios import context as helios_ctx
from app.helios import registry as reg
from app.helios.persistence import load_scenario_snapshot, trigger_scenario_autosave
from app.services import material_apply
from app.services.eav_validation import (
    REQUIRED_OBJECT_PROPERTIES,
    api_error,
    decode_value,
    load_type_properties,
    next_default_name,
    project_or_404,
    validate_cross_field,
    validate_name,
    validate_properties,
)

_GROUP_PREFIX = "Group"

# Object-data key stamped on every built object so hydration can re-map a loaded
# compound object back to its DB scenario_object.id (objIDs/UUIDs are NOT
# preserved across writeXML/loadXML, but object data is).
_SO_ID_TAG = "helios_gui_so_id"


# ── Scope / lookup helpers ───────────────────────────────────────────────────


def _resolve_scope(db: Session, session_id: str, project_id: str, scenario_id: str) -> Scenario:
    project_or_404(db, session_id, project_id)
    scenario = (
        db.query(Scenario)
        .filter(Scenario.id == scenario_id, Scenario.project_id == project_id)
        .first()
    )
    if scenario is None:
        raise api_error(404, "SCENARIO_NOT_FOUND",
                        f"Scenario {scenario_id} not found in this project")
    return scenario


def _sctx(session_id: str, project_id: str, scenario_id: str):
    """Per-scenario context (the SAME instance weather uses), with its PyHelios
    Context created + snapshot loaded on first use. Geometry and weather share
    this context → both persist to scenarios/<sid>/context_file/context.xml.
    Caller has already validated scope via _resolve_scope.

    The scenario lock is held ONLY to create the context (idempotent get-or-
    create), then released — it must NOT be held across return (the plain Lock
    is not re-entrant; mutation blocks re-acquire it separately)."""
    with session_registry._scenario_lock:
        sctx = session_registry.get_or_create_scenario_context(session_id, project_id, scenario_id)
        if helios_ctx.PYHELIOS_AVAILABLE and sctx.context is None:
            sctx.context = helios_ctx.Context()
            load_scenario_snapshot(sctx)
    return sctx


def _with_scenario_lock(fn):
    """Serialize a context-touching helper under the (re-entrant) scenario lock.
    Geometry + weather now share one sctx.context, so every read/mutation/
    serialize of it must be serialized. RLock makes the nested calls safe
    (e.g. _apply_assignment_change → _rebuild → _teardown + _build)."""
    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        with session_registry._scenario_lock:
            return fn(*args, **kwargs)
    return _wrapper


def _object_or_404(db: Session, scenario_id: str, object_id: int) -> ScenarioObject:
    so = (
        db.query(ScenarioObject)
        .filter(ScenarioObject.id == object_id, ScenarioObject.scenario_id == scenario_id)
        .first()
    )
    if so is None:
        raise api_error(404, "GEOMETRY_NOT_FOUND", f"Geometry {object_id} not found")
    return so


def _group_or_404(db: Session, scenario_id: str, group_id: int) -> ObjectGroup:
    grp = (
        db.query(ObjectGroup)
        .filter(ObjectGroup.id == group_id, ObjectGroup.scenario_id == scenario_id)
        .first()
    )
    if grp is None:
        raise api_error(404, "GROUP_NOT_FOUND", f"Group {group_id} not found")
    return grp


def _object_names_lower(db: Session, scenario_id: str) -> set[str]:
    """Object names taken within ONE scenario (uniqueness is per-scenario —
    migration 019; a name may repeat across scenarios of a project)."""
    rows = db.query(ScenarioObject.name).filter(ScenarioObject.scenario_id == scenario_id).all()
    return {r[0].lower() for r in rows}


def _group_names_lower(db: Session, project_id: str) -> set[str]:
    rows = db.query(ObjectGroup.name).filter(ObjectGroup.project_id == project_id).all()
    return {r[0].lower() for r in rows}


# ── Value access ─────────────────────────────────────────────────────────────


def _intrinsic_canonical(db: Session, so_id: int) -> dict[str, str | None]:
    rows = (
        db.query(PropertyType.property, ObjectPropertyData.value)
        .join(ObjectPropertyData, ObjectPropertyData.property_type_id == PropertyType.id)
        .filter(
            ObjectPropertyData.scenario_object_id == so_id,
            ObjectPropertyData.project_material_id.is_(None),
        )
        .all()
    )
    return dict(rows)


def _intrinsic_native(db: Session, so_id: int) -> dict:
    rows = (
        db.query(PropertyType.property, ObjectPropertyData.value, Datatype.name)
        .join(ObjectPropertyData, ObjectPropertyData.property_type_id == PropertyType.id)
        .join(Datatype, Datatype.id == PropertyType.datatype_id)
        .filter(
            ObjectPropertyData.scenario_object_id == so_id,
            ObjectPropertyData.project_material_id.is_(None),
        )
        .all()
    )
    return {prop: decode_value(value, dt) for prop, value, dt in rows}


def _upsert_intrinsic(db: Session, so_id: int, canonical: dict[str, str | None], defs) -> None:
    by_pt = {defs[name].property_type_id: value for name, value in canonical.items()}
    existing = {
        row.property_type_id: row
        for row in db.query(ObjectPropertyData)
        .filter(
            ObjectPropertyData.scenario_object_id == so_id,
            ObjectPropertyData.project_material_id.is_(None),
            ObjectPropertyData.property_type_id.in_(by_pt.keys()),
        )
        .all()
    }
    for pt_id, value in by_pt.items():
        row = existing.get(pt_id)
        if value is None:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(ObjectPropertyData(scenario_object_id=so_id, project_material_id=None,
                                      property_type_id=pt_id, value=value))
        else:
            row.value = value


def _material_values_canonical(db: Session, material_id: int) -> dict[int, str]:
    rows = (
        db.query(MaterialData.property_type_id, MaterialData.value)
        .filter(MaterialData.project_material_id == material_id)
        .all()
    )
    return {pt: v for pt, v in rows if v is not None}


def _frozen_rows(db: Session, so_id: int, material_id: int) -> list[ObjectPropertyData]:
    return (
        db.query(ObjectPropertyData)
        .filter(
            ObjectPropertyData.scenario_object_id == so_id,
            ObjectPropertyData.project_material_id == material_id,
        )
        .all()
    )


def _snapshot_frozen(db: Session, so_id: int, material_id: int) -> None:
    """Copy the material's CURRENT library values into the assignment's snapshot
    rows (object_property_data). Run for EVERY assignment regardless of sync —
    these rows are the single source of truth the viewport re-applies from, so
    later library edits never propagate (decision #1: snapshot-on-assign)."""
    for row in _frozen_rows(db, so_id, material_id):
        db.delete(row)
    db.flush()
    for pt_id, value in _material_values_canonical(db, material_id).items():
        db.add(ObjectPropertyData(scenario_object_id=so_id, project_material_id=material_id,
                                  property_type_id=pt_id, value=value))
    # The session runs with autoflush=False — flush so follow-up queries in
    # the same request (e.g. freeze-and-edit) see the snapshot rows.
    db.flush()


# ── Viewport build (spec §12.1) ──────────────────────────────────────────────


def _winner_texture_path(db: Session, so: ScenarioObject) -> str | None:
    """Resolved texture file of the precedence-winning assignment, or None.
    Texture is baked into the TileObject at build time (PyHelios has no in-place
    texture/repeat setter), so this drives the rebuild-vs-reapply decision."""
    assignments = (
        db.query(ObjectMaterial)
        .filter(ObjectMaterial.scenario_object_id == so.id)
        .all()
    )
    winner = material_apply._winning_assignment(db, assignments)
    if winner is None:
        return None
    values = material_apply._assignment_snapshot_native(db, so.id, winner.project_material_id)
    return material_apply.resolve_texture_path(values.get("texture_file"))


@_with_scenario_lock
def _teardown(sctx, so: ScenarioObject) -> None:
    """Remove the compound object (and its primitives) + runtime entries from
    the live context. ctx_object_id on the DB row is cleared (best-effort
    session cache; never trusted without a persisted_objects membership check)."""
    ctx_object_id = sctx.ctx_objects.pop(so.id, None)
    obj_id = sctx.persisted_objects.pop(so.id, None)
    if obj_id is not None and obj_id in sctx.registry:
        reg.delete_object(sctx, obj_id)
    if helios_ctx.PYHELIOS_AVAILABLE and sctx.context is not None:
        try:
            if ctx_object_id is not None and sctx.context.doesObjectExist(ctx_object_id):
                sctx.context.deleteObject(ctx_object_id)
            else:
                uuids = json.loads(so.helios_uuids or "[]")
                if uuids:
                    sctx.context.deletePrimitive(uuids)
        except Exception:
            pass    # object/primitives may already be gone (fresh context)
    so.ctx_object_id = None
    material_apply.invalidate_geometry_caches(sctx)


@_with_scenario_lock
def _build(db: Session, sctx, so: ScenarioObject) -> list[int]:
    """Build the ground as a TileObject from its intrinsic properties, capture
    the compound-object id, persist the primitive UUIDs, then apply materials.

    Geometry is ALWAYS a TileObject (decision #1) — even untextured — so a stable
    ctx_object_id exists for in-place edits. The winning material's texture (if
    any) is baked in with the intrinsic texture_x/texture_y repeat; color and
    model properties are applied per-primitive afterward.

    On a PyHelios failure the row is left consistent (helios_uuids=[],
    ctx_object_id=None, not registered) and BUILD_FAILED is raised — callers
    decide whether to compensate (create) or surface it (rebuild); hydration
    skips and retries later."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        so.helios_uuids = "[]"
        so.ctx_object_id = None
        db.commit()
        return []

    props = _intrinsic_native(db, so.id)
    texture_path = _winner_texture_path(db, so)

    try:
        ctx = helios_ctx.get_context(sctx)
        from pyhelios.types import RGBcolor, SphericalCoord, int2, vec2, vec3

        center = vec3(props.get("position_x") or 0,
                      props.get("position_y") or 0,
                      props.get("position_z") or 0)
        size = vec2(props.get("length") or 1, props.get("breadth") or 1)
        subdiv = int2(int(props.get("resolution_x") or 1), int(props.get("resolution_y") or 1))
        # Rotate by azimuth about z (in-place spin) before translating to center.
        rotation = SphericalCoord(1, 0, math.radians(float(props.get("rotation_z") or 0)))
        repeat = int2(int(props.get("texture_x") or 1), int(props.get("texture_y") or 1))

        if texture_path:
            ctx_object_id = ctx.addTileObject(
                center=center, size=size, rotation=rotation, subdiv=subdiv,
                texturefile=texture_path, texture_repeat=repeat,
            )
        else:
            ctx_object_id = ctx.addTileObject(
                center=center, size=size, rotation=rotation, subdiv=subdiv,
                texturefile=material_apply._DEFAULT_GROUND_TEXTURE, texture_repeat=subdiv,
            )
        uuids = list(ctx.getObjectPrimitiveUUIDs(ctx_object_id))
        # Tag the object with its DB id so hydration can re-map it back to this
        # row after a loadXML (object data round-trips; objIDs/UUIDs do not).
        ctx.setObjectDataUInt(ctx_object_id, _SO_ID_TAG, so.id)
    except HTTPException:
        raise
    except Exception:
        so.helios_uuids = "[]"
        so.ctx_object_id = None
        db.commit()
        raise api_error(500, "BUILD_FAILED",
                        "Unable to create geometry. Please try again")

    obj_id = reg.register_object(
        sctx, so.name, "ground", uuids,
        scenario_object_id=so.id,
        ctx_object_id=ctx_object_id,
        # The texture baked into THIS build — the rebuild-vs-reapply check.
        built_texture=texture_path,
    )
    sctx.persisted_objects[so.id] = obj_id
    sctx.ctx_objects[so.id] = ctx_object_id

    so.helios_uuids = json.dumps(uuids)
    so.ctx_object_id = ctx_object_id
    db.commit()

    material_apply.reapply_all_materials(db, sctx, so)
    _autosave(sctx)
    return uuids


@_with_scenario_lock
def _autosave(sctx) -> None:
    """Persist the scenario's context (geometry + weather) to context.xml after
    a geometry change. Locked so writeXML never serializes a context mid-mutation.
    Best-effort — `trigger_scenario_autosave` no-ops when headless and
    swallows/logs any writeXML failure (never fails the request)."""
    trigger_scenario_autosave(sctx)


def _rebuild(db: Session, sctx, so: ScenarioObject) -> None:
    _teardown(sctx, so)
    _build(db, sctx, so)


@_with_scenario_lock
def _apply_assignment_change(db: Session, sctx, so: ScenarioObject,
                             cleared_type_id: int | None = None) -> None:
    """Repaint after an assignment add/remove/edit. Rebuild when the winning
    texture changed (texture is baked into the TileObject), else clear the
    removed/edited material type's stale labels and re-apply color/model data
    in place. Safe no-op when headless or the object isn't built this session."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        return
    if so.id not in sctx.persisted_objects:
        return    # hydration will paint it correctly later
    desired = _winner_texture_path(db, so)
    obj_id = sctx.persisted_objects.get(so.id)
    built = sctx.registry.get(obj_id, {}).get("built_texture") if obj_id is not None else None
    if (desired or None) != (built or None):
        _rebuild(db, sctx, so)    # fresh primitives; _build re-applies everything
        return
    if cleared_type_id is not None:
        material_apply.clear_material_from_primitives(db, sctx, so, cleared_type_id)
    material_apply.reapply_all_materials(db, sctx, so)
    _autosave(sctx)   # rebuild branch already autosaved via _build + returned


# Intrinsic Ground properties that map to an in-place PyHelios object op.
_TRANSLATE_KEYS = {"position_x", "position_y", "position_z"}
_SCALE_KEYS = {"length", "breadth"}
_RESOLUTION_KEYS = {"resolution_x", "resolution_y"}
_INPLACE_KEYS = _TRANSLATE_KEYS | _SCALE_KEYS | _RESOLUTION_KEYS | {"rotation_z"}
# texture_x/texture_y (repeat) are baked into the TileObject — no in-place setter.
_RECREATE_KEYS = {"texture_x", "texture_y"}


@_with_scenario_lock
def _apply_intrinsic_change(db: Session, sctx, so: ScenarioObject,
                            old_vals: dict, new_vals: dict, changed: set[str]) -> None:
    """Apply an intrinsic-property change WITHOUT a full rebuild where possible
    (decision #6): position→translateObject, length/breadth→scaleObject,
    rotation_z→rotateObject, resolution→setTileObjectSubdivisionCount. texture
    repeat (and any non-decomposable change) recreates the one object. Falls back
    to a rebuild whenever the live object id is missing/dead."""
    if not helios_ctx.PYHELIOS_AVAILABLE or so.id not in sctx.persisted_objects:
        return
    ctx = helios_ctx.get_context(sctx)
    ctx_object_id = sctx.ctx_objects.get(so.id)
    if ctx_object_id is None or not ctx.doesObjectExist(ctx_object_id):
        _rebuild(db, sctx, so)
        return

    # texture-repeat or a non-decomposable key → recreate this object only.
    if (changed & _RECREATE_KEYS) or (changed - _INPLACE_KEYS - _RECREATE_KEYS):
        _rebuild(db, sctx, so)
        return

    from pyhelios.types import int2, vec3

    if changed & _TRANSLATE_KEYS:
        dx = (new_vals.get("position_x") or 0) - (old_vals.get("position_x") or 0)
        dy = (new_vals.get("position_y") or 0) - (old_vals.get("position_y") or 0)
        dz = (new_vals.get("position_z") or 0) - (old_vals.get("position_z") or 0)
        if dx or dy or dz:
            ctx.translateObject(ctx_object_id, vec3(dx, dy, dz))

    if "rotation_z" in changed:
        delta = (new_vals.get("rotation_z") or 0) - (old_vals.get("rotation_z") or 0)
        if delta:
            # Rotate about the object's own center (origin=None default).
            ctx.rotateObject(ctx_object_id, math.radians(delta), "z")

    if changed & _SCALE_KEYS:
        # scaleObject scales along WORLD axes; on a z-rotated tile that would
        # shear it, so recreate when the (post-change) tile is rotated. Apply
        # rotation first (above) so a rotate-to-zero is already in effect here.
        if (new_vals.get("rotation_z") or 0) % 360 != 0:
            _rebuild(db, sctx, so)
            return
        old_l = old_vals.get("length") or 1
        old_b = old_vals.get("breadth") or 1
        sx = (new_vals.get("length") or 1) / old_l if old_l else 1
        sy = (new_vals.get("breadth") or 1) / old_b if old_b else 1
        if sx != 1 or sy != 1:
            ctx.scaleObject(ctx_object_id, vec3(sx, sy, 1), about_center=True)

    if changed & _RESOLUTION_KEYS:
        rx = int(new_vals.get("resolution_x") or 1)
        ry = int(new_vals.get("resolution_y") or 1)
        ctx.setTileObjectSubdivisionCount(ctx_object_id, int2(rx, ry))
        # Subdivision REGENERATES the tile's primitives — re-read UUIDs and
        # re-apply materials. If the object id didn't survive, recreate.
        if not ctx.doesObjectExist(ctx_object_id):
            _rebuild(db, sctx, so)
            return
        uuids = list(ctx.getObjectPrimitiveUUIDs(ctx_object_id))
        so.helios_uuids = json.dumps(uuids)
        db.commit()
        material_apply.reapply_all_materials(db, sctx, so)

    material_apply.invalidate_geometry_caches(sctx)
    _autosave(sctx)


@_with_scenario_lock
def ensure_hydrated(db: Session, sctx, scenario_id: str) -> None:
    """Make this scenario's geometry live in its own context, exactly once.

    The scenario's `context.xml` (loaded by `_sctx` via `load_scenario_snapshot`)
    already holds the saved geometry + materials + weather. Re-map each loaded
    compound object back to its DB row via the `helios_gui_so_id` object-data tag
    — objIDs/UUIDs are NOT preserved across the XML round-trip, so refresh the
    session-scoped `ctx_object_id`/`helios_uuids`. There is NO material repaint:
    color (material label), model data, and texture all came back with the XML.

    DB rows with no tagged object in the XML are built from the DB (the normal
    `_build` path — applies materials + tags + autosaves). Tagged objects with no
    DB row are deleted (the DB owns the object set). Marked hydrated only after
    the pass; per-object build failures leave the row DB-only and are retried."""
    if sctx.hydrated:
        return
    if not helios_ctx.PYHELIOS_AVAILABLE:
        sctx.hydrated = True
        return

    ctx = helios_ctx.get_context(sctx)
    rows = (
        db.query(ScenarioObject)
        .filter(ScenarioObject.scenario_id == scenario_id)
        .order_by(ScenarioObject.created_at, ScenarioObject.id)
        .all()
    )
    by_id = {so.id: so for so in rows}
    mapped: set[int] = set()

    # Re-map objects restored from context.xml back to their DB rows via the tag.
    try:
        loaded_obj_ids = list(ctx.getAllObjectIDs())
    except Exception:
        loaded_obj_ids = []
    for obj_id in loaded_obj_ids:
        try:
            if not ctx.doesObjectDataExist(obj_id, _SO_ID_TAG):
                continue
            so_id = int(ctx.getObjectData(obj_id, _SO_ID_TAG))
        except Exception:
            continue
        so = by_id.get(so_id)
        if so is None:
            try:
                ctx.deleteObject(obj_id)   # orphan: DB owns the object set
            except Exception:
                pass
            continue
        uuids = list(ctx.getObjectPrimitiveUUIDs(obj_id))
        reg_id = reg.register_object(
            sctx, so.name, "ground", uuids,
            scenario_object_id=so.id, ctx_object_id=obj_id,
            built_texture=_winner_texture_path(db, so),
        )
        sctx.persisted_objects[so.id] = reg_id
        sctx.ctx_objects[so.id] = obj_id
        so.helios_uuids = json.dumps(uuids)
        so.ctx_object_id = obj_id
        mapped.add(so.id)

    # DB rows missing from the XML → build from DB (applies materials + tags).
    for so in rows:
        if so.id not in mapped and so.id not in sctx.persisted_objects:
            try:
                _build(db, sctx, so)
            except HTTPException:
                continue    # leave DB-only; retried by rebuild-first paths

    db.commit()
    sctx.hydrated = True


# ── Serialization ────────────────────────────────────────────────────────────


def _parse_models_map(db: Session, models: dict) -> dict[int, bool]:
    """Validate a {"<model_type_id>": bool} map against the model catalog."""
    if not isinstance(models, dict):
        raise api_error(400, "DATATYPE_MISMATCH", "models must be an object")
    parsed: dict[int, bool] = {}
    for key, value in models.items():
        try:
            model_id = int(key)
        except (TypeError, ValueError):
            raise api_error(400, "DATATYPE_MISMATCH",
                            f"models key '{key}' must be a model_type id")
        if not isinstance(value, bool):
            raise api_error(400, "DATATYPE_MISMATCH",
                            f"models['{key}'] must be a boolean")
        parsed[model_id] = value
    if parsed:
        known = {
            r[0] for r in db.query(ModelType.id)
            .filter(ModelType.id.in_(parsed.keys()))
            .all()
        }
        for model_id in parsed:
            if model_id not in known:
                raise api_error(404, "MODEL_TYPE_NOT_FOUND",
                                f"model_type_id {model_id} not found")
    return parsed


def _top_level_model_ids(db: Session) -> list[int]:
    return [r[0] for r in db.query(ModelType.id).filter(ModelType.parent_id.is_(None)).all()]


def _set_object_models(db: Session, so_id: int, states: dict[int, bool]) -> None:
    """Per-model participation upsert (absent row = enabled): enabled → delete
    the disable row; disabled → upsert a ScenarioObjectModel(enabled=0) row."""
    for model_id, enabled in states.items():
        row = db.get(ScenarioObjectModel, (so_id, model_id))
        if enabled:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(ScenarioObjectModel(scenario_object_id=so_id,
                                       model_type_id=model_id, enabled=0))
        else:
            row.enabled = 0


def _apply_visibility(db: Session, so: ScenarioObject, payload: dict) -> None:
    """Persist a partial visibility update (caller commits).

    viewport → scenario_object.visible (independent).
    render ↔ models are COUPLED — render is the master switch over the top-level
    models: render=true enables all, render=false disables all; an explicit
    models map then applies granular overrides; finally render_enabled is
    recomputed as OR(model states) so it always reflects "any model enabled"."""
    if not isinstance(payload, dict):
        raise api_error(400, "DATATYPE_MISMATCH", "visibility must be an object")

    if "viewport" in payload:
        if not isinstance(payload["viewport"], bool):
            raise api_error(400, "DATATYPE_MISMATCH", "visibility.viewport must be a boolean")
        so.visible = 1 if payload["viewport"] else 0

    has_render = "render" in payload
    if has_render and not isinstance(payload["render"], bool):
        raise api_error(400, "DATATYPE_MISMATCH", "visibility.render must be a boolean")
    models = _parse_models_map(db, payload.get("models") or {})

    if not has_render and not models:
        return    # nothing model-related to apply; render untouched

    top_level = _top_level_model_ids(db)
    # Merge master switch + granular overrides into ONE state map so each model
    # id is upserted exactly once. (Two separate _set_object_models calls would
    # double-insert: the session is autoflush=False, so the second call's
    # db.get() can't see the first call's still-pending rows → duplicate INSERT
    # → UNIQUE violation on (scenario_object_id, model_type_id).)
    states: dict[int, bool] = {mid: payload["render"] for mid in top_level} if has_render else {}
    states.update(models)   # granular overrides the master switch
    if states:
        _set_object_models(db, so.id, states)

    # Recompute render = OR(top-level model states). Flush first so the freshly
    # written rows are visible to the query.
    db.flush()
    disabled = {
        r[0] for r in db.query(ScenarioObjectModel.model_type_id)
        .filter(ScenarioObjectModel.scenario_object_id == so.id,
                ScenarioObjectModel.enabled == 0)
        .all()
    }
    so.render_enabled = 1 if any(mid not in disabled for mid in top_level) else 0


def _visibility_of(db: Session, so: ScenarioObject) -> dict:
    """DB-backed visibility: full map over the top-level model catalog plus
    any explicitly-set submodel entries (absent row = enabled)."""
    explicit = {
        str(model_id): bool(enabled)
        for model_id, enabled in db.query(
            ScenarioObjectModel.model_type_id, ScenarioObjectModel.enabled)
        .filter(ScenarioObjectModel.scenario_object_id == so.id)
        .all()
    }
    models = {
        str(r[0]): True
        for r in db.query(ModelType.id).filter(ModelType.parent_id.is_(None)).all()
    }
    models.update(explicit)
    return {
        "viewport": bool(so.visible),
        "render": bool(so.render_enabled),
        "models": models,
    }


def _assignment_payload(db: Session, so: ScenarioObject, om: ObjectMaterial) -> dict:
    pm = db.get(ProjectMaterial, om.project_material_id)
    mt = db.get(MaterialType, om.material_type_id)
    defs = load_type_properties(db, material_type_id=om.material_type_id)

    def _native(rows):
        return {prop: decode_value(value, dt) for prop, value, dt in rows}

    library_rows = (
        db.query(PropertyType.property, MaterialData.value, Datatype.name)
        .join(MaterialData, MaterialData.property_type_id == PropertyType.id)
        .join(Datatype, Datatype.id == PropertyType.datatype_id)
        .filter(MaterialData.project_material_id == om.project_material_id)
        .all()
    )
    library = _native(library_rows)

    payload = {
        "object_id": so.id,
        "material_id": om.project_material_id,
        "name": pm.name if pm else None,
        "material_type_id": om.material_type_id,
        "material_type": mt.materialtype if mt else None,
        "sync": bool(om.sync),
        "source": "library" if om.sync else "frozen",
    }
    if om.sync:
        values = library
    else:
        frozen_db_rows = (
            db.query(PropertyType.property, ObjectPropertyData.value, Datatype.name)
            .join(ObjectPropertyData, ObjectPropertyData.property_type_id == PropertyType.id)
            .join(Datatype, Datatype.id == PropertyType.datatype_id)
            .filter(
                ObjectPropertyData.scenario_object_id == so.id,
                ObjectPropertyData.project_material_id == om.project_material_id,
            )
            .all()
        )
        values = _native(frozen_db_rows)
        if any(values.get(k) != library.get(k) for k in defs):
            payload["library_drift"] = True
    payload["properties"] = {name: values.get(name) for name in defs}
    return payload


def serialize_object(db: Session, sctx, so: ScenarioObject,
                     include_materials: bool = True) -> dict:
    ot = db.get(ObjectType, so.object_type_id)
    defs = load_type_properties(db, object_type_id=so.object_type_id)
    values = _intrinsic_native(db, so.id)
    obj_id = sctx.persisted_objects.get(so.id)
    # ctx_object_id is session-scoped — only meaningful when the object is built
    # in THIS session; emit null otherwise so a stale DB value never leaks.
    ctx_object_id = sctx.ctx_objects.get(so.id) if so.id in sctx.persisted_objects else None
    out = {
        "id": so.id,
        "name": so.name,
        "object_type_id": so.object_type_id,
        "object_type": ot.object if ot else None,
        "scenario_id": so.scenario_id,
        "group_id": so.group_id,
        "created_at": so.created_at,
        "updated_at": so.updated_at,
        "properties": {name: values.get(name) for name in defs},
        "visibility": _visibility_of(db, so),
        "helios_uuids": json.loads(so.helios_uuids or "[]"),
        "viewport": {"object_id": obj_id, "ctx_object_id": ctx_object_id},
    }
    if include_materials:
        assignments = (
            db.query(ObjectMaterial)
            .filter(ObjectMaterial.scenario_object_id == so.id)
            .order_by(ObjectMaterial.created_at)
            .all()
        )
        out["materials"] = [_assignment_payload(db, so, om) for om in assignments]
    return out


# ── Geometry endpoints (spec §5) ─────────────────────────────────────────────


def create_object(db: Session, session_id: str, project_id: str,
                  scenario_id: str, body) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    ensure_hydrated(db, sctx, scenario_id)

    ot = db.get(ObjectType, body.object_type_id)
    if ot is None:
        raise api_error(404, "OBJECT_TYPE_NOT_FOUND",
                        f"object_type_id {body.object_type_id} not found")
    defs = load_type_properties(db, object_type_id=ot.id)
    if not defs:
        raise api_error(400, "UNKNOWN_PROPERTY",
                        f"Object type {ot.object} has no property catalog yet")
    canonical = validate_properties(
        body.properties, defs, type_label=ot.object,
        required=REQUIRED_OBJECT_PROPERTIES.get(ot.object),
    )
    validate_cross_field(
        {n: decode_value(v, defs[n].datatype)
         for n, v in canonical.items() if v is not None},
        ot.object,
    )

    taken = _object_names_lower(db, scenario_id)
    if body.name is None:
        name = next_default_name(taken, ot.object)
    else:
        name = validate_name(body.name)
        if name.lower() in taken:
            raise api_error(409, "GEOMETRY_NAME_EXISTS", "Geometry name already exists")

    # Validate requested assignments before writing anything.
    seen_types: dict[int, int] = {}
    materials: list[tuple[ProjectMaterial, bool]] = []
    for entry in body.materials:
        # Materials are GLOBAL (migration 019): any material may be assigned to
        # any object regardless of its project/scenario — no scope validation.
        pm = db.get(ProjectMaterial, entry.material_id)
        if pm is None:
            raise api_error(404, "MATERIAL_NOT_FOUND",
                            f"Material {entry.material_id} not found")
        if pm.material_type_id in seen_types:
            mt = db.get(MaterialType, pm.material_type_id)
            raise api_error(
                409, "DUPLICATE_MATERIAL_TYPE_ASSIGNMENT",
                f"A {mt.materialtype if mt else 'material of this type'} material "
                "is already assigned to this geometry",
            )
        seen_types[pm.material_type_id] = pm.id
        materials.append((pm, entry.sync))

    so = ScenarioObject(scenario_id=scenario_id, project_id=project_id,
                        name=name, object_type_id=ot.id)
    db.add(so)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise api_error(409, "GEOMETRY_NAME_EXISTS", "Geometry name already exists")
    _upsert_intrinsic(db, so.id, canonical, defs)
    if body.visibility is not None:
        _apply_visibility(db, so, body.visibility)
    for pm, sync in materials:
        db.add(ObjectMaterial(scenario_object_id=so.id, project_material_id=pm.id,
                              material_type_id=pm.material_type_id, sync=1 if sync else 0))
        db.flush()
        _snapshot_frozen(db, so.id, pm.id)   # snapshot every assignment
    db.commit()
    db.refresh(so)

    try:
        _build(db, sctx, so)
    except HTTPException:
        # Compensate: a create whose build failed must not leave a
        # DB-only object behind (user story: "Unable to create geometry").
        db.delete(so)
        db.commit()
        raise
    return {"success": True, "object": serialize_object(db, sctx, so)}


def list_objects(db: Session, session_id: str, project_id: str,
                 scenario_id: str, search: str | None) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    ensure_hydrated(db, sctx, scenario_id)

    rows = (
        db.query(ScenarioObject)
        .filter(ScenarioObject.scenario_id == scenario_id)
        .order_by(ScenarioObject.created_at, ScenarioObject.id)   # newest at bottom
        .all()
    )
    if search:
        needle = search.lower()
        rows = [r for r in rows if needle in r.name.lower()]
    type_names = dict(db.query(ObjectType.id, ObjectType.object).all())
    material_counts: dict[int, int] = {}
    if rows:
        for (so_id,) in (
            db.query(ObjectMaterial.scenario_object_id)
            .filter(ObjectMaterial.scenario_object_id.in_([r.id for r in rows]))
            .all()
        ):
            material_counts[so_id] = material_counts.get(so_id, 0) + 1
    return {"objects": [
        {
            "id": so.id,
            "name": so.name,
            "object_type": type_names.get(so.object_type_id),
            "group_id": so.group_id,
            "visibility": _visibility_of(db, so),
            "viewport": {"object_id": sctx.persisted_objects.get(so.id)},
            "material_count": material_counts.get(so.id, 0),
            "created_at": so.created_at,
            "updated_at": so.updated_at,
        }
        for so in rows
    ]}


def get_object(db: Session, session_id: str, project_id: str,
               scenario_id: str, object_id: int) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    ensure_hydrated(db, sctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)
    return {"object": serialize_object(db, sctx, so)}


def update_object(db: Session, session_id: str, project_id: str,
                  scenario_id: str, object_id: int, body) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    ensure_hydrated(db, sctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)

    intrinsic_change = None
    if body.properties:
        ot = db.get(ObjectType, so.object_type_id)
        defs = load_type_properties(db, object_type_id=so.object_type_id)
        # A PATCH may not null out a required property.
        required = REQUIRED_OBJECT_PROPERTIES.get(ot.object, set())
        canonical = validate_properties(
            body.properties, defs, type_label=ot.object,
            required=required & set(body.properties.keys()),
        )
        # Snapshot the OLD native values before the write so in-place ops can
        # compute deltas/ratios; derive the new values from the canonical patch.
        old_vals = _intrinsic_native(db, so.id)
        new_vals = dict(old_vals)
        for name, ctext in canonical.items():
            if ctext is None:
                new_vals.pop(name, None)
            else:
                new_vals[name] = decode_value(ctext, defs[name].datatype)
        validate_cross_field(new_vals, ot.object)
        _upsert_intrinsic(db, so.id, canonical, defs)
        changed = {name for name in canonical if old_vals.get(name) != new_vals.get(name)}
        if changed:
            intrinsic_change = (old_vals, new_vals, changed)

    if "group_id" in body.model_fields_set:
        if body.group_id is None:
            so.group_id = None
        else:
            _group_or_404(db, scenario_id, body.group_id)
            so.group_id = body.group_id

    if body.visibility is not None:
        _apply_visibility(db, so, body.visibility)

    so.updated_at = _now()   # property edits live in child rows; bump explicitly
    db.commit()
    db.refresh(so)

    if intrinsic_change is not None:
        _apply_intrinsic_change(db, sctx, so, *intrinsic_change)
    return {"success": True, "object": serialize_object(db, sctx, so)}


def rename_object(db: Session, session_id: str, project_id: str,
                  scenario_id: str, object_id: int, name: str) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)
    new_name = validate_name(name)
    if new_name.lower() != so.name.lower() and new_name.lower() in _object_names_lower(db, scenario_id):
        raise api_error(409, "GEOMETRY_NAME_EXISTS", "Geometry name already exists")
    so.name = new_name
    db.commit()
    db.refresh(so)
    obj_id = sctx.persisted_objects.get(so.id)
    if obj_id is not None and obj_id in sctx.registry:
        sctx.registry[obj_id]["name"] = new_name
    return {"success": True,
            "object": {"id": so.id, "name": so.name, "updated_at": so.updated_at}}


def delete_object(db: Session, session_id: str, project_id: str,
                  scenario_id: str, object_id: int) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    ensure_hydrated(db, sctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)

    # deleteObject removes the compound object and its primitives, so the
    # per-primitive material data dies with them — no label cleanup needed.
    _teardown(sctx, so)
    db.delete(so)   # cascades intrinsic + snapshot rows + assignments
    db.commit()
    _autosave(sctx)
    return {"success": True, "object_id": object_id}


def next_name(db: Session, session_id: str, project_id: str,
              scenario_id: str, object_type: str) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    ot = db.query(ObjectType).filter(ObjectType.object == object_type).first()
    if ot is None:
        raise api_error(404, "OBJECT_TYPE_NOT_FOUND", f"object type '{object_type}' not found")
    return {"name": next_default_name(_object_names_lower(db, scenario_id), ot.object)}


@_with_scenario_lock
def get_object_geometry_binary(db: Session, session_id: str, project_id: str,
                               scenario_id: str, object_id: int) -> bytes:
    """getObjectGeometry (spec §5.8): binary buffer for the stored UUIDs.
    Rebuild-first contract: an object not built in this session is built
    before serving, so stale prior-session UUIDs are never packed."""
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    ensure_hydrated(db, sctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)
    if not helios_ctx.PYHELIOS_AVAILABLE:
        raise api_error(503, "PYHELIOS_UNAVAILABLE", "PyHelios not available")
    if so.id not in sctx.persisted_objects:
        _build(db, sctx, so)    # retry path for a previously failed build
    uuids = json.loads(so.helios_uuids or "[]")
    from app.services.geometry_pack import pack_primitives_binary
    return pack_primitives_binary(helios_ctx.get_context(sctx), uuids)


@_with_scenario_lock
def get_scene_geometry_binary(db: Session, session_id: str, project_id: str,
                              scenario_id: str) -> bytes:
    """Whole-scene binary for one scenario's persisted objects (spec §12.3
    'before the frontend's first geometry fetch' — fetching hydrates).

    The legacy GET /api/geometry/all/binary predates the per-project
    context refactor and cannot reach this context; this scenario-scoped
    endpoint is the supported whole-scene fetch for persisted geometry.
    """
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    ensure_hydrated(db, sctx, scenario_id)
    if not helios_ctx.PYHELIOS_AVAILABLE:
        raise api_error(503, "PYHELIOS_UNAVAILABLE", "PyHelios not available")
    rows = (
        db.query(ScenarioObject)
        .filter(ScenarioObject.scenario_id == scenario_id)
        .order_by(ScenarioObject.created_at, ScenarioObject.id)
        .all()
    )
    uuids: list[int] = []
    for so in rows:
        if so.id in sctx.persisted_objects:
            uuids.extend(json.loads(so.helios_uuids or "[]"))
    from app.services.geometry_pack import pack_primitives_binary
    return pack_primitives_binary(helios_ctx.get_context(sctx), uuids)


# ── Scenario-level run configuration (spec §5.9) ─────────────────────────────
# Which models RUN when the user clicks the Run button — per scenario,
# independent of any geometry's per-model participation.


def _scenario_models_payload(db: Session, scenario_id: str) -> dict:
    explicit = dict(
        db.query(ScenarioModel.model_type_id, ScenarioModel.enabled)
        .filter(ScenarioModel.scenario_id == scenario_id)
        .all()
    )
    rows = (
        db.query(ModelType)
        .filter(ModelType.parent_id.is_(None))
        .order_by(ModelType.id)
        .all()
    )
    return {"models": [
        {"model_type_id": r.id, "model": r.model,
         "enabled": bool(explicit.get(r.id, 1))}
        for r in rows
    ]}


def get_scenario_models(db: Session, session_id: str, project_id: str,
                        scenario_id: str) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    return _scenario_models_payload(db, scenario_id)


def update_scenario_models(db: Session, session_id: str, project_id: str,
                           scenario_id: str, models: dict) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    for model_id, enabled in _parse_models_map(db, models).items():
        row = db.get(ScenarioModel, (scenario_id, model_id))
        if enabled:
            if row is not None:
                db.delete(row)      # absent row = enabled
        elif row is None:
            db.add(ScenarioModel(scenario_id=scenario_id,
                                 model_type_id=model_id, enabled=0))
        else:
            row.enabled = 0
    db.commit()
    return {"success": True, **_scenario_models_payload(db, scenario_id)}


# ── Group endpoints (spec §6) ────────────────────────────────────────────────


def create_group(db: Session, session_id: str, project_id: str,
                 scenario_id: str, body) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    if len(set(body.member_ids)) < 2:
        raise api_error(400, "GROUP_MIN_MEMBERS",
                        "A group must contain at least 2 geometries")
    taken = _group_names_lower(db, project_id)
    if body.name is None:
        name = next_default_name(taken, _GROUP_PREFIX)
    else:
        name = validate_name(body.name)
        if name.lower() in taken:
            raise api_error(409, "GROUP_NAME_EXISTS", "Group name already exists")

    members = [_object_or_404(db, scenario_id, oid) for oid in body.member_ids]
    grp = ObjectGroup(scenario_id=scenario_id, project_id=project_id, name=name)
    db.add(grp)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise api_error(409, "GROUP_NAME_EXISTS", "Group name already exists")
    for so in members:
        so.group_id = grp.id
    db.commit()
    db.refresh(grp)
    return {"success": True, "group": _group_payload(db, grp)}


def _group_payload(db: Session, grp: ObjectGroup) -> dict:
    member_ids = [
        r[0] for r in db.query(ScenarioObject.id)
        .filter(ScenarioObject.group_id == grp.id)
        .order_by(ScenarioObject.id)
        .all()
    ]
    return {"id": grp.id, "name": grp.name, "scenario_id": grp.scenario_id,
            "member_ids": member_ids,
            "created_at": grp.created_at, "updated_at": grp.updated_at}


def list_groups(db: Session, session_id: str, project_id: str, scenario_id: str) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    rows = (
        db.query(ObjectGroup)
        .filter(ObjectGroup.scenario_id == scenario_id)
        .order_by(ObjectGroup.created_at, ObjectGroup.id)
        .all()
    )
    return {"groups": [_group_payload(db, g) for g in rows]}


def rename_group(db: Session, session_id: str, project_id: str,
                 scenario_id: str, group_id: int, name: str) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    grp = _group_or_404(db, scenario_id, group_id)
    new_name = validate_name(name)
    if new_name.lower() != grp.name.lower() and new_name.lower() in _group_names_lower(db, project_id):
        raise api_error(409, "GROUP_NAME_EXISTS", "Group name already exists")
    grp.name = new_name
    db.commit()
    db.refresh(grp)
    return {"success": True,
            "group": {"id": grp.id, "name": grp.name, "updated_at": grp.updated_at}}


def delete_group(db: Session, session_id: str, project_id: str,
                 scenario_id: str, group_id: int) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    grp = _group_or_404(db, scenario_id, group_id)
    member_rows = (
        db.query(ScenarioObject)
        .filter(ScenarioObject.group_id == grp.id)
        .all()
    )
    ungrouped = [so.id for so in member_rows]
    for so in member_rows:      # explicit — mirrors the DDL's SET NULL
        so.group_id = None
    db.delete(grp)
    db.commit()
    return {"success": True, "group_id": group_id, "ungrouped": ungrouped}


def _group_members(db: Session, group_id: int) -> list[ScenarioObject]:
    return (
        db.query(ScenarioObject)
        .filter(ScenarioObject.group_id == group_id)
        .order_by(ScenarioObject.id)
        .all()
    )


def update_group_visibility(db: Session, session_id: str, project_id: str,
                            scenario_id: str, group_id: int, body) -> dict:
    """Bulk-apply a visibility object ({viewport?, render?, models?}) to every
    member of a group, reusing the per-object writer (so the render↔models
    master-switch coupling applies identically). DB-only — the live viewport
    reads these flags at serialization; nothing in the PyHelios context changes."""
    _resolve_scope(db, session_id, project_id, scenario_id)
    _group_or_404(db, scenario_id, group_id)

    visibility = body.visibility
    if not visibility:
        raise api_error(400, "NO_VISIBILITY_FIELDS",
                        "visibility must contain viewport, render and/or models")

    members = _group_members(db, group_id)
    for so in members:
        _apply_visibility(db, so, visibility)   # reuses the per-object writer
        so.updated_at = _now()
    db.commit()
    return {"success": True, "group_id": group_id,
            "visibility": visibility,
            "member_ids": [so.id for so in members]}


def delete_group_objects(db: Session, session_id: str, project_id: str,
                         scenario_id: str, group_id: int) -> dict:
    """Delete every member geometry of a group AND the group itself (full
    purge). Each member is torn down from the live context, then the DB rows
    cascade (intrinsic + snapshot + assignment + per-model rows)."""
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    ensure_hydrated(db, sctx, scenario_id)   # so live objects exist for teardown
    grp = _group_or_404(db, scenario_id, group_id)

    members = _group_members(db, group_id)
    deleted = [so.id for so in members]
    for so in members:
        _teardown(sctx, so)
        db.delete(so)
    db.delete(grp)
    db.commit()
    _autosave(sctx)
    return {"success": True, "group_id": group_id, "deleted_object_ids": deleted}


# ── Assignment endpoints (spec §8) ───────────────────────────────────────────


def assign_material(db: Session, session_id: str, project_id: str,
                    scenario_id: str, object_id: int, body) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    ensure_hydrated(db, sctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)

    # Materials are GLOBAL (migration 019) — no same-project/scenario check.
    pm = db.get(ProjectMaterial, body.material_id)
    if pm is None:
        raise api_error(404, "MATERIAL_NOT_FOUND", f"Material {body.material_id} not found")

    om = ObjectMaterial(scenario_object_id=so.id, project_material_id=pm.id,
                        material_type_id=pm.material_type_id,
                        sync=1 if body.sync else 0)
    db.add(om)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        mt = db.get(MaterialType, pm.material_type_id)
        raise api_error(
            409, "DUPLICATE_MATERIAL_TYPE_ASSIGNMENT",
            f"A {mt.materialtype if mt else 'material of this type'} material "
            "is already assigned to this geometry",
        )
    _snapshot_frozen(db, so.id, pm.id)   # snapshot every assignment
    db.commit()

    _apply_assignment_change(db, sctx, so)
    om = db.get(ObjectMaterial, (so.id, pm.id))
    return {"success": True, "assignment": _assignment_payload(db, so, om)}


def list_assignments(db: Session, session_id: str, project_id: str,
                     scenario_id: str, object_id: int) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    ensure_hydrated(db, sctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)
    assignments = (
        db.query(ObjectMaterial)
        .filter(ObjectMaterial.scenario_object_id == so.id)
        .order_by(ObjectMaterial.created_at)
        .all()
    )
    return {"materials": [_assignment_payload(db, so, om) for om in assignments]}


def _assignment_or_404(db: Session, so_id: int, material_id: int) -> ObjectMaterial:
    om = db.get(ObjectMaterial, (so_id, material_id))
    if om is None:
        raise api_error(404, "ASSIGNMENT_NOT_FOUND",
                        f"Material {material_id} is not assigned to this geometry")
    return om


def update_assignment(db: Session, session_id: str, project_id: str,
                      scenario_id: str, object_id: int, material_id: int, body) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    ensure_hydrated(db, sctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)
    om = _assignment_or_404(db, so.id, material_id)

    target_sync = bool(om.sync) if body.sync is None else body.sync

    if body.sync is not None and body.sync != bool(om.sync):
        if body.sync:
            # Unfreeze = relink + refresh: re-snapshot the CURRENT library values
            # (the snapshot stays the source of truth — there is no live sync).
            _snapshot_frozen(db, so.id, material_id)
            om.sync = 1
        else:
            # Freeze = detach: keep the existing snapshot as the editable copy.
            om.sync = 0

    if body.properties:
        if target_sync:
            raise api_error(400, "CANNOT_EDIT_SYNCED",
                            "Freeze the material before editing per-geometry values")
        mt = db.get(MaterialType, om.material_type_id)
        defs = load_type_properties(db, material_type_id=om.material_type_id)
        canonical = validate_properties(
            body.properties, defs, type_label=mt.materialtype, type_kind="material type"
        )
        existing = {row.property_type_id: row for row in _frozen_rows(db, so.id, material_id)}
        for name, value in canonical.items():
            pt_id = defs[name].property_type_id
            row = existing.get(pt_id)
            if value is None:
                if row is not None:
                    db.delete(row)
            elif row is None:
                db.add(ObjectPropertyData(scenario_object_id=so.id,
                                          project_material_id=material_id,
                                          property_type_id=pt_id, value=value))
            else:
                row.value = value

    db.commit()
    # Clear this material type's stale labels then re-apply (or rebuild if the
    # winning texture changed). cleared_type_id drops labels a property edit
    # may have removed.
    _apply_assignment_change(db, sctx, so, cleared_type_id=om.material_type_id)

    om = db.get(ObjectMaterial, (so.id, material_id))
    return {"success": True, "assignment": _assignment_payload(db, so, om)}


def unassign_material(db: Session, session_id: str, project_id: str,
                      scenario_id: str, object_id: int, material_id: int) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    sctx = _sctx(session_id, project_id, scenario_id)
    ensure_hydrated(db, sctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)
    om = _assignment_or_404(db, so.id, material_id)

    old_type_id = om.material_type_id   # capture BEFORE the delete cascades it
    db.delete(om)   # cascades the assignment's snapshot rows
    db.commit()
    # Clear the removed material type's labels + reset color, then re-apply the
    # remaining assignments (or the default when none remain).
    _apply_assignment_change(db, sctx, so, cleared_type_id=old_type_id)
    return {"success": True, "object_id": object_id, "material_id": material_id}
