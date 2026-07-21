"""Request models for the material-group library (migration 022).

Groups are GLOBAL; members are nameless (one per material type; a group may be
EMPTY). Payload rules that need custom {error, code} shapes (duplicate types,
unknown type ids) are enforced in the service, not here — pydantic 422s are
reserved for structurally-wrong JSON.
"""
from typing import Any, Optional
from pydantic import BaseModel, Field


class GroupMaterialIn(BaseModel):
    """One member of a group: a material type + its property values. Doubles
    as the add-one-member request body."""
    material_type_id: int
    properties: dict[str, Any] = Field(default_factory=dict)


class GroupMaterialPutRequest(BaseModel):
    """Standalone FULL-REPLACEMENT of one member's properties (the stored set
    becomes exactly `properties`; omitted keys are cleared). Visualiser members
    are required-by-mode. The member is addressed by material_type_id in the URL."""
    properties: dict[str, Any] = Field(default_factory=dict)


class MaterialGroupCreateRequest(BaseModel):
    name: Optional[str] = None          # omitted → auto 'Material.001'
    project_id: Optional[str] = None    # provenance: where it was created
    scenario_id: Optional[str] = None   # provenance: derives/checks project_id
    materials: list[GroupMaterialIn] = Field(default_factory=list)   # may be empty


class MaterialGroupPutRequest(BaseModel):
    """Full-replacement member set. Types absent from `materials` are removed
    (an empty list removes every member — the group survives, empty); kept
    types are updated in place (per-member properties merge-upsert: provided
    keys written, explicit null clears, absent keys untouched)."""
    name: Optional[str] = None          # omitted → keep current name
    materials: list[GroupMaterialIn] = Field(default_factory=list)


class MaterialGroupRenameRequest(BaseModel):
    """Rename only — members untouched. (PUT can rename too, but it is a
    full-replacement member set, so renaming through it means resending every
    member or losing them.)"""
    name: str
