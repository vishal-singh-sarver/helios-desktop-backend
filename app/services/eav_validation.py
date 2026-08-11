"""
Shared EAV property validation for milestone-2 geometry & materials.

API payloads carry one flat `properties` object keyed by
property_type.property with native JSON types. This module validates each
key against the catalog (object_property_type / material_property_type
joined to property_type + datatype) and converts between native JSON
values and the canonical TEXT stored in object_property_data /
material_data.

Canonical stored forms (spec §3):
    float    decimal string, ≤7 dp (half-even), trailing zeros/dot stripped
    integer  base-10 string
    boolean  '0' / '1'
    string   as-is
    date     YYYY-MM-DD
    time     HH:MM:SS (24h)
    file     project-relative path or plugin:<name>/<file>
    enum     exact token from property_type.enum_values

Spec: docs/api/milestone-2-materials-geometry.md (helios_gui repo).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as _date, time as _time
from decimal import Context as DecimalContext, Decimal, InvalidOperation, ROUND_HALF_EVEN

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import (
    Datatype,
    MaterialPropertyType,
    ObjectPropertyType,
    Project,
    PropertyType,
)

# Properties whose lower bound is exclusive (> min, not >= min). Currently none:
# ground size (length/breadth) uses an INCLUSIVE floor of 0.01 m (migration 021),
# so the catalog min alone enforces value >= 0.01 (0.01 accepted, 0.009 rejected).
_EXCLUSIVE_MIN: set[str] = set()

# Required intrinsic properties per object type (the catalog has no
# `required` column — this is the single source for both the catalog
# response and create-time validation).
#
# Story (create ground): the form populates EVERY parameter with a default, and
# clearing any one (on create or edit) must fail "Field is required" — so every
# Ground parameter is required, including position_x/y/z and rotation_z.
REQUIRED_OBJECT_PROPERTIES = {
    "Ground": {"length", "breadth", "resolution_x", "resolution_y",
               "position_x", "position_y", "position_z", "rotation_z",
               "texture_x", "texture_y"},
}

# Visualisation properties — owned SOLELY by the "Visualiser" material type
# (migrations 024/025), rendered on the Visualisation surface; everything else
# is a model parameter. `opacity` is the RGBA alpha channel as a 0..100 percent
# (material_apply divides by 100 for the 0..1 alpha). `texture_toggle` is the
# mode selector (true = texture, false = solid colour).
VISUALISATION_PROPERTIES = {"color_r", "color_g", "color_b", "texture_file",
                            "opacity", "texture_toggle"}

# The two mutually-exclusive Visualiser modes (migration 025). texture_toggle
# picks one; that mode's fields are required and the other's must be absent.
VISUALISER_TEXTURE_FIELDS = {"texture_file"}
VISUALISER_COLOUR_FIELDS = {"color_r", "color_g", "color_b", "opacity"}

# Returned in BOTH radiation modes even though migration 031 puts them in a group
# gated on use_radiation_bands='false'. The group stays — the Radiation form finds
# these two fields BY that selector and draws them under the spectral file — but
# the VALUES must not be withheld: the form renders both dropdowns in either mode,
# and the write path is ungated (load_type_properties is unfiltered), so a label
# saved in per-band mode really is stored. Gating the read alone made a saved
# value unreadable, which is worse than never accepting it.
ALWAYS_RETURNED_PROPERTIES = {"reflectivity_spectrum", "transmissivity_spectrum"}


def visualiser_mode_required(properties: dict) -> set[str]:
    """Required-by-mode rule for a Visualiser member on a FULL-REPLACEMENT write.

    `texture_toggle` selects the mode; that mode's fields are required and the
    OTHER mode's fields must be absent (a Visualiser member is always exactly one
    complete mode — never empty, never both). Returns the set of property names
    to require; raises 400 on a missing/invalid toggle or a cross-mode field.
    """
    toggle = properties.get("texture_toggle")
    if toggle is None:
        raise api_error(400, "MISSING_REQUIRED_PROPERTY", "texture_toggle is required")
    if not isinstance(toggle, bool):
        raise api_error(400, "DATATYPE_MISMATCH", "texture_toggle must be a boolean")
    active, forbidden = ((VISUALISER_TEXTURE_FIELDS, VISUALISER_COLOUR_FIELDS) if toggle
                         else (VISUALISER_COLOUR_FIELDS, VISUALISER_TEXTURE_FIELDS))
    present = sorted(f for f in forbidden if properties.get(f) is not None)
    if present:
        mode = "texture" if toggle else "colour"
        raise api_error(400, "VISUALISER_MODE_CONFLICT",
                        f"{', '.join(present)} not allowed in {mode} mode")
    return {"texture_toggle"} | active

_MAX_DECIMALS = 7


def api_error(status: int, code: str, message: str,
              extra: dict | None = None) -> HTTPException:
    """House error shape: detail = {"error": ..., "code": ...}. `extra` merges
    additional structured fields into the detail (e.g. the `conflicts` list on
    DUPLICATE_MATERIAL_TYPE_ASSIGNMENT)."""
    detail = {"error": message, "code": code}
    if extra:
        detail.update(extra)
    return HTTPException(status, detail)


def project_or_404(db: Session, session_id: str, project_id: str) -> Project:
    """Project ownership check with the m2 {error, code} detail shape."""
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.session_id == session_id)
        .first()
    )
    if project is None:
        raise api_error(404, "PROJECT_NOT_FOUND", f"Project {project_id} not found")
    return project


@dataclass
class PropDef:
    """One property as it applies to a specific object/material type
    (catalog row with link-table range overrides already applied)."""
    property_type_id: int
    property: str
    description: str | None
    datatype: str
    min: float | None
    max: float | None
    enum_values: list | None
    display_order: int
    # Material-only grouping metadata (migration 027); None for object types
    # (object_property_type has no such columns — see the getattr in the loader).
    group_name: str | None = None
    selector_property: str | None = None
    selector_value: str | None = None
    label: str | None = None
    # 'editable' | 'external' | 'computed' (migration 029); None for object types.
    visibility: str | None = None


def load_type_properties(db: Session, *, object_type_id: int | None = None,
                         material_type_id: int | None = None) -> dict[str, PropDef]:
    """Catalog lookup: property defs attached to one object/material type,
    keyed by property name, with min/max overrides applied."""
    if object_type_id is not None:
        link, fk = ObjectPropertyType, ObjectPropertyType.object_type_id == object_type_id
    else:
        link, fk = MaterialPropertyType, MaterialPropertyType.material_type_id == material_type_id

    rows = (
        db.query(link, PropertyType, Datatype.name)
        .join(PropertyType, PropertyType.id == link.property_type_id)
        .join(Datatype, Datatype.id == PropertyType.datatype_id)
        .filter(fk)
        .order_by(link.display_order)
        .all()
    )
    defs: dict[str, PropDef] = {}
    for link_row, pt, dt_name in rows:
        defs[pt.property] = PropDef(
            property_type_id=pt.id,
            property=pt.property,
            description=pt.description,
            datatype=dt_name,
            min=link_row.min_override if link_row.min_override is not None else pt.min,
            max=link_row.max_override if link_row.max_override is not None else pt.max,
            enum_values=json.loads(pt.enum_values) if pt.enum_values else None,
            display_order=link_row.display_order,
            # getattr: object_property_type has no grouping columns → None.
            group_name=getattr(link_row, "group_name", None),
            selector_property=getattr(link_row, "selector_property", None),
            selector_value=getattr(link_row, "selector_value", None),
            label=getattr(link_row, "label", None),
            visibility=getattr(link_row, "visibility", None),
        )
    return defs


def member_property_values(defs: dict, values: dict) -> dict:
    """A member's property VALUES with mutually-exclusive sub-model params gated
    by their selector (migration 027): a selector-gated property (e.g. bbl_gs0,
    whose selector is stomatal_model='BBL') is included only when the member's
    current selector value matches — so only the chosen sub-model's params are
    returned, never the other sub-models'. Non-gated properties are unaffected.

    Compared as lowercase TEXT: selector_value is a catalog TEXT column while the
    member's value is already decoded, so a BOOLEAN selector (migration 031's
    use_radiation_bands) arrives as Python False and `False == 'false'` would
    never match — hiding its properties in every mode. String selectors such as
    stomatal_model are unaffected ('BBL' -> 'bbl' == 'bbl').

    ALWAYS_RETURNED_PROPERTIES escape the gate entirely — see that constant. The
    stomatal sub-models are untouched: only the chosen model's params come back."""
    return {
        name: values.get(name)
        for name, p in defs.items()
        if p.selector_property is None
        or name in ALWAYS_RETURNED_PROPERTIES
        or str(values.get(p.selector_property)).lower() == str(p.selector_value).lower()
    }


