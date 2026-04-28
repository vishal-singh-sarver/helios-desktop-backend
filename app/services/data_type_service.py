"""
Master-data service for helios_data_types.

Catalog is global per the locked design — NOT session-scoped, no session_id
parameter. Any session can read, create, update, or delete entries.

Conventions match scenario_service.py:
    catch IntegrityError on unique violations -> HTTPException(409)
    catch generic Exception -> rollback + HTTPException(500)
    return dict with success: True
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import DataUnit, HeliosDataType, WeatherDataHeader
from app.services.data_unit_service import serialize as serialize_data_unit


def _serialize(row: HeliosDataType) -> dict:
    return {
        "id": row.id,
        "data_type": row.data_type,
        "description": row.description,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def create_data_type(
    data_type: str, description: str | None, db: Session
) -> dict:
    row = HeliosDataType(data_type=data_type, description=description)
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"data_type '{data_type}' already exists")
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to create data_type")
    return {"success": True, "data_type": _serialize(row)}


def list_data_types(db: Session) -> dict:
    """Return all data types, each with its data_units nested.

    Two flat queries grouped in Python — cheaper for a small catalog than
    a JOIN that returns one row per (type, unit) pair. Types with no
    units come back with `units: []`.

    Ordering: types by id asc, units within each type by id asc.
    """
    types = db.query(HeliosDataType).order_by(HeliosDataType.id.asc()).all()
    units = db.query(DataUnit).order_by(DataUnit.id.asc()).all()

    units_by_type: dict[int, list[dict]] = {}
    for u in units:
        units_by_type.setdefault(u.data_type_id, []).append(serialize_data_unit(u))

    return {
        "data_types": [
            {**_serialize(t), "units": units_by_type.get(t.id, [])}
            for t in types
        ]
    }


def get_data_type(data_type_id: int, db: Session) -> dict:
    row = (
        db.query(HeliosDataType)
        .filter(HeliosDataType.id == data_type_id)
        .first()
    )
    if row is None:
        raise HTTPException(404, f"data_type {data_type_id} not found")
    return {"data_type": _serialize(row)}


def update_data_type(
    data_type_id: int,
    data_type: str | None,
    description: str | None,
    db: Session,
) -> dict:
    """Partial update. Fields with None are left unchanged."""
    row = (
        db.query(HeliosDataType)
        .filter(HeliosDataType.id == data_type_id)
        .first()
    )
    if row is None:
        raise HTTPException(404, f"data_type {data_type_id} not found")

    if data_type is not None:
        row.data_type = data_type
    if description is not None:
        row.description = description

    try:
        db.commit()
        db.refresh(row)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"data_type '{data_type}' already exists")
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to update data_type")
    return {"success": True, "data_type": _serialize(row)}


def delete_data_type(data_type_id: int, db: Session) -> dict:
    """Delete a data_type. RESTRICT: blocked (409) if any header still uses it."""
    row = (
        db.query(HeliosDataType)
        .filter(HeliosDataType.id == data_type_id)
        .first()
    )
    if row is None:
        raise HTTPException(404, f"data_type {data_type_id} not found")

    # Count usage upfront so we can return a useful 409 message instead of
    # waiting for SQLite's generic FK violation.
    in_use = (
        db.query(WeatherDataHeader)
        .filter(WeatherDataHeader.helios_data_type_id == data_type_id)
        .count()
    )
    if in_use:
        raise HTTPException(
            409, f"data_type {data_type_id} is in use by {in_use} header(s)"
        )

    try:
        db.delete(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"data_type {data_type_id} is in use")
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to delete data_type")
    return {"success": True, "data_type_id": data_type_id}
