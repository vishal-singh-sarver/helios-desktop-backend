"""
Request bodies for weather endpoints.

Matches the pseudocode in the design doc:
    POST /add     body: AddRequest
    POST /update  body: UpdateRequest
    POST /delete  body: DeleteRequest
"""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RowRef(BaseModel):
    """Identifies a row by its (date, time) pair."""
    date: str
    time: str


class AddColumn(BaseModel):
    """Spec for adding a new column."""
    columnname: str
    values: list[str] = Field(default_factory=list)


class AddRequest(BaseModel):
    """Body for POST /add. Either `column` or `rows` (or both) must be set."""
    model_config = ConfigDict(extra="forbid")

    column: AddColumn | None = None
    rows: list[dict[str, Any]] | None = None


class UpdateRequest(BaseModel):
    """Body for POST /update. Updates a single cell identified by (col, row)."""
    model_config = ConfigDict(extra="forbid")

    col: str
    row: RowRef
    value: str = ""


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
