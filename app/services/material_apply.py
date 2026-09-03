"""
Per-primitive material application (snapshot model).

A material assignment applies the material's CURRENT property values directly
onto the geometry's primitives (decision: snapshot-on-assign — there is no live
library sync). Two channels are handled here:

    color_r/g/b   -> a per-object Helios material LABEL (addMaterial +
                     setMaterialColor + assignMaterialToPrimitive). A material
                     label is serialized by writeXML (a raw setPrimitiveColor is
                     NOT), so color survives a context.xml reload with no repaint.
    everything    -> ctx.setPrimitiveData<typed>(uuids, name, value) (model data,
    else                                                              one label each)

The TEXTURE channel (texture_file) is applied here too, on the SAME per-object
label: setMaterialTexture puts the winning texture — or the default soil when
nothing is assigned — on the label; an empty string clears it so the material
colour shows. The UVs/tiling are baked into the TileObject by
scene_object_service._build (subdiv x texture_repeat); only the texture *image*
is set here.

Snapshot source: object_property_data rows for the (object, member) pair —
written by material_sync_service._snapshot_frozen for EVERY materialized
member (assign + reconcile). reapply_all_materials re-applies from these rows,
so hydration and in-place regeneration repaint without re-reading the
(possibly edited) library — including STALE rows whose library member/group
was deleted: they keep painting until the scenario is synced (migration 022).

The single-valued color channel is owned by the precedence-winning assignment
(the Visualiser member; with no Visualiser member there is no winner, so the
object falls back to the soil texture + the default colour). Model-data labels
are additive — each assigned material contributes its own labels.

All PyHelios access degrades to a no-op when the native library is unavailable
(headless / CI) or when the object was not built in THIS session.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Datatype,
    MaterialType,
    ObjectMaterial,
    ObjectPropertyData,
    PropertyType,
)
from app.helios import context as helios_ctx
from app.helios import registry as reg
from app.services.eav_validation import (
    VISUALISATION_PROPERTIES,
    api_error,
    decode_value,
    load_type_properties,
)

logger = logging.getLogger(__name__)

# Viewport precedence among a geometry's assigned materials (Plan B): the
# Visualiser-type material is the SOLE owner of the single-valued colour/texture/
# opacity channel; with no Visualiser member there is no winner, and the object
# falls back to the default soil texture + default colour.
_PRECEDENCE_TYPE = "Visualiser"

def _resolve_default_ground_texture() -> str:
    """Absolute path to the bundled PyHelios soil texture
    (plugins/visualizer/textures/dirt.jpg), used as the default ground surface
    when no material texture is assigned. The existing plugin texture is used
    directly — no copy — and it sits inside get_texture_dirs(), so
    /api/textures/serve is allowed to serve it."""
    try:
        import pyhelios
        # __path__[0] is the inner package; the plugin trees live at its parent.
        bases = [Path(p) for p in pyhelios.__path__]
        bases += [b.parent for b in bases]
    except Exception:
        return ""
    for base in bases:
        for cand in (
            base / "pyhelios_build/build/plugins/visualizer/textures/dirt.jpg",
            base / "helios-core/plugins/visualizer/textures/dirt.jpg",
        ):
            if cand.exists():
                return str(cand)
    return ""


# Default ground surface texture — the bundled PyHelios soil
# (plugins/visualizer/textures/dirt.jpg). SINGLE source for the path;
# scene_object_service._build bakes the same file at create time.
_DEFAULT_GROUND_TEXTURE = _resolve_default_ground_texture()

# datatype -> the Context.setPrimitiveData<typed> method that stores it.
# boolean has no native bool setter, so it rides UInt as 0/1 (matching Helios'
# own twosided-flag convention). date/time/file/enum/string all store as text.
_DATATYPE_SETTER = {
    "float": "setPrimitiveDataFloat",
    "integer": "setPrimitiveDataInt",
    "boolean": "setPrimitiveDataUInt",
    "string": "setPrimitiveDataString",
    "enum": "setPrimitiveDataString",
    "date": "setPrimitiveDataString",
    "time": "setPrimitiveDataString",
    "file": "setPrimitiveDataString",
}

# Enum properties Helios consumes as a numeric 0/1 flag, not a string. The
# catalog models the heat-transfer flag as an enum (One Sided / Two Sided) for a
# clean dropdown (migration 028), but the engine keeps its twosided-flag
# convention — map the token back to a UInt on write.
_ENUM_UINT_FLAGS = {
    "two_sided_heat_transfer": {"One Sided": 0, "Two Sided": 1},
}


def resolve_texture_path(value: str | None) -> str | None:
    """Resolve a stored texture_file value to an absolute path.

    plugin:<plugin>/<file> → PyHelios texture library dirs
    uploads/<...>          → settings.data_dir / uploads / <...>
    anything else          → returned as-is (already a path)
    """
    if not value:
        return None
    if value.startswith("plugin:"):
        spec = value[len("plugin:"):]
        plugin, _, filename = spec.partition("/")
        from app.services.material_service import get_texture_dirs
        for name, tex_dir in get_texture_dirs():
            if name == plugin and (tex_dir / filename).exists():
                return str(tex_dir / filename)
        return None
    if value.startswith("uploads/"):
        # .resolve() so the baked value is ABSOLUTE: data_dir defaults to the
        # relative Path("data"), and a bare "data/uploads/..." would get the
        # data_dir prefix applied a second time when served back.
        path = (settings.data_dir / value).resolve()
        return str(path) if path.exists() else None
    return value


def _snap(subdiv: int, repeat: int) -> int:
    """The repeat the engine will really use: clamped to the subdivision, then
    walked down to a divisor of it (Context_object.cpp:353-359). Mirroring only
    the inequality and not this is the classic wrong answer — 521 at repeat 2
    reads as legal, but 521 is odd so the repeat collapses to 1 and it fails."""
    r = max(1, min(int(subdiv), int(repeat)))
    while subdiv % r:
        r -= 1
    return r


def _texture_pixels(path: str) -> tuple[int, int] | None:
    """The image's pixel dimensions, read from its header.

    Pillow reads the size lazily — header only, no pixel decode — so this stays
    cheap even for a large PNG.

    Deliberately NOT asked of the engine. Helios exposes no path-keyed getter,
    so the only way to ask it is to add a textured primitive and read
    getPrimitiveTextureSize. That permanently inserts the file into Context's
    `textures` map, which nothing ever erases, and for a PNG with an alpha
    channel it forces a full decode — a lot of residue for two integers.

    Routed the way the ENGINE routes, which is by extension and case-sensitive:
    exactly ".png" goes to its PNG reader, everything else to its JPEG reader
    (Context.cpp:106-127). Pillow instead sniffs the file's real content, so the
    two can disagree — an uppercase ".PNG" is a valid PNG to Pillow and is handed
    to the JPEG loader by Helios, which rejects it. Where the reader Helios will
    use does not match what the file actually is, report unknown and let the
    engine raise its own error rather than measure a file it cannot open.

    Returns None when the size cannot be established: missing file, unsupported
    format, corrupt header, or that reader mismatch. That means UNKNOWN, and the
    caller must let the build proceed rather than reject on it.
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            fmt = im.format
            w, h = im.size
        helios_reads_as_png = path.endswith(".png")
        if helios_reads_as_png != (fmt == "PNG"):
            logger.debug("[texture] %r is %s but Helios will read it as %s",
                         path, fmt, "PNG" if helios_reads_as_png else "JPEG")
            return None
        return (int(w), int(h)) if w > 0 and h > 0 else None
    except Exception:
        logger.debug("[texture] could not read pixel size of %r", path, exc_info=True)
        return None


