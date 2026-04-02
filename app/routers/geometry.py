import asyncio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.helios import registry as reg
from app.schemas.geometry import TileRequest, PatchRequest, TexturedTileRequest, TexturedTriangleRequest
from app.services import geometry_service

router = APIRouter()


@router.post("/tile")
async def add_tile(req: TileRequest):
    try:
        return await asyncio.to_thread(geometry_service.add_tile, req)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/patch")
async def add_patch(req: PatchRequest):
    try:
        return await asyncio.to_thread(geometry_service.add_patch, req)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/tile/textured")
async def add_textured_tile(req: TexturedTileRequest):
    try:
        return await asyncio.to_thread(geometry_service.add_textured_tile, req)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/triangle/textured")
async def add_textured_triangle(req: TexturedTriangleRequest):
    try:
        return await asyncio.to_thread(geometry_service.add_textured_triangle, req)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/all/binary")
async def get_all_geometry_binary():
    try:
        return Response(
            content=await asyncio.to_thread(geometry_service.get_all_geometry_binary),
            media_type="application/octet-stream",
        )
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/binary")
async def get_geometry_binary_subset(request: Request):
    body = await request.json()
    uuids = body.get("uuids", [])
    try:
        return Response(
            content=await asyncio.to_thread(geometry_service.get_geometry_binary_subset, uuids),
            media_type="application/octet-stream",
        )
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/all/gpu")
async def get_all_geometry_gpu():
    try:
        return Response(
            content=await asyncio.to_thread(geometry_service.get_all_geometry_gpu),
            media_type="application/octet-stream",
        )
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/count")
async def get_geometry_count():
    try:
        return await asyncio.to_thread(geometry_service.get_geometry_count)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/objects")
async def get_objects():
    return {"objects": [
        {"object_id": oid, "name": obj["name"], "type": obj["type"],
         "primitive_uuids": obj["primitive_uuids"], "visible": True}
        for oid, obj in reg.get_all_objects().items()
    ]}


@router.delete("/object/{object_id}")
async def delete_object(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    try:
        return await asyncio.to_thread(geometry_service.delete_object, object_id)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/{uuid}")
async def delete_primitive(uuid: int):
    try:
        return await asyncio.to_thread(geometry_service.delete_primitive, uuid)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/delete-batch")
async def delete_primitives_batch(req: dict):
    uuids = req.get("uuids", [])
    if not uuids:
        return {"success": True}
    try:
        return await asyncio.to_thread(geometry_service.delete_primitives_batch, uuids)
    except Exception as e:
        raise HTTPException(500, str(e))
