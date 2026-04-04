from typing import Optional, List
from pydantic import BaseModel


class MaterialCreateRequest(BaseModel):
    label: str


class MaterialRenameRequest(BaseModel):
    new_label: str


class MaterialColorRequest(BaseModel):
    r: float
    g: float
    b: float
    a: float = 1.0


class MaterialTextureRequest(BaseModel):
    texture_file: str


class MaterialTwosidedRequest(BaseModel):
    twosided: bool


class MaterialTextureOverrideRequest(BaseModel):
    override: bool


class MaterialAssignRequest(BaseModel):
    material_label: str
    object_id: Optional[int] = None
    primitive_uuids: Optional[List[int]] = None
