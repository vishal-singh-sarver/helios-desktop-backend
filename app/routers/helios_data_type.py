"""
Router for helios_data_types — global master-data catalog.

Mounted at /api/data-types in main.py. Not session-scoped per the locked
design: any caller can read/create/update/delete entries.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.data_catalog import (
    HeliosDataTypeCreateRequest,
    HeliosDataTypeUpdateRequest,
)
from app.services import data_type_service

router = APIRouter()


@router.post("/", status_code=201)
async def create_data_type(
    req: HeliosDataTypeCreateRequest, db: Session = Depends(get_db)
):
    return data_type_service.create_data_type(req.data_type, req.description, db)


@router.get("/")
async def list_data_types(db: Session = Depends(get_db)):
    return data_type_service.list_data_types(db)


@router.get("/with-units")
async def list_data_types_with_units(db: Session = Depends(get_db)):
    """Return all data types with their data_units nested under each."""
    return data_type_service.list_data_types_with_units(db)


@router.get("/{data_type_id}")
async def get_data_type(data_type_id: int, db: Session = Depends(get_db)):
    return data_type_service.get_data_type(data_type_id, db)


@router.patch("/{data_type_id}")
async def update_data_type(
    data_type_id: int,
    req: HeliosDataTypeUpdateRequest,
    db: Session = Depends(get_db),
):
    return data_type_service.update_data_type(
        data_type_id, req.data_type, req.description, db
    )


@router.delete("/{data_type_id}")
async def delete_data_type(data_type_id: int, db: Session = Depends(get_db)):
    return data_type_service.delete_data_type(data_type_id, db)
