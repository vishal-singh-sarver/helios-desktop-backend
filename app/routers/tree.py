import asyncio
import traceback
from fastapi import APIRouter, HTTPException

from app.helios.context import PYHELIOS_AVAILABLE
from app.schemas.tree import TreeBuildRequest
from app.services import tree_service

router = APIRouter()


@router.get("/types")
async def get_tree_types():
    return tree_service.get_tree_types()


@router.post("/build")
async def build_tree(req: TreeBuildRequest):
    if not PYHELIOS_AVAILABLE:
        raise HTTPException(503, "PyHelios not available")
    from app.helios.context import WPTType
    tree_type = next((t for t in WPTType if t.value.lower() == req.type.lower()), None)
    if tree_type is None:
        raise HTTPException(400, f"Unknown tree type: {req.type}. Available: {[t.value for t in WPTType]}")
    try:
        return await asyncio.to_thread(tree_service.build_tree, tree_type, req)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


@router.get("/{tree_id}/parts")
async def get_tree_parts(tree_id: int):
    try:
        return await asyncio.to_thread(tree_service.get_tree_parts, tree_id)
    except Exception as e:
        raise HTTPException(500, str(e))
