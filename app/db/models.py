"""
SQLAlchemy ORM models — mirrors 001_initial.sql exactly.
"""
import uuid as _uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, Text, Float, LargeBinary,
    ForeignKey, UniqueConstraint, Index,
    func,
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
    utc_offset         = Column(Float, nullable=False, default=0.0)
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
