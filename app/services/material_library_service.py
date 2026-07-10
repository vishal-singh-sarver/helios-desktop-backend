"""
Global material-group library (migration 022).

Materials exist only as members of a named MATERIAL GROUP: one member per
material type, EAV values in material_data, no per-member name — identity is
(group, material type). Groups are GLOBAL: names are unique across everything;
project_id/scenario_id are pure provenance (SET NULL when the parent dies).

Library writes are DB-only EXCEPT for the optional eager hook: PUT/DELETE/
file-upload accept ?scenario_id= (the ACTIVE scenario) and run the shared
reconcile engine (material_sync_service.apply_sync) on it inline — full
"cascade" semantics plus repaint. Every other scenario keeps its last-applied
snapshot state (the migration-022 break point) and reports/settles the drift
through the GET/PUT material-sync APIs.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Datatype,
    MaterialData,
    MaterialGroup,
    MaterialType,
    ObjectMaterialGroup,
    ProjectMaterial,
    PropertyType,
    Scenario,
    ScenarioObject,
    _now,
)
from app.services import material_sync_service as sync_svc
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
# Allowed upload extensions per file-typed material property.
_FILE_PROPERTY_EXTENSIONS = {
    "texture_file": {".png", ".jpg", ".jpeg"},
    "spectral_data": {".xml"},
}
_PRECEDENCE_TYPE = "Radiation"   # list preview mirrors the viewport winner


# ── Helpers ──────────────────────────────────────────────────────────────────


def _group_or_404(db: Session, group_id: int) -> MaterialGroup:
    grp = db.get(MaterialGroup, group_id)
    if grp is None:
        raise api_error(404, "MATERIAL_GROUP_NOT_FOUND",
                        f"Material group {group_id} not found")
    return grp


def _group_names_lower(db: Session) -> set[str]:
    """All group names (GLOBAL namespace)."""
    rows = db.query(MaterialGroup.name).all()
    return {r[0].lower() for r in rows}


def _group_members(db: Session, group_id: int) -> list[ProjectMaterial]:
    return (
        db.query(ProjectMaterial)
        .filter(ProjectMaterial.material_group_id == group_id)
        .order_by(ProjectMaterial.material_type_id)
        .all()
    )


def _values_native(db: Session, material_id: int) -> dict:
    rows = (
        db.query(PropertyType.property, MaterialData.value, Datatype.name)
        .join(MaterialData, MaterialData.property_type_id == PropertyType.id)
        .join(Datatype, Datatype.id == PropertyType.datatype_id)
        .filter(MaterialData.project_material_id == material_id)
        .all()
    )
    return {prop: decode_value(value, dt) for prop, value, dt in rows}


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


def _serialize_member(db: Session, pm: ProjectMaterial, type_names: dict[int, str]) -> dict:
    defs = load_type_properties(db, material_type_id=pm.material_type_id)
    values = _values_native(db, pm.id)
    return {
        "material_id": pm.id,
        "material_type_id": pm.material_type_id,
        "material_type": type_names.get(pm.material_type_id),
        "created_at": pm.created_at,
        "updated_at": pm.updated_at,
        "properties": {name: values.get(name) for name in defs},
    }


def serialize_group(db: Session, grp: MaterialGroup) -> dict:
    type_names = dict(db.query(MaterialType.id, MaterialType.materialtype).all())
    return {
        "id": grp.id,
        "project_id": grp.project_id,
        "scenario_id": grp.scenario_id,
        "name": grp.name,
        "created_at": grp.created_at,
        "updated_at": grp.updated_at,
        "materials": [_serialize_member(db, pm, type_names)
                      for pm in _group_members(db, grp.id)],
    }


def _validate_members_payload(db: Session, materials) -> list[tuple]:
    """Shared POST/PUT member-list validation → [(MaterialType, defs, canonical)].
    ≥1 member, no duplicate types, known type ids, per-member property checks."""
    if not materials:
        raise api_error(400, "MATERIAL_GROUP_EMPTY",
                        "A material group must contain at least one material type")
    out: list[tuple] = []
    seen: set[int] = set()
    for entry in materials:
        mt = db.get(MaterialType, entry.material_type_id)
        if mt is None:
            raise api_error(404, "MATERIAL_TYPE_NOT_FOUND",
                            f"material_type_id {entry.material_type_id} not found")
        if mt.id in seen:
            raise api_error(400, "DUPLICATE_MATERIAL_TYPE_IN_GROUP",
                            f"Material type {mt.materialtype} appears more than once")
        seen.add(mt.id)
        defs = load_type_properties(db, material_type_id=mt.id)
        canonical = validate_properties(
            entry.properties, defs, type_label=mt.materialtype, type_kind="material type"
        )
        out.append((mt, defs, canonical))
    return out


def _resolve_provenance(db: Session, session_id: str,
                        project_id: str | None, scenario_id: str | None) -> tuple:
    """Validate optional creation provenance. A scenario derives (and
    cross-checks) its project; a bare project is checked for session ownership."""
    if scenario_id is not None:
        scn = db.get(Scenario, scenario_id)
        if scn is None or (project_id is not None and scn.project_id != project_id):
            raise api_error(404, "SCENARIO_NOT_FOUND",
                            f"Scenario {scenario_id} not found")
        project_or_404(db, session_id, scn.project_id)
        return scn.project_id, scenario_id
    if project_id is not None:
        project_or_404(db, session_id, project_id)
    return project_id, None


def _scenario_scope_or_404(db: Session, session_id: str, scenario_id: str) -> Scenario:
    scn = db.get(Scenario, scenario_id)
    if scn is None:
        raise api_error(404, "SCENARIO_NOT_FOUND", f"Scenario {scenario_id} not found")
    project_or_404(db, session_id, scn.project_id)
    return scn


def _eager_reconcile(db: Session, session_id: str, scn: Scenario, group_id: int) -> dict:
    """Full-cascade semantics for the ACTIVE scenario: hydrate, reconcile this
    group's applied state to library truth, repaint. Runs AFTER the library
    commit; conflicts are skipped + reported (never raised).

    Never fails the request: the library mutation already stands, so a
    hydration/repaint failure (e.g. BUILD_FAILED on a bad texture) must not
    masquerade as a failed write — any residual drift is recoverable through
    PUT material-sync. Errors come back as {"error": ...} in the sync block."""
    from app.services import scene_object_service as sos   # mls → sos → sync_svc (acyclic)

    result = {"applied": {"removed_groups": 0, "removed_members": 0,
                          "added_members": 0, "refreshed_values": 0},
              "conflicts": [], "cleared_type_ids": {}}
    try:
        sctx = sos._sctx(session_id, scn.project_id, scn.id)
        sos.ensure_hydrated(db, sctx, scn.id)
        result = sync_svc.apply_sync(db, scn.id, group_ids=[group_id])
        db.commit()
        for so_id, cleared in result["cleared_type_ids"].items():
            so = db.get(ScenarioObject, so_id)
            if so is not None:
                sos._apply_assignment_change(db, sctx, so, cleared_type_ids=cleared)
    except Exception as exc:   # noqa: BLE001 — reconcile is best-effort by design
        db.rollback()
        detail = getattr(exc, "detail", None)
        message = detail.get("error") if isinstance(detail, dict) else str(exc)
        result["error"] = message or "Scenario reconcile failed"
    return result


def _sync_block(scn: Scenario, result: dict) -> dict:
    block = {"scenario_id": scn.id,
             "applied": result["applied"],
             "conflicts": result["conflicts"]}
    if "error" in result:
        block["error"] = result["error"]
    return block


def _assigned_object_ids(db: Session, group_id: int, scenario_id: str) -> list[int]:
    rows = (
        db.query(ObjectMaterialGroup.scenario_object_id)
        .join(ScenarioObject,
              ScenarioObject.id == ObjectMaterialGroup.scenario_object_id)
        .filter(ObjectMaterialGroup.material_group_id == group_id,
                ScenarioObject.scenario_id == scenario_id)
        .all()
    )
    return [r[0] for r in rows]


# ── Endpoint handlers ────────────────────────────────────────────────────────


def create_group(db: Session, session_id: str, body) -> dict:
    try:
        project_id, scenario_id = _resolve_provenance(
            db, session_id, body.project_id, body.scenario_id)
        members = _validate_members_payload(db, body.materials)

        taken = _group_names_lower(db)   # GLOBAL namespace
        if body.name is None:
            name = next_default_name(taken, _NAME_PREFIX)
        else:
            name = validate_name(body.name)
            if name.lower() in taken:
                raise api_error(409, "MATERIAL_GROUP_NAME_EXISTS",
                                "Material group name already exists")

        grp = MaterialGroup(project_id=project_id, scenario_id=scenario_id, name=name)
        db.add(grp)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise api_error(409, "MATERIAL_GROUP_NAME_EXISTS",
                            "Material group name already exists")
        for mt, defs, canonical in members:
            pm = ProjectMaterial(material_group_id=grp.id, material_type_id=mt.id)
            db.add(pm)
            db.flush()
            _upsert_values(db, pm.id, canonical, defs)
        db.commit()
        db.refresh(grp)

        # A new group is assigned nowhere — no reconcile/repaint to do.
        return {"success": True, "group": serialize_group(db, grp)}
    except HTTPException:
        raise   # deliberate validation errors keep their specific message/code
    except Exception:
        db.rollback()
        raise api_error(500, "MATERIAL_CREATE_FAILED",
                        "Unable to create material. Please try again")


def list_groups(db: Session, session_id: str,
                search: str | None, material_type_id: int | None) -> dict:
    """GLOBAL list (groups are not scoped to a project)."""
    groups = (
        db.query(MaterialGroup)
        .order_by(MaterialGroup.created_at.desc(), MaterialGroup.id.desc())
        .all()
    )
    if search:
        needle = search.lower()
        groups = [g for g in groups if needle in g.name.lower()]

    members_by_group: dict[int, list[ProjectMaterial]] = {}
    for pm in db.query(ProjectMaterial).order_by(ProjectMaterial.material_type_id).all():
        members_by_group.setdefault(pm.material_group_id, []).append(pm)
    if material_type_id is not None:
        groups = [g for g in groups
                  if any(pm.material_type_id == material_type_id
                         for pm in members_by_group.get(g.id, []))]

    type_names = dict(db.query(MaterialType.id, MaterialType.materialtype).all())
    out = []
    for grp in groups:
        pms = members_by_group.get(grp.id, [])
        # Preview mirrors viewport precedence: the Radiation member owns the
        # color/texture channel, else the most recently added member.
        winner = next(
            (pm for pm in pms if type_names.get(pm.material_type_id) == _PRECEDENCE_TYPE),
            max(pms, key=lambda pm: (pm.created_at or "", pm.id), default=None),
        )
        preview_values = _values_native(db, winner.id) if winner else {}
        out.append({
            "id": grp.id,
            "name": grp.name,
            "material_type_ids": [pm.material_type_id for pm in pms],
            "material_types": [type_names.get(pm.material_type_id) for pm in pms],
            "preview": {name: preview_values.get(name)
                        for name in sorted(VISUALISATION_PROPERTIES)},
            "created_at": grp.created_at,
        })
    return {"groups": out}


def get_group(db: Session, session_id: str, group_id: int) -> dict:
    grp = _group_or_404(db, group_id)
    return {"group": serialize_group(db, grp)}


def next_name(db: Session, session_id: str) -> dict:
    return {"name": next_default_name(_group_names_lower(db), _NAME_PREFIX)}


def update_group(db: Session, session_id: str, group_id: int, body,
                 scenario_id: str | None) -> dict:
    """PUT: full-replacement member set + optional rename.

    Kept types are UPDATEd in place (their pm.id — and with it every frozen
    per-geometry override keyed on it — survives). Removed types ride the
    library cascade only; the eager reconcile (and, later, each scenario's own
    PUT material-sync) removes the applied rows."""
    grp = _group_or_404(db, group_id)
    members = _validate_members_payload(db, body.materials)

    new_name = None
    if body.name is not None:
        new_name = validate_name(body.name)
        if new_name.lower() != grp.name.lower() and new_name.lower() in _group_names_lower(db):
            raise api_error(409, "MATERIAL_GROUP_NAME_EXISTS",
                            "Material group name already exists")

    # Resolve the active scenario BEFORE mutating anything (fail fast).
    scn = _scenario_scope_or_404(db, session_id, scenario_id) if scenario_id else None

    current = {pm.material_type_id: pm for pm in _group_members(db, grp.id)}
    desired = {mt.id: (mt, defs, canonical) for mt, defs, canonical in members}
    to_remove = [pm for type_id, pm in current.items() if type_id not in desired]
    to_add = [payload for type_id, payload in desired.items() if type_id not in current]
    to_update = [(current[type_id], payload)
                 for type_id, payload in desired.items() if type_id in current]

    # Advisory pre-check (UX): would the ACTIVE scenario's eager reconcile hit a
    # type conflict on the added members? Reads object_material directly, so
    # STALE rows count as blockers; the group's own rows are excluded (the same
    # reconcile pass deletes them first). Other scenarios surface conflicts at
    # their own sync time; the engine itself always skips + reports.
    if scn is not None and to_add:
        so_ids = _assigned_object_ids(db, grp.id, scn.id)
        blockers = sync_svc.find_type_blockers(
            db, so_ids, [mt.id for mt, _defs, _canonical in to_add],
            exclude_group_id=grp.id,
        )
        if blockers:
            so_map = {so.id: so for so in db.query(ScenarioObject)
                      .filter(ScenarioObject.id.in_({b.scenario_object_id for b in blockers}))}
            raise api_error(
                409, "DUPLICATE_MATERIAL_TYPE_ASSIGNMENT",
                "A material of this type is already assigned to a geometry "
                "using this group",
                extra={"conflicts": [
                    sync_svc.blocker_conflict(db, so_map[b.scenario_object_id], b)
                    for b in blockers
                ]},
            )

    for pm in to_remove:
        db.delete(pm)   # library cascade only (material_data); applied rows wait for sync
    db.flush()
    for mt, defs, canonical in to_add:
        pm = ProjectMaterial(material_group_id=grp.id, material_type_id=mt.id)
        db.add(pm)
        db.flush()
        _upsert_values(db, pm.id, canonical, defs)
    for pm, (mt, defs, canonical) in to_update:
        _upsert_values(db, pm.id, canonical, defs)
        pm.updated_at = _now()
    if new_name is not None:
        grp.name = new_name
    grp.updated_at = _now()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise api_error(409, "MATERIAL_GROUP_NAME_EXISTS",
                        "Material group name already exists")

    out = {"success": True}
    if scn is not None:
        result = _eager_reconcile(db, session_id, scn, grp.id)
        out["sync"] = _sync_block(scn, result)
    db.refresh(grp)
    out["group"] = serialize_group(db, grp)
    return out


def delete_group(db: Session, session_id: str, group_id: int,
                 scenario_id: str | None) -> dict:
    grp = _group_or_404(db, group_id)
    scn = _scenario_scope_or_404(db, session_id, scenario_id) if scenario_id else None

    db.delete(grp)   # cascades members + material_data — LIBRARY ONLY: applied
    db.commit()      # rows in every scenario survive until their sync
    # NOTE: upload files under uploads/ are deliberately NOT removed — surviving
    # frozen snapshots in out-of-sync scenarios still resolve texture_file from
    # disk until every scenario syncs.

    out = {"success": True, "group_id": group_id, "unassigned_from": 0}
    if scn is not None:
        result = _eager_reconcile(db, session_id, scn, group_id)
        out["unassigned_from"] = result["applied"]["removed_groups"]
        out["sync"] = _sync_block(scn, result)
    return out


async def upload_file_property(db: Session, session_id: str, group_id: int,
                               material_type_id: int, property_name: str, file,
                               scenario_id: str | None) -> dict:
    grp = _group_or_404(db, group_id)
    scn = _scenario_scope_or_404(db, session_id, scenario_id) if scenario_id else None
    pm = (
        db.query(ProjectMaterial)
        .filter(ProjectMaterial.material_group_id == grp.id,
                ProjectMaterial.material_type_id == material_type_id)
        .first()
    )
    if pm is None:
        raise api_error(404, "MATERIAL_TYPE_NOT_IN_GROUP",
                        f"material_type_id {material_type_id} is not in this group")
    defs = load_type_properties(db, material_type_id=pm.material_type_id)
    prop = defs.get(property_name)
    if prop is None or prop.datatype != "file":
        raise api_error(400, "UNKNOWN_PROPERTY",
                        f"'{property_name}' is not a file property of this material type")

    filename = Path(file.filename or "").name
    if not filename:
        raise api_error(400, "INVALID_FILE_FORMAT", "File format not supported")
    allowed = _FILE_PROPERTY_EXTENSIONS.get(property_name)
    if allowed is not None and Path(filename).suffix.lower() not in allowed:
        raise api_error(400, "INVALID_FILE_FORMAT", "File format not supported")

    # Groups are global — uploads live under a project-free path. Values stored
    # before migration 022 keep their old uploads/{project_id}/... paths; both
    # resolve through material_apply.resolve_texture_path.
    rel = Path("uploads") / "materials" / str(pm.id) / filename
    dest = settings.data_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())

    value = str(rel).replace("\\", "/")
    _upsert_values(db, pm.id, {property_name: value}, defs)
    pm.updated_at = _now()
    grp.updated_at = _now()
    db.commit()

    out = {"success": True, "property": property_name, "value": value}
    if scn is not None:
        # A file value is a library values-change like any other → same eager hook.
        result = _eager_reconcile(db, session_id, scn, grp.id)
        out["sync"] = _sync_block(scn, result)
    db.refresh(grp)
    out["group"] = serialize_group(db, grp)
    return out
