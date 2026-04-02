import math
import os
from app.helios.context import get_context, vec3
from app.helios import registry as reg


def import_obj(req) -> dict:
    ctx = get_context()
    uuids = ctx.loadOBJ(req.file_path)
    if req.scale and (req.scale.x != 1 or req.scale.y != 1 or req.scale.z != 1):
        ctx.scalePrimitive(uuids, vec3(req.scale.x, req.scale.y, req.scale.z))
    if req.rotation:
        for axis, deg in [("x", req.rotation.x), ("y", req.rotation.y), ("z", req.rotation.z)]:
            if deg != 0:
                ctx.rotatePrimitive(uuids, math.radians(deg), axis)
    if req.origin and (req.origin.x != 0 or req.origin.y != 0 or req.origin.z != 0):
        ctx.translatePrimitive(uuids, vec3(req.origin.x, req.origin.y, req.origin.z))
    if uuids:
        labels = ctx.getPrimitiveMaterialLabel(uuids)
        unassigned = [uuids[j] for j, lbl in enumerate(labels) if not lbl or lbl in ("__default__", "")]
        if unassigned:
            reg.ensure_default_material(ctx, unassigned)
    base_name = os.path.splitext(os.path.basename(req.file_path))[0]
    obj_id = reg.register_object(base_name, "import", uuids, unique_name=True)
    return {"uuids": uuids, "object_id": obj_id, "name": reg.get_object(obj_id)["name"]}


def import_ply(req) -> dict:
    ctx = get_context()
    uuids = ctx.loadPLY(req.file_path)
    if req.scale and (req.scale.x != 1 or req.scale.y != 1 or req.scale.z != 1):
        ctx.scalePrimitive(uuids, vec3(req.scale.x, req.scale.y, req.scale.z))
    if req.rotation:
        for axis, deg in [("x", req.rotation.x), ("y", req.rotation.y), ("z", req.rotation.z)]:
            if deg != 0:
                ctx.rotatePrimitive(uuids, math.radians(deg), axis)
    if req.origin and (req.origin.x != 0 or req.origin.y != 0 or req.origin.z != 0):
        ctx.translatePrimitive(uuids, vec3(req.origin.x, req.origin.y, req.origin.z))
    reg.ensure_default_material(ctx, uuids)
    base_name = os.path.splitext(os.path.basename(req.file_path))[0]
    obj_id = reg.register_object(base_name, "import", uuids, unique_name=True)
    return {"uuids": uuids, "object_id": obj_id, "name": reg.get_object(obj_id)["name"]}