def check_resolution(subdiv: tuple[int, int], repeat: tuple[int, int],
                     texture_path: str | None, ground_name: str) -> None:
    """Raise if this ground cannot be built with this texture.

    addTileObject refuses `subdiv >= snapped_repeat * texture_pixels` on either
    axis (Context_object.cpp:377-380) — note `>=`, so a 512px texture caps the
    subdivision at 511. Called before a material is applied and before a
    texture is changed under grounds already using it, so the user is told
    instead of the write landing and the repaint silently failing.

    Silent when it cannot answer — no texture, colour mode, headless, or an
    unreadable file. We would rather let the engine refuse a build than block
    one it would have accepted.
    """
    if not texture_path:
        return
    px = _texture_pixels(texture_path)
    if px is None:
        return
    if not any(int(s) >= _snap(int(s), int(r)) * p
               for s, r, p in zip(subdiv, repeat, px)):
        return

    raise api_error(
        422, "RESOLUTION_TOO_HIGH",
        f"This texture is {px[0]}x{px[1]} pixels, too small for "
        f"'{ground_name}' at {subdiv[0]} x {subdiv[1]}.")


# ── Snapshot reads ───────────────────────────────────────────────────────────


def _assignment_snapshot_native(db: Session, so_id: int, material_id: int) -> dict:
    """Snapshot (object_property_data) values for one assignment, as
    {property_name: native value}."""
    rows = (
        db.query(PropertyType.property, ObjectPropertyData.value, Datatype.name)
        .join(ObjectPropertyData, ObjectPropertyData.property_type_id == PropertyType.id)
        .join(Datatype, Datatype.id == PropertyType.datatype_id)
        .filter(
            ObjectPropertyData.scenario_object_id == so_id,
            ObjectPropertyData.project_material_id == material_id,
        )
        .all()
    )
    return {prop: decode_value(value, dt) for prop, value, dt in rows}


