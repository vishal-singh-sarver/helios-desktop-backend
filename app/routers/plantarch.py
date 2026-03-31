import asyncio
import logging
import traceback
import queue
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, Response

logger = logging.getLogger(__name__)

from app.helios.context import get_context, get_plantarch, PLANTARCH_AVAILABLE
from app.helios.context import vec2, vec3, int2
from app.helios import registry as reg
from app.schemas.tree import CanopyBuildRequest

router = APIRouter()


def _finalize_canopy(ctx, pa, plant_ids: list, species: str):
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
    result = {"object_id": obj_id, "plant_ids": plant_ids,
              "primitive_count": len(all_uuids), "material_labels": mat_labels}
    return result, mat_labels, colors_list


@router.get("/species")
async def get_plant_species():
    if not PLANTARCH_AVAILABLE:
        raise HTTPException(503, "PlantArchitecture plugin not available")
    def _do():
        return {"species": get_plantarch().getAvailablePlantModels()}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


@router.post("/canopy")
async def build_canopy(req: CanopyBuildRequest):
    if not PLANTARCH_AVAILABLE:
        raise HTTPException(503, "PlantArchitecture plugin not available")
    def _do():
        pa = get_plantarch()
        ctx = get_context()
        pa.loadPlantModelFromLibrary(req.species)
        plant_ids = pa.buildPlantCanopyFromLibrary(
            canopy_center=vec3(req.canopy_center.x, req.canopy_center.y, req.canopy_center.z),
            plant_spacing=vec2(req.plant_spacing.x, req.plant_spacing.y),
            plant_count=int2(req.plant_count_x, req.plant_count_y),
            age=req.age,
        )
        result, _, _ = _finalize_canopy(ctx, pa, plant_ids, req.species)
        return result
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


@router.post("/canopy/stream")
async def build_canopy_stream(req: CanopyBuildRequest):
    if not PLANTARCH_AVAILABLE:
        raise HTTPException(503, "PlantArchitecture plugin not available")

    progress_queue: queue.Queue = queue.Queue()

    def _build():
        try:
            logger.debug("starting")
            pa = get_plantarch()
            ctx = get_context()
            pa.loadPlantModelFromLibrary(req.species)

            def _progress_cb(frac, msg=""):
                progress_queue.put({"progress": round(frac * 0.5, 3), "message": msg})

            plant_ids = pa.buildPlantCanopyFromLibrary(
                canopy_center=vec3(req.canopy_center.x, req.canopy_center.y, req.canopy_center.z),
                plant_spacing=vec2(req.plant_spacing.x, req.plant_spacing.y),
                plant_count=int2(req.plant_count_x, req.plant_count_y),
                age=req.age,
            )
            logger.debug("built %d plants", len(plant_ids))
            progress_queue.put({"progress": 0.55, "message": "Finalizing materials..."})
            result, mat_labels, colors_list = _finalize_canopy(ctx, pa, plant_ids, req.species)
            logger.debug("finalized: primitive_count=%d", result['primitive_count'])

            # Pre-pack GPU geometry
            obj_id = result["object_id"]
            all_uuids = reg.get_object(obj_id)["primitive_uuids"]
            progress_queue.put({"progress": 0.70, "message": "Packing GPU buffers..."})
            from app.routers._geometry_pack import pack_children_gpu
            children = reg.get_object(obj_id).get("children", [])
            logger.debug("packing GPU buffers for %d UUIDs, %d children", len(all_uuids), len(children))
            reg._gpu_geometry_cache[obj_id] = ctx.packGPUBuffers(all_uuids)
            reg._gpu_children_cache[obj_id] = pack_children_gpu(ctx, children, all_uuids)

            logger.debug("done, sending result")
            progress_queue.put({"progress": 1.0, "message": "Done", "result": result})
        except Exception as ex:
            traceback.print_exc()
            progress_queue.put({"error": str(ex)})

    async def _stream():
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        loop.run_in_executor(None, _build)
        while True:
            await _asyncio.sleep(0.1)
            while not progress_queue.empty():
                event = progress_queue.get_nowait()
                try:
                    payload = json.dumps(event)
                except Exception as je:
                    logger.error("json.dumps failed: %r for event keys=%s", je, list(event.keys()))
                    traceback.print_exc()
                    yield f"data: {json.dumps({'error': f'Serialization error: {je}'})}\n\n"
                    return
                yield f"data: {payload}\n\n"
                if event.get("progress", 0) >= 1.0 or "error" in event:
                    return

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.get("/canopy")
async def build_canopy_get(req: CanopyBuildRequest):
    """Alias kept for frontend compatibility."""
    return await build_canopy(req)
