"""
Request bodies for the master-data catalog endpoints.

Two resources, both global (not session-scoped):
    helios_data_types  — kinds of measurements (Temperature, Humidity, ...)
    data_units         — units belonging to a data type (Celsius → Temperature, ...)
"""
from pydantic import BaseModel, field_validator, model_validator


# ─── helios_data_types ───────────────────────────────────────────────────────


class HeliosDataTypeCreateRequest(BaseModel):
    """POST /api/data-types"""

    data_type: str
    description: str | None = None

    @field_validator("data_type")
    @classmethod
    def _trim(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("data_type is required")
        if len(v) > 50:
            raise ValueError("data_type must be 50 characters or fewer")
        return v


class HeliosDataTypeUpdateRequest(BaseModel):
    """PATCH /api/data-types/{id} — partial update."""

    data_type: str | None = None
    description: str | None = None

    @field_validator("data_type")
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("data_type cannot be empty")
        if len(v) > 50:
            raise ValueError("data_type must be 50 characters or fewer")
        return v


# ─── data_units ──────────────────────────────────────────────────────────────


class DataUnitCreateRequest(BaseModel):
    """POST /api/data-units

    Conversion fields (`to_base_factor`, `to_base_offset`, `is_base`) describe
    the affine map back to the data type's canonical unit:
        value_in_base = value * to_base_factor + to_base_offset
    Only one unit per data_type may have is_base=True (enforced by a
    partial unique index in migration 009).
    """

    unit: str
    alias: str | None = None
    data_type_id: int
    min: float | None = None
    max: float | None = None
    to_base_factor: float = 1.0
    to_base_offset: float = 0.0
    is_base: bool = False

    @model_validator(mode="after")
    def _bounds(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must be <= max")
        return self


class DataUnitUpdateRequest(BaseModel):
    """PATCH /api/data-units/{id} — partial update.

    `data_type_id` is intentionally absent: a unit's parent type is immutable.

    The bounds validator mirrors the one on Create — required by the doc's
    test list (Section 8: "PATCH min > max -> 422"). Without it, a PATCH that
    inverts min/max would succeed silently.
    """

    unit: str | None = None
    alias: str | None = None
    min: float | None = None
    max: float | None = None
    to_base_factor: float | None = None
    to_base_offset: float | None = None
    is_base: bool | None = None

    @model_validator(mode="after")
    def _bounds(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must be <= max")
        return self
