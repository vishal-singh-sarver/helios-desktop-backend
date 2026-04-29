"""
Master-data service for data_units.

Each unit belongs to one helios_data_type (CASCADE on parent delete).
Optional min/max range hints. Catalog is global per the locked design.

The schema enforces UNIQUE(data_type_id, unit) so the same unit name
("Celsius") can exist under different types. Per the doc, update operations
must NOT change data_type_id (immutable). The DataUnitUpdateRequest schema
omits the field, so any value the client sends is silently ignored.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import DataUnit, HeliosDataType, WeatherDataHeader


def serialize(row: DataUnit) -> dict:
    return {
        "id": row.id,
        "unit": row.unit,
        "alias": row.alias,
        "data_type_id": row.data_type_id,
        "min": row.min,
        "max": row.max,
        "to_base_factor": row.to_base_factor,
        "to_base_offset": row.to_base_offset,
        "is_base": bool(row.is_base),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _existing_base_for_type(data_type_id: int, db: Session) -> DataUnit | None:
    return (
        db.query(DataUnit)
        .filter(DataUnit.data_type_id == data_type_id, DataUnit.is_base == 1)
        .first()
    )


def create_data_unit(
    unit: str,
    alias: str | None,
    data_type_id: int,
    min: float | None,
    max: float | None,
    to_base_factor: float,
    to_base_offset: float,
    is_base: bool,
    db: Session,
) -> dict:
    # Verify parent type exists upfront, so we 404 cleanly instead of letting
    # SQLite raise a generic FK violation.
    parent = (
        db.query(HeliosDataType)
        .filter(HeliosDataType.id == data_type_id)
        .first()
    )
    if parent is None:
        raise HTTPException(404, f"data_type_id {data_type_id} not found")

    # Pre-check the one-base-per-type invariant so the 409 is clear instead
    # of leaking the partial unique index name through an IntegrityError.
    if is_base:
        existing_base = _existing_base_for_type(data_type_id, db)
        if existing_base is not None:
            raise HTTPException(
                409,
                f"data_type {data_type_id} already has a base unit "
                f"(id={existing_base.id}, '{existing_base.unit}')",
            )

    row = DataUnit(
        unit=unit,
        alias=alias,
        data_type_id=data_type_id,
        min=min,
        max=max,
        to_base_factor=to_base_factor,
        to_base_offset=to_base_offset,
        is_base=1 if is_base else 0,
    )
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, f"unit '{unit}' already exists for data_type {data_type_id}"
        )
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to create data_unit")
    return {"success": True, "data_unit": serialize(row)}


def list_data_units(data_type_id: int | None, db: Session) -> dict:
    """List units. Optional filter by parent data_type_id."""
    q = db.query(DataUnit)
    if data_type_id is not None:
        q = q.filter(DataUnit.data_type_id == data_type_id)
    rows = q.order_by(DataUnit.id.asc()).all()
    return {"data_units": [serialize(r) for r in rows]}


def get_data_unit(data_unit_id: int, db: Session) -> dict:
    row = db.query(DataUnit).filter(DataUnit.id == data_unit_id).first()
    if row is None:
        raise HTTPException(404, f"data_unit {data_unit_id} not found")
    return {"data_unit": serialize(row)}


def update_data_unit(
    data_unit_id: int,
    unit: str | None,
    alias: str | None,
    min: float | None,
    max: float | None,
    to_base_factor: float | None,
    to_base_offset: float | None,
    is_base: bool | None,
    db: Session,
) -> dict:
    """Partial update. data_type_id is intentionally absent — a unit's parent
    type is immutable per the locked design. Fields with None are unchanged."""
    row = db.query(DataUnit).filter(DataUnit.id == data_unit_id).first()
    if row is None:
        raise HTTPException(404, f"data_unit {data_unit_id} not found")

    # Pre-check before flipping is_base on, so we return a clear 409 if some
    # OTHER unit already holds the base flag for this data_type.
    if is_base is True and not row.is_base:
        existing_base = _existing_base_for_type(row.data_type_id, db)
        if existing_base is not None:
            raise HTTPException(
                409,
                f"data_type {row.data_type_id} already has a base unit "
                f"(id={existing_base.id}, '{existing_base.unit}')",
            )

    if unit is not None:
        row.unit = unit
    if alias is not None:
        row.alias = alias
    if min is not None:
        row.min = min
    if max is not None:
        row.max = max
    if to_base_factor is not None:
        row.to_base_factor = to_base_factor
    if to_base_offset is not None:
        row.to_base_offset = to_base_offset
    if is_base is not None:
        row.is_base = 1 if is_base else 0

    try:
        db.commit()
        db.refresh(row)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, f"unit '{unit}' already exists for this data_type"
        )
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to update data_unit")
    return {"success": True, "data_unit": serialize(row)}


def delete_data_unit(data_unit_id: int, db: Session) -> dict:
    """Delete a data_unit. RESTRICT: blocked (409) if any header still uses it."""
    row = db.query(DataUnit).filter(DataUnit.id == data_unit_id).first()
    if row is None:
        raise HTTPException(404, f"data_unit {data_unit_id} not found")

    in_use = (
        db.query(WeatherDataHeader)
        .filter(WeatherDataHeader.unit_id == data_unit_id)
        .count()
    )
    if in_use:
        raise HTTPException(
            409, f"data_unit {data_unit_id} is in use by {in_use} header(s)"
        )

    try:
        db.delete(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"data_unit {data_unit_id} is in use")
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to delete data_unit")
    return {"success": True, "data_unit_id": data_unit_id}
