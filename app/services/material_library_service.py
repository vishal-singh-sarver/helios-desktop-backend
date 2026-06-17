"""
Global material library (milestone 2, spec §7; materials globalised in
migration 019).

Each material = exactly one material type (parameter group) + EAV values in
material_data. Materials are GLOBAL: names are unique across all projects, and a
material may be assigned to any object regardless of its project/scenario.
project_id/scenario_id are stored only to record where a material was created
(both NULL for app-shipped defaults).

Library edits/deletes are DB-only and do NOT repaint assigned geometry —
assignment is snapshot-on-assign (decision #1), so applied values change only on
an explicit per-object (re)assignment ("sync again"). A scene reflects whatever
was last saved to its context.xml until it is reopened or re-synced.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Datatype,
    MaterialData,
    MaterialType,
    ObjectMaterial,
    ProjectMaterial,
    PropertyType,
    Scenario,
    _now,
)
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


def _material_or_404(db: Session, material_id: int) -> ProjectMaterial:
    # Materials are GLOBAL — reachable regardless of project (migration 019).
    pm = db.get(ProjectMaterial, material_id)
    if pm is None:
        raise api_error(404, "MATERIAL_NOT_FOUND", f"Material {material_id} not found")
    return pm


def _existing_names_lower(db: Session) -> set[str]:
    """All material names (GLOBAL namespace — names are unique everywhere)."""
    rows = db.query(ProjectMaterial.name).all()
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


# ── Endpoint handlers ────────────────────────────────────────────────────────


def create_material(db: Session, session_id: str, project_id: str, body) -> dict:
    project_or_404(db, session_id, project_id)
    mt = db.get(MaterialType, body.material_type_id)
    if mt is None:
        raise api_error(404, "MATERIAL_TYPE_NOT_FOUND",
                        f"material_type_id {body.material_type_id} not found")

    # Record the scenario when a material is created inside one (optional).
    scenario_id = getattr(body, "scenario_id", None)
    if scenario_id is not None:
        scn = (
            db.query(Scenario)
            .filter(Scenario.id == scenario_id, Scenario.project_id == project_id)
            .first()
        )
        if scn is None:
            raise api_error(404, "SCENARIO_NOT_FOUND",
                            f"Scenario {scenario_id} not found in this project")

    taken = _existing_names_lower(db)   # GLOBAL namespace
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

    pm = ProjectMaterial(project_id=project_id, scenario_id=scenario_id,
                         material_type_id=mt.id, name=name)
    db.add(pm)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise api_error(409, "MATERIAL_NAME_EXISTS", "Material name already exists")
    _upsert_values(db, pm.id, canonical, defs)
    db.commit()
    db.refresh(pm)

    # Snapshot model: library creation does not touch any geometry.
    return {"success": True, "material": serialize_material(db, pm)}


def list_materials(db: Session, session_id: str, project_id: str,
                   search: str | None, material_type_id: int | None) -> dict:
    project_or_404(db, session_id, project_id)
    # Materials are GLOBAL — list the whole library (defaults + all projects').
    q = db.query(ProjectMaterial)
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
    pm = _material_or_404(db, material_id)
    return {"material": serialize_material(db, pm)}


def update_material(db: Session, session_id: str, project_id: str,
                    material_id: int, body) -> dict:
    project_or_404(db, session_id, project_id)
    pm = _material_or_404(db, material_id)
    mt = db.get(MaterialType, pm.material_type_id)
    defs = load_type_properties(db, material_type_id=pm.material_type_id)
    canonical = validate_properties(
        body.properties, defs, type_label=mt.materialtype, type_kind="material type"
    )
    _upsert_values(db, pm.id, canonical, defs)
    pm.updated_at = _now()   # value edits live in child rows; bump explicitly
    db.commit()
    db.refresh(pm)

    # Snapshot model (decision #1): editing the library does NOT repaint already
    # assigned geometry. Applied values change only on (re)assignment.
    return {"success": True, "material": serialize_material(db, pm)}


def rename_material(db: Session, session_id: str, project_id: str,
                    material_id: int, name: str) -> dict:
    project_or_404(db, session_id, project_id)
    pm = _material_or_404(db, material_id)
    new_name = validate_name(name)
    if new_name.lower() != pm.name.lower() and new_name.lower() in _existing_names_lower(db):
        raise api_error(409, "MATERIAL_NAME_EXISTS", "Material name already exists")
    pm.name = new_name
    db.commit()
    db.refresh(pm)
    return {"success": True,
            "material": {"id": pm.id, "name": pm.name, "updated_at": pm.updated_at}}


def delete_material(db: Session, session_id: str, project_id: str, material_id: int) -> dict:
    project_or_404(db, session_id, project_id)
    pm = _material_or_404(db, material_id)

    affected_object_ids = [
        row[0] for row in db.query(ObjectMaterial.scenario_object_id)
        .filter(ObjectMaterial.project_material_id == pm.id)
        .all()
    ]

    db.delete(pm)   # cascades material_data + assignments + snapshot rows
    db.commit()

    # Library-level deletes are DB-only — live geometry is NOT auto-repainted
    # (snapshot model; the user re-syncs by re-assigning). Affected scenes show
    # the prior material until reopened/re-synced; a reopen reflects context.xml.
    return {"success": True, "material_id": material_id,
            "unassigned_from": len(affected_object_ids)}


def next_name(db: Session, session_id: str, project_id: str) -> dict:
    project_or_404(db, session_id, project_id)
    return {"name": next_default_name(_existing_names_lower(db), _NAME_PREFIX)}


async def upload_file_property(db: Session, session_id: str, project_id: str,
                               material_id: int, property_name: str, file) -> dict:
    project_or_404(db, session_id, project_id)
    pm = _material_or_404(db, material_id)
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

    # Snapshot model: uploading a texture/file to the library does not repaint
    # assigned geometry until the material is (re)assigned.
    return {"success": True, "property": property_name, "value": value,
            "material": serialize_material(db, pm)}
