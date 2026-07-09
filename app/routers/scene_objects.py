"""
Persisted scene-object endpoints (milestone 2, spec §5/§6/§8).

All under:
    /api/geometry/project/{project_id}/scenario/{scenario_id}/...

Required header: session-id. The legacy in-memory /api/geometry/* primitive
endpoints remain preview-only and never write these tables.
"""
import asyncio

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.dependencies import get_session_id
from app.db.database import get_db
from app.schemas.scene_objects import (
    AssignMaterialGroupRequest,
    GroupAssignmentUpdateRequest,
    GroupCreateRequest,
    GroupRenameRequest,
    GroupVisibilityRequest,
    MaterialSyncRequest,
    SceneObjectCreateRequest,
    SceneObjectRenameRequest,
    SceneObjectUpdateRequest,
    ScenarioModelsUpdateRequest,
)
from app.services import scene_object_service as svc

router = APIRouter()

_BASE = "/project/{project_id}/scenario/{scenario_id}"


# ── Geometry (spec §5) ───────────────────────────────────────────────────────


@router.post(_BASE + "/objects", status_code=201)
def create_object(
    project_id: str,
    scenario_id: str,
    body: SceneObjectCreateRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.create_object(db, session_id, project_id, scenario_id, body)


@router.get(_BASE + "/objects")
def list_objects(
    project_id: str,
    scenario_id: str,
    search: str | None = Query(default=None),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.list_objects(db, session_id, project_id, scenario_id, search)


@router.get(_BASE + "/objects/next-name")
def next_name(
    project_id: str,
    scenario_id: str,
    object_type: str = Query(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.next_name(db, session_id, project_id, scenario_id, object_type)


@router.get(_BASE + "/objects/{object_id}")
def get_object(
    project_id: str,
    scenario_id: str,
    object_id: int,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.get_object(db, session_id, project_id, scenario_id, object_id)


@router.patch(_BASE + "/objects/{object_id}")
def update_object(
    project_id: str,
    scenario_id: str,
    object_id: int,
    body: SceneObjectUpdateRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.update_object(db, session_id, project_id, scenario_id, object_id, body)


@router.patch(_BASE + "/objects/{object_id}/rename")
def rename_object(
    project_id: str,
    scenario_id: str,
    object_id: int,
    body: SceneObjectRenameRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.rename_object(db, session_id, project_id, scenario_id, object_id, body.name)


@router.delete(_BASE + "/objects/{object_id}")
def delete_object(
    project_id: str,
    scenario_id: str,
    object_id: int,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.delete_object(db, session_id, project_id, scenario_id, object_id)


@router.get(_BASE + "/objects/{object_id}/geometry/binary")
async def get_object_geometry_binary(
    project_id: str,
    scenario_id: str,
    object_id: int,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """getObjectGeometry (spec §5.8) — binary buffer for the stored UUIDs."""
    content = await asyncio.to_thread(
        svc.get_object_geometry_binary, db, session_id, project_id, scenario_id, object_id
    )
    return Response(content=content, media_type="application/octet-stream")


@router.get(_BASE + "/geometry/binary")
async def get_scene_geometry_binary(
    project_id: str,
    scenario_id: str,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Whole-scene binary for the scenario's persisted geometry. Fetching
    hydrates (spec §12.3) — use this as the first viewport load."""
    content = await asyncio.to_thread(
        svc.get_scene_geometry_binary, db, session_id, project_id, scenario_id
    )
    return Response(content=content, media_type="application/octet-stream")


# ── Scenario run configuration (spec §5.9) ───────────────────────────────────


@router.get(_BASE + "/models")
def get_scenario_models(
    project_id: str,
    scenario_id: str,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Which models run on the Run button for this scenario."""
    return svc.get_scenario_models(db, session_id, project_id, scenario_id)


@router.patch(_BASE + "/models")
def update_scenario_models(
    project_id: str,
    scenario_id: str,
    body: ScenarioModelsUpdateRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.update_scenario_models(db, session_id, project_id, scenario_id, body.models)


# ── Groups (spec §6) ─────────────────────────────────────────────────────────


@router.post(_BASE + "/groups", status_code=201)
def create_group(
    project_id: str,
    scenario_id: str,
    body: GroupCreateRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.create_group(db, session_id, project_id, scenario_id, body)


@router.get(_BASE + "/groups")
def list_groups(
    project_id: str,
    scenario_id: str,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.list_groups(db, session_id, project_id, scenario_id)


@router.patch(_BASE + "/groups/{group_id}/rename")
def rename_group(
    project_id: str,
    scenario_id: str,
    group_id: int,
    body: GroupRenameRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.rename_group(db, session_id, project_id, scenario_id, group_id, body.name)


@router.delete(_BASE + "/groups/{group_id}")
def delete_group(
    project_id: str,
    scenario_id: str,
    group_id: int,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.delete_group(db, session_id, project_id, scenario_id, group_id)


@router.patch(_BASE + "/groups/{group_id}/visibility")
def update_group_visibility(
    project_id: str,
    scenario_id: str,
    group_id: int,
    body: GroupVisibilityRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.update_group_visibility(db, session_id, project_id, scenario_id, group_id, body)


@router.delete(_BASE + "/groups/{group_id}/objects")
def delete_group_objects(
    project_id: str,
    scenario_id: str,
    group_id: int,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.delete_group_objects(db, session_id, project_id, scenario_id, group_id)


# ── Material-group assignment (migration 022) ────────────────────────────────


@router.post(_BASE + "/objects/{object_id}/material-groups", status_code=201)
def assign_material_group(
    project_id: str,
    scenario_id: str,
    object_id: int,
    body: AssignMaterialGroupRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.assign_material_group(db, session_id, project_id, scenario_id,
                                     object_id, body)


@router.get(_BASE + "/objects/{object_id}/material-groups")
def list_assignments(
    project_id: str,
    scenario_id: str,
    object_id: int,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.list_assignments(db, session_id, project_id, scenario_id, object_id)


@router.patch(_BASE + "/objects/{object_id}/material-groups/{group_id}")
def update_group_assignment(
    project_id: str,
    scenario_id: str,
    object_id: int,
    group_id: int,
    body: GroupAssignmentUpdateRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.update_group_assignment(db, session_id, project_id, scenario_id,
                                       object_id, group_id, body)


@router.delete(_BASE + "/objects/{object_id}/material-groups/{group_id}")
def unassign_material_group(
    project_id: str,
    scenario_id: str,
    object_id: int,
    group_id: int,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.unassign_material_group(db, session_id, project_id, scenario_id,
                                       object_id, group_id)


# ── Scenario material-sync (migration 022) ───────────────────────────────────


@router.get(_BASE + "/material-sync")
def get_material_sync(
    project_id: str,
    scenario_id: str,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Drift report: is this scenario's applied material state in sync with
    the library, and what would PUT change (dry-run)."""
    return svc.get_material_sync(db, session_id, project_id, scenario_id)


@router.put(_BASE + "/material-sync")
def apply_material_sync(
    project_id: str,
    scenario_id: str,
    body: MaterialSyncRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.apply_material_sync(db, session_id, project_id, scenario_id, body)
