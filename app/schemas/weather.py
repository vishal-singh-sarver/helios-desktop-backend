"""Request models for the weather endpoints."""
from typing import Optional

from pydantic import BaseModel, Field


class AddColumnBody(BaseModel):
    columnname: str
    values: list[str] = Field(default_factory=list)


class AddRequest(BaseModel):
    """POST /api/weather/{project_id}/add — provide rows, column, or both.

    Each row must include 'date' and 'time'; other keys map to existing
    column slugs. Column name gets slugified (e.g. "Pressure (kPa)" →
    "pressure_kpa") and the resulting slug is returned in the response.
    """
    rows: Optional[list[dict]] = None
    column: Optional[AddColumnBody] = None


class RowKey(BaseModel):
    """Identifies a row by its date and time."""
    date: str
    time: str


class UpdateRequest(BaseModel):
    """POST /api/weather/{project_id}/update — change a single cell."""
    row: RowKey
    col: str
    value: str


class DeleteColumnBody(BaseModel):
    columnname: str


class DeleteRequest(BaseModel):
    """POST /api/weather/{project_id}/delete — provide row, column, or both."""
    row: Optional[RowKey] = None
    column: Optional[DeleteColumnBody] = None
