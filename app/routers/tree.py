import asyncio
import traceback
from fastapi import APIRouter, HTTPException
from app.helios.context import get_context, get_wpt, PYHELIOS_AVAILABLE
from app.helios.context import vec3
from app.helios import registry as reg
from app.schemas.tree import TreeBuildRequest

router = APIRouter()


@router.get("/types")
async def get_tree_types():
    if not PYHELIOS_AVAILABLE:
        return {"types": ["Almond","Apple","Avocado","Lemon","Olive","Orange","Peach","Pistachio","Walnut"]}
    from app.helios.context import WPTType
    return {"types": [t.value for t in WPTType]}


@router.post("/build")
async def build_tree(req: TreeBuildRequest):
    if not PYHELIOS_AVAILABLE:
        raise HTTPException(503, "PyHelios not available")
    from app.helios.context import WPTType
    tree_type = next((t for t in WPTType if t.value.lower() == req.type.lower()), None)
    if tree_type is None:
        raise HTTPException(400, f"Unknown tree type: {req.type}. Available: {[t.value for t in WPTType]}")
    def _do():
        wpt = get_wpt()
        tree_id = wpt.buildTree(tree_type, origin=vec3(req.origin.x, req.origin.y, req.origin.z), scale=req.scale)
        trunk_uuids  = wpt.getTrunkUUIDs(tree_id)
        branch_uuids = wpt.getBranchUUIDs(tree_id)
        leaf_uuids   = wpt.getLeafUUIDs(tree_id)
        all_uuids    = wpt.getAllUUIDs(tree_id)
        reg.ensure_default_material(get_context(), all_uuids)
        obj_id = reg.register_object(f"{req.type} Tree", "tree", all_uuids,
                                     tree_id=tree_id, tree_type=req.type,
                                     trunk_uuids=trunk_uuids, branch_uuids=branch_uuids,
                                     leaf_uuids=leaf_uuids)
        return {"tree_id": tree_id, "trunk_uuids": trunk_uuids, "branch_uuids": branch_uuids,
                "leaf_uuids": leaf_uuids, "all_uuids": all_uuids, "object_id": obj_id}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


@router.get("/{tree_id}/parts")
async def get_tree_parts(tree_id: int):
    def _do():
        wpt = get_wpt()
        return {"trunk_uuids": wpt.getTrunkUUIDs(tree_id),
                "branch_uuids": wpt.getBranchUUIDs(tree_id),
                "leaf_uuids": wpt.getLeafUUIDs(tree_id)}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))
