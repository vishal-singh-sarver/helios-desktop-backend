"""
Persisted scene objects (milestone 2, spec §5/§6/§8/§12).

DB-first write-through into the per-project PyHelios context:

    POST .../objects   one DB transaction (scenario_object + intrinsic
                       object_property_data rows) → in-memory ground build
                       via the existing createTile methods → UUIDs written
                       back to scenario_object.helios_uuids.

The ground build (spec §12.1):
    plain    ctx.addTile(center, size, subdiv, color) + rotatePrimitive(z)
    textured ctx.addTileObject(center, size, rotation, subdiv,
                               texturefile, texture_repeat)
selected by whether the appearance-winning assigned material carries a
texture. Stored UUIDs are session-scoped — `ensure_hydrated` rebuilds a
scenario's objects after a restart and rewrites the column.

Material assignment applies color/texture to the viewport through the
label scheme in app/services/material_apply.py.

All PyHelios work degrades to a DB-only operation when the native library
is unavailable (helios_uuids stays []; getObjectGeometry returns 503).
"""
from __future__ import annotations

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
from app.services import material_apply
from app.services.eav_validation import (
    REQUIRED_OBJECT_PROPERTIES,
    api_error,
    decode_value,
    load_type_properties,
    next_default_name,
    project_or_404,
    validate_name,
    validate_properties,
)

_GROUP_PREFIX = "Group"


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


def _pctx(session_id: str, project_id: str):
    return session_registry.get_or_create_context(session_id, project_id)


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


def _object_names_lower(db: Session, project_id: str) -> set[str]:
    rows = db.query(ScenarioObject.name).filter(ScenarioObject.project_id == project_id).all()
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
    """Copy the material's current library values into frozen rows."""
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


def _winner_effective_texture(db: Session, so: ScenarioObject) -> str | None:
    assignments = (
        db.query(ObjectMaterial)
        .filter(ObjectMaterial.scenario_object_id == so.id)
        .all()
    )
    winner = material_apply._winning_assignment(db, assignments)
    if winner is None:
        return None
    vis = (
        material_apply.library_vis_values(db, winner.project_material_id)
        if winner.sync
        else material_apply.frozen_vis_values(db, so.id, winner.project_material_id)
    )
    return vis.get("texture_file")


def _teardown(pctx, so: ScenarioObject) -> None:
    """Remove the object's primitives + registry entry from the live context."""
    uuids = json.loads(so.helios_uuids or "[]")
    obj_id = pctx.persisted_objects.pop(so.id, None)
    if obj_id is not None and obj_id in pctx.registry:
        reg.delete_object(pctx, obj_id)
    if uuids and helios_ctx.PYHELIOS_AVAILABLE and pctx.context is not None:
        try:
            pctx.context.deletePrimitive(uuids)
        except Exception:
            pass    # primitives may already be gone (fresh context)
    material_apply.invalidate_geometry_caches(pctx)


def _build(db: Session, pctx, so: ScenarioObject) -> list[int]:
    """Build the ground in the live context from its intrinsic properties.
    Returns the new primitive UUIDs (and persists them on the row).

    On a PyHelios failure the row is left consistent (helios_uuids=[],
    not registered) and BUILD_FAILED is raised — callers decide whether to
    compensate (create) or surface it (rebuild); hydration skips and
    retries later.
    """
    if not helios_ctx.PYHELIOS_AVAILABLE:
        so.helios_uuids = "[]"
        db.commit()
        return []

    props = _intrinsic_native(db, so.id)
    texture_value = _winner_effective_texture(db, so)
    texture_path = material_apply.resolve_texture_path(texture_value)

    try:
        ctx = helios_ctx.get_context(pctx)
        from pyhelios.types import RGBcolor, SphericalCoord, int2, vec2, vec3

        center = vec3(props.get("position_x") or 0,
                      props.get("position_y") or 0,
                      props.get("position_z") or 0)
        size = vec2(props.get("length") or 1, props.get("breadth") or 1)
        subdiv = int2(int(props.get("resolution_x") or 1), int(props.get("resolution_y") or 1))
        # Both tile builders rotate by -azimuth about z BEFORE translating
        # to center (in-place spin) — same convention on both paths.
        rotation = SphericalCoord(1, 0, math.radians(float(props.get("rotation_z") or 0)))

        if texture_path:
            pyh_obj_id = ctx.addTileObject(
                center=center, size=size, rotation=rotation, subdiv=subdiv,
                texturefile=texture_path,
                texture_repeat=int2(int(props.get("texture_x") or 1),
                                    int(props.get("texture_y") or 1)),
            )
            uuids = [p.uuid for p in ctx.getPrimitivesInfoForObject(pyh_obj_id)]
        else:
            uuids = ctx.addTile(center=center, size=size, rotation=rotation,
                                subdiv=subdiv,
                                color=RGBcolor(*reg.DEFAULT_MATERIAL_COLOR[:3]))
    except HTTPException:
        raise
    except Exception:
        so.helios_uuids = "[]"
        db.commit()
        raise api_error(500, "BUILD_FAILED",
                        "Unable to create geometry. Please try again")

    obj_id = reg.register_object(
        pctx, so.name, "ground", uuids,
        scenario_object_id=so.id,
        # Track what was ACTUALLY built — when the texture file failed to
        # resolve, the plain path ran and _sync_viewport must know that.
        built_texture=texture_value if texture_path else None,
    )
    pctx.persisted_objects[so.id] = obj_id

    so.helios_uuids = json.dumps(uuids)
    db.commit()

    material_apply.apply_object_appearance(db, pctx, so)
    return uuids


