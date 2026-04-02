import logging
import traceback
import queue

from app.helios.context import get_context, get_plantarch, vec2, vec3, int2
from app.helios import registry as reg
from app.services.geometry_pack import pack_children_gpu

logger = logging.getLogger(__name__)


def finalize_canopy(ctx, pa, plant_ids: list, species: str):
    all_uuids = []
    for pid in plant_ids:
        try:
            all_uuids.extend(pa.getAllPlantUUIDs(pid))
        except Exception:
            pass

    # Promote __auto_ materials
    mat_labels = []
    colors_list = []
    auto_mats = [m for m in ctx.listMaterials() if m.startswith("__auto_")]
    for auto_mat in auto_mats:
        new_label = reg.next_material_name(ctx)
        ctx.addMaterial(new_label)
        try:
            from pyhelios.types import RGBAcolor
            c = ctx.getMaterialColor(auto_mat)
            ctx.setMaterialColor(new_label, RGBAcolor(*c))
            colors_list.append(list(c[:3]))
        except Exception:
            colors_list.append([0.5, 0.5, 0.5])
        prim_list = ctx.getPrimitivesUsingMaterial(auto_mat)
        if prim_list:
            ctx.assignMaterialToPrimitive(prim_list, new_label)
        ctx.deleteMaterial(auto_mat)
        mat_labels.append(new_label)

    # Unassigned primitives
    unassigned = []
    try:
        labels = ctx.getPrimitiveMaterialLabel(all_uuids)
        unassigned = [all_uuids[i] for i, lbl in enumerate(labels)
                      if not lbl or lbl in ("__default__", "")]
    except Exception:
        pass
    if unassigned:
        reg.ensure_default_material(ctx, unassigned)

    # Build children list
    children = []
    for mat in mat_labels:
        try:
            mat_uuids = ctx.getPrimitivesUsingMaterial(mat)
            if mat_uuids:
                children.append({"label": mat, "uuids": mat_uuids})
        except Exception:
            pass

    obj_id = reg.register_object(
        f"{species} Canopy", "canopy", all_uuids,
        plant_ids=plant_ids, children=children,
    )
    result = {
        "object_id": obj_id,
        "plant_ids": plant_ids,
        "primitive_count": len(all_uuids),
        "material_labels": mat_labels,
    }
    return result, mat_labels, colors_list


def get_plant_species() -> dict:
    return {"species": get_plantarch().getAvailablePlantModels()}


def build_canopy(req) -> dict:
    pa = get_plantarch()
    ctx = get_context()
    pa.loadPlantModelFromLibrary(req.species)
    plant_ids = pa.buildPlantCanopyFromLibrary(
        canopy_center=vec3(req.canopy_center.x, req.canopy_center.y, req.canopy_center.z),
        plant_spacing=vec2(req.plant_spacing.x, req.plant_spacing.y),
        plant_count=int2(req.plant_count_x, req.plant_count_y),
        age=req.age,
    )
    result, _, _ = finalize_canopy(ctx, pa, plant_ids, req.species)
    return result


def build_canopy_bg(req, progress_queue: queue.Queue) -> None:
    """Runs canopy build in a background thread, posting progress events to the queue."""
    try:
        logger.debug("starting")
        pa = get_plantarch()
        ctx = get_context()
        pa.loadPlantModelFromLibrary(req.species)

        plant_ids = pa.buildPlantCanopyFromLibrary(
            canopy_center=vec3(req.canopy_center.x, req.canopy_center.y, req.canopy_center.z),
            plant_spacing=vec2(req.plant_spacing.x, req.plant_spacing.y),
            plant_count=int2(req.plant_count_x, req.plant_count_y),
            age=req.age,
        )
        logger.debug("built %d plants", len(plant_ids))
        progress_queue.put({"progress": 0.55, "message": "Finalizing materials..."})
        result, mat_labels, colors_list = finalize_canopy(ctx, pa, plant_ids, req.species)
        logger.debug("finalized: primitive_count=%d", result["primitive_count"])

        # Pre-pack GPU geometry
        obj_id = result["object_id"]
        all_uuids = reg.get_object(obj_id)["primitive_uuids"]
        progress_queue.put({"progress": 0.70, "message": "Packing GPU buffers..."})
        children = reg.get_object(obj_id).get("children", [])
        logger.debug("packing GPU buffers for %d UUIDs, %d children", len(all_uuids), len(children))
        reg._gpu_geometry_cache[obj_id] = ctx.packGPUBuffers(all_uuids)
        reg._gpu_children_cache[obj_id] = pack_children_gpu(ctx, children, all_uuids)

        logger.debug("done, sending result")
        progress_queue.put({"progress": 1.0, "message": "Done", "result": result})
    except Exception as ex:
        traceback.print_exc()
        progress_queue.put({"error": str(ex)})
