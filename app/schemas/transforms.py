from typing import Optional, List
from pydantic import BaseModel
from app.schemas.common import Vec3Model


class TranslateRequest(BaseModel):
    object_id: int
    shift: Vec3Model
    primitive_uuids: Optional[List[int]] = None


class RotateRequest(BaseModel):
    object_id: int
    angle: float
    axis: str
    primitive_uuids: Optional[List[int]] = None


class ScaleRequest(BaseModel):
    object_id: int
    scale: Vec3Model
    primitive_uuids: Optional[List[int]] = None
