import asyncio
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from app.helios.context import get_context
from app.helios import registry as reg
from app.schemas.materials import (
    MaterialCreateRequest, MaterialRenameRequest, MaterialColorRequest,
    MaterialTextureRequest, MaterialTwosidedRequest, MaterialTextureOverrideRequest,
    MaterialAssignRequest,
)

router = APIRouter()

_TEXTURE_EXCLUDE = {"Helios_watermark.png", "PSL_logo_leafonly.png", "USDA_logo.jpg", "compass.jpg"}
_TEXTURE_EXCLUDE_PREFIXES = ("nav_gizmo_",)


def _material_snapshot(ctx, label: str) -> dict:
    snap = {"label": label}
    try:
        c = ctx.getMaterialColor(label)
        snap["color"] = {"r": float(c[0]), "g": float(c[1]), "b": float(c[2]), "a": float(c[3])}
    except Exception:
        snap["color"] = None
    try:
        snap["texture"] = ctx.getMaterialTexture(label) or None
    except Exception:
        snap["texture"] = None
    try:
        snap["twosided"] = bool(ctx.getMaterialTwosidedFlag(label))
    except Exception:
        snap["twosided"] = False
    try:
        snap["texture_color_override"] = bool(ctx.getMaterialTextureColorOverride(label))
    except Exception:
        snap["texture_color_override"] = False
    return snap


def _get_texture_dirs() -> list:
    dirs = []
    try:
        import pyhelios
        base = Path(pyhelios.__file__).parent
        for candidate in [
            base / "assets" / "build" / "plugins",
            base / "pyhelios_build" / "build" / "plugins",
            base / "helios-core" / "plugins",
        ]:
            if candidate.exists():
                for plugin_dir in candidate.iterdir():
                    for tex_dir in (plugin_dir / "textures", plugin_dir / "assets"):
                        if tex_dir.exists():
                            dirs.append((plugin_dir.name, tex_dir))
    except Exception:
        pass
    return dirs


@router.get("")
async def list_materials():
    def _do():
        ctx = get_context()
        return {"materials": [
            _material_snapshot(ctx, lbl)
            for lbl in ctx.listMaterials()
            if not lbl.startswith("__")
        ]}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("")
async def create_material(req: MaterialCreateRequest):
    def _do():
        ctx = get_context()
        if ctx.doesMaterialExist(req.label):
            raise HTTPException(409, f"Material '{req.label}' already exists")
        ctx.addMaterial(req.label)
        return _material_snapshot(ctx, req.label)
    try:
        return await asyncio.to_thread(_do)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/{label}/rename")
async def rename_material(label: str, req: MaterialRenameRequest):
    def _do():
        ctx = get_context()
        if not ctx.doesMaterialExist(label):
            raise HTTPException(404, f"Material '{label}' not found")
        if ctx.doesMaterialExist(req.new_label):
            raise HTTPException(409, f"Material '{req.new_label}' already exists")
        snap = _material_snapshot(ctx, label)
        ctx.addMaterial(req.new_label)
        from pyhelios.types import RGBAcolor
        if snap["color"]:
            c = snap["color"]
            ctx.setMaterialColor(req.new_label, RGBAcolor(c["r"], c["g"], c["b"], c["a"]))
        if snap["texture"]:
            ctx.setMaterialTexture(req.new_label, snap["texture"])
        primitives = ctx.getPrimitivesUsingMaterial(label)
        if primitives:
            ctx.assignMaterialToPrimitive(primitives, req.new_label)
        ctx.deleteMaterial(label)
        if reg._default_material_label == label:
            reg._default_material_label = req.new_label
        return _material_snapshot(ctx, req.new_label)
    try:
        return await asyncio.to_thread(_do)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/{label}")
async def delete_material(label: str):
    def _do():
        ctx = get_context()
        if not ctx.doesMaterialExist(label):
            raise HTTPException(404, f"Material '{label}' not found")
        ctx.deleteMaterial(label)
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/{label}/color")
async def set_material_color(label: str, req: MaterialColorRequest):
    def _do():
        ctx = get_context()
        if not ctx.doesMaterialExist(label):
            raise HTTPException(404, f"Material '{label}' not found")
        from pyhelios.types import RGBAcolor
        ctx.setMaterialColor(label, RGBAcolor(req.r, req.g, req.b, req.a))
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/{label}/texture")
async def set_material_texture(label: str, req: MaterialTextureRequest):
    def _do():
        ctx = get_context()
        if not ctx.doesMaterialExist(label):
            raise HTTPException(404, f"Material '{label}' not found")
        ctx.setMaterialTexture(label, req.texture_file)
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/{label}/twosided")
async def set_material_twosided(label: str, req: MaterialTwosidedRequest):
    def _do():
        ctx = get_context()
        if not ctx.doesMaterialExist(label):
            raise HTTPException(404, f"Material '{label}' not found")
        ctx.setMaterialTwosidedFlag(label, 1 if req.twosided else 0)
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/{label}/texture-override")
async def set_material_texture_override(label: str, req: MaterialTextureOverrideRequest):
    def _do():
        ctx = get_context()
        if not ctx.doesMaterialExist(label):
            raise HTTPException(404, f"Material '{label}' not found")
        ctx.setMaterialTextureColorOverride(label, req.override)
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/assign")
async def assign_material(req: MaterialAssignRequest):
    def _do():
        ctx = get_context()
        if not ctx.doesMaterialExist(req.material_label):
            raise HTTPException(404, f"Material '{req.material_label}' not found")
        if req.object_id is not None:
            if req.object_id not in reg.get_all_objects():
                raise HTTPException(404, f"Object {req.object_id} not found")
            uuids = reg.get_object(req.object_id)["primitive_uuids"]
            if uuids:
                ctx.assignMaterialToPrimitive(uuids, req.material_label)
        elif req.primitive_uuids is not None:
            if req.primitive_uuids:
                ctx.assignMaterialToPrimitive(req.primitive_uuids, req.material_label)
        else:
            raise HTTPException(400, "Provide object_id or primitive_uuids")
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/{label}/primitives")
async def get_material_primitives(label: str):
    def _do():
        ctx = get_context()
        if not ctx.doesMaterialExist(label):
            raise HTTPException(404, f"Material '{label}' not found")
        return {"uuids": ctx.getPrimitivesUsingMaterial(label)}
    try:
        return await asyncio.to_thread(_do)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/textures/library")
async def get_texture_library():
    categories = {}
    for name, tex_dir in _get_texture_dirs():
        files = [
            f.name for f in tex_dir.iterdir()
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg")
            and f.name not in _TEXTURE_EXCLUDE
            and not any(f.name.startswith(p) for p in _TEXTURE_EXCLUDE_PREFIXES)
        ]
        if files:
            categories[name] = sorted(files)
    return {"categories": categories}


@router.get("/textures/preview")
async def get_texture_preview(path: str):
    allowed = False
    for _, tex_dir in _get_texture_dirs():
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