def _winning_assignment(db: Session, assignments: list[ObjectMaterial]) -> ObjectMaterial | None:
    if not assignments:
        return None
    type_names = dict(
        db.query(MaterialType.id, MaterialType.materialtype)
        .filter(MaterialType.id.in_({a.material_type_id for a in assignments}))
        .all()
    )
    for a in assignments:
        if type_names.get(a.material_type_id) == _PRECEDENCE_TYPE:
            return a
    return None   # Plan B: no Visualiser member -> no winner (soil + default colour)


# ── Per-primitive writers ────────────────────────────────────────────────────


def _color_label(so) -> str:
    """Per-object Helios material label that carries the object's color. A
    material label is serialized by writeXML (a raw setPrimitiveColor is NOT),
    so color survives a context.xml round-trip with no repaint on reload."""
    return f"so_{so.id}"


def _set_color_label(ctx, uuids: list[int], label: str,
                     rgba: tuple[float, float, float, float], tex: str) -> None:
    """Point the object's primitives at the per-object Helios material, carrying
    its colour (`rgba`, 0..1 incl. the alpha/opacity channel) AND its texture
    (`tex`: a file path to show that image, or "" to show the solid colour).
    Created on first use, updated after. The texture image lives on this
    material; the UVs were baked at build time."""
    from pyhelios.types import RGBAcolor
    if not ctx.doesMaterialExist(label):
        ctx.addMaterial(label)
    ctx.setMaterialColor(label, RGBAcolor(rgba[0], rgba[1], rgba[2], rgba[3]))
    ctx.setMaterialTexture(label, tex)
    ctx.assignMaterialToPrimitive(uuids, label)


def _winner_color(db: Session, so_id: int, winner) -> tuple[float, float, float, float]:
    """RGBA (0..1) of the precedence-winning assignment, else the default. The
    alpha channel comes from the winner's `opacity` (a 0..100 percent); an absent
    opacity is treated as fully opaque (1.0), matching the pre-Plan-B default."""
    if winner is not None:
        values = _assignment_snapshot_native(db, so_id, winner.project_material_id)
        r, g, b = values.get("color_r"), values.get("color_g"), values.get("color_b")
        o = values.get("opacity")
        if r is not None or g is not None or b is not None or o is not None:
            alpha = 1.0 if o is None else max(0.0, min(1.0, o / 100.0))
            return ((r or 0) / 255.0, (g or 0) / 255.0, (b or 0) / 255.0, alpha)
    return reg.DEFAULT_MATERIAL_COLOR


def _is_texture_mode(values: dict) -> bool:
    """Whether a Visualiser winner's snapshot is in TEXTURE mode (vs colour).
    Honors the explicit `texture_toggle` (migration 025); for a legacy member
    with no toggle, falls back to 'has a resolvable texture_file'."""
    toggle = values.get("texture_toggle")
    if toggle is not None:
        return bool(toggle)
    return bool(resolve_texture_path(values.get("texture_file")))


def _winner_texture(db: Session, so_id: int, winner) -> str:
    """Texture string for the object's material, decided by the precedence winner:
        no material          -> the default soil (an unstyled ground reads as soil)
        texture-mode winner  -> that texture's resolved path (soil if missing)
        colour-mode winner   -> "" (cleared, so the winner's solid colour shows)
    Fed to setMaterialTexture: a path renders the image, "" renders the colour
    (geometry_pack keys off getPrimitiveTextureFile being non-empty vs empty)."""
    if winner is None:
        return _DEFAULT_GROUND_TEXTURE
    values = _assignment_snapshot_native(db, so_id, winner.project_material_id)
    if _is_texture_mode(values):
        return resolve_texture_path(values.get("texture_file")) or _DEFAULT_GROUND_TEXTURE
    return ""


