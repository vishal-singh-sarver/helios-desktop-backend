"""
Per-scenario weather header service.

Each scenario has a list of headers that map a CSV column name to a
(helios_data_type, data_unit) pair from the global catalog. The list is
managed as a single bulk resource: GET returns it, PUT atomically replaces
it, DELETE clears it.

Auth is delegated to scenario_service._resolve_scenario (404 if the project
or scenario doesn't belong to the calling session).

Service-level invariant (per doc Section 3):
    For each header, the chosen `unit_id` must point at a `data_unit` whose
    `data_type_id` matches the header's `helios_data_type_id`. SQLite can't
    enforce this as a CHECK; the service rejects mismatches at PUT time.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import DataUnit, HeliosDataType, WeatherDataHeader
from app.services.scenario_service import _resolve_scenario


def _serialize(row: WeatherDataHeader) -> dict:
    return {
        "id": row.id,
        "scenario_id": row.scenario_id,
        "name": row.name,
        "helios_data_type_id": row.helios_data_type_id,
        "unit_id": row.unit_id,
        "status": bool(row.status),
        "display_order": row.display_order,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def get_headers(
    session_id: str, project_id: str, scenario_id: str, db: Session
) -> dict:
    """Return the scenario's header set, ordered by display_order ascending."""
    _resolve_scenario(session_id, project_id, scenario_id, db)

    rows = (
        db.query(WeatherDataHeader)
        .filter_by(scenario_id=scenario_id)
        .order_by(WeatherDataHeader.display_order.asc())
        .all()
    )
    return {
        "success": True,
        "count": len(rows),
        "headers": [_serialize(r) for r in rows],
    }


def replace_headers(
    session_id: str,
    project_id: str,
    scenario_id: str,
    items: list[dict],
    db: Session,
) -> dict:
    """Atomically replace the scenario's header set.

    1. Validates auth.
    2. Per item, confirms helios_data_type_id and unit_id exist AND that the
       unit actually belongs to the declared type.
    3. Deletes all existing headers for the scenario, inserts the new set,
       commits as one transaction. On any failure mid-write, rolls back so
       the original set survives.

    An empty `items` list clears the scenario's headers (delete-then-insert-
    nothing). Pydantic-level validation (duplicate name / display_order)
    happens BEFORE this function runs.
    """
    _resolve_scenario(session_id, project_id, scenario_id, db)

    # ── Per-item consistency check (DB-aware) ──
    # Run BEFORE any mutation so a bad item doesn't half-delete the existing set.
    for i, item in enumerate(items):
        unit = db.get(DataUnit, item["unit_id"])
        if unit is None:
            raise HTTPException(
                404, f"Header {i}: unit_id {item['unit_id']} not found"
            )
        if db.get(HeliosDataType, item["helios_data_type_id"]) is None:
            raise HTTPException(
                404,
                f"Header {i}: helios_data_type_id {item['helios_data_type_id']} not found",
            )
        if unit.data_type_id != item["helios_data_type_id"]:
            raise HTTPException(
                400,
                f"Header {i}: unit '{unit.unit}' belongs to data_type "
                f"{unit.data_type_id}, not {item['helios_data_type_id']}",
            )

    # ── Atomic replace: delete-all + insert-all in one transaction ──
    # The implicit transaction (autobegin) provides atomicity. If insert
    # fails, rollback undoes the delete too.
    try:
        db.query(WeatherDataHeader).filter_by(scenario_id=scenario_id).delete()
        for item in items:
            db.add(WeatherDataHeader(scenario_id=scenario_id, **item))
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to replace headers")

    rows = (
        db.query(WeatherDataHeader)
        .filter_by(scenario_id=scenario_id)
        .order_by(WeatherDataHeader.display_order.asc())
        .all()
    )
    return {
        "success": True,
        "count": len(rows),
        "headers": [_serialize(r) for r in rows],
    }


def clear_headers(
    session_id: str, project_id: str, scenario_id: str, db: Session
) -> dict:
    """Remove all headers for the scenario. Returns the count removed."""
    _resolve_scenario(session_id, project_id, scenario_id, db)

    try:
        removed = (
            db.query(WeatherDataHeader)
            .filter_by(scenario_id=scenario_id)
            .delete()
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to clear headers")

    return {"success": True, "count": removed}
