import asyncio
import struct
import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.helios.context import get_context, PYHELIOS_AVAILABLE
from app.helios.context import vec2, vec3, int2, RGBcolor, SphericalCoord
from app.helios import registry as reg
from app.schemas.geometry import TileRequest, PatchRequest, TexturedTileRequest, TexturedTriangleRequest

router = APIRouter()


@router.post("/tile")
async def add_tile(req: TileRequest):
    def _do():
        ctx = get_context()
        uuids = ctx.addTile(
            center=vec3(req.center.x, req.center.y, req.center.z),
            size=vec2(req.size.x, req.size.y),
            subdiv=int2(req.subdivisions.x, req.subdivisions.y),
            color=RGBcolor(req.color.r, req.color.g, req.color.b),
        )
        reg.ensure_default_material(ctx, uuids)
        obj_id = reg.register_object(f"Ground Tile ({req.size.x}x{req.size.y})", "tile", uuids)
        return {"uuids": uuids, "object_id": obj_id}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/patch")
async def add_patch(req: PatchRequest):
    def _do():
        ctx = get_context()
        uuid = ctx.addPatch(
            center=vec3(req.center.x, req.center.y, req.center.z),
            size=vec2(req.size.x, req.size.y),
            color=RGBcolor(req.color.r, req.color.g, req.color.b),
        )
        reg.ensure_default_material(ctx, [uuid])
        obj_id = reg.register_object(f"Patch ({req.size.x}x{req.size.y})", "primitive", [uuid])
        return {"uuid": uuid, "object_id": obj_id}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/tile/textured")
async def add_textured_tile(req: TexturedTileRequest):
    def _do():
        ctx = get_context()
        rot = SphericalCoord(1, 0, 0)
        if req.rotation:
            rot = SphericalCoord(req.rotation.x, req.rotation.y, req.rotation.z)
        kwargs = dict(
            center=vec3(req.center.x, req.center.y, req.center.z),
            size=vec2(req.size.x, req.size.y),
            rotation=rot,
            subdiv=int2(req.subdivisions.x, req.subdivisions.y),
            texturefile=req.texture_file,
        )
        if req.texture_repeat:
            kwargs["texture_repeat"] = int2(req.texture_repeat.x, req.texture_repeat.y)
        pyh_obj_id = ctx.addTileObject(**kwargs)
        new_uuids = [p.uuid for p in ctx.getPrimitivesInfoForObject(pyh_obj_id)]
        mat_label = reg.next_material_name(ctx)
        ctx.addMaterial(mat_label)
        from pyhelios.types import RGBAcolor
        if req.color:
            ctx.setMaterialColor(mat_label, RGBAcolor(req.color.x, req.color.y, req.color.z, 1.0))
        else:
            ctx.setMaterialColor(mat_label, RGBAcolor(*reg.DEFAULT_MATERIAL_COLOR))
        ctx.setMaterialTexture(mat_label, req.texture_file)
        if new_uuids:
            ctx.assignMaterialToPrimitive(new_uuids, mat_label)
        obj_id = reg.register_object(f"Textured Tile ({req.size.x}x{req.size.y})", "tile", new_uuids)
        return {"object_id": obj_id, "uuids": new_uuids, "material_label": mat_label}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/triangle/textured")
async def add_textured_triangle(req: TexturedTriangleRequest):
    def _do():
        ctx = get_context()
        uuid = ctx.addTriangleTextured(
            vec3(req.v0.x, req.v0.y, req.v0.z), vec3(req.v1.x, req.v1.y, req.v1.z),
            vec3(req.v2.x, req.v2.y, req.v2.z), req.texture_file,
            vec2(req.uv0.x, req.uv0.y), vec2(req.uv1.x, req.uv1.y), vec2(req.uv2.x, req.uv2.y),
        )
        obj_id = reg.register_object("Textured Triangle", "primitive", [uuid])
        return {"uuid": uuid, "object_id": obj_id}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/all/binary")
async def get_all_geometry_binary():
    def _do():
        from app.routers._geometry_pack import pack_primitives_binary
        ctx = get_context()
        return pack_primitives_binary(ctx, ctx.getAllUUIDs())
    try:
        return Response(content=await asyncio.to_thread(_do), media_type="application/octet-stream")
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/binary")
async def get_geometry_binary_subset(request: Request):
    body = await request.json()
    uuids = body.get("uuids", [])
    def _do():
        from app.routers._geometry_pack import pack_primitives_binary
        return pack_primitives_binary(get_context(), uuids)
    try:
        return Response(content=await asyncio.to_thread(_do), media_type="application/octet-stream")
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/all/gpu")
async def get_all_geometry_gpu():
    def _do():
        ctx = get_context()
        return ctx.packGPUBuffers(ctx.getAllUUIDs())
    try:
        return Response(content=await asyncio.to_thread(_do), media_type="application/octet-stream")
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/count")
async def get_geometry_count():
    def _do():
        return {"count": get_context().getPrimitiveCount()}
    try:
        return await asyncio.to_thread(_do)
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
    def _do():
        ctx = get_context()
        obj = reg.get_object(object_id)
        mat_labels = set()
        if obj["primitive_uuids"]:
            try:
                mat_labels = set(ctx.getPrimitiveMaterialLabel(obj["primitive_uuids"]))
            except Exception:
                pass
            ctx.deletePrimitive(obj["primitive_uuids"])
        reg.cleanup_orphaned_materials(ctx, mat_labels)
        reg.delete_object(object_id)
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/{uuid}")
async def delete_primitive(uuid: int):
    def _do():
        ctx = get_context()
        mat_label = ctx.getPrimitiveMaterialLabel(uuid)
        ctx.deletePrimitive(uuid)
        reg.cleanup_orphaned_materials(ctx, {mat_label})
        for obj_id, obj in list(reg.get_all_objects().items()):
            if uuid in obj["primitive_uuids"]:
                obj["primitive_uuids"].remove(uuid)
                if not obj["primitive_uuids"]:
                    reg.delete_object(obj_id)
                break
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/delete-batch")
async def delete_primitives_batch(req: dict):
    uuids = req.get("uuids", [])
    if not uuids:
        return {"success": True}
    def _do():
        ctx = get_context()
        mat_labels = set()
        try:
            mat_labels = set(ctx.getPrimitiveMaterialLabel(uuids))
        except Exception:
            pass
        ctx.deletePrimitive(uuids)
        reg.cleanup_orphaned_materials(ctx, mat_labels)
        deleted_set = set(uuids)
        for obj_id, obj in list(reg.get_all_objects().items()):
            obj["primitive_uuids"] = [u for u in obj["primitive_uuids"] if u not in deleted_set]
            if not obj["primitive_uuids"]:
                reg.delete_object(obj_id)
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))
