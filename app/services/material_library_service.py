"""
Persisted project material library (milestone 2, spec §7).

Each material = exactly one material type (parameter group) + EAV values in
material_data. Visualisation values (color_r/g/b, texture_file,
two_sided_heat_transfer) are mirrored onto the material's shared PyHelios
label pm_{id}, so every SYNCED assignment follows library edits live.
Frozen assignments use dedicated labels and are never touched from here.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.session_store import registry as session_registry
from app.db.models import (
    Datatype,
    MaterialData,
    MaterialType,
    ObjectMaterial,
    ProjectMaterial,
    PropertyType,
    ScenarioObject,
    _now,
)
from app.services import material_apply
from app.services.eav_validation import (
    VISUALISATION_PROPERTIES,
    api_error,
    decode_value,
    load_type_properties,
    next_default_name,
    project_or_404,
    validate_name,
    validate_properties,
)

_NAME_PREFIX = "Material"
_TEXTURE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _pctx(session_id: str, project_id: str):
    return session_registry.get_or_create_context(session_id, project_id)


def _material_or_404(db: Session, project_id: str, material_id: int) -> ProjectMaterial:
    pm = (
        db.query(ProjectMaterial)
        .filter(ProjectMaterial.id == material_id, ProjectMaterial.project_id == project_id)
        .first()
    )
    if pm is None:
        raise api_error(404, "MATERIAL_NOT_FOUND", f"Material {material_id} not found")
    return pm


def _existing_names_lower(db: Session, project_id: str) -> set[str]:
    rows = db.query(ProjectMaterial.name).filter(ProjectMaterial.project_id == project_id).all()
    return {r[0].lower() for r in rows}


def _values_native(db: Session, material_id: int) -> dict:
    rows = (
        db.query(PropertyType.property, MaterialData.value, Datatype.name)
        .join(MaterialData, MaterialData.property_type_id == PropertyType.id)
        .join(Datatype, Datatype.id == PropertyType.datatype_id)
        .filter(MaterialData.project_material_id == material_id)
        .all()
    )
    return {prop: decode_value(value, dt) for prop, value, dt in rows}


def serialize_material(db: Session, pm: ProjectMaterial) -> dict:
    mt = db.get(MaterialType, pm.material_type_id)
    defs = load_type_properties(db, material_type_id=pm.material_type_id)
    values = _values_native(db, pm.id)
    return {
        "id": pm.id,
        "project_id": pm.project_id,
        "scenario_id": pm.scenario_id,
        "name": pm.name,
        "material_type_id": pm.material_type_id,
        "material_type": mt.materialtype if mt else None,
        "created_at": pm.created_at,
        "updated_at": pm.updated_at,
        "properties": {name: values.get(name) for name in defs},
    }


def _upsert_values(db: Session, material_id: int, canonical: dict[str, str | None],
                   defs: dict) -> None:
    """Write canonical values into material_data (None deletes the row)."""
    by_pt = {defs[name].property_type_id: (name, value) for name, value in canonical.items()}
    existing = {
        row.property_type_id: row
        for row in db.query(MaterialData)
        .filter(
            MaterialData.project_material_id == material_id,
            MaterialData.property_type_id.in_(by_pt.keys()),
        )
        .all()
    }
    for pt_id, (_name, value) in by_pt.items():
        row = existing.get(pt_id)
        if value is None:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(MaterialData(project_material_id=material_id,
                                property_type_id=pt_id, value=value))
        else:
            row.value = value


def _refresh_shared_label(db: Session, session_id: str, project_id: str, pm: ProjectMaterial) -> None:
    """Push library vis values onto pm_{id} — all synced geometries follow."""
    pctx = _pctx(session_id, project_id)
    material_apply.apply_vis_to_label(
        pctx, material_apply.pm_label(pm.id), material_apply.library_vis_values(db, pm.id)
    )
    material_apply.invalidate_geometry_caches(pctx)


def _resync_built_objects(db: Session, session_id: str, project_id: str,
                          object_ids: list[int]) -> None:
    """Route affected geometries through the assignment-level viewport sync
    (handles the plain<->textured rebuild rule). Only objects built in THIS
    session are touched — stored UUIDs are session-scoped, and a post-commit
    viewport repair must never fail the request."""
    if not object_ids:
        return
    from app.services import scene_object_service
    pctx = _pctx(session_id, project_id)
    for oid in object_ids:
        if oid not in pctx.persisted_objects:
            continue            # hydration will paint it correctly later
        so = db.get(ScenarioObject, oid)
        if so is None:
            continue
        try:
            scene_object_service._sync_viewport(db, pctx, so)
        except Exception:
            pass


def _synced_object_ids(db: Session, material_id: int) -> list[int]:
    return [
        row[0] for row in db.query(ObjectMaterial.scenario_object_id)
        .filter(ObjectMaterial.project_material_id == material_id,
                ObjectMaterial.sync == 1)
        .all()
    ]


# ── Endpoint handlers ────────────────────────────────────────────────────────


def create_material(db: Session, session_id: str, project_id: str, body) -> dict:
    project_or_404(db, session_id, project_id)
    mt = db.get(MaterialType, body.material_type_id)
    if mt is None:
        raise api_error(404, "MATERIAL_TYPE_NOT_FOUND",
                        f"material_type_id {body.material_type_id} not found")

    taken = _existing_names_lower(db, project_id)
    if body.name is None:
        name = next_default_name(taken, _NAME_PREFIX)
    else:
        name = validate_name(body.name)
        if name.lower() in taken:
            raise api_error(409, "MATERIAL_NAME_EXISTS", "Material name already exists")

    defs = load_type_properties(db, material_type_id=mt.id)
    canonical = validate_properties(
        body.properties, defs, type_label=mt.materialtype, type_kind="material type"
    )

    pm = ProjectMaterial(project_id=project_id, material_type_id=mt.id, name=name)
    db.add(pm)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise api_error(409, "MATERIAL_NAME_EXISTS", "Material name already exists")
    _upsert_values(db, pm.id, canonical, defs)
    db.commit()
    db.refresh(pm)

    _refresh_shared_label(db, session_id, project_id, pm)
    return {"success": True, "material": serialize_material(db, pm)}


def list_materials(db: Session, session_id: str, project_id: str,
                   search: str | None, material_type_id: int | None) -> dict:
    project_or_404(db, session_id, project_id)
    q = db.query(ProjectMaterial).filter(ProjectMaterial.project_id == project_id)
    if material_type_id is not None:
        q = q.filter(ProjectMaterial.material_type_id == material_type_id)
    rows = q.order_by(ProjectMaterial.created_at.desc(), ProjectMaterial.id.desc()).all()
    if search:
        needle = search.lower()
        rows = [r for r in rows if needle in r.name.lower()]
    type_names = dict(db.query(MaterialType.id, MaterialType.materialtype).all())
    out = []
    for pm in rows:
        values = _values_native(db, pm.id)
        out.append({
            "id": pm.id,
            "name": pm.name,
            "material_type_id": pm.material_type_id,
            "material_type": type_names.get(pm.material_type_id),
            "preview": {name: values.get(name) for name in sorted(VISUALISATION_PROPERTIES)},
            "created_at": pm.created_at,
        })
    return {"materials": out}


def get_material(db: Session, session_id: str, project_id: str, material_id: int) -> dict:
    project_or_404(db, session_id, project_id)
    pm = _material_or_404(db, project_id, material_id)
    return {"material": serialize_material(db, pm)}


def update_material(db: Session, session_id: str, project_id: str,
                    material_id: int, body) -> dict:
    project_or_404(db, session_id, project_id)
    pm = _material_or_404(db, project_id, material_id)
    mt = db.get(MaterialType, pm.material_type_id)
    defs = load_type_properties(db, material_type_id=pm.material_type_id)
    canonical = validate_properties(
        body.properties, defs, type_label=mt.materialtype, type_kind="material type"
    )
    _upsert_values(db, pm.id, canonical, defs)
    pm.updated_at = _now()   # value edits live in child rows; bump explicitly
    db.commit()
    db.refresh(pm)

    if any(name in VISUALISATION_PROPERTIES for name in canonical):
        # Synced assignments share pm_{id}; frozen labels are untouched.
        _refresh_shared_label(db, session_id, project_id, pm)
        if "texture_file" in canonical:
            # Texture-state changes require the plain<->textured rebuild
            # check on every synced geometry (spec §12.2 design rule).
            _resync_built_objects(db, session_id, project_id,
                                  _synced_object_ids(db, pm.id))
    return {"success": True, "material": serialize_material(db, pm)}


def rename_material(db: Session, session_id: str, project_id: str,
                    material_id: int, name: str) -> dict:
    project_or_404(db, session_id, project_id)
    pm = _material_or_404(db, project_id, material_id)
    new_name = validate_name(name)
    if new_name.lower() != pm.name.lower() and new_name.lower() in _existing_names_lower(db, project_id):
        raise api_error(409, "MATERIAL_NAME_EXISTS", "Material name already exists")
    pm.name = new_name
    db.commit()
    db.refresh(pm)
    return {"success": True,
            "material": {"id": pm.id, "name": pm.name, "updated_at": pm.updated_at}}


def delete_material(db: Session, session_id: str, project_id: str, material_id: int) -> dict:
    project_or_404(db, session_id, project_id)
    pm = _material_or_404(db, project_id, material_id)

    affected_object_ids = [
        row[0] for row in db.query(ObjectMaterial.scenario_object_id)
        .filter(ObjectMaterial.project_material_id == pm.id)
        .all()
    ]
    frozen_labels = [material_apply.frozen_label(pm.id, oid) for oid in affected_object_ids]
    shared = material_apply.pm_label(pm.id)

    db.delete(pm)   # cascades material_data + assignments + frozen rows
    db.commit()

    # Repaint (and rebuild plain<->textured where needed) the geometries
    # built this session, then drop the now-orphaned labels.
    _resync_built_objects(db, session_id, project_id, affected_object_ids)
    pctx = _pctx(session_id, project_id)
    for label in [shared, *frozen_labels]:
        material_apply.cleanup_label(pctx, label)
    material_apply.invalidate_geometry_caches(pctx)

    return {"success": True, "material_id": material_id,
            "unassigned_from": len(affected_object_ids)}


def next_name(db: Session, session_id: str, project_id: str) -> dict:
    project_or_404(db, session_id, project_id)
    return {"name": next_default_name(_existing_names_lower(db, project_id), _NAME_PREFIX)}


async def upload_file_property(db: Session, session_id: str, project_id: str,
                               material_id: int, property_name: str, file) -> dict:
    project_or_404(db, session_id, project_id)
    pm = _material_or_404(db, project_id, material_id)
    defs = load_type_properties(db, material_type_id=pm.material_type_id)
    prop = defs.get(property_name)
    if prop is None or prop.datatype != "file":
        raise api_error(400, "UNKNOWN_PROPERTY",
                        f"'{property_name}' is not a file property of this material type")

    filename = Path(file.filename or "").name
    if not filename:
        raise api_error(400, "INVALID_FILE_FORMAT", "File format not supported")
    if property_name == "texture_file" and Path(filename).suffix.lower() not in _TEXTURE_EXTENSIONS:
        raise api_error(400, "INVALID_FILE_FORMAT", "File format not supported")

    rel = Path("uploads") / project_id / "materials" / str(pm.id) / filename
    dest = settings.data_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())

    value = str(rel).replace("\\", "/")
    _upsert_values(db, pm.id, {property_name: value}, defs)
    db.commit()
    db.refresh(pm)

    if property_name in VISUALISATION_PROPERTIES:
        _refresh_shared_label(db, session_id, project_id, pm)
        if property_name == "texture_file":
            _resync_built_objects(db, session_id, project_id,
                                  _synced_object_ids(db, pm.id))
    return {"success": True, "property": property_name, "value": value,
            "material": serialize_material(db, pm)}
