import numpy as np
from app.helios.context import get_context, vec3
from app.helios import registry as reg


def get_object_centroid(object_id: int) -> dict:
    ctx = get_context()
    uuids = reg.get_object(object_id)["primitive_uuids"]
    if not uuids:
        return {"centroid": {"x": 0, "y": 0, "z": 0}}
    vert_data, _ = ctx.getPrimitiveVertices(uuids)
    if len(vert_data) == 0:
        return {"centroid": {"x": 0, "y": 0, "z": 0}}
    c = vert_data.reshape(-1, 3).mean(axis=0)
    return {"centroid": {"x": float(c[0]), "y": float(c[1]), "z": float(c[2])}}


def translate_object(object_id: int, shift, primitive_uuids=None) -> dict:
    ctx = get_context()
    uuids = primitive_uuids or reg.get_object(object_id)["primitive_uuids"]
    if uuids:
        ctx.translatePrimitive(uuids, vec3(shift.x, shift.y, shift.z))
    return {"success": True}


def rotate_object(object_id: int, angle: float, axis: str, primitive_uuids=None) -> dict:
    ctx = get_context()
    uuids = primitive_uuids or reg.get_object(object_id)["primitive_uuids"]
    if uuids:
        ctx.rotatePrimitive(uuids, angle, axis)
    return {"success": True}


def scale_object(object_id: int, scale, primitive_uuids=None) -> dict:
    ctx = get_context()
    uuids = primitive_uuids or reg.get_object(object_id)["primitive_uuids"]
    if uuids:
        ctx.scalePrimitive(uuids, vec3(scale.x, scale.y, scale.z))
    return {"success": True}
