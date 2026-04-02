from app.helios.context import get_context, get_wpt, PYHELIOS_AVAILABLE, vec3
from app.helios import registry as reg


def get_tree_types() -> dict:
    if not PYHELIOS_AVAILABLE:
        return {"types": ["Almond", "Apple", "Avocado", "Lemon", "Olive", "Orange", "Peach", "Pistachio", "Walnut"]}
    from app.helios.context import WPTType
    return {"types": [t.value for t in WPTType]}


def build_tree(tree_type, req) -> dict:
    wpt = get_wpt()
    tree_id = wpt.buildTree(tree_type, origin=vec3(req.origin.x, req.origin.y, req.origin.z), scale=req.scale)
    trunk_uuids = wpt.getTrunkUUIDs(tree_id)
    branch_uuids = wpt.getBranchUUIDs(tree_id)
    leaf_uuids = wpt.getLeafUUIDs(tree_id)
    all_uuids = wpt.getAllUUIDs(tree_id)
    reg.ensure_default_material(get_context(), all_uuids)
    obj_id = reg.register_object(
        f"{req.type} Tree", "tree", all_uuids,
        tree_id=tree_id, tree_type=req.type,
        trunk_uuids=trunk_uuids, branch_uuids=branch_uuids, leaf_uuids=leaf_uuids,
    )
    return {
        "tree_id": tree_id,
        "trunk_uuids": trunk_uuids,
        "branch_uuids": branch_uuids,
        "leaf_uuids": leaf_uuids,
        "all_uuids": all_uuids,
        "object_id": obj_id,
    }


def get_tree_parts(tree_id: int) -> dict:
    wpt = get_wpt()
    return {
        "trunk_uuids": wpt.getTrunkUUIDs(tree_id),
        "branch_uuids": wpt.getBranchUUIDs(tree_id),
        "leaf_uuids": wpt.getLeafUUIDs(tree_id),
    }
