from pydantic import BaseModel
from app.schemas.common import Vec3Model, Vec2Model


class TreeBuildRequest(BaseModel):
    type: str = "Almond"
    origin: Vec3Model = Vec3Model()
    scale: float = 1.0


class CanopyBuildRequest(BaseModel):
    species: str
    canopy_center: Vec3Model = Vec3Model()
    plant_spacing: Vec2Model = Vec2Model(x=1.0, y=1.0)
    plant_count_x: int = 3
    plant_count_y: int = 3
    age: float = 30.0
