
"""
Request bodies for weather endpoints.

    POST /addCol  body: AddColumnsRequest
    POST /addRow  body: AddRowsRequest
    POST /update  body: UpdateRequest
    POST /delete  body: DeleteRequest

The `addCol` flow links each new column to the metadata catalog
(helios_data_types, data_units) and persists a row in weather_data_headers
in addition to writing the cells into PyHelios. The PyHelios label used is
the new header's autoincrement id (stringified), not the user-facing name.
"""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RowRef(BaseModel):
    """Identifies a row by its (date, time) pair."""
    date: str
    time: str


class ColumnValue(BaseModel):
    """One {date, time, value} cell inside an AddColumn payload."""
    model_config = ConfigDict(extra="forbid")

    date: str
    time: str
    value: str = "NAN"   # "NAN" is treated as empty/NaN


class AddColumn(BaseModel):
    """One column spec inside the AddRequest.column list.

    Two optional FKs link the column to the master catalog:
      datatype  -> helios_data_types.id
      data_unit -> data_units.id
    When non-null and both present, the unit must belong to the type
    (service-level invariant; the schema doesn't enforce cross-table CHECKs).

    `default_value` (optional) fills any scenario timestamp NOT covered by
    `values[]`. Numeric (or numeric string). Null/absent = no fill.
    """
    model_config = ConfigDict(extra="forbid")

    id: int | None = None  # Required for updateCol (lookup key); ignored by addCol
    name: str
    datatype: int | None = None
    data_unit: int | None = None
    values: list[ColumnValue] = Field(default_factory=list)
    default_value: float | str | None = "NAN"

    @field_validator("name")
    @classmethod
    def _trim(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name is required")
        if len(v) > 100:
            raise ValueError("name must be 100 characters or fewer")
        return v

    @field_validator("default_value", mode="before")
    @classmethod
    def _coerce_default(cls, v):
        if v is None:
            return None
        if isinstance(v, bool):
            raise ValueError("default_value must be numeric")
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            s = v.strip()
            if s == "" or s.upper() == "NAN":
                return float("nan")
            try:
                return float(s)
            except ValueError:
                raise ValueError(f"default_value '{v}' is not numeric")
        raise ValueError("default_value must be numeric")


class AddColumnsRequest(BaseModel):
    """Body for POST /addCol — add one or more columns in a single batch.

    `column` is a list (frontend always sends an array, even for a single
    column). Empty list is rejected at the service layer for a clearer
    error than a silent no-op.
    """
    model_config = ConfigDict(extra="forbid")

    column: list[AddColumn]

    @model_validator(mode="after")
    def _no_dup_column_names(self):
        names = [c.name for c in self.column]
        if len(names) != len(set(names)):
            raise ValueError("duplicate column name in request body")
        return self


class UpdateColumnsRequest(BaseModel):
    """Body for PATCH /updateCol — update one or more existing columns.

    Each column item identifies the target by `id` (the DB primary key
    returned by addCol). `name` is optional and only used if you also
    want to rename the column. Per-item:
      - `datatype` / `data_unit`: PATCH semantics — only updated when non-null.
      - `values[]`: each cell upserts (creates if missing, overwrites if exists).
      - `default_value`: writes the default at every scenario timestamp NOT
        listed in `values[]`, OVERWRITING any existing cell at that timestamp.
    """
    model_config = ConfigDict(extra="forbid")

    column: list[AddColumn]

    @model_validator(mode="after")
    def _no_dup_column_ids(self):
        ids = [c.id for c in self.column if c.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate column id in request body")
        return self


class AddRowsRequest(BaseModel):
    """Body for POST /addRow — append one or more rows.

    Each row dict must include `date` + `time` plus exactly the set of
    existing column labels (the str(header.id) values). Mismatch is a 400.
    """
    model_config = ConfigDict(extra="forbid")

    rows: list[dict[str, Any]]


class UpdateValue(BaseModel):
    """One cell update inside the UpdateRequest.updates list.

    `col` is the PyHelios label (str(header.id)). `row` is the cell's
    (date, time). `value` empty-string is treated as NaN (clears the cell).
    """
    model_config = ConfigDict(extra="forbid")

    col: str
    row: RowRef
    value: str = "NAN"


class UpdateRequest(BaseModel):
    """Body for POST /update — batch update of one or more existing cells.

    Each item identifies a cell by (col, row) and provides the new value.
    Used by the frontend when re-converting a column's values after the
    user changes its data_unit. Empty `updates` list is rejected at the
    service layer so the frontend gets a clear signal instead of a no-op.
    """
    model_config = ConfigDict(extra="forbid")

    updates: list[UpdateValue]


class DeleteColumn(BaseModel):
    """Identifies a column to delete."""
    columnname: str


class DeleteRequest(BaseModel):
    """Body for POST /delete.

    Neither row nor column → wipe all.
    row only → clear that row across every column.
    column only → clear that column across every row.
    Both → clear the row first, then the column.
    """
    model_config = ConfigDict(extra="forbid")

    row: RowRef | None = None
    column: DeleteColumn | None = None
