"""
Binary geometry packing helpers shared across services.
"""
import struct
import numpy as np


class PackCancelled(Exception):
    """Raised when the client that asked for this geometry has gone away.

    Packing a whole scene is the longest READ in the app — 228 MB on a 1000x1000
    ground — and it used to run to completion even after the browser had closed
    the connection, because nothing was watching. It is a Python loop, so unlike
    an engine call it CAN stop part-way.
    """


# How often the packing loop checks whether the client is still there. Per
# primitive would put an Event.is_set() call in the innermost loop of the
# hottest path in the app; per 2048 bounds the wasted work at well under a
# millisecond while costing nothing measurable.
_CANCEL_CHECK_EVERY = 2048


def _default_uvs(nv: int):
    """The UVs Helios MEANS when a textured primitive stores none.

    Empty UV is not missing data in the engine — it is a valid state meaning
    "stretch the whole image across this shape" (Context.cpp checks
    uv.size() == 4 against empty in copyPrimitive). Texture and UVs live in two
    different places: getTextureFile reads the MATERIAL, getTextureUV reads the
    primitive's own uv field, and assigning a textured material touches only the
    first. So a colour-mode ground — built by addTileObject with no texturefile,
    hence no UVs — that later gets the default soil texture through its material
    label is textured with no UVs, and the engine considers that perfectly normal.

    The wire format cannot express it: the reader requires vertexCount*8 bytes of
    UV whenever the texture path is non-empty. Writing the full-image quad here
    says the same thing the engine means, so the texture still renders.

    Returns None for vertex counts with no obvious full-image mapping; the caller
    then declares no texture at all rather than emit a buffer that cannot be read.
    """
    if nv == 4:
        return np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                        dtype=np.float32)
    if nv == 3:
        return np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    return None


def pack_primitives_binary(ctx, uuids: list, progress_cb=None,
                           prefetched_mat_labels=None,
                           prefetched_colors=None,
                           cancelled=None) -> bytes:
    if not uuids:
        return struct.pack("<I", 0)

    def _abort_if_cancelled():
        if cancelled is not None and cancelled.is_set():
            raise PackCancelled()

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
        for k, idx in enumerate(indices):
            if k % _CANCEL_CHECK_EVERY == 0:
                _abort_if_cancelled()
            idx = int(idx)
            v_start = int(vert_offsets[idx])
            v_end = int(vert_offsets[idx + 1])
            verts = vert_data[v_start:v_end].astype(np.float32)
            clr = colors[idx].astype(np.float32)
            uuid_val = uuids[idx]
            # UVs are RESOLVED BEFORE the texture length is written, because the
            # two have to agree. The reader requires exactly nv*8 bytes of UV
            # whenever the length is non-zero, and this used to write the length
            # unconditionally and the UVs only if the engine had any — so a
            # textured primitive with no stored UVs produced a buffer that could
            # not be parsed, and the WHOLE object failed to draw (HELIO-339:
            # "needed 32 more byte(s) at offset 233, buffer is 233 byte(s)").
            uv_raw = None
            if tex_bytes:
                uv_j = textured_uv_idx.get(idx)
                if uv_j is not None:
                    uv_start = int(uv_offsets[uv_j])
                    uv_end = int(uv_offsets[uv_j + 1])
                    cand = uv_data[uv_start:uv_end].reshape(-1, 2).astype(np.float32)
                    if cand.shape[0] == nv:
                        cand[:, 1] = 1.0 - cand[:, 1]   # V-flip for Three.js
                        uv_raw = cand
                if uv_raw is None:
                    # No UVs stored, or a count that does not match the vertices.
                    # The full-image quad is what the engine means by empty UV.
                    uv_raw = _default_uvs(nv)

            chunk = struct.pack("<iI", uuid_val, nv)
            chunk += verts.tobytes()
            chunk += clr[:3].tobytes()
            if tex_bytes and uv_raw is not None:
                chunk += struct.pack("<H", len(tex_bytes))
                chunk += tex_bytes
                chunk += uv_raw.tobytes()
            else:
                # Declaring no texture is the only other self-consistent option.
                # Renders as flat colour rather than losing the whole object.
                chunk += struct.pack("<H", 0)
            chunks.append(chunk)

    for nv in np.unique(vc_arr):
        mask = np.where(vc_arr == nv)[0]
        for tex_bytes in set(tex_bytes_list[i] for i in mask):
            # Checked HERE as well as inside _pack_group. This rescan is
            # O(len(mask) x distinct textures) and on a large scene it is the
            # dominant cost, not the packing below it — guarding only the inner
            # loop leaves most of the work uninterruptible.
            _abort_if_cancelled()
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
