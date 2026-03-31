import asyncio
from fastapi import APIRouter, HTTPException
from app.helios.context import get_context
from app.helios.context import vec3
from app.helios import registry as reg
from app.schemas.transforms import TranslateRequest, RotateRequest, ScaleRequest

router = APIRouter()


@router.get("/object/{object_id}/centroid")
async def get_object_centroid(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    def _do():
        import numpy as np
        ctx = get_context()
        uuids = reg.get_object(object_id)["primitive_uuids"]
        if not uuids:
            return {"centroid": {"x": 0, "y": 0, "z": 0}}
        vert_data, _ = ctx.getPrimitiveVertices(uuids)
        if len(vert_data) == 0:
            return {"centroid": {"x": 0, "y": 0, "z": 0}}
        c = vert_data.reshape(-1, 3).mean(axis=0)
        return {"centroid": {"x": float(c[0]), "y": float(c[1]), "z": float(c[2])}}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/translate")
async def translate_object(req: TranslateRequest):
    if req.object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {req.object_id} not found")
    def _do():
        ctx = get_context()
        uuids = req.primitive_uuids or reg.get_object(req.object_id)["primitive_uuids"]
        if uuids:
            ctx.translatePrimitive(uuids, vec3(req.shift.x, req.shift.y, req.shift.z))
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/rotate")
async def rotate_object(req: RotateRequest):
    if req.object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {req.object_id} not found")
    if req.axis not in ("x", "y", "z"):
        raise HTTPException(400, f"Invalid axis '{req.axis}'. Must be x, y, or z.")
    def _do():
        ctx = get_context()
        uuids = req.primitive_uuids or reg.get_object(req.object_id)["primitive_uuids"]
        if uuids:
            ctx.rotatePrimitive(uuids, req.angle, req.axis)
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/scale")
async def scale_object(req: ScaleRequest):
    if req.object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {req.object_id} not found")
    def _do():
        ctx = get_context()
        uuids = req.primitive_uuids or reg.get_object(req.object_id)["primitive_uuids"]
        if uuids:
            ctx.scalePrimitive(uuids, vec3(req.scale.x, req.scale.y, req.scale.z))
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))
