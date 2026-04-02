import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.helios import registry as reg
from app.services import object_service

router = APIRouter()


@router.get("/{object_id}/info")
async def get_object_info(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    return object_service.get_object_info(object_id)


@router.get("/{object_id}/geometry/binary")
async def get_object_geometry_binary(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    try:
        return Response(
            content=await asyncio.to_thread(object_service.get_object_geometry_binary, object_id),
            media_type="application/octet-stream",
        )
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/{object_id}/geometry/gpu")
async def get_object_geometry_gpu(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    try:
        return Response(
            content=await asyncio.to_thread(object_service.get_object_geometry_gpu, object_id),
            media_type="application/octet-stream",
        )
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/{object_id}/children/binary")
async def get_object_children_binary(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    return Response(
        content=object_service.get_object_children_binary(object_id),
        media_type="application/octet-stream",
    )


@router.get("/{object_id}/children/gpu")
async def get_object_children_gpu(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    try:
        return Response(
            content=await asyncio.to_thread(object_service.get_object_children_gpu, object_id),
            media_type="application/octet-stream",
        )
    except Exception as e:
        raise HTTPException(500, str(e))
