"""
Material sync / reconcile engine (migration 022).

Library truth (material_group / project_material / material_data) and a
scenario's APPLIED state (object_material_group / object_material /
object_property_data snapshots) are linked only by soft ids — library edits
never cascade into applied rows (the "break point"). This module owns the ONE
diff/apply implementation used by every reconciliation path:

    eager  — group PUT/DELETE/upload with ?scenario_id= (the active scenario)
    lazy   — PUT  /project/{pid}/scenario/{sid}/material-sync
    status — GET  /project/{pid}/scenario/{sid}/material-sync (dry-run)
    assign — the assignment endpoints reuse the materialize/remove primitives

Issue kinds per geometry:
    group_deleted   assignment's group no longer exists → drop assignment +
                    its member rows (snapshots cascade via the composite FK)
    member_removed  materialized row's member no longer exists (or moved) →
                    drop the row
    member_added    live assigned group has a member with no materialized row →
                    insert + snapshot; skipped + reported when another group's
                    row already owns the material type (UNIQUE(so, type))
    values_stale    sync=1 assignment whose snapshot differs from the library →
                    re-snapshot (sync=0 keeps its frozen values forever)

Apply order is load-bearing: deletions → flush → additions → value refreshes.
Otherwise remove-then-re-add of a material type (new member id) would
spuriously conflict with the stale row the same pass deletes.

IMPORT DIRECTION: this module must not import scene_object_service (cycle) —
repainting is the caller's job, driven by the returned per-object
cleared_type_ids (scene_object_service._apply_assignment_change).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import (
    MaterialData,
    MaterialGroup,
    MaterialType,
    ObjectMaterial,
    ObjectMaterialGroup,
    ObjectPropertyData,
    ProjectMaterial,
    PropertyType,
    ScenarioObject,
)


# ── Snapshot primitives (moved from scene_object_service, migration 022) ─────


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
    """Copy the member's CURRENT library values into the (object, member)
    snapshot rows (object_property_data). Run for EVERY materialized member —
    these rows are the single source of truth the viewport re-applies from;
    library edits reach them only through a reconcile (eager or PUT sync)."""
    for row in _frozen_rows(db, so_id, material_id):
        db.delete(row)
    db.flush()
    for pt_id, value in _material_values_canonical(db, material_id).items():
        db.add(ObjectPropertyData(scenario_object_id=so_id, project_material_id=material_id,
                                  property_type_id=pt_id, value=value))
    # The session runs with autoflush=False — flush so follow-up queries in
    # the same request (e.g. freeze-and-edit) see the snapshot rows.
    db.flush()


# ── Materialize / remove primitives (shared with the assignment endpoints) ───


def materialize_member(db: Session, so_id: int, pm: ProjectMaterial) -> None:
    """Insert one materialized row for a group member + snapshot its values.
    Caller is responsible for the type-collision check (or an IntegrityError
    backstop on UNIQUE(scenario_object_id, material_type_id))."""
    db.add(ObjectMaterial(
        scenario_object_id=so_id,
        project_material_id=pm.id,
        material_group_id=pm.material_group_id,
        material_type_id=pm.material_type_id,
    ))
    db.flush()
    _snapshot_frozen(db, so_id, pm.id)


def group_member_rows(db: Session, so_id: int, group_id: int) -> list[ObjectMaterial]:
    """Materialized rows attributed to one group on one geometry — includes
    STALE rows (member/group deleted in the library), which is the point of
    the attribution column."""
    return (
        db.query(ObjectMaterial)
        .filter(
            ObjectMaterial.scenario_object_id == so_id,
            ObjectMaterial.material_group_id == group_id,
        )
        .all()
    )


def find_type_blockers(db: Session, so_ids: list[int], type_ids: list[int],
                       *, exclude_group_id: int | None = None) -> list[ObjectMaterial]:
    """object_material rows on the given geometries occupying any of the given
    material types. STALE rows count — they keep painting, so they keep owning
    their type slot until synced away. exclude_group_id skips rows attributed
    to that group (its own rows are handled by the same reconcile pass)."""
    if not so_ids or not type_ids:
        return []
    q = (
        db.query(ObjectMaterial)
        .filter(
            ObjectMaterial.scenario_object_id.in_(so_ids),
            ObjectMaterial.material_type_id.in_(type_ids),
        )
    )
    if exclude_group_id is not None:
        q = q.filter(ObjectMaterial.material_group_id != exclude_group_id)
    return q.all()


def om_is_live(db: Session, om: ObjectMaterial) -> bool:
    """A materialized row is LIVE when its member still exists in its group and
    the group is still assigned to the geometry; anything else is stale."""
    pm = db.get(ProjectMaterial, om.project_material_id)
    if pm is None or pm.material_group_id != om.material_group_id:
        return False
    if db.get(MaterialGroup, om.material_group_id) is None:
        return False
    return db.get(ObjectMaterialGroup,
                  (om.scenario_object_id, om.material_group_id)) is not None


def blocker_conflict(db: Session, so: ScenarioObject, om: ObjectMaterial) -> dict:
    """Conflict payload for a 409 / sync report naming the row that owns the
    contested material type. Name fields are None when the library rows are
    gone (that is what `stale` means)."""
    mt = db.get(MaterialType, om.material_type_id)
    grp = db.get(MaterialGroup, om.material_group_id)
    out = {
        "object_id": so.id,
        "object_name": so.name,
        "material_type_id": om.material_type_id,
        "material_type": mt.materialtype if mt else None,
        "group_id": om.material_group_id,
        "group_name": grp.name if grp else None,
    }
    if not om_is_live(db, om):
        out["stale"] = True
    return out


# ── Scenario diff ─────────────────────────────────────────────────────────────


def _scenario_diff(db: Session, scenario_id: str, *,
                   group_ids: list[int] | None = None,
                   object_ids: list[int] | None = None) -> list[dict]:
    """Pure-read diff of a scenario's applied state vs library truth.

    Returns one entry per geometry that has work or conflicts:
        {so, issues, del_omg, del_om, additions, refreshes, conflicts}
    additions = [(omg, pm)], refreshes = [(omg, pm, changed_pt_ids)].
    Scoping: group_ids / object_ids restrict which assignments are examined
    (rows attributed to out-of-scope groups are left untouched AND still count
    as type-slot blockers for in-scope additions). An EMPTY list means "touch
    nothing" for both — only None means unscoped.
    """
    scope = set(group_ids) if group_ids is not None else None

    objects = (
        db.query(ScenarioObject)
        .filter(ScenarioObject.scenario_id == scenario_id)
        .order_by(ScenarioObject.created_at, ScenarioObject.id)
        .all()
    )
    if object_ids is not None:
        wanted = set(object_ids)
        objects = [so for so in objects if so.id in wanted]
    if not objects:
        return []
    so_ids = [so.id for so in objects]

    omgs = (
        db.query(ObjectMaterialGroup)
        .filter(ObjectMaterialGroup.scenario_object_id.in_(so_ids))
        # material_group_id breaks created_at ties (second granularity), so
        # which group wins a contested type slot is deterministic.
        .order_by(ObjectMaterialGroup.created_at, ObjectMaterialGroup.material_group_id)
        .all()
    )
    oms = (
        db.query(ObjectMaterial)
        .filter(ObjectMaterial.scenario_object_id.in_(so_ids))
        .all()
    )

    referenced_groups = {r.material_group_id for r in omgs} | {r.material_group_id for r in oms}
    groups: dict[int, MaterialGroup] = {
        g.id: g for g in db.query(MaterialGroup)
        .filter(MaterialGroup.id.in_(referenced_groups)).all()
    } if referenced_groups else {}

    live_group_ids = list(groups.keys())
    members_by_group: dict[int, list[ProjectMaterial]] = {}
    members_by_id: dict[int, ProjectMaterial] = {}
    if live_group_ids:
        for pm in (
            db.query(ProjectMaterial)
            .filter(ProjectMaterial.material_group_id.in_(live_group_ids))
            .order_by(ProjectMaterial.material_type_id)
            .all()
        ):
            members_by_group.setdefault(pm.material_group_id, []).append(pm)
            members_by_id[pm.id] = pm

    type_names = dict(db.query(MaterialType.id, MaterialType.materialtype).all())

    out: list[dict] = []
    for so in objects:
        so_omgs = [r for r in omgs if r.scenario_object_id == so.id]
        so_oms = [r for r in oms if r.scenario_object_id == so.id]
        issues: list[dict] = []
        del_omg: list[ObjectMaterialGroup] = []
        del_om: list[ObjectMaterial] = []
        additions: list[tuple] = []
        refreshes: list[tuple] = []
        conflicts: list[dict] = []

        def _in_scope(gid: int) -> bool:
            return scope is None or gid in scope

        # 1. Assignments whose group is gone → drop assignment + member rows.
        assigned: dict[int, ObjectMaterialGroup] = {}
        for omg in so_omgs:
            grp = groups.get(omg.material_group_id)
            if grp is not None:
                assigned[omg.material_group_id] = omg
                continue
            if not _in_scope(omg.material_group_id):
                continue
            del_omg.append(omg)
            del_om.extend(r for r in so_oms
                          if r.material_group_id == omg.material_group_id)
            issues.append({
                "kind": "group_deleted",
                "group_id": omg.material_group_id,
                "group_name": None,
            })

        scheduled = {(r.scenario_object_id, r.project_material_id) for r in del_om}

        # 2. Materialized rows whose member is gone/moved, or whose group is
        #    not assigned at all (orphans from a partial operation).
        for om in so_oms:
            key = (om.scenario_object_id, om.project_material_id)
            if key in scheduled or not _in_scope(om.material_group_id):
                continue
            pm = members_by_id.get(om.project_material_id)
            member_gone = pm is None or pm.material_group_id != om.material_group_id
            unattached = om.material_group_id not in assigned and om.material_group_id not in {
                r.material_group_id for r in del_omg
            }
            if member_gone or unattached:
                del_om.append(om)
                scheduled.add(key)
                grp = groups.get(om.material_group_id)
                issues.append({
                    "kind": "member_removed",
                    "group_id": om.material_group_id,
                    "group_name": grp.name if grp else None,
                    "material_id": om.project_material_id,
                    "material_type_id": om.material_type_id,
                    "material_type": type_names.get(om.material_type_id),
                })

        # 3. Missing members of live assigned groups. Deletion-before-add
        #    semantics: rows scheduled for deletion do not occupy type slots.
        occupied: dict[int, ObjectMaterial] = {
            r.material_type_id: r for r in so_oms
            if (r.scenario_object_id, r.project_material_id) not in scheduled
        }
        materialized = {
            r.project_material_id for r in so_oms
            if (r.scenario_object_id, r.project_material_id) not in scheduled
        }
        for gid, omg in assigned.items():
            if not _in_scope(gid):
                continue
            grp = groups[gid]
            for pm in members_by_group.get(gid, []):
                if pm.id in materialized:
                    continue
                issue = {
                    "kind": "member_added",
                    "group_id": gid,
                    "group_name": grp.name,
                    "material_id": pm.id,
                    "material_type_id": pm.material_type_id,
                    "material_type": type_names.get(pm.material_type_id),
                }
                blocker = occupied.get(pm.material_type_id)
                if blocker is not None:
                    conflict = blocker_conflict(db, so, blocker)
                    conflict.update({
                        "group_id": gid, "group_name": grp.name,
                        "material_id": pm.id,
                        "blocking_group_id": blocker.material_group_id,
                        "blocking_group_name": (groups[blocker.material_group_id].name
                                                if blocker.material_group_id in groups else None),
                        "blocking_stale": not om_is_live(db, blocker),
                    })
                    conflict.pop("stale", None)
                    issue["conflict"] = conflict
                    conflicts.append(conflict)
                else:
                    additions.append((omg, pm))
                    occupied[pm.material_type_id] = ObjectMaterial(
                        scenario_object_id=so.id, project_material_id=pm.id,
                        material_group_id=gid, material_type_id=pm.material_type_id,
                    )
                issues.append(issue)

        # 4. Snapshot drift for sync=1 assignments (frozen ones never refresh).
        for gid, omg in assigned.items():
            if not _in_scope(gid) or not omg.sync:
                continue
            grp = groups[gid]
            for pm in members_by_group.get(gid, []):
                if pm.id not in materialized:
                    continue
                lib = _material_values_canonical(db, pm.id)
                snap = {
                    row.property_type_id: row.value
                    for row in _frozen_rows(db, so.id, pm.id)
                    if row.value is not None
                }
                if lib == snap:
                    continue
                changed_pt_ids = sorted(
                    pt for pt in set(lib) | set(snap) if lib.get(pt) != snap.get(pt)
                )
                refreshes.append((omg, pm, changed_pt_ids))
                issues.append({
                    "kind": "values_stale",
                    "group_id": gid,
                    "group_name": grp.name,
                    "material_id": pm.id,
                    "material_type_id": pm.material_type_id,
                    "material_type": type_names.get(pm.material_type_id),
                    "changed_property_type_ids": changed_pt_ids,
                })

        if issues:
            out.append({
                "so": so, "issues": issues,
                "del_omg": del_omg, "del_om": del_om,
                "additions": additions, "refreshes": refreshes,
                "conflicts": conflicts,
            })
    return out


def _property_names(db: Session, pt_ids: set[int]) -> dict[int, str]:
    if not pt_ids:
        return {}
    return dict(
        db.query(PropertyType.id, PropertyType.property)
        .filter(PropertyType.id.in_(pt_ids))
        .all()
    )


# ── Public entry points ───────────────────────────────────────────────────────


def compute_sync(db: Session, scenario_id: str) -> dict:
    """Dry-run drift report for one scenario (GET material-sync)."""
    diff = _scenario_diff(db, scenario_id)

    all_pt_ids: set[int] = set()
    for entry in diff:
        for issue in entry["issues"]:
            all_pt_ids.update(issue.get("changed_property_type_ids") or [])
    pt_names = _property_names(db, all_pt_ids)

    objects = []
    for entry in diff:
        issues = []
        for issue in entry["issues"]:
            issue = dict(issue)
            pt_ids = issue.pop("changed_property_type_ids", None)
            if pt_ids is not None:
                issue["changed_properties"] = [pt_names.get(pt, str(pt)) for pt in pt_ids]
            issues.append(issue)
        objects.append({
            "object_id": entry["so"].id,
            "object_name": entry["so"].name,
            "issues": issues,
        })
    return {
        "scenario_id": scenario_id,
        "in_sync": not objects,
        "objects": objects,
    }


def apply_sync(db: Session, scenario_id: str, *,
               group_ids: list[int] | None = None,
               object_ids: list[int] | None = None) -> dict:
    """Reconcile a scenario's applied state to library truth (optionally scoped).

    Mutates within the caller's transaction and does NOT commit — the caller
    commits and then repaints each returned object
    (scene_object_service._apply_assignment_change with cleared_type_ids).
    Conflicts are skipped + reported, never raised (partial success is normal).
    """
    diff = _scenario_diff(db, scenario_id, group_ids=group_ids, object_ids=object_ids)

    removed_groups = removed_members = added_members = refreshed_values = 0
    conflicts: list[dict] = []
    cleared_type_ids: dict[int, list[int]] = {}

    for entry in diff:
        so = entry["so"]
        cleared: set[int] = set()

        for om in entry["del_om"]:
            cleared.add(om.material_type_id)
            db.delete(om)   # snapshot rows cascade via the composite FK
            removed_members += 1
        for omg in entry["del_omg"]:
            db.delete(omg)
            removed_groups += 1
        db.flush()   # deletions land BEFORE additions (type slots freed)

        for _omg, pm in entry["additions"]:
            materialize_member(db, so.id, pm)
            added_members += 1

        for _omg, pm, changed_pt_ids in entry["refreshes"]:
            _snapshot_frozen(db, so.id, pm.id)
            cleared.add(pm.material_type_id)
            refreshed_values += 1

        conflicts.extend(entry["conflicts"])
        if cleared or entry["additions"] or entry["del_omg"] or entry["del_om"]:
            cleared_type_ids[so.id] = sorted(cleared)

    return {
        "applied": {
            "removed_groups": removed_groups,
            "removed_members": removed_members,
            "added_members": added_members,
            "refreshed_values": refreshed_values,
        },
        "conflicts": conflicts,
        "cleared_type_ids": cleared_type_ids,
    }
