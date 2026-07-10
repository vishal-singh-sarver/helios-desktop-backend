"""
Material-group library endpoints (migration 022).

All under:
    /api/materials/library/groups...

No project segment — material groups are GLOBAL (project_id/scenario_id are
creation provenance in the request body). Required header: session-id.

This router shares its /api/materials mount with the legacy in-memory label
router (app/routers/materials.py). Keep every route here ≥2 segments under
/library and never define a bare "/library" route — it would be captured by
the legacy single-segment routes (e.g. DELETE /{label}).

PUT / DELETE / file-upload accept ?scenario_id= — the ACTIVE scenario, which
is reconciled + repainted inline (full-cascade semantics). Other scenarios
keep their applied state and settle drift via the material-sync APIs.
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import get_session_id
from app.db.database import get_db
from app.schemas.material_library import (
    GroupMaterialIn,
    GroupMaterialPatchRequest,
    MaterialGroupCreateRequest,
    MaterialGroupPutRequest,
)
from app.services import material_library_service as svc

router = APIRouter()

_BASE = "/library"


@router.post(_BASE + "/groups", status_code=201)
def create_group(
    body: MaterialGroupCreateRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.create_group(db, session_id, body)


@router.get(_BASE + "/groups")
def list_groups(
    search: Optional[str] = Query(default=None),
    material_type_id: Optional[int] = Query(default=None),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.list_groups(db, session_id, search, material_type_id)


# Registered BEFORE /groups/{group_id}: group_id is int-typed, so a later
# /groups/next-name request would otherwise 422 on the path param.
@router.get(_BASE + "/groups/next-name")
def next_name(
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.next_name(db, session_id)


@router.get(_BASE + "/groups/{group_id}")
def get_group(
    group_id: int,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.get_group(db, session_id, group_id)


@router.put(_BASE + "/groups/{group_id}")
def update_group(
    group_id: int,
    body: MaterialGroupPutRequest,
    scenario_id: Optional[str] = Query(default=None),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.update_group(db, session_id, group_id, body, scenario_id)


@router.delete(_BASE + "/groups/{group_id}")
def delete_group(
    group_id: int,
    scenario_id: Optional[str] = Query(default=None),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.delete_group(db, session_id, group_id, scenario_id)


# ── Per-member CRUD (members addressed by material type; a group may be empty) ──


@router.post(_BASE + "/groups/{group_id}/materials", status_code=201)
def add_group_material(
    group_id: int,
    body: GroupMaterialIn,
    scenario_id: Optional[str] = Query(default=None),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.add_group_material(db, session_id, group_id, body, scenario_id)


@router.patch(_BASE + "/groups/{group_id}/materials/{material_type_id}")
def update_group_material(
    group_id: int,
    material_type_id: int,
    body: GroupMaterialPatchRequest,
    scenario_id: Optional[str] = Query(default=None),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.update_group_material(db, session_id, group_id, material_type_id,
                                     body, scenario_id)


@router.delete(_BASE + "/groups/{group_id}/materials/{material_type_id}")
def remove_group_material(
    group_id: int,
    material_type_id: int,
    scenario_id: Optional[str] = Query(default=None),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.remove_group_material(db, session_id, group_id, material_type_id,
                                     scenario_id)


@router.post(_BASE + "/groups/{group_id}/materials/{material_type_id}/files/{property_name}")
async def upload_file_property(
    group_id: int,
    material_type_id: int,
    property_name: str,
    scenario_id: Optional[str] = Query(default=None),
    file: UploadFile = File(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return await svc.upload_file_property(db, session_id, group_id,
                                          material_type_id, property_name,
                                          file, scenario_id)
