import asyncio
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.schemas.materials import (
    MaterialCreateRequest, MaterialRenameRequest, MaterialColorRequest,
    MaterialTextureRequest, MaterialTwosidedRequest, MaterialTextureOverrideRequest,
    MaterialAssignRequest,
)
from app.services import material_service

router = APIRouter()


@router.get("")
async def list_materials():
    try:
        return await asyncio.to_thread(material_service.list_materials)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("")
async def create_material(req: MaterialCreateRequest):
    try:
        return await asyncio.to_thread(material_service.create_material, req.label)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/{label}/rename")
async def rename_material(label: str, req: MaterialRenameRequest):
    try:
        return await asyncio.to_thread(material_service.rename_material, label, req.new_label)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/{label}")
async def delete_material(label: str):
    try:
        return await asyncio.to_thread(material_service.delete_material, label)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/{label}/color")
async def set_material_color(label: str, req: MaterialColorRequest):
    try:
        return await asyncio.to_thread(
            material_service.set_material_color, label, req.r, req.g, req.b, req.a
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/{label}/texture")
async def set_material_texture(label: str, req: MaterialTextureRequest):
    try:
        return await asyncio.to_thread(
            material_service.set_material_texture, label, req.texture_file
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/{label}/twosided")
async def set_material_twosided(label: str, req: MaterialTwosidedRequest):
    try:
        return await asyncio.to_thread(
            material_service.set_material_twosided, label, req.twosided
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/{label}/texture-override")
async def set_material_texture_override(label: str, req: MaterialTextureOverrideRequest):
    try:
        return await asyncio.to_thread(
            material_service.set_material_texture_override, label, req.override
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/assign")
async def assign_material(req: MaterialAssignRequest):
    try:
        return await asyncio.to_thread(
            material_service.assign_material, req.material_label, req.object_id, req.primitive_uuids
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/{label}/primitives")
async def get_material_primitives(label: str):
    try:
        return await asyncio.to_thread(material_service.get_material_primitives, label)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/textures/library")
async def get_texture_library():
    return material_service.get_texture_library()


@router.get("/textures/preview")
async def get_texture_preview(path: str):
    allowed = False
    for _, tex_dir in material_service.get_texture_dirs():
        try:
            Path(path).resolve().relative_to(tex_dir.resolve())
            allowed = True
            break
        except ValueError:
            pass
    if not allowed:
        raise HTTPException(403, "Texture path not in library")
    p = Path(path)
    if not p.is_file():
        raise HTTPException(404, "Texture file not found")
    return FileResponse(str(p))
