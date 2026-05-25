"""
Request bodies for the per-scenario weather-header mapping.

A header maps one CSV column in a scenario to an entry in the master catalog
(helios_data_types + data_units). The whole set is replaced atomically via
PUT — there are no per-row IDs in the URL.
"""
from pydantic import BaseModel, field_validator, model_validator


class WeatherDataHeaderItem(BaseModel):
    """One header row inside the PUT payload."""

    name: str
    helios_data_type_id: int
    unit_id: int
    status: bool = True
    display_order: int = 0

    @field_validator("name")
    @classmethod
    def _trim(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name is required")
        if len(v) > 100:
            raise ValueError("name must be 100 characters or fewer")
        return v


class WeatherDataHeaderReplaceRequest(BaseModel):
    """PUT /api/weather/project/{pid}/scenario/{sid}/weather_data_header

    Replaces the entire header set for the scenario in one transaction.
    Sending an empty `headers` array clears the set.
    """

    headers: list[WeatherDataHeaderItem]

    @model_validator(mode="after")
    def _no_dupes(self):
        names = [h.name for h in self.headers]
        orders = [h.display_order for h in self.headers]
        if len(names) != len(set(names)):
            raise ValueError("duplicate name in headers array")
        if len(orders) != len(set(orders)):
            raise ValueError("duplicate display_order in headers array")
        return self


class WeatherDataHeaderUpdateRequest(BaseModel):
    """PATCH /api/weather/project/{pid}/scenario/{sid}/weather_data_header/{header_id}

    Partial update of a single header row. Only the provided fields change.

    `helios_data_type_id` and `unit_id` are optional in the DB (migration 008),
    so partial-mapping rows stay legal: a row may have both null, just one
    set, or both set. When both end up non-null the service still verifies
    `unit.data_type_id == helios_data_type_id`.
    """

    name: str | None = None
    helios_data_type_id: int | None = None
    unit_id: int | None = None
    display_order: int | None = None

    @field_validator("name")
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        if len(v) > 100:
            raise ValueError("name must be 100 characters or fewer")
        return v