# ── Value validation + canonical encoding ────────────────────────────────────


def _decimal_places(d: Decimal) -> int:
    exp = d.as_tuple().exponent
    return -exp if isinstance(exp, int) and exp < 0 else 0


def _canonical_number(value, prop: PropDef) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise api_error(400, "DATATYPE_MISMATCH", f"{prop.property} must be a number")
    try:
        d = Decimal(str(value))
        if not d.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        raise api_error(400, "INVALID_NUMBER", "This input is not supported")
    if _decimal_places(d) > _MAX_DECIMALS:
        raise api_error(400, "TOO_MANY_DECIMALS", "Only 7 Decimal places are supported")
    try:
        # Default 28-digit precision overflows quantize for |value| >= 1e21.
        d = d.quantize(Decimal("1e-7"), rounding=ROUND_HALF_EVEN,
                       context=DecimalContext(prec=60))
    except InvalidOperation:
        raise api_error(400, "INVALID_NUMBER", "This input is not supported")
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _fmt_bound(x: float | None) -> str:
    """Human-readable range bound: a plain integer for whole numbers
    (1000000, not 1e+06), a trimmed decimal otherwise, 'unbounded' for None."""
    if x is None:
        return "unbounded"
    f = float(x)
    if f.is_integer():
        return str(int(f))
    return f"{f:.7f}".rstrip("0").rstrip(".")


