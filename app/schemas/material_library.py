"""Request models for the persisted material library (milestone 2, spec §7)."""
from typing import Any, Optional
from pydantic import BaseModel, Field


class MaterialCreateRequest(BaseModel):
    material_type_id: int
    name: Optional[str] = None          # omitted → auto 'Material.001'
    scenario_id: Optional[str] = None   # set when created inside a scenario
    properties: dict[str, Any] = Field(default_factory=dict)


class MaterialUpdateRequest(BaseModel):
    properties: dict[str, Any] = Field(default_factory=dict)


class MaterialRenameRequest(BaseModel):
    name: str
