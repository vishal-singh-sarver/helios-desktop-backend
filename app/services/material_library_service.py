"""
Global material-group library (migration 022).

Materials exist only as members of a named MATERIAL GROUP: one member per
material type, EAV values in material_data, no per-member name — identity is
(group, material type). A group may be EMPTY (zero members). Groups are
GLOBAL: names are unique across everything; project_id/scenario_id are pure
provenance (SET NULL when the parent dies). Members are managed in bulk (PUT
full member set) or one at a time (POST/PATCH/DELETE
/groups/{id}/materials[/{material_type_id}]).

Library writes are DB-only EXCEPT for the optional eager hook: every mutating
endpoint (PUT/DELETE group, member add/update/remove, file-upload) accepts
?scenario_id= (the ACTIVE scenario) and runs the shared reconcile engine
(material_sync_service.apply_sync) on it inline — full "cascade" semantics
plus repaint. Every other scenario keeps its last-applied snapshot state (the
migration-022 break point) and reports/settles the drift through the GET/PUT
material-sync APIs.

PATCH /groups/{id}/rename is the one exception — it takes no ?scenario_id=.
A rename changes no library VALUE and no membership, and applied state keys off
material_group_id (never the name), so there is nothing to reconcile.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
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
    ObjectPropertyData,
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
    member_property_values,
    next_default_name,
    project_or_404,
    validate_name,
    validate_properties,
    visualiser_mode_required,
)

_NAME_PREFIX = "Material"
# Allowed upload extensions per file-typed material property.
_FILE_PROPERTY_EXTENSIONS = {
    "texture_file": {".png", ".jpg", ".jpeg"},
    "spectral_data": {".xml"},
}
_PRECEDENCE_TYPE = "Visualiser"   # list preview mirrors the viewport winner


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
                   defs: dict, *, replace: bool = False) -> None:
    """Write canonical values into material_data (None deletes the row).

    replace=False (merge): only properties present in `canonical` are touched;
    absent ones are left as they were.
    replace=True (full replacement): the member's stored properties become
    EXACTLY `canonical` — every other property of the type is cleared. Used by
    the PUT paths (per-member and group-level) so a member's persisted state is
    precisely what the client sent."""
    value_by_pt = {defs[name].property_type_id: value for name, value in canonical.items()}
    # Under replace, the scope is every property of the type (so omitted ones get
    # cleared); under merge, only the properties in the request.
    scope = ({d.property_type_id for d in defs.values()} if replace
             else set(value_by_pt.keys()))
    existing = {
        row.property_type_id: row
        for row in db.query(MaterialData)
        .filter(
            MaterialData.project_material_id == material_id,
            MaterialData.property_type_id.in_(scope),
        )
        .all()
    }
    for pt_id in scope:
        value = value_by_pt.get(pt_id)   # absent under replace -> None -> cleared
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
        "properties": member_property_values(defs, values),
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


def _validate_member_entry(db: Session, material_type_id: int, properties: dict) -> tuple:
    """One member's validation → (MaterialType, defs, canonical). Shared by the
    bulk POST/PUT payload validator and the single add-member endpoint."""
    mt = db.get(MaterialType, material_type_id)
    if mt is None:
        raise api_error(404, "MATERIAL_TYPE_NOT_FOUND",
                        f"material_type_id {material_type_id} not found")
    defs = load_type_properties(db, material_type_id=mt.id)
    # Visualiser members are full-replacement + required-by-mode (migration 025):
    # texture_toggle picks colour|texture and that mode's fields become required.
    required = (visualiser_mode_required(properties)
                if mt.materialtype == "Visualiser" else None)
    canonical = validate_properties(
        properties, defs, type_label=mt.materialtype, type_kind="material type",
        required=required,
    )
    return mt, defs, canonical


def _validate_members_payload(db: Session, materials) -> list[tuple]:
    """Shared POST/PUT member-list validation → [(MaterialType, defs, canonical)].
    No duplicate types, known type ids, per-member property checks. An EMPTY
    list is legal (a group may hold no material types)."""
    out: list[tuple] = []
    seen: set[int] = set()
    for entry in materials:
        mt, defs, canonical = _validate_member_entry(
            db, entry.material_type_id, entry.properties)
        if mt.id in seen:
            raise api_error(400, "DUPLICATE_MATERIAL_TYPE_IN_GROUP",
                            f"Material type {mt.materialtype} appears more than once")
        seen.add(mt.id)
        out.append((mt, defs, canonical))
    return out


def _member_or_404(db: Session, group_id: int, material_type_id: int) -> ProjectMaterial:
    pm = (
        db.query(ProjectMaterial)
        .filter(ProjectMaterial.material_group_id == group_id,
                ProjectMaterial.material_type_id == material_type_id)
        .first()
    )
    if pm is None:
        raise api_error(404, "MATERIAL_TYPE_NOT_IN_GROUP",
                        f"material_type_id {material_type_id} is not in this group")
    return pm


def _precheck_add_conflicts(db: Session, grp: MaterialGroup, scenario_id: str,
                            type_ids: list[int]) -> None:
    """Advisory (UX) pre-check for the eager path: would materializing the
    added types onto the ACTIVE scenario's assigned geometries hit a
    material-type collision? Reads object_material directly, so STALE rows
    count as blockers; the group's own rows are excluded (the same reconcile
    pass deletes them first). Other scenarios surface conflicts at their own
    sync time; the engine itself always skips + reports."""
    if not type_ids:
        return
    so_ids = _assigned_object_ids(db, grp.id, scenario_id)
    blockers = sync_svc.find_type_blockers(db, so_ids, type_ids,
                                           exclude_group_id=grp.id)
    if not blockers:
        return
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
            _upsert_values(db, pm.id, canonical, defs, replace=True)
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
        # Preview mirrors viewport precedence (Plan B): the Visualiser member is
        # the sole owner of the colour/texture/opacity channel; with none there is
        # no winner and the preview is empty (soil + default colour in the viewport).
        winner = next(
            (pm for pm in pms if type_names.get(pm.material_type_id) == _PRECEDENCE_TYPE),
            None,
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

    if scn is not None and to_add:
        _precheck_add_conflicts(db, grp, scn.id,
                                [mt.id for mt, _defs, _canonical in to_add])

    for pm in to_remove:
        db.delete(pm)   # library cascade only (material_data); applied rows wait for sync
    db.flush()
    for mt, defs, canonical in to_add:
        pm = ProjectMaterial(material_group_id=grp.id, material_type_id=mt.id)
        db.add(pm)
        db.flush()
        _upsert_values(db, pm.id, canonical, defs, replace=True)
    for pm, (mt, defs, canonical) in to_update:
        _upsert_values(db, pm.id, canonical, defs, replace=True)   # group PUT = full replace
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


def rename_group(db: Session, session_id: str, group_id: int, name: str) -> dict:
    """PATCH the group's NAME only — members and their values are untouched.

    No eager `scenario_id` hook and no repaint: a rename changes no library
    value and no membership, and the applied state (object_material_group /
    object_material) references material_group_id, never the name — so the
    reconcile engine would produce an empty diff.

    Returns the FULL group, exactly like GET/PUT on this resource. A slim body
    under the same "group" key would be a trap: a client that swaps its
    `PUT /groups/{id}` refresh for this endpoint would drop `materials` from its
    local state — the very member-loss this endpoint exists to avoid.
    """
    grp = _group_or_404(db, group_id)
    new_name = validate_name(name)
    # Skip the collision check against the group's OWN name so a case-only
    # rename ("Grass Set" -> "grass set") is allowed.
    if new_name.lower() != grp.name.lower() and new_name.lower() in _group_names_lower(db):
        raise api_error(409, "MATERIAL_GROUP_NAME_EXISTS",
                        "Material group name already exists")
    grp.name = new_name
    # Explicit: SQLAlchemy emits no UPDATE when the name is byte-identical, so
    # `onupdate` would never fire and updated_at would go stale.
    grp.updated_at = _now()
    try:
        db.commit()
    except IntegrityError:
        # validate_name already rules out the only other constraint on the row
        # (CHECK length 1..20), so a survivor here is the NOCASE unique index —
        # i.e. we lost the race between the pre-check above and this commit.
        db.rollback()
        raise api_error(409, "MATERIAL_GROUP_NAME_EXISTS",
                        "Material group name already exists")
    db.refresh(grp)
    return {"success": True, "group": serialize_group(db, grp)}


def add_group_material(db: Session, session_id: str, group_id: int, body,
                       scenario_id: str | None) -> dict:
    """POST one member into a group. The eager `scenario_id` hook materializes
    it (row + snapshot) onto that scenario's assigned geometries + repaints;
    other scenarios pick it up via their material-sync."""
    grp = _group_or_404(db, group_id)
    scn = _scenario_scope_or_404(db, session_id, scenario_id) if scenario_id else None
    mt, defs, canonical = _validate_member_entry(db, body.material_type_id, body.properties)

    existing = (
        db.query(ProjectMaterial)
        .filter(ProjectMaterial.material_group_id == grp.id,
                ProjectMaterial.material_type_id == mt.id)
        .first()
    )
    if existing is not None:
        raise api_error(409, "DUPLICATE_MATERIAL_TYPE_IN_GROUP",
                        f"Material type {mt.materialtype} is already in this group")
    if scn is not None:
        _precheck_add_conflicts(db, grp, scn.id, [mt.id])

    pm = ProjectMaterial(material_group_id=grp.id, material_type_id=mt.id)
    db.add(pm)
    try:
        db.flush()
        _upsert_values(db, pm.id, canonical, defs, replace=True)
        grp.updated_at = _now()
        db.commit()
    except IntegrityError:
        # UNIQUE(material_group_id, material_type_id) backstop for a race.
        db.rollback()
        raise api_error(409, "DUPLICATE_MATERIAL_TYPE_IN_GROUP",
                        f"Material type {mt.materialtype} is already in this group")

    out = {"success": True}
    if scn is not None:
        out["sync"] = _sync_block(scn, _eager_reconcile(db, session_id, scn, grp.id))
    db.refresh(grp)
    out["group"] = serialize_group(db, grp)
    return out


def update_group_material(db: Session, session_id: str, group_id: int,
                          material_type_id: int, body,
                          scenario_id: str | None) -> dict:
    """PUT one member's properties standalone — FULL REPLACEMENT: the member's
    stored properties become exactly `body.properties` (any omitted property is
    cleared). Visualiser members are required-by-mode (texture_toggle picks
    colour|texture). The eager `scenario_id` hook refreshes that scenario's
    sync=1 snapshots + repaints."""
    grp = _group_or_404(db, group_id)
    scn = _scenario_scope_or_404(db, session_id, scenario_id) if scenario_id else None
    pm = _member_or_404(db, grp.id, material_type_id)

    mt = db.get(MaterialType, pm.material_type_id)
    defs = load_type_properties(db, material_type_id=pm.material_type_id)
    required = (visualiser_mode_required(body.properties)
                if mt and mt.materialtype == "Visualiser" else None)
    canonical = validate_properties(
        body.properties, defs,
        type_label=mt.materialtype if mt else "material", type_kind="material type",
        required=required,
    )
    _upsert_values(db, pm.id, canonical, defs, replace=True)
    pm.updated_at = _now()
    grp.updated_at = _now()
    db.commit()

    out = {"success": True}
    if scn is not None:
        out["sync"] = _sync_block(scn, _eager_reconcile(db, session_id, scn, grp.id))
    db.refresh(grp)
    out["group"] = serialize_group(db, grp)
    return out


def remove_group_material(db: Session, session_id: str, group_id: int,
                          material_type_id: int, scenario_id: str | None) -> dict:
    """DELETE one member from a group (removing the last member leaves a legal
    EMPTY group). Library cascade only (material_data); applied state in other
    scenarios survives as out-of-sync until their material-sync — the eager
    `scenario_id` hook cleans the active scenario immediately."""
    grp = _group_or_404(db, group_id)
    scn = _scenario_scope_or_404(db, session_id, scenario_id) if scenario_id else None
    pm = _member_or_404(db, grp.id, material_type_id)

    db.delete(pm)   # cascades material_data; applied rows wait for sync
    grp.updated_at = _now()
    db.commit()

    out = {"success": True, "group_id": group_id, "material_type_id": material_type_id}
    if scn is not None:
        out["sync"] = _sync_block(scn, _eager_reconcile(db, session_id, scn, grp.id))
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


async def upload_file_property(db: Session, group_id: int, property_name: str,
                               file) -> dict:
    """Upload a material file (texture_file / spectral_data) for a GROUP.

    No material member is involved: the file is stored and its path returned,
    nothing is written to any material. The save API persists that path into the
    member's property — so a texture can be uploaded before the member exists.

    Stored group-scoped (uploads/groups/<group id>/) rather than under
    uploads/materials/, whose folders are keyed by member id."""
    grp = _group_or_404(db, group_id)
    allowed = _FILE_PROPERTY_EXTENSIONS.get(property_name)
    if allowed is None:
        raise api_error(400, "UNKNOWN_PROPERTY",
                        f"'{property_name}' is not a material file property")

    filename = Path(file.filename or "").name
    if not filename or Path(filename).suffix.lower() not in allowed:
        raise api_error(400, "INVALID_FILE_FORMAT", "File format not supported")

    rel = Path("uploads") / "groups" / str(grp.id) / filename
    dest = settings.data_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    return {"success": True, "property": property_name,
            "path": str(rel).replace("\\", "/")}


async def upload_spectral_data(db: Session, group_id: int, file) -> dict:
    """Dedicated spectral-file upload. Delegates to the shared file handler
    (which enforces .xml and stores the file) and returns just the stored path."""
    out = await upload_file_property(db, group_id, "spectral_data", file)
    return {"success": True, "path": out["path"]}


def spectral_labels(db: Session, group_id: int, path: str) -> dict:
    """The labels of the spectra held in a stored spectral file.

    One spectral file holds MANY spectra, each a <globaldata_vec2 label="…">
    block; the Radiation material names the two it wants in
    reflectivity_spectrum / transmissivity_spectrum (migration 031). Reading them
    back is what lets the client offer pickers instead of free-text boxes — and a
    mistyped label does not fail loudly: RadiationModel warns and falls back to a
    reflectivity of 0 (RadiationModel.cpp:2326), silently blackening the surface
    for the whole simulation.

    DIRECT children of <helios> only — exactly what the engine reads
    (Context::scanXMLForTag walks helios.child(tag)/next_sibling(tag),
    Context_fileIO.cpp:2653). A label found at any deeper nesting is one the
    engine could not resolve, so offering it would recreate the same silent
    failure this endpoint exists to prevent.

    Path handling matches delete_file: .resolve() collapses ../ AND follows
    symlinks before the comparison, so a traversal, an absolute path, another
    group's folder, and a symlink planted inside this one all land outside
    `base` and are refused."""
    grp = _group_or_404(db, group_id)
    base = (settings.data_dir / "uploads" / "groups" / str(grp.id)).resolve()
    target = (settings.data_dir / path).resolve()
    if target.parent != base:
        raise api_error(400, "INVALID_PATH", "Path is not in this group's uploads")
    if not target.is_file():
        raise api_error(404, "FILE_NOT_FOUND", "File not found")

    # One ParseError covers every bad file: a ZIP or binary renamed .xml, an
    # empty file, and hostile XML — expat refuses external entities (XXE) and
    # entity amplification (billion laughs) on its own, so nothing else is needed.
    try:
        root = ET.parse(target).getroot()
    except ET.ParseError:
        root = None
    if root is None or root.tag != "helios":
        raise api_error(400, "INVALID_FILE_FORMAT", "File is not a valid spectral data file")

    return {"labels": [label for el in root.findall("globaldata_vec2")
                       if (label := el.get("label"))]}


def delete_file(db: Session, group_id: int, path: str) -> dict:
    """Delete an uploaded file from this group's upload folder.

    Refused while the path is still referenced by a library value (material_data)
    or by a frozen per-geometry snapshot (object_property_data): those resolve the
    file from disk until every scenario syncs, so deleting it would silently drop
    their texture. Only files inside uploads/groups/<group id>/ are reachable."""
    grp = _group_or_404(db, group_id)
    base = (settings.data_dir / "uploads" / "groups" / str(grp.id)).resolve()
    target = (settings.data_dir / path).resolve()
    if target.parent != base:
        raise api_error(400, "INVALID_PATH", "Path is not in this group's uploads")

    in_use = (
        db.query(MaterialData.value).filter(MaterialData.value == path).first()
        or db.query(ObjectPropertyData.value)
        .filter(ObjectPropertyData.value == path).first()
    )
    if in_use:
        raise api_error(409, "FILE_IN_USE", "File is still used by a material")

    if not target.is_file():
        raise api_error(404, "FILE_NOT_FOUND", "File not found")
    target.unlink()
    return {"success": True, "path": path}
