"""Request models for the scenario endpoints."""
from typing import Optional

from pydantic import BaseModel, field_validator


class ScenarioCreateRequest(BaseModel):
    """POST /api/project/{project_id}/scenarios/create.

    - name: required user-given label. Non-empty, <=30 chars, unique per project.
    - source_scenario_id: optional. If given, the new scenario is a fork —
      its weather CSV is copied from the source. If omitted, the new
      scenario starts empty.
    """
    name: str
    source_scenario_id: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Scenario name is required")
        if len(value) > 30:
            raise ValueError("This field supports up to 30 characters only")
        return value

    @field_validator("source_scenario_id")
    @classmethod
    def validate_source_scenario_id(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None
