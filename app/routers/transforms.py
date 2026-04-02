import asyncio
from fastapi import APIRouter, HTTPException

from app.helios import registry as reg
from app.schemas.transforms import TranslateRequest, RotateRequest, ScaleRequest
from app.services import transform_service

router = APIRouter()


@router.get("/object/{object_id}/centroid")
async def get_object_centroid(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    try:
        return await asyncio.to_thread(transform_service.get_object_centroid, object_id)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/translate")
async def translate_object(req: TranslateRequest):
    if req.object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {req.object_id} not found")
    try:
        return await asyncio.to_thread(
            transform_service.translate_object, req.object_id, req.shift, req.primitive_uuids
        )
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/rotate")
async def rotate_object(req: RotateRequest):
    if req.object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {req.object_id} not found")
    if req.axis not in ("x", "y", "z"):
        raise HTTPException(400, f"Invalid axis '{req.axis}'. Must be x, y, or z.")
    try:
        return await asyncio.to_thread(
            transform_service.rotate_object, req.object_id, req.angle, req.axis, req.primitive_uuids
        )
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/scale")
async def scale_object(req: ScaleRequest):
    if req.object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {req.object_id} not found")
    try:
        return await asyncio.to_thread(
            transform_service.scale_object, req.object_id, req.scale, req.primitive_uuids
        )
    except Exception as e:
        raise HTTPException(500, str(e))
