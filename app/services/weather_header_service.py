"""
Per-scenario weather header service.

Each scenario has a list of headers that map a CSV column name to a
(helios_data_type, data_unit) pair from the global catalog. Bulk ops:
GET reads the list, PUT replaces it atomically, DELETE clears it.
Single-row ops: PATCH /{header_id} updates one row; DELETE /{header_id}
removes one row.

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


def serialize(row: WeatherDataHeader) -> dict:
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
        "headers": [serialize(r) for r in rows],
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
        "headers": [serialize(r) for r in rows],
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


def delete_header(
    session_id: str,
    project_id: str,
    scenario_id: str,
    header_id: int,
    db: Session,
) -> dict:
    """Delete one header row and best-effort NaN-clear its PyHelios cells.

    PyHelios v0.1.19 has no remove API, so the cells under the header's
    `str(id)` label are overwritten with NaN — same approach as
    `weather_service.delete()` for column-clear. The label itself stays
    visible in `listTimeseriesVariables()`; that's a known limitation of
    the PyHelios version.

    Order: SQL delete first (transactional), then PyHelios NaN as
    best-effort. If the PyHelios cleanup throws (label never registered
    because the column was created with values=[]), we swallow it — the
    SQL row is the source of truth.
    """
    sctx = _resolve_scenario(session_id, project_id, scenario_id, db)

    row = (
        db.query(WeatherDataHeader)
        .filter_by(id=header_id, scenario_id=scenario_id)
        .first()
    )
    if row is None:
        raise HTTPException(404, f"header {header_id} not found in scenario")

    label = str(row.id)

    try:
        db.delete(row)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to delete header")

    # Best-effort PyHelios cleanup. A column with values=[] never registered
    # a label, in which case getTimeseriesLength raises — that's fine, swallow.
    if sctx.context is not None:
        ctx = sctx.context
        try:
            n = ctx.getTimeseriesLength(label)
            for i in range(n):
                d = ctx.queryTimeseriesDate(label, i)
                t = ctx.queryTimeseriesTime(label, i)
                ctx.updateTimeseriesData(label, d, t, float("nan"))
        except Exception:
            pass

    return {"success": True, "header_id": header_id}


def update_header(
    session_id: str,
    project_id: str,
    scenario_id: str,
    header_id: int,
    data: dict,
    db: Session,
) -> dict:
    """Partial update of one header. Editable fields: name, datatype FK,
    unit FK, display_order.

    Auth + scope: the header must belong to the scenario in the URL, which
    must belong to the calling session. Cross-scenario access yields 404.

    Per-scenario uniqueness is checked against (name) and (display_order),
    excluding the row being updated so a no-op patch doesn't self-collide.
    The unit/type consistency invariant is verified against the post-update
    state and only when BOTH are non-null (partial mappings remain legal
    per migration 009).
    """
    _resolve_scenario(session_id, project_id, scenario_id, db)

    row = (
        db.query(WeatherDataHeader)
        .filter_by(id=header_id, scenario_id=scenario_id)
        .first()
    )
    if row is None:
        raise HTTPException(404, f"header {header_id} not found in scenario")

    # Extract values from data dict for validation
    name = data.get("name")
    helios_data_type_id = data.get("helios_data_type_id")
    unit_id = data.get("unit_id")
    display_order = data.get("display_order")

    # Reserved-name guard mirrors the column-add flow.
    if name is not None and name in ("date", "time"):
        raise HTTPException(400, f"name '{name}' is reserved")

    # Per-scenario uniqueness, excluding self.
    if name is not None and name != row.name:
        clash = (
            db.query(WeatherDataHeader.id)
            .filter(
                WeatherDataHeader.scenario_id == scenario_id,
                WeatherDataHeader.name == name,
                WeatherDataHeader.id != header_id,
            )
            .first()
        )
        if clash is not None:
            raise HTTPException(
                409, f"name '{name}' already exists in scenario"
            )

    if display_order is not None and display_order != row.display_order:
        clash = (
            db.query(WeatherDataHeader.id)
            .filter(
                WeatherDataHeader.scenario_id == scenario_id,
                WeatherDataHeader.display_order == display_order,
                WeatherDataHeader.id != header_id,
            )
            .first()
        )
        if clash is not None:
            raise HTTPException(
                409, f"display_order {display_order} already in use in scenario"
            )

    # FK existence checks before computing post-update consistency, so the
    # 404 is clean (rather than an IntegrityError on commit).
    if helios_data_type_id is not None:
        if db.get(HeliosDataType, helios_data_type_id) is None:
            raise HTTPException(
                404, f"helios_data_type_id {helios_data_type_id} not found"
            )

    new_unit_row = None
    if unit_id is not None:
        new_unit_row = db.get(DataUnit, unit_id)
        if new_unit_row is None:
            raise HTTPException(404, f"unit_id {unit_id} not found")

    # Consistency check against the POST-update pair. Only enforced when both
    # FKs end up non-null; one-sided partial mapping stays legal.
    # Note: we use "in data" to check if the user is explicitly setting a field.
    final_dt = (
        helios_data_type_id if "helios_data_type_id" in data else row.helios_data_type_id
    )
    final_unit = unit_id if "unit_id" in data else row.unit_id

    if final_dt is not None and final_unit is not None:
        unit_for_check = new_unit_row if new_unit_row is not None else db.get(DataUnit, final_unit)
        if unit_for_check.data_type_id != final_dt:
            raise HTTPException(
                400,
                f"unit '{unit_for_check.unit}' belongs to data_type "
                f"{unit_for_check.data_type_id}, not {final_dt}",
            )

    # ── Apply Updates ──
    for key, val in data.items():
        setattr(row, key, val)

    try:
        db.commit()
        db.refresh(row)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to update header")

    return {"success": True, "header": serialize(row)}
