"""
Milestone-2 catalog endpoints (global, read-only).

    GET /api/catalog/datatypes
    GET /api/catalog/object-types
    GET /api/catalog/material-types

Spec: docs/api/milestone-2-materials-geometry.md §4 (helios_gui repo).
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services import catalog_service

router = APIRouter()


@router.get("/datatypes")
def list_datatypes(db: Session = Depends(get_db)):
    return catalog_service.list_datatypes(db)


@router.get("/object-types")
def list_object_types(db: Session = Depends(get_db)):
    return catalog_service.list_object_types(db)


@router.get("/material-types")
def list_material_types(db: Session = Depends(get_db)):
    return catalog_service.list_material_types(db)


@router.get("/model-types")
def list_model_types(db: Session = Depends(get_db)):
    return catalog_service.list_model_types(db)