def _rebuild(db: Session, pctx, so: ScenarioObject) -> None:
    _teardown(pctx, so)
    _build(db, pctx, so)


def _sync_viewport(db: Session, pctx, so: ScenarioObject) -> None:
    """After an assignment change: rebuild when the texture state changed
    (plain ↔ textured build paths differ), else relabel only (spec §12.2)."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        return
    desired = _winner_effective_texture(db, so)
    obj_id = pctx.persisted_objects.get(so.id)
    built = None
    if obj_id is not None and obj_id in pctx.registry:
        built = pctx.registry[obj_id].get("built_texture")
    if (desired or None) != (built or None):
        _rebuild(db, pctx, so)
    else:
        material_apply.apply_object_appearance(db, pctx, so)


def ensure_hydrated(db: Session, pctx, scenario_id: str) -> None:
    """Hydrate this scenario's persisted objects into the live context —
    stored UUIDs are session-scoped (spec §12.3). Cheap no-op once done.

    Scenario switch (spec §12.3): the per-project context holds ONE active
    scenario at a time — other scenarios' persisted objects are torn down
    first, so whole-context reads never mix scenarios.

    The scenario is marked hydrated only after the loop; individual build
    failures are skipped (object stays DB-only with helios_uuids=[]) and
    retried on the next touch via the rebuild-first paths.
    """
    if scenario_id in pctx.hydrated_scenarios:
        return

    # Tear down persisted objects belonging to OTHER scenarios.
    if pctx.persisted_objects:
        foreign = (
            db.query(ScenarioObject)
            .filter(
                ScenarioObject.id.in_(list(pctx.persisted_objects.keys())),
                ScenarioObject.scenario_id != scenario_id,
            )
            .all()
        )
        for so in foreign:
            _teardown(pctx, so)
        if foreign:
            pctx.hydrated_scenarios.clear()

    rows = (
        db.query(ScenarioObject)
        .filter(ScenarioObject.scenario_id == scenario_id)
        .order_by(ScenarioObject.created_at, ScenarioObject.id)
        .all()
    )
    for so in rows:
        if so.id not in pctx.persisted_objects:
            try:
                _build(db, pctx, so)
            except HTTPException:
                continue    # leave DB-only; retried by rebuild-first paths
    pctx.hydrated_scenarios.add(scenario_id)


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


def _apply_visibility(db: Session, so: ScenarioObject, payload: dict) -> None:
    """Persist a partial visibility update (caller commits).

    viewport → scenario_object.visible, render → scenario_object.render_enabled,
    models → scenario_object_model rows (row deleted when set back to True —
    absent = enabled)."""
    if not isinstance(payload, dict):
        raise api_error(400, "DATATYPE_MISMATCH", "visibility must be an object")
    if "viewport" in payload:
        if not isinstance(payload["viewport"], bool):
            raise api_error(400, "DATATYPE_MISMATCH", "visibility.viewport must be a boolean")
        so.visible = 1 if payload["viewport"] else 0
    if "render" in payload:
        if not isinstance(payload["render"], bool):
            raise api_error(400, "DATATYPE_MISMATCH", "visibility.render must be a boolean")
        so.render_enabled = 1 if payload["render"] else 0
    for model_id, enabled in _parse_models_map(db, payload.get("models") or {}).items():
        row = db.get(ScenarioObjectModel, (so.id, model_id))
        if enabled:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(ScenarioObjectModel(scenario_object_id=so.id,
                                       model_type_id=model_id, enabled=0))
        else:
            row.enabled = 0


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


def serialize_object(db: Session, pctx, so: ScenarioObject,
                     include_materials: bool = True) -> dict:
    ot = db.get(ObjectType, so.object_type_id)
    defs = load_type_properties(db, object_type_id=so.object_type_id)
    values = _intrinsic_native(db, so.id)
    obj_id = pctx.persisted_objects.get(so.id)
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
        "viewport": {"object_id": obj_id},
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
    pctx = _pctx(session_id, project_id)
    ensure_hydrated(db, pctx, scenario_id)

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

    taken = _object_names_lower(db, project_id)
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
        pm = (
            db.query(ProjectMaterial)
            .filter(ProjectMaterial.id == entry.material_id,
                    ProjectMaterial.project_id == project_id)
            .first()
        )
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
        if not sync:
            db.flush()
            _snapshot_frozen(db, so.id, pm.id)
    db.commit()
    db.refresh(so)

    try:
        _build(db, pctx, so)
    except HTTPException:
        # Compensate: a create whose build failed must not leave a
        # DB-only object behind (user story: "Unable to create geometry").
        db.delete(so)
        db.commit()
        raise
    return {"success": True, "object": serialize_object(db, pctx, so)}


def list_objects(db: Session, session_id: str, project_id: str,
                 scenario_id: str, search: str | None) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    pctx = _pctx(session_id, project_id)
    ensure_hydrated(db, pctx, scenario_id)

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
            "viewport": {"object_id": pctx.persisted_objects.get(so.id)},
            "material_count": material_counts.get(so.id, 0),
            "created_at": so.created_at,
            "updated_at": so.updated_at,
        }
        for so in rows
    ]}


def get_object(db: Session, session_id: str, project_id: str,
               scenario_id: str, object_id: int) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    pctx = _pctx(session_id, project_id)
    ensure_hydrated(db, pctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)
    return {"object": serialize_object(db, pctx, so)}


def update_object(db: Session, session_id: str, project_id: str,
                  scenario_id: str, object_id: int, body) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    pctx = _pctx(session_id, project_id)
    ensure_hydrated(db, pctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)

    properties_changed = False
    if body.properties:
        ot = db.get(ObjectType, so.object_type_id)
        defs = load_type_properties(db, object_type_id=so.object_type_id)
        # A PATCH may not null out a required property.
        required = REQUIRED_OBJECT_PROPERTIES.get(ot.object, set())
        canonical = validate_properties(
            body.properties, defs, type_label=ot.object,
            required=required & set(body.properties.keys()),
        )
        _upsert_intrinsic(db, so.id, canonical, defs)
        properties_changed = True

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

    if properties_changed:
        _rebuild(db, pctx, so)
    return {"success": True, "object": serialize_object(db, pctx, so)}


def rename_object(db: Session, session_id: str, project_id: str,
                  scenario_id: str, object_id: int, name: str) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    pctx = _pctx(session_id, project_id)
    so = _object_or_404(db, scenario_id, object_id)
    new_name = validate_name(name)
    if new_name.lower() != so.name.lower() and new_name.lower() in _object_names_lower(db, project_id):
        raise api_error(409, "GEOMETRY_NAME_EXISTS", "Geometry name already exists")
    so.name = new_name
    db.commit()
    db.refresh(so)
    obj_id = pctx.persisted_objects.get(so.id)
    if obj_id is not None and obj_id in pctx.registry:
        pctx.registry[obj_id]["name"] = new_name
    return {"success": True,
            "object": {"id": so.id, "name": so.name, "updated_at": so.updated_at}}


def delete_object(db: Session, session_id: str, project_id: str,
                  scenario_id: str, object_id: int) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    pctx = _pctx(session_id, project_id)
    ensure_hydrated(db, pctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)

    frozen_labels = [
        material_apply.frozen_label(om.project_material_id, so.id)
        for om in db.query(ObjectMaterial)
        .filter(ObjectMaterial.scenario_object_id == so.id)
        .all()
    ]
    _teardown(pctx, so)
    db.delete(so)   # cascades intrinsic + frozen rows + assignments
    db.commit()
    for label in frozen_labels:
        material_apply.cleanup_label(pctx, label)
    return {"success": True, "object_id": object_id}


def next_name(db: Session, session_id: str, project_id: str,
              scenario_id: str, object_type: str) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    ot = db.query(ObjectType).filter(ObjectType.object == object_type).first()
    if ot is None:
        raise api_error(404, "OBJECT_TYPE_NOT_FOUND", f"object type '{object_type}' not found")
    return {"name": next_default_name(_object_names_lower(db, project_id), ot.object)}


def get_object_geometry_binary(db: Session, session_id: str, project_id: str,
                               scenario_id: str, object_id: int) -> bytes:
    """getObjectGeometry (spec §5.8): binary buffer for the stored UUIDs.
    Rebuild-first contract: an object not built in this session is built
    before serving, so stale prior-session UUIDs are never packed."""
    _resolve_scope(db, session_id, project_id, scenario_id)
    pctx = _pctx(session_id, project_id)
    ensure_hydrated(db, pctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)
    if not helios_ctx.PYHELIOS_AVAILABLE:
        raise api_error(503, "PYHELIOS_UNAVAILABLE", "PyHelios not available")
    if so.id not in pctx.persisted_objects:
        _build(db, pctx, so)    # retry path for a previously failed build
    uuids = json.loads(so.helios_uuids or "[]")
    from app.services.geometry_pack import pack_primitives_binary
    return pack_primitives_binary(helios_ctx.get_context(pctx), uuids)


def get_scene_geometry_binary(db: Session, session_id: str, project_id: str,
                              scenario_id: str) -> bytes:
    """Whole-scene binary for one scenario's persisted objects (spec §12.3
    'before the frontend's first geometry fetch' — fetching hydrates).

    The legacy GET /api/geometry/all/binary predates the per-project
    context refactor and cannot reach this context; this scenario-scoped
    endpoint is the supported whole-scene fetch for persisted geometry.
    """
    _resolve_scope(db, session_id, project_id, scenario_id)
    pctx = _pctx(session_id, project_id)
    ensure_hydrated(db, pctx, scenario_id)
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
        if so.id in pctx.persisted_objects:
            uuids.extend(json.loads(so.helios_uuids or "[]"))
    from app.services.geometry_pack import pack_primitives_binary
    return pack_primitives_binary(helios_ctx.get_context(pctx), uuids)


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


# ── Assignment endpoints (spec §8) ───────────────────────────────────────────


def assign_material(db: Session, session_id: str, project_id: str,
                    scenario_id: str, object_id: int, body) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    pctx = _pctx(session_id, project_id)
    ensure_hydrated(db, pctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)

    pm = (
        db.query(ProjectMaterial)
        .filter(ProjectMaterial.id == body.material_id,
                ProjectMaterial.project_id == project_id)
        .first()
    )
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
    if not body.sync:
        _snapshot_frozen(db, so.id, pm.id)
    db.commit()

    _sync_viewport(db, pctx, so)
    om = db.get(ObjectMaterial, (so.id, pm.id))
    return {"success": True, "assignment": _assignment_payload(db, so, om)}


def list_assignments(db: Session, session_id: str, project_id: str,
                     scenario_id: str, object_id: int) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    pctx = _pctx(session_id, project_id)
    ensure_hydrated(db, pctx, scenario_id)
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
    pctx = _pctx(session_id, project_id)
    ensure_hydrated(db, pctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)
    om = _assignment_or_404(db, so.id, material_id)

    target_sync = bool(om.sync) if body.sync is None else body.sync

    if body.sync is not None and body.sync != bool(om.sync):
        if body.sync:
            # Unfreeze: drop frozen rows, follow the library again.
            for row in _frozen_rows(db, so.id, material_id):
                db.delete(row)
            om.sync = 1
        else:
            # Freeze: snapshot current library values.
            _snapshot_frozen(db, so.id, material_id)
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
    if not target_sync:
        # Refresh the dedicated frozen label from the frozen values.
        material_apply.apply_vis_to_label(
            pctx, material_apply.frozen_label(material_id, so.id),
            material_apply.frozen_vis_values(db, so.id, material_id),
        )
        _sync_viewport(db, pctx, so)
    else:
        # Re-point primitives FIRST, then drop the orphaned frozen label —
        # cleanup only deletes labels with no primitives still using them.
        _sync_viewport(db, pctx, so)
        material_apply.cleanup_label(pctx, material_apply.frozen_label(material_id, so.id))

    om = db.get(ObjectMaterial, (so.id, material_id))
    return {"success": True, "assignment": _assignment_payload(db, so, om)}


def unassign_material(db: Session, session_id: str, project_id: str,
                      scenario_id: str, object_id: int, material_id: int) -> dict:
    _resolve_scope(db, session_id, project_id, scenario_id)
    pctx = _pctx(session_id, project_id)
    ensure_hydrated(db, pctx, scenario_id)
    so = _object_or_404(db, scenario_id, object_id)
    om = _assignment_or_404(db, so.id, material_id)

    db.delete(om)   # cascades the assignment's frozen rows
    db.commit()
    # Re-point primitives first; only then can the frozen label be orphaned
    # and actually deleted by cleanup.
    _sync_viewport(db, pctx, so)
    material_apply.cleanup_label(pctx, material_apply.frozen_label(material_id, so.id))
    return {"success": True, "object_id": object_id, "material_id": material_id}