def _check_range(value: float, prop: PropDef) -> None:
    lo, hi = prop.min, prop.max
    if lo is None and hi is None:
        return
    exclusive_lo = prop.property in _EXCLUSIVE_MIN
    out = (
        (lo is not None and (value <= lo if exclusive_lo else value < lo))
        or (hi is not None and value > hi)
    )
    if out:
        lo_s = _fmt_bound(lo)
        hi_s = _fmt_bound(hi)
        raise api_error(
            400, "VALUE_OUT_OF_RANGE",
            f"Values should be between ({lo_s} - {hi_s})",
        )


def canonicalize_value(value, prop: PropDef) -> str:
    """Validate one native-JSON value against its PropDef and return the
    canonical TEXT form. `value` must not be None (callers skip nulls)."""
    dt = prop.datatype

    if dt == "float":
        text = _canonical_number(value, prop)
        _check_range(float(text), prop)
        return text

    if dt == "integer":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise api_error(400, "DATATYPE_MISMATCH", f"{prop.property} must be an integer")
        if isinstance(value, float):
            if not value.is_integer():
                raise api_error(400, "DATATYPE_MISMATCH", f"{prop.property} must be an integer")
            value = int(value)
        _check_range(float(value), prop)
        return str(value)

    if dt == "boolean":
        if not isinstance(value, bool):
            raise api_error(400, "DATATYPE_MISMATCH", f"{prop.property} must be a boolean")
        return "1" if value else "0"

    if dt == "string":
        if not isinstance(value, str):
            raise api_error(400, "DATATYPE_MISMATCH", f"{prop.property} must be a string")
        return value

    if dt == "date":
        if not isinstance(value, str):
            raise api_error(400, "DATATYPE_MISMATCH", f"{prop.property} must be a YYYY-MM-DD date string")
        try:
            return _date.fromisoformat(value).isoformat()
        except ValueError:
            raise api_error(400, "DATATYPE_MISMATCH", f"{prop.property} must be a YYYY-MM-DD date string")

    if dt == "time":
        if not isinstance(value, str):
            raise api_error(400, "DATATYPE_MISMATCH", f"{prop.property} must be a HH:MM[:SS] time string")
        try:
            return _time.fromisoformat(value).isoformat(timespec="seconds")
        except ValueError:
            raise api_error(400, "DATATYPE_MISMATCH", f"{prop.property} must be a HH:MM[:SS] time string")

    if dt == "file":
        if not isinstance(value, str):
            raise api_error(400, "DATATYPE_MISMATCH", f"{prop.property} must be a file reference string")
        return value

    if dt == "enum":
        options = prop.enum_values or []
        if not isinstance(value, str) or value not in options:
            raise api_error(
                400, "ENUM_INVALID_OPTION",
                f"{prop.property} must be one of {', '.join(options)}",
            )
        return value

    raise api_error(500, "UNKNOWN_DATATYPE", f"Unhandled datatype '{dt}'")


