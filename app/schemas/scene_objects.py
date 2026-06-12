"""Request models for persisted scene objects, groups and material
assignment (milestone 2, spec §5, §6, §8)."""
from typing import Any, Optional
from pydantic import BaseModel, Field


class AssignmentIn(BaseModel):
    material_id: int
    sync: bool = True


class SceneObjectCreateRequest(BaseModel):
    object_type_id: int
    name: Optional[str] = None              # omitted → auto 'Ground.001'
    properties: dict[str, Any] = Field(default_factory=dict)
    visibility: Optional[dict[str, Any]] = None
    materials: list[AssignmentIn] = Field(default_factory=list)


class SceneObjectUpdateRequest(BaseModel):
    properties: Optional[dict[str, Any]] = None
    visibility: Optional[dict[str, Any]] = None
    group_id: Optional[int] = None          # null = ungroup; check fields_set


class SceneObjectRenameRequest(BaseModel):
    name: str


class GroupCreateRequest(BaseModel):
    name: Optional[str] = None              # omitted → auto 'Group.001'
    member_ids: list[int] = Field(default_factory=list)


class GroupRenameRequest(BaseModel):
    name: str


class AssignMaterialRequest(BaseModel):
    material_id: int
    sync: bool = True


class AssignmentUpdateRequest(BaseModel):
    sync: Optional[bool] = None
    properties: Optional[dict[str, Any]] = None


class ScenarioModelsUpdateRequest(BaseModel):
    # {"<model_type_id>": bool} — validated against the model catalog in the
    # service (custom 400/404 shapes instead of pydantic 422s).
    models: dict[str, Any] = Field(default_factory=dict)
