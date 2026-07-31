"""
Milestone-2 catalog reads: datatypes, object types and material types with
their property definitions. Global master data (no session scoping), seeded
by migration 017. Drives the dynamic forms in the renderer's right panel.
"""
from sqlalchemy.orm import Session

from app.db.models import Datatype, MaterialType, ModelType, ObjectType
from app.services.eav_validation import (
    REQUIRED_MATERIAL_PROPERTIES,
    REQUIRED_OBJECT_PROPERTIES,
    load_type_properties,
)


def list_datatypes(db: Session) -> dict:
    rows = db.query(Datatype).order_by(Datatype.id).all()
    return {"datatypes": [{"id": r.id, "name": r.name} for r in rows]}


def _object_type_payload(db: Session, ot: ObjectType) -> dict:
    required = REQUIRED_OBJECT_PROPERTIES.get(ot.object, set())
    props = load_type_properties(db, object_type_id=ot.id)
    return {
        "id": ot.id,
        "object": ot.object,
        "properties": [
            {
                "property_type_id": p.property_type_id,
                "property": p.property,
                "description": p.description,
                "datatype": p.datatype,
                "min": p.min,
                "max": p.max,
                **({"enum_values": p.enum_values} if p.enum_values else {}),
                "required": p.property in required,
                "display_order": p.display_order,
            }
            for p in props.values()
        ],
    }


def list_object_types(db: Session) -> dict:
    rows = db.query(ObjectType).order_by(ObjectType.id).all()
    return {"object_types": [_object_type_payload(db, ot) for ot in rows]}


def _property_payload(p, required: set | frozenset = frozenset()) -> dict:
    return {
        "property_type_id": p.property_type_id,
        "property": p.property,
        **({"label": p.label} if p.label else {}),
        "description": p.description,
        "datatype": p.datatype,
        "min": p.min,
        "max": p.max,
        **({"enum_values": p.enum_values} if p.enum_values else {}),
        "required": p.property in required,
        "display_order": p.display_order,
    }


def _material_type_payload(db: Session, mt: MaterialType) -> dict:
    """Material type properties split into ungrouped `properties` and nested
    `groups` (migration 027). A group with a `selector_property` set is a
    mutually-exclusive sub-model (Stomatal Conductance's `stomatal_model`): the
    frontend shows only the group whose `selector_value` matches the selector's
    current value. A NULL selector is a plain collapsible group (Farquhar). Group
    order follows its first member's display_order; the selector metadata is taken
    from that member (all members of a group share it).

    Only visibility='editable' (light-green) properties are returned (migration
    029); 'external' (weather/global) and 'computed' (shown only when the owning
    model is disabled) are withheld here — the write path keeps them via the
    unfiltered load_type_properties. Skipping non-editable members before a group
    is created means an all-hidden group never appears."""
    props = load_type_properties(db, material_type_id=mt.id)
    required = REQUIRED_MATERIAL_PROPERTIES.get(mt.materialtype, frozenset())
    top_level: list[dict] = []
    groups: list[dict] = []
    by_name: dict[str, dict] = {}
    for p in props.values():
        if p.visibility != "editable":
            continue
        if p.group_name is None:
            top_level.append(_property_payload(p, required))
            continue
        group = by_name.get(p.group_name)
        if group is None:
            group = {
                "name": p.group_name,
                "selector_property": p.selector_property,
                "selector_value": p.selector_value,
                "display_order": p.display_order,
                "properties": [],
            }
            by_name[p.group_name] = group
            groups.append(group)
        group["properties"].append(_property_payload(p, required))
    return {
        "id": mt.id,
        "materialtype": mt.materialtype,
        "description": mt.description,
        "properties": top_level,
        "groups": groups,
    }


def list_material_types(db: Session) -> dict:
    rows = db.query(MaterialType).order_by(MaterialType.id).all()
    return {"material_types": [_material_type_payload(db, mt) for mt in rows]}


def list_model_types(db: Session) -> dict:
    """Runnable simulation models, one-level hierarchy (submodels nested)."""
    rows = db.query(ModelType).order_by(ModelType.id).all()
    children: dict[int, list] = {}
    for r in rows:
        if r.parent_id is not None:
            children.setdefault(r.parent_id, []).append(
                {"id": r.id, "model": r.model, "description": r.description})
    return {"model_types": [
        {
            "id": r.id,
            "model": r.model,
            "description": r.description,
            "submodels": children.get(r.id, []),
        }
        for r in rows if r.parent_id is None
    ]}