def decode_value(canonical: str | None, datatype: str):
    """Canonical TEXT → native JSON value."""
    if canonical is None:
        return None
    if datatype == "float":
        f = float(canonical)
        return int(f) if f.is_integer() and abs(f) < 1e15 else f
    if datatype == "integer":
        return int(canonical)
    if datatype == "boolean":
        return canonical == "1"
    return canonical


def validate_properties(
    properties: dict,
    defs: dict[str, PropDef],
    *,
    type_label: str,
    type_kind: str = "object type",
    required: set[str] | None = None,
) -> dict[str, str | None]:
    """Validate a request `properties` dict against the type's catalog.

    Returns {property_name: canonical_text_or_None}. None means the client
    explicitly cleared the value (the row is removed / not stored).
    """
    if not isinstance(properties, dict):
        raise api_error(400, "DATATYPE_MISMATCH", "properties must be an object")

    out: dict[str, str | None] = {}
    for name, value in properties.items():
        prop = defs.get(name)
        if prop is None:
            if type_kind == "material type":
                # Spec §9: properties of a different material type get their
                # own code, not the generic UNKNOWN_PROPERTY.
                raise api_error(400, "MATERIAL_TYPE_MISMATCH",
                                f"{name} is not a property of {type_label}")
            raise api_error(400, "UNKNOWN_PROPERTY",
                            f"Unknown property '{name}' for {type_kind} {type_label}")
        out[name] = None if value is None else canonicalize_value(value, prop)

    for name in (required or set()):
        if out.get(name) is None:
            raise api_error(400, "MISSING_REQUIRED_PROPERTY", f"{name} is required")
    return out


def validate_cross_field(values: dict, object_type: str) -> None:
    """Cross-field range rules a single property can't express on its own.

    Ground: the texture repeat counts must not exceed the resolution
    (texture_x <= resolution_x, texture_y <= resolution_y). `values` holds the
    EFFECTIVE native values (on create the full set; on update the existing
    values merged with the patch). A pair is skipped when either side is absent.
    """
    if object_type != "Ground":
        return
    for tex, res in (("texture_x", "resolution_x"), ("texture_y", "resolution_y")):
        t, r = values.get(tex), values.get(res)
        if t is not None and r is not None and t > r:
            raise api_error(
                400, "VALUE_OUT_OF_RANGE",
                f"Values should be between (1 - {int(r)})",
            )


# ── Name rules (geometry / material / group names) ───────────────────────────

_NAME_MAX = 20


def validate_name(name: str) -> str:
    """≤20 chars including spaces, non-empty after strip, no control characters."""
    if not isinstance(name, str) or not name.strip():
        raise api_error(400, "NAME_REQUIRED", "Name is required")
    name = name.strip()
    # Control chars (NUL especially) must be rejected HERE: str.strip() leaves NUL
    # in place and Python counts it, but SQLite's length() stops at the first NUL —
    # so a name like "\x00abc" would sail past this check and then die on the
    # CHECK(length(name) BETWEEN 1 AND 20) constraint at commit, surfacing as a
    # bogus "name already exists" from the callers' IntegrityError handlers.
    if any(ch < " " for ch in name):
        raise api_error(400, "NAME_INVALID", "Name contains invalid characters")
    if len(name) > _NAME_MAX:
        raise api_error(400, "NAME_TOO_LONG", "Character limit exceeded")
    return name


def next_default_name(existing_lower: set[str], prefix: str) -> str:
    """Auto-number 'Ground.001', 'Material.001', ... case-insensitively."""
    counter = 1
    while f"{prefix}.{counter:03d}".lower() in existing_lower:
        counter += 1
    return f"{prefix}.{counter:03d}"
