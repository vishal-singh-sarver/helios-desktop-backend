"""
Persisted material library endpoints (milestone 2, spec §7).

All under:
    /api/materials/project/{project_id}/library...

Required header: session-id. Distinct from the legacy in-memory
/api/materials/* label endpoints, which remain preview-only.
"""
from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import get_session_id
from app.db.database import get_db
from app.schemas.material_library import (
    MaterialCreateRequest,
    MaterialRenameRequest,
    MaterialUpdateRequest,
)
from app.services import material_library_service as svc

router = APIRouter()

_BASE = "/project/{project_id}/library"


@router.post(_BASE, status_code=201)
def create_material(
    project_id: str,
    body: MaterialCreateRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.create_material(db, session_id, project_id, body)


@router.get(_BASE)
def list_materials(
    project_id: str,
    search: str | None = Query(default=None),
    material_type_id: int | None = Query(default=None),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.list_materials(db, session_id, project_id, search, material_type_id)


@router.get(_BASE + "/next-name")
def next_name(
    project_id: str,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.next_name(db, session_id, project_id)


@router.get(_BASE + "/{material_id}")
def get_material(
    project_id: str,
    material_id: int,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.get_material(db, session_id, project_id, material_id)


@router.patch(_BASE + "/{material_id}")
def update_material(
    project_id: str,
    material_id: int,
    body: MaterialUpdateRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.update_material(db, session_id, project_id, material_id, body)


@router.patch(_BASE + "/{material_id}/rename")
def rename_material(
    project_id: str,
    material_id: int,
    body: MaterialRenameRequest,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.rename_material(db, session_id, project_id, material_id, body.name)


@router.delete(_BASE + "/{material_id}")
def delete_material(
    project_id: str,
    material_id: int,
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return svc.delete_material(db, session_id, project_id, material_id)


@router.post(_BASE + "/{material_id}/files/{property_name}")
async def upload_file_property(
    project_id: str,
    material_id: int,
    property_name: str,
    file: UploadFile = File(...),
    session_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
):
    return await svc.upload_file_property(db, session_id, project_id,
                                          material_id, property_name, file)
