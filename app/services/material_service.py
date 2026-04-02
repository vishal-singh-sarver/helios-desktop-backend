from pathlib import Path
from app.helios.context import get_context
from app.helios import registry as reg

_TEXTURE_EXCLUDE = {"Helios_watermark.png", "PSL_logo_leafonly.png", "USDA_logo.jpg", "compass.jpg"}
_TEXTURE_EXCLUDE_PREFIXES = ("nav_gizmo_",)


def material_snapshot(ctx, label: str) -> dict:
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


def get_texture_dirs() -> list:
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


def list_materials() -> dict:
    ctx = get_context()
    return {"materials": [
        material_snapshot(ctx, lbl)
        for lbl in ctx.listMaterials()
        if not lbl.startswith("__")
    ]}


def create_material(label: str) -> dict:
    from fastapi import HTTPException
    ctx = get_context()
    if ctx.doesMaterialExist(label):
        raise HTTPException(409, f"Material '{label}' already exists")
    ctx.addMaterial(label)
    return material_snapshot(ctx, label)


def rename_material(label: str, new_label: str) -> dict:
    from fastapi import HTTPException
    from pyhelios.types import RGBAcolor
    ctx = get_context()
    if not ctx.doesMaterialExist(label):
        raise HTTPException(404, f"Material '{label}' not found")
    if ctx.doesMaterialExist(new_label):
        raise HTTPException(409, f"Material '{new_label}' already exists")
    snap = material_snapshot(ctx, label)
    ctx.addMaterial(new_label)
    if snap["color"]:
        c = snap["color"]
        ctx.setMaterialColor(new_label, RGBAcolor(c["r"], c["g"], c["b"], c["a"]))
    if snap["texture"]:
        ctx.setMaterialTexture(new_label, snap["texture"])
    primitives = ctx.getPrimitivesUsingMaterial(label)
    if primitives:
        ctx.assignMaterialToPrimitive(primitives, new_label)
    ctx.deleteMaterial(label)
    if reg._default_material_label == label:
        reg._default_material_label = new_label
    return material_snapshot(ctx, new_label)


def delete_material(label: str) -> dict:
    from fastapi import HTTPException
    ctx = get_context()
    if not ctx.doesMaterialExist(label):
        raise HTTPException(404, f"Material '{label}' not found")
    ctx.deleteMaterial(label)
    return {"success": True}


def set_material_color(label: str, r: float, g: float, b: float, a: float) -> dict:
    from fastapi import HTTPException
    from pyhelios.types import RGBAcolor
    ctx = get_context()
    if not ctx.doesMaterialExist(label):
        raise HTTPException(404, f"Material '{label}' not found")
    ctx.setMaterialColor(label, RGBAcolor(r, g, b, a))
    return {"success": True}


def set_material_texture(label: str, texture_file: str) -> dict:
    from fastapi import HTTPException
    ctx = get_context()
    if not ctx.doesMaterialExist(label):
        raise HTTPException(404, f"Material '{label}' not found")
    ctx.setMaterialTexture(label, texture_file)
    return {"success": True}


def set_material_twosided(label: str, twosided: bool) -> dict:
    from fastapi import HTTPException
    ctx = get_context()
    if not ctx.doesMaterialExist(label):
        raise HTTPException(404, f"Material '{label}' not found")
    ctx.setMaterialTwosidedFlag(label, 1 if twosided else 0)
    return {"success": True}


def set_material_texture_override(label: str, override: bool) -> dict:
    from fastapi import HTTPException
    ctx = get_context()
    if not ctx.doesMaterialExist(label):
        raise HTTPException(404, f"Material '{label}' not found")
    ctx.setMaterialTextureColorOverride(label, override)
    return {"success": True}


def assign_material(material_label: str, object_id=None, primitive_uuids=None) -> dict:
    from fastapi import HTTPException
    ctx = get_context()
    if not ctx.doesMaterialExist(material_label):
        raise HTTPException(404, f"Material '{material_label}' not found")
    if object_id is not None:
        if object_id not in reg.get_all_objects():
            raise HTTPException(404, f"Object {object_id} not found")
        uuids = reg.get_object(object_id)["primitive_uuids"]
        if uuids:
            ctx.assignMaterialToPrimitive(uuids, material_label)
    elif primitive_uuids is not None:
        if primitive_uuids:
            ctx.assignMaterialToPrimitive(primitive_uuids, material_label)
    else:
        raise HTTPException(400, "Provide object_id or primitive_uuids")
    return {"success": True}


def get_material_primitives(label: str) -> dict:
    from fastapi import HTTPException
    ctx = get_context()
    if not ctx.doesMaterialExist(label):
        raise HTTPException(404, f"Material '{label}' not found")
    return {"uuids": ctx.getPrimitivesUsingMaterial(label)}


def get_texture_library() -> dict:
    categories = {}
    for name, tex_dir in get_texture_dirs():
        files = [
            f.name for f in tex_dir.iterdir()
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg")
            and f.name not in _TEXTURE_EXCLUDE
            and not any(f.name.startswith(p) for p in _TEXTURE_EXCLUDE_PREFIXES)
        ]
        if files:
            categories[name] = sorted(files)
    return {"categories": categories}
