import asyncio
import struct
import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.helios.context import get_context
from app.helios import registry as reg

router = APIRouter()


@router.get("/{object_id}/info")
async def get_object_info(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    obj = reg.get_object(object_id)
    return {
        "object_id": object_id,
        "name": obj["name"],
        "type": obj["type"],
        "uuids": obj["primitive_uuids"],
        "plant_ids": obj.get("plant_ids", []),
        "children": obj.get("children", []),
    }


@router.get("/{object_id}/geometry/binary")
async def get_object_geometry_binary(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    cached = reg._geometry_cache.pop(object_id, None)
    if cached is not None:
        return Response(content=cached, media_type="application/octet-stream")
    uuids = reg.get_object(object_id)["primitive_uuids"]
    def _do():
        from app.routers._geometry_pack import pack_primitives_binary
        return pack_primitives_binary(get_context(), uuids)
    try:
        return Response(content=await asyncio.to_thread(_do), media_type="application/octet-stream")
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/{object_id}/geometry/gpu")
async def get_object_geometry_gpu(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    cached = reg._gpu_geometry_cache.pop(object_id, None)
    if cached is not None:
        return Response(content=cached, media_type="application/octet-stream")
    uuids = reg.get_object(object_id)["primitive_uuids"]
    def _do():
        return get_context().packGPUBuffers(uuids)
    try:
        return Response(content=await asyncio.to_thread(_do), media_type="application/octet-stream")
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/{object_id}/children/binary")
async def get_object_children_binary(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    children = reg.get_object(object_id).get("children", [])
    chunks = [struct.pack("<I", len(children))]
    for child in children:
        label_bytes = child["label"].encode("utf-8")
        uuids = child["uuids"]
        chunks += [struct.pack("<H", len(label_bytes)), label_bytes,
                   struct.pack("<I", len(uuids))]
        if uuids:
            chunks.append(np.array(uuids, dtype=np.int32).tobytes())
    return Response(content=b"".join(chunks), media_type="application/octet-stream")


@router.get("/{object_id}/children/gpu")
async def get_object_children_gpu(object_id: int):
    if object_id not in reg.get_all_objects():
        raise HTTPException(404, f"Object {object_id} not found")
    cached = reg._gpu_children_cache.pop(object_id, None)
    if cached is not None:
        return Response(content=cached, media_type="application/octet-stream")
    obj = reg.get_object(object_id)
    def _do():
        from app.routers._geometry_pack import pack_children_gpu
        return pack_children_gpu(get_context(), obj.get("children", []), obj["primitive_uuids"])
    try:
        return Response(content=await asyncio.to_thread(_do), media_type="application/octet-stream")
    except Exception as e:
        raise HTTPException(500, str(e))
