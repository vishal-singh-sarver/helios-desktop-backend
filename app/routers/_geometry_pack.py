"""
Binary geometry packing helpers shared by geometry.py, objects.py, canopy.py.
Extracted here to avoid circular imports.
"""
import struct
import time
import numpy as np


def pack_primitives_binary(ctx, uuids: list, progress_cb=None,
                           prefetched_mat_labels=None,
                           prefetched_colors=None) -> bytes:
    if not uuids:
        return struct.pack("<I", 0)

    vert_data, vert_offsets = ctx.getPrimitiveVertices(uuids)
    if prefetched_colors is not None:
        colors = np.array(prefetched_colors, copy=True)
    else:
        colors = np.array(ctx.getPrimitiveColor(uuids), copy=True)

    # Resolve material texture suppression
    try:
        mat_labels = prefetched_mat_labels or ctx.getPrimitiveMaterialLabel(uuids)
        for i, lbl in enumerate(mat_labels):
            if lbl and lbl not in ("__default__", ""):
                try:
                    if ctx.getMaterialTextureColorOverride(lbl):
                        colors[i] = [1.0, 1.0, 1.0]
                except Exception:
                    pass
    except Exception:
        pass

    tex_files = ctx.getPrimitiveTextureFile(uuids)
    textured_indices = [i for i, tf in enumerate(tex_files) if tf]
    textured_uuids = [uuids[i] for i in textured_indices]
    if textured_uuids:
        uv_data, uv_offsets = ctx.getPrimitiveTextureUV(textured_uuids)
    else:
        uv_data = np.empty((0,), dtype=np.float32)
        uv_offsets = np.zeros((1,), dtype=np.uint32)
    textured_uv_idx = {i: j for j, i in enumerate(textured_indices)}

    n = len(uuids)
    vc_arr = np.array([int(vert_offsets[i+1] - vert_offsets[i]) // 3 for i in range(n)], dtype=np.int32)

    tex_bytes_list = [tf.encode("utf-8") if tf else b"" for tf in tex_files]

    chunks = []

    def _pack_group(indices, nv, tex_bytes):
        for idx in indices:
            idx = int(idx)
            v_start = int(vert_offsets[idx])
            v_end = int(vert_offsets[idx + 1])
            verts = vert_data[v_start:v_end].astype(np.float32)
            clr = colors[idx].astype(np.float32)
            uuid_val = uuids[idx]
            chunk = struct.pack("<iI", uuid_val, nv)
            chunk += verts.tobytes()
            chunk += clr[:3].tobytes()
            chunk += struct.pack("<H", len(tex_bytes))
            if tex_bytes:
                chunk += tex_bytes
                uv_j = textured_uv_idx.get(idx)
                if uv_j is not None:
                    uv_start = int(uv_offsets[uv_j])
                    uv_end = int(uv_offsets[uv_j + 1])
                    uv_raw = uv_data[uv_start:uv_end].reshape(-1, 2).astype(np.float32)
                    uv_raw[:, 1] = 1.0 - uv_raw[:, 1]  # V-flip for Three.js
                    chunk += uv_raw.tobytes()
            chunks.append(chunk)

    for nv in np.unique(vc_arr):
        mask = np.where(vc_arr == nv)[0]
        for tex_bytes in set(tex_bytes_list[i] for i in mask):
            grp = np.array([i for i in mask if tex_bytes_list[i] == tex_bytes], dtype=np.int64)
            _pack_group(grp, int(nv), tex_bytes)

    return struct.pack("<I", n) + b"".join(chunks)


def pack_children_gpu(ctx, children: list, all_uuids: list) -> bytes:
    if not children:
        # No children — pack all as single unnamed child
        data = ctx.packGPUBuffers(all_uuids)
        hdr = struct.pack("<I", 1) + struct.pack("<H", 0) + struct.pack("<I", len(data))
        return hdr + data

    chunks = [struct.pack("<I", len(children))]
    for child in children:
        label_bytes = child["label"].encode("utf-8")
        gpu_data = ctx.packGPUBuffers(child["uuids"]) if child["uuids"] else b""
        chunks += [
            struct.pack("<H", len(label_bytes)), label_bytes,
            struct.pack("<I", len(gpu_data)), gpu_data,
        ]
    return b"".join(chunks)
