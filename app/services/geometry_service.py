from app.helios.context import get_context, vec2, vec3, int2, RGBcolor, SphericalCoord
from app.helios import registry as reg
from app.services.geometry_pack import pack_primitives_binary


def add_tile(req) -> dict:
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


def add_patch(req) -> dict:
    ctx = get_context()
    uuid = ctx.addPatch(
        center=vec3(req.center.x, req.center.y, req.center.z),
        size=vec2(req.size.x, req.size.y),
        color=RGBcolor(req.color.r, req.color.g, req.color.b),
    )
    reg.ensure_default_material(ctx, [uuid])
    obj_id = reg.register_object(f"Patch ({req.size.x}x{req.size.y})", "primitive", [uuid])
    return {"uuid": uuid, "object_id": obj_id}


def add_textured_tile(req) -> dict:
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


def add_textured_triangle(req) -> dict:
    ctx = get_context()
    uuid = ctx.addTriangleTextured(
        vec3(req.v0.x, req.v0.y, req.v0.z), vec3(req.v1.x, req.v1.y, req.v1.z),
        vec3(req.v2.x, req.v2.y, req.v2.z), req.texture_file,
        vec2(req.uv0.x, req.uv0.y), vec2(req.uv1.x, req.uv1.y), vec2(req.uv2.x, req.uv2.y),
    )
    obj_id = reg.register_object("Textured Triangle", "primitive", [uuid])
    return {"uuid": uuid, "object_id": obj_id}


def get_all_geometry_binary() -> bytes:
    ctx = get_context()
    return pack_primitives_binary(ctx, ctx.getAllUUIDs())


def get_geometry_binary_subset(uuids: list) -> bytes:
    return pack_primitives_binary(get_context(), uuids)


def get_all_geometry_gpu() -> bytes:
    ctx = get_context()
    return ctx.packGPUBuffers(ctx.getAllUUIDs())


def get_geometry_count() -> dict:
    return {"count": get_context().getPrimitiveCount()}


def delete_object(object_id: int) -> dict:
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


def delete_primitive(uuid: int) -> dict:
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


def delete_primitives_batch(uuids: list) -> dict:
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
