"""Request models for persisted scene objects, geometry groups, material-group
assignment and scenario material-sync (milestone 2 / migration 022)."""
from typing import Any, Optional
from pydantic import BaseModel, Field


class GroupAssignmentIn(BaseModel):
    group_id: int
    sync: bool = True


class SceneObjectCreateRequest(BaseModel):
    object_type_id: int
    name: Optional[str] = None              # omitted → auto 'Ground.001'
    properties: dict[str, Any] = Field(default_factory=dict)
    visibility: Optional[dict[str, Any]] = None
    # Material-GROUP assignments (migration 022). The field keeps the pre-022
    # name `materials` — the old per-material shape ({material_id, sync}) is
    # dead, and the frontend's existing `materials: []` lands here unchanged.
    materials: list[GroupAssignmentIn] = Field(default_factory=list)


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


class GroupVisibilityRequest(BaseModel):
    # Bulk-apply a visibility object ({viewport?, render?, models?}) to every
    # member of a group — same shape as SceneObjectUpdateRequest.visibility. The
    # service rejects an empty object.
    visibility: dict[str, Any] = Field(default_factory=dict)


class AssignMaterialGroupRequest(BaseModel):
    group_id: int
    sync: bool = True


class FrozenMaterialPatch(BaseModel):
    """Per-member frozen-value edit, addressed by material type (members are
    nameless — identity is (group, material type))."""
    material_type_id: int
    properties: dict[str, Any] = Field(default_factory=dict)


class GroupAssignmentUpdateRequest(BaseModel):
    sync: Optional[bool] = None
    materials: Optional[list[FrozenMaterialPatch]] = None


class MaterialSyncRequest(BaseModel):
    """PUT material-sync scoping — omitted/None = reconcile everything."""
    group_ids: Optional[list[int]] = None
    object_ids: Optional[list[int]] = None


class ScenarioModelsUpdateRequest(BaseModel):
    # {"<model_type_id>": bool} — validated against the model catalog in the
    # service (custom 400/404 shapes instead of pydantic 422s).
    models: dict[str, Any] = Field(default_factory=dict)
