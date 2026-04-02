import asyncio
import io
import time
import traceback
from contextlib import redirect_stdout, redirect_stderr

from app.helios.context import get_context, get_wpt
from app.helios import registry as reg


async def execute_script(code: str, timeout: float) -> dict:
    ctx = get_context()
    wpt = get_wpt()

    uuids_before = set(ctx.getAllUUIDs())
    mats_before = set(ctx.listMaterials())

    from app.helios.context import vec2, vec3, int2, RGBcolor, SphericalCoord, Date, Time
    namespace = {
        "ctx": ctx, "wpt": wpt,
        "vec2": vec2, "vec3": vec3, "int2": int2,
        "RGBcolor": RGBcolor, "SphericalCoord": SphericalCoord,
        "Date": Date, "Time": Time,
    }

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    error = None
    t0 = time.time()

    try:
        async with asyncio.timeout(timeout):
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                await asyncio.to_thread(exec, code, namespace)
    except TimeoutError:
        error = f"Script timed out after {timeout}s"
    except Exception:
        error = traceback.format_exc()

    duration = time.time() - t0

    uuids_after = set(ctx.getAllUUIDs())
    new_uuids = sorted(uuids_after - uuids_before)
    deleted_uuids = sorted(uuids_before - uuids_after)

    new_object = None
    if new_uuids:
        mats_after = set(ctx.listMaterials())
        new_mats = mats_after - mats_before
        reg._script_object_counter += 1
        obj_name = f"Script Object {reg._script_object_counter}"

        children = []
        for mat in new_mats:
            if mat.startswith("__auto_"):
                new_label = reg.next_material_name(ctx)
                ctx.addMaterial(new_label)
                try:
                    from pyhelios.types import RGBAcolor
                    c = ctx.getMaterialColor(mat)
                    ctx.setMaterialColor(new_label, RGBAcolor(*c))
                except Exception:
                    pass
                prim_list = ctx.getPrimitivesUsingMaterial(mat)
                if prim_list:
                    ctx.assignMaterialToPrimitive(prim_list, new_label)
                ctx.deleteMaterial(mat)
                mat = new_label

            mat_uuids = [u for u in new_uuids if u in set(ctx.getPrimitivesUsingMaterial(mat))]
            if mat_uuids:
                children.append({"label": mat, "uuids": mat_uuids})

        if not children:
            reg.ensure_default_material(ctx, new_uuids)

        obj_id = reg.register_object(obj_name, "script", new_uuids,
                                     unique_name=True, children=children)
        new_object = {
            "object_id": obj_id,
            "name": reg.get_object(obj_id)["name"],
            "uuids": new_uuids,
        }

    return {
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "error": error,
        "duration": duration,
        "new_object": new_object,
        "deleted_uuids": deleted_uuids,
    }