def _apply_model_data(ctx, uuids: list[int], defs: dict, values: dict) -> None:
    """Apply every non-visualisation property as typed primitive data."""
    for name, prop in defs.items():
        if name in VISUALISATION_PROPERTIES:
            continue
        value = values.get(name)
        if value is None:
            continue
        flag_map = _ENUM_UINT_FLAGS.get(name)
        if flag_map is not None:
            # Enum token stored/shown, but the engine wants the 0/1 flag.
            ctx.setPrimitiveDataUInt(uuids, name, flag_map.get(str(value), 0))
            continue
        setter_name = _DATATYPE_SETTER.get(prop.datatype)
        if setter_name is None:
            continue
        setter = getattr(ctx, setter_name)
        if prop.datatype == "boolean":
            setter(uuids, name, 1 if value else 0)
        elif prop.datatype == "float":
            setter(uuids, name, float(value))
        elif prop.datatype == "integer":
            setter(uuids, name, int(value))
        else:
            setter(uuids, name, str(value))


# ── Public entry points ──────────────────────────────────────────────────────


def clear_material_from_primitives(db: Session, sctx, so, material_type_id: int) -> None:
    """Remove a material type's model-data labels from the object's primitives
    (the color channel is owned by reapply_all_materials via the object's color
    material label). Call BEFORE deleting an assignment, with the OLD material
    type, so stale primitive data never lingers. No-op when headless or the
    object isn't built this session."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        return
    if so.id not in sctx.persisted_objects:
        return
    uuids = json.loads(so.helios_uuids or "[]")
    if not uuids:
        return
    ctx = helios_ctx.get_context(sctx)
    defs = load_type_properties(db, material_type_id=material_type_id)
    for name in defs:
        if name in VISUALISATION_PROPERTIES:
            continue
        try:
            ctx.clearPrimitiveData(uuids, name)
        except Exception:
            pass    # label may not be present on these primitives
    invalidate_geometry_caches(sctx)


def reapply_all_materials(db: Session, sctx, so) -> None:
    """Re-apply every current assignment's snapshot onto the object's primitives.

    Applies every assignment's model-data labels (setPrimitiveData), then sets the
    object's per-object material to the precedence winner's colour AND texture
    (default soil texture + default colour when nothing is assigned). Both go
    through a Helios material label so they serialize into context.xml — reload
    needs no repaint. Safe no-op when headless, the object isn't built this
    session, or it has no live primitives."""
    if not helios_ctx.PYHELIOS_AVAILABLE:
        return
    if so.id not in sctx.persisted_objects:
        return
    uuids = json.loads(so.helios_uuids or "[]")
    if not uuids:
        return
    ctx = helios_ctx.get_context(sctx)

    assignments = (
        db.query(ObjectMaterial)
        .filter(ObjectMaterial.scenario_object_id == so.id)
        .all()
    )

    for om in assignments:
        defs = load_type_properties(db, material_type_id=om.material_type_id)
        values = _assignment_snapshot_native(db, so.id, om.project_material_id)
        _apply_model_data(ctx, uuids, defs, values)

    winner = _winning_assignment(db, assignments)
    _set_color_label(ctx, uuids, _color_label(so),
                     _winner_color(db, so.id, winner),
                     _winner_texture(db, so.id, winner))

    invalidate_geometry_caches(sctx)


def invalidate_geometry_caches(sctx) -> None:
    """The viewport's next binary fetch must see the change."""
    sctx.geometry_cache = {}
    sctx.gpu_geometry_cache = {}
    sctx.gpu_children_cache = {}


# ── Spectral global data ─────────────────────────────────────────────────────
#
# A spectral .xml holds <globaldata_vec2> entries that each become a context-wide
# global-data spectrum keyed by a label. The UI parses those labels from the
# file, so removal is driven by the labels the caller passes in. (Where/when the
# file is LOADED into the context is a separate decision, not wired here.)


def remove_spectral_labels(sctx, labels: list[str]) -> int:
    """Remove the given global-data labels from the live context; return how many
    existed. Labels come from the caller (the UI parsed them from the .xml) —
    used both to delete labels and when a spectral file is deleted."""
    if not helios_ctx.PYHELIOS_AVAILABLE or sctx.context is None:
        return 0
    ctx, removed = sctx.context, 0
    for label in labels:
        if ctx.doesGlobalDataExist(label):
            ctx.clearGlobalData(label)
            removed += 1
    return removed
