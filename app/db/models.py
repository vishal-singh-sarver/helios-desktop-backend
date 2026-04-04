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
