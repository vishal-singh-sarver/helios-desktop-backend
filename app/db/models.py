"""
SQLAlchemy ORM models — mirrors 001_initial.sql exactly.
"""
import uuid as _uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, Text, Float, LargeBinary,
    ForeignKey, ForeignKeyConstraint, UniqueConstraint, Index,
)
from app.db.database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(_uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id                 = Column(Text, primary_key=True, default=_new_id)
    session_id         = Column(Text, nullable=False, index=True)
    name               = Column(Text, nullable=False)
    latitude           = Column(Float, nullable=False, default=0.0)
    longitude          = Column(Float, nullable=False, default=0.0)
    utc_offset         = Column(Text, nullable=False, default="+00:00")
    created_at         = Column(Text, nullable=False, default=_now)
    updated_at         = Column(Text, nullable=False, default=_now, onupdate=_now)
    current_version_id = Column(Integer, ForeignKey("project_versions.id", ondelete="SET NULL"), nullable=True)


class ProjectVersion(Base):
    __tablename__ = "project_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version_num"),
    )

    id               = Column(Integer, primary_key=True, autoincrement=True)
    project_id       = Column(Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    version_num      = Column(Integer, nullable=False)
    label            = Column(Text, nullable=True)
    created_at       = Column(Text, nullable=False, default=_now)
    scene_xml        = Column(LargeBinary, nullable=False)   # lzma compressed
    registry_json    = Column(Text, nullable=False)
    bytes_original   = Column(Integer, nullable=False, default=0)
    bytes_compressed = Column(Integer, nullable=False, default=0)


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        UniqueConstraint("project_id", "name"),
    )

    id                = Column(Text, primary_key=True, default=_new_id)
    project_id        = Column(Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name              = Column(Text, nullable=False)
    weather_file_path = Column(Text, nullable=True)
    context_file_path = Column(Text, nullable=True)
    created_at        = Column(Text, nullable=False, default=_now)
    updated_at        = Column(Text, nullable=False, default=_now, onupdate=_now)


class ProjectObject(Base):
    __tablename__ = "project_objects"
    __table_args__ = (
        UniqueConstraint("project_id", "object_id"),
        Index("idx_objects_project", "project_id"),
    )

    id              = Column(Integer, primary_key=True, autoincrement=True)
    project_id      = Column(Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    object_id       = Column(Integer, nullable=False)
    name            = Column(Text, nullable=False)
    type            = Column(Text, nullable=False)
    primitive_uuids = Column(Text, nullable=False, default="[]")   # JSON
    children        = Column(Text, nullable=False, default="[]")   # JSON


class HeliosDataType(Base):
    """Master-data: a kind of measurement (Temperature, Humidity, ...). Global, not session-scoped."""
    __tablename__ = "helios_data_types"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    data_type   = Column(Text, nullable=False, unique=True)
    description = Column(Text, nullable=True)
    created_at  = Column(Text, nullable=False, default=_now)
    updated_at  = Column(Text, nullable=False, default=_now, onupdate=_now)


class DataUnit(Base):
    """Master-data: a unit belonging to one data type (Celsius → Temperature).

    Affine conversion to the type's canonical/base unit:
        value_in_base = value * to_base_factor + to_base_offset
    `is_base=1` marks the canonical unit. The partial unique index
    `idx_data_units_one_base` enforces at most one base per data_type.
    """
    __tablename__ = "data_units"
    __table_args__ = (
        UniqueConstraint("data_type_id", "unit"),
        Index(
            "idx_data_units_one_base",
            "data_type_id",
            unique=True,
            sqlite_where=Column("is_base") == 1,
        ),
    )

    id             = Column(Integer, primary_key=True, autoincrement=True)
    unit           = Column(Text, nullable=False)
    alias          = Column(Text, nullable=True)
    data_type_id   = Column(
        Integer,
        ForeignKey("helios_data_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    min            = Column(Float, nullable=True)
    max            = Column(Float, nullable=True)
    to_base_factor = Column(Float, nullable=False, default=1.0)
    to_base_offset = Column(Float, nullable=False, default=0.0)
    is_base        = Column(Integer, nullable=False, default=0)
    created_at     = Column(Text, nullable=False, default=_now)
    updated_at     = Column(Text, nullable=False, default=_now, onupdate=_now)


class WeatherDataHeader(Base):
    """Per-scenario: maps a CSV column name to a (data_type, unit) from the catalog."""
    __tablename__ = "weather_data_headers"
    __table_args__ = (
        UniqueConstraint("scenario_id", "name"),
    )

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    scenario_id         = Column(
        Text,
        ForeignKey("scenarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    helios_data_type_id = Column(
        Integer,
        ForeignKey("helios_data_types.id", ondelete="RESTRICT"),
        nullable=True,
    )
    unit_id             = Column(
        Integer,
        ForeignKey("data_units.id", ondelete="RESTRICT"),
        nullable=True,
    )
    name                = Column(Text, nullable=False)
    status              = Column(Integer, nullable=False, default=1)
    display_order       = Column(Integer, nullable=False, default=0)
    created_at          = Column(Text, nullable=False, default=_now)
    updated_at          = Column(Text, nullable=False, default=_now, onupdate=_now)


# ── Milestone 2: Materials & Geometry persistence (migration 017) ────────────
# Spec: docs/api/milestone-2-materials-geometry.md in the parent helios_gui
# repo. The migration SQL is authoritative; these classes mirror it.
# Name columns are COLLATE NOCASE in the DDL — case-insensitive uniqueness
# comes from the schema, not from these mappings.


class Datatype(Base):
    """Catalog: a value type (float, integer, boolean, string, date, time, file, enum)."""
    __tablename__ = "datatype"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    name       = Column(Text, nullable=False, unique=True)
    created_at = Column(Text, nullable=False, default=_now)
    updated_at = Column(Text, nullable=False, default=_now, onupdate=_now)


class PropertyType(Base):
    """Catalog: one named property shared across object/material types."""
    __tablename__ = "property_type"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    property    = Column(Text, nullable=False, unique=True)
    description = Column(Text, nullable=True)
    datatype_id = Column(Integer, ForeignKey("datatype.id", ondelete="RESTRICT"), nullable=False)
    min         = Column(Float, nullable=True)
    max         = Column(Float, nullable=True)
    enum_values = Column(Text, nullable=True)   # JSON array of allowed tokens
    created_at  = Column(Text, nullable=False, default=_now)
    updated_at  = Column(Text, nullable=False, default=_now, onupdate=_now)


class ObjectType(Base):
    """Catalog: a geometry kind (Ground, Crop)."""
    __tablename__ = "object_types"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    object     = Column(Text, nullable=False, unique=True)
    created_at = Column(Text, nullable=False, default=_now)
    updated_at = Column(Text, nullable=False, default=_now, onupdate=_now)


class ObjectPropertyType(Base):
    """Catalog M:N: which properties an object type has (+ range narrowing)."""
    __tablename__ = "object_property_type"
    __table_args__ = (
        UniqueConstraint("object_type_id", "property_type_id"),
    )

    id               = Column(Integer, primary_key=True, autoincrement=True)
    object_type_id   = Column(Integer, ForeignKey("object_types.id", ondelete="CASCADE"), nullable=False)
    property_type_id = Column(Integer, ForeignKey("property_type.id", ondelete="CASCADE"), nullable=False)
    min_override     = Column(Float, nullable=True)
    max_override     = Column(Float, nullable=True)
    display_order    = Column(Integer, nullable=False, default=0)


class ObjectGroup(Base):
    """Geometry tree group. Deleting a group SET NULLs its members."""
    __tablename__ = "object_group"
    __table_args__ = (
        Index("idx_object_group_project_name_ci", "project_id", "name", unique=True),
    )

    id          = Column(Integer, primary_key=True, autoincrement=True)
    scenario_id = Column(Text, ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id  = Column(Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name        = Column(Text, nullable=False)
    created_at  = Column(Text, nullable=False, default=_now)
    updated_at  = Column(Text, nullable=False, default=_now, onupdate=_now)


class ScenarioObject(Base):
    """A persisted geometry instance (e.g. one Ground), scenario-scoped.

    project_id is denormalized from scenarios. Name uniqueness is per-SCENARIO
    (migration 019): a name may repeat across scenarios of a project but not
    within one.
    helios_uuids holds the live PyHelios primitive UUIDs (JSON array) and
    ctx_object_id the live PyHelios compound-object id — both rewritten on every
    build/rebuild; session-scoped, never trusted across a restart without
    hydration (guard on so.id in pctx.persisted_objects before use).
    """
    __tablename__ = "scenario_object"
    __table_args__ = (
        Index("idx_scenario_object_scenario_name_ci", "scenario_id", "name", unique=True),
    )

    id             = Column(Integer, primary_key=True, autoincrement=True)
    scenario_id    = Column(Text, ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id     = Column(Text, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name           = Column(Text, nullable=False)
    object_type_id = Column(Integer, ForeignKey("object_types.id", ondelete="RESTRICT"), nullable=False)
    group_id       = Column(Integer, ForeignKey("object_group.id", ondelete="SET NULL"), nullable=True, index=True)
    helios_uuids   = Column(Text, nullable=False, default="[]")   # JSON primitive UUIDs
    ctx_object_id  = Column(Integer, nullable=True)               # live PyHelios object id (session-scoped)
    visible        = Column(Integer, nullable=False, default=1)   # eye icon (3D viewport)
    render_enabled = Column(Integer, nullable=False, default=1)   # render icon (all models)
    created_at     = Column(Text, nullable=False, default=_now)
    updated_at     = Column(Text, nullable=False, default=_now, onupdate=_now)


class ModelType(Base):
    """Catalog: a runnable simulation model (Radiation, Energy Balance, ...).

    Hierarchical — parent_id NULL marks a top-level model, otherwise the
    row is a submodel (e.g. Stomatal Conductance → Ball-Woodrow-Berry).
    Distinct from MaterialType (parameter groups) even though the
    top-level models correspond 1:1 to the six groups today.
    """
    __tablename__ = "model_type"
    __table_args__ = (
        UniqueConstraint("model", "parent_id"),
        Index(
            "idx_model_type_toplevel_name_ci",
            "model",
            unique=True,
            sqlite_where=Column("parent_id").is_(None),
        ),
    )

    id          = Column(Integer, primary_key=True, autoincrement=True)
    model       = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    parent_id   = Column(Integer, ForeignKey("model_type.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at  = Column(Text, nullable=False, default=_now)
    updated_at  = Column(Text, nullable=False, default=_now, onupdate=_now)


class ScenarioObjectModel(Base):
    """Per-GEOMETRY model participation: which models USE this geometry.
    Absent row = enabled; only explicit settings are stored."""
    __tablename__ = "scenario_object_model"

    scenario_object_id = Column(Integer, ForeignKey("scenario_object.id", ondelete="CASCADE"), primary_key=True)
    model_type_id      = Column(Integer, ForeignKey("model_type.id", ondelete="RESTRICT"), primary_key=True)
    enabled            = Column(Integer, nullable=False, default=1)
    created_at         = Column(Text, nullable=False, default=_now)
    updated_at         = Column(Text, nullable=False, default=_now, onupdate=_now)


class ScenarioModel(Base):
    """Per-SCENARIO run configuration: which models RUN on the Run button.
    Absent row = enabled; only explicit settings are stored."""
    __tablename__ = "scenario_model"

    scenario_id   = Column(Text, ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True)
    model_type_id = Column(Integer, ForeignKey("model_type.id", ondelete="RESTRICT"), primary_key=True)
    enabled       = Column(Integer, nullable=False, default=1)
    created_at    = Column(Text, nullable=False, default=_now)
    updated_at    = Column(Text, nullable=False, default=_now, onupdate=_now)


class MaterialType(Base):
    """Catalog: a model parameter group (Radiation, Energy Balance, ...)."""
    __tablename__ = "material_type"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    materialtype = Column(Text, nullable=False, unique=True)
    description  = Column(Text, nullable=True)
    created_at   = Column(Text, nullable=False, default=_now)
    updated_at   = Column(Text, nullable=False, default=_now, onupdate=_now)


class MaterialPropertyType(Base):
    """Catalog M:N: which properties a material type has (+ range narrowing)."""
    __tablename__ = "material_property_type"
    __table_args__ = (
        UniqueConstraint("material_type_id", "property_type_id"),
    )

    id               = Column(Integer, primary_key=True, autoincrement=True)
    material_type_id = Column(Integer, ForeignKey("material_type.id", ondelete="CASCADE"), nullable=False)
    property_type_id = Column(Integer, ForeignKey("property_type.id", ondelete="CASCADE"), nullable=False)
    min_override     = Column(Float, nullable=True)
    max_override     = Column(Float, nullable=True)
    display_order    = Column(Integer, nullable=False, default=0)
    # Parameter grouping for the material form (migration 027). group_name marks a
    # named sub-group ("Farquhar model"); selector_property/selector_value gate a
    # mutually-exclusive sub-model (Stomatal Conductance's `stomatal_model` enum).
    # label is the display name; NULL = frontend humanizes `property`.
    group_name        = Column(Text, nullable=True)
    selector_property = Column(Text, nullable=True)
    selector_value    = Column(Text, nullable=True)
    label             = Column(Text, nullable=True)
    # Material-form visibility (migration 029). 'editable' (light green) is the
    # only value returned by the catalog endpoint; 'external' (weather/global) and
    # 'computed' (shown only when the owning model is disabled) are withheld.
    visibility        = Column(Text, nullable=False, default="editable")


class MaterialGroup(Base):
    """A named material group — the user-facing library object (migration 022).

    GLOBAL: the name is unique across everything (NOCASE); project_id and
    scenario_id are pure provenance (where the group was created) and are SET
    NULL when their parent dies — a group never dies with a project/scenario.
    Members live in project_material (one per material type). Distinct from
    ObjectGroup, which groups GEOMETRY in the scene tree.
    """
    __tablename__ = "material_group"
    __table_args__ = (
        Index("idx_material_group_name_ci", "name", unique=True),
    )

    id          = Column(Integer, primary_key=True, autoincrement=True)
    project_id  = Column(Text, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    scenario_id = Column(Text, ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True)
    name        = Column(Text, nullable=False)
    created_at  = Column(Text, nullable=False, default=_now)
    updated_at  = Column(Text, nullable=False, default=_now, onupdate=_now)


class ProjectMaterial(Base):
    """A nameless material-group MEMBER — exactly one material type
    (migration 022). Identity is (group, material type); the group carries the
    user-facing name; EAV values live in material_data. Ids are never reused
    (AUTOINCREMENT), so a stale soft reference in object_material can never
    re-bind to a new member.
    """
    __tablename__ = "project_material"
    __table_args__ = (
        UniqueConstraint("material_group_id", "material_type_id"),
    )

    id                = Column(Integer, primary_key=True, autoincrement=True)
    material_group_id = Column(Integer, ForeignKey("material_group.id", ondelete="CASCADE"), nullable=False, index=True)
    material_type_id  = Column(Integer, ForeignKey("material_type.id", ondelete="RESTRICT"), nullable=False)
    created_at        = Column(Text, nullable=False, default=_now)
    updated_at        = Column(Text, nullable=False, default=_now, onupdate=_now)


class MaterialData(Base):
    """The library material's own property values (one row per property)."""
    __tablename__ = "material_data"
    __table_args__ = (
        UniqueConstraint("project_material_id", "property_type_id"),
    )

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    project_material_id = Column(Integer, ForeignKey("project_material.id", ondelete="CASCADE"), nullable=False, index=True)
    property_type_id    = Column(Integer, ForeignKey("property_type.id", ondelete="RESTRICT"), nullable=False)
    value               = Column(Text, nullable=True)
    created_at          = Column(Text, nullable=False, default=_now)
    updated_at          = Column(Text, nullable=False, default=_now, onupdate=_now)


class ObjectMaterial(Base):
    """MATERIALIZED member application: one row per (geometry, group member),
    maintained by material_sync_service — never by FK cascade (migration 022).

    project_material_id and material_group_id are SOFT references (no FK — the
    "break point"): deleting library rows must not reach into other scenarios'
    applied state. A row whose member/group no longer exists is STALE — it
    keeps painting (material_apply reads it + its snapshots) and keeps owning
    its type slot until the scenario is synced. material_group_id attributes
    the row to its group even after the library row is gone.
    UNIQUE(scenario_object_id, material_type_id) is the DB-level enforcer of
    "no duplicate material type across the groups assigned to one geometry".
    """
    __tablename__ = "object_material"
    __table_args__ = (
        UniqueConstraint("scenario_object_id", "material_type_id"),
        Index("idx_object_material_group", "scenario_object_id", "material_group_id"),
    )

    scenario_object_id  = Column(Integer, ForeignKey("scenario_object.id", ondelete="CASCADE"), primary_key=True)
    project_material_id = Column(Integer, primary_key=True)
    material_group_id   = Column(Integer, nullable=False)
    material_type_id    = Column(Integer, ForeignKey("material_type.id", ondelete="RESTRICT"), nullable=False)
    created_at          = Column(Text, nullable=False, default=_now)
    updated_at          = Column(Text, nullable=False, default=_now, onupdate=_now)


class ObjectMaterialGroup(Base):
    """The user-facing assignment: a material group applied to a geometry,
    with ONE sync flag per pair (migration 022).

    sync=1 → follows the library (reconciled eagerly in the active scenario,
             via PUT material-sync elsewhere).
    sync=0 → frozen: snapshot values in object_property_data are never
             refreshed (membership add/remove still applies on sync).
    material_group_id is a SOFT reference (no FK) — an assignment whose group
    was deleted survives as the out-of-sync signal until the scenario syncs.
    """
    __tablename__ = "object_material_group"

    scenario_object_id = Column(Integer, ForeignKey("scenario_object.id", ondelete="CASCADE"), primary_key=True)
    material_group_id  = Column(Integer, primary_key=True)
    sync               = Column(Integer, nullable=False, default=1)
    created_at         = Column(Text, nullable=False, default=_now)
    updated_at         = Column(Text, nullable=False, default=_now, onupdate=_now)


class ObjectPropertyData(Base):
    """Merged per-geometry value table.

    project_material_id IS NULL     → intrinsic geometry parameter
                                      (length, breadth, position, ...).
    project_material_id IS NOT NULL → snapshot value for one materialized
                                      member (object_material row) — written
                                      for EVERY member; refreshed on sync when
                                      the assignment follows the library.
    Uniqueness is enforced by two partial unique indexes because SQLite
    treats NULLs as distinct in a plain UNIQUE. The composite FK to
    object_material is skipped by SQLite when project_material_id is NULL,
    so intrinsic rows are exempt while snapshot rows cascade when their
    object_material row is deleted (the only FK that still reaches this
    table from the material side — the migration-022 "break point" keeps
    library deletes away from applied state).
    """
    __tablename__ = "object_property_data"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_object_id", "project_material_id"],
            ["object_material.scenario_object_id", "object_material.project_material_id"],
            ondelete="CASCADE",
        ),
        Index(
            "idx_opd_intrinsic",
            "scenario_object_id", "property_type_id",
            unique=True,
            sqlite_where=Column("project_material_id").is_(None),
        ),
        Index(
            "idx_opd_frozen",
            "scenario_object_id", "project_material_id", "property_type_id",
            unique=True,
            sqlite_where=Column("project_material_id").isnot(None),
        ),
    )

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    scenario_object_id  = Column(Integer, ForeignKey("scenario_object.id", ondelete="CASCADE"), nullable=False, index=True)
    project_material_id = Column(Integer, nullable=True)
    property_type_id    = Column(Integer, ForeignKey("property_type.id", ondelete="RESTRICT"), nullable=False)
    value               = Column(Text, nullable=True)
    created_at          = Column(Text, nullable=False, default=_now)
    updated_at          = Column(Text, nullable=False, default=_now, onupdate=_now)
