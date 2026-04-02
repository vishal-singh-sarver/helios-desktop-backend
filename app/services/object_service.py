import struct
import numpy as np
from app.helios.context import get_context
from app.helios import registry as reg
from app.services.geometry_pack import pack_primitives_binary, pack_children_gpu


def get_object_info(object_id: int) -> dict:
    obj = reg.get_object(object_id)
    return {
        "object_id": object_id,
        "name": obj["name"],
        "type": obj["type"],
        "uuids": obj["primitive_uuids"],
        "plant_ids": obj.get("plant_ids", []),
        "children": obj.get("children", []),
    }


def get_object_geometry_binary(object_id: int) -> bytes:
    cached = reg._geometry_cache.pop(object_id, None)
    if cached is not None:
        return cached
    uuids = reg.get_object(object_id)["primitive_uuids"]
    return pack_primitives_binary(get_context(), uuids)


def get_object_geometry_gpu(object_id: int) -> bytes:
    cached = reg._gpu_geometry_cache.pop(object_id, None)
    if cached is not None:
        return cached
    uuids = reg.get_object(object_id)["primitive_uuids"]
    return get_context().packGPUBuffers(uuids)


def get_object_children_binary(object_id: int) -> bytes:
    children = reg.get_object(object_id).get("children", [])
    chunks = [struct.pack("<I", len(children))]
    for child in children:
        label_bytes = child["label"].encode("utf-8")
        uuids = child["uuids"]
        chunks += [struct.pack("<H", len(label_bytes)), label_bytes,
                   struct.pack("<I", len(uuids))]
        if uuids:
            chunks.append(np.array(uuids, dtype=np.int32).tobytes())
    return b"".join(chunks)


def get_object_children_gpu(object_id: int) -> bytes:
    cached = reg._gpu_children_cache.pop(object_id, None)
    if cached is not None:
        return cached
    obj = reg.get_object(object_id)
    return pack_children_gpu(get_context(), obj.get("children", []), obj["primitive_uuids"])
