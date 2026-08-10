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

PATCH /groups/{id}/rename takes NO ?scenario_id= — a name change cannot drift
applied state (which keys off material_group_id), so there is nothing to
reconcile.
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import get_session_id
from app.db.database import get_db
from app.schemas.material_library import (
    GroupMaterialIn,
    GroupMaterialPutRequest,
    MaterialGroupCreateRequest,
    MaterialGroupPutRequest,
    MaterialGroupRenameRequest,
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


@router.patch(_BASE + "/groups/{group_id}/rename")
def rename_group(
    group_id: int,
    body: MaterialGroupRenameRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Rename the group only — members untouched (PUT would replace them).
    No ?scenario_id=: a name change cannot drift applied state."""
    return svc.rename_group(db, session_id, group_id, body.name)


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


@router.put(_BASE + "/groups/{group_id}/materials/{material_type_id}")
def update_group_material(
    group_id: int,
    material_type_id: int,
    body: GroupMaterialPutRequest,
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


@router.post(_BASE + "/groups/{group_id}/files/{property_name}")
async def upload_file_property(
    group_id: int,
    property_name: str,
    file: UploadFile = File(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Store a material file and return its path. No material member needed —
    the save API writes the returned path into the member's property."""
    return await svc.upload_file_property(db, group_id, property_name, file)


@router.post(_BASE + "/groups/{group_id}/spectral")
async def upload_spectral(
    group_id: int,
    file: UploadFile = File(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Dedicated spectral upload — same member-less flow, returns the path."""
    return await svc.upload_spectral_data(db, group_id, file)


@router.get(_BASE + "/groups/{group_id}/spectral/labels")
def spectral_labels(
    group_id: int,
    path: str = Query(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """The spectrum labels inside a stored spectral file, so the client can offer
    reflectivity_spectrum / transmissivity_spectrum as pickers rather than
    free-text. `path` is the value the upload returned."""
    return svc.spectral_labels(db, group_id, path)


@router.delete(_BASE + "/groups/{group_id}/files")
def delete_file(
    group_id: int,
    path: str = Query(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    """Delete an uploaded file. 409 while any material or frozen per-geometry
    snapshot still references it."""
    return svc.delete_file(db, group_id, path)
