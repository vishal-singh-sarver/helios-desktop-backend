from typing import Optional
from pydantic import BaseModel
from app.schemas.common import Vec3Model, Vec2Model, Int2Model, RGBColorModel


class TileRequest(BaseModel):
    center: Vec3Model = Vec3Model()
    size: Vec2Model = Vec2Model()
    subdivisions: Int2Model = Int2Model()
    color: RGBColorModel = RGBColorModel()


class PatchRequest(BaseModel):
    center: Vec3Model = Vec3Model()
    size: Vec2Model = Vec2Model()
    color: RGBColorModel = RGBColorModel()


class TexturedTileRequest(BaseModel):
    center: Vec3Model = Vec3Model()
    size: Vec2Model = Vec2Model()
    rotation: Optional[Vec3Model] = None
    subdivisions: Int2Model = Int2Model()
    texture_file: str
    texture_repeat: Optional[Int2Model] = None
    color: Optional[Vec3Model] = None


class TexturedTriangleRequest(BaseModel):
    v0: Vec3Model
    v1: Vec3Model
    v2: Vec3Model
    texture_file: str
    uv0: Vec2Model
    uv1: Vec2Model
    uv2: Vec2Model


class ImportRequest(BaseModel):
    file_path: str
    origin: Optional[Vec3Model] = None
    scale: Optional[Vec3Model] = None
    rotation: Optional[Vec3Model] = None
