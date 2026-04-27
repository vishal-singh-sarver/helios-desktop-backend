"""
Router for data_units — global master-data catalog.

Mounted at /api/data-units in main.py. The list endpoint accepts an optional
`?data_type_id=N` query param to filter units by their parent type.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.data_catalog import (
    DataUnitCreateRequest,
    DataUnitUpdateRequest,
)
from app.services import data_unit_service

router = APIRouter()


@router.post("/", status_code=201)
async def create_data_unit(
    req: DataUnitCreateRequest, db: Session = Depends(get_db)
):
    return data_unit_service.create_data_unit(
        req.unit, req.alias, req.data_type_id, req.min, req.max, db
    )


@router.get("/")
async def list_data_units(
    data_type_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return data_unit_service.list_data_units(data_type_id, db)


@router.get("/{data_unit_id}")
async def get_data_unit(data_unit_id: int, db: Session = Depends(get_db)):
    return data_unit_service.get_data_unit(data_unit_id, db)


@router.patch("/{data_unit_id}")
async def update_data_unit(
    data_unit_id: int,
    req: DataUnitUpdateRequest,
    db: Session = Depends(get_db),
):
    return data_unit_service.update_data_unit(
        data_unit_id, req.unit, req.alias, req.min, req.max, db
    )


@router.delete("/{data_unit_id}")
async def delete_data_unit(data_unit_id: int, db: Session = Depends(get_db)):
    return data_unit_service.delete_data_unit(data_unit_id, db)
