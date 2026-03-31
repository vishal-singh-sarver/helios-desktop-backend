from pydantic import BaseModel


class Vec3Model(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class Vec2Model(BaseModel):
    x: float = 1.0
    y: float = 1.0


class Int2Model(BaseModel):
    x: int = 1
    y: int = 1


class RGBColorModel(BaseModel):
    r: float = 0.5
    g: float = 0.5
    b: float = 0.5
