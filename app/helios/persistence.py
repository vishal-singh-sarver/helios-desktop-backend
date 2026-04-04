"""
Context persistence — save, load, and version snapshots.

Storage layout:
  data/projects/{project_id}/
      current.xml.gz    ← gzip snapshot of live context  (fast r/w)
      registry.json     ← _object_registry + project metadata

  SQLite project_versions table:
      scene_xml BLOB    ← lzma-compressed XML  (archived versions, ~85-90% smaller)
      registry_json TEXT

Compression tiers:
  gzip  (stdlib) — current working file.  Fast, ~70% reduction.
  lzma  (stdlib) — archived versions.     Slower, ~85-90% reduction.
"""
import gzip
import json
import lzma
import tempfile
from pathlib import Path

from app.core.config import settings


def _project_dir(project_id: str) -> Path:
    d = settings.resolved_projects_dir / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Save ──────────────────────────────────────────────────────────────────────

def save_snapshot(project_id: str, ctx, registry: dict, metadata: dict) -> None:
    """
    Persist current context to disk and archive a version in SQLite.

    Steps:
      1. ctx.writeXML(tmp)         → write raw XML
      2. gzip compress              → data/projects/{id}/current.xml.gz
      3. write registry.json
      4. lzma compress XML         → INSERT project_versions row
    """
    proj_dir = _project_dir(project_id)

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        ctx.writeXML(tmp_path)

        raw_xml = Path(tmp_path).read_bytes()

        # Tier 1 — gzip for fast working-file reads
        gz_data = gzip.compress(raw_xml, compresslevel=6)
        (proj_dir / "current.xml.gz").write_bytes(gz_data)

        # Registry sidecar
        (proj_dir / "registry.json").write_text(
            json.dumps({"metadata": metadata, "objects": registry}, indent=2),
            encoding="utf-8",
        )

    finally:
        Path(tmp_path).unlink(missing_ok=True)


def save_version(project_id: str, label: str, ctx, registry: dict,
                 metadata: dict, db) -> int:
    """
    Compress current XML with lzma and insert a new project_versions row.
    Returns the new version id.
    """
    from app.db.models import ProjectVersion, Project
    from sqlalchemy import func

    proj_dir = _project_dir(project_id)

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        ctx.writeXML(tmp_path)
        raw_xml = Path(tmp_path).read_bytes()
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # Tier 2 — lzma for maximum archive compression
    compressed = lzma.compress(raw_xml, preset=6)

    # Next version number for this project
    last = (
        db.query(func.max(ProjectVersion.version_num))
        .filter(ProjectVersion.project_id == project_id)
        .scalar()
    )
    next_num = (last or 0) + 1

    row = ProjectVersion(
        project_id=project_id,
        version_num=next_num,
        label=label or f"Version {next_num}",
        scene_xml=compressed,
        registry_json=json.dumps({"metadata": metadata, "objects": registry}),
        bytes_original=len(raw_xml),
        bytes_compressed=len(compressed),
    )
    db.add(row)

    # Update project updated_at + current_version_id
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        from datetime import datetime, timezone
        project.updated_at = datetime.now(timezone.utc).isoformat()
        project.current_version_id = row.id

    db.commit()
    db.refresh(row)
    return row.id


# ── Load ──────────────────────────────────────────────────────────────────────

def load_snapshot(project_id: str, ctx) -> dict:
    """
    Restore context from current.xml.gz on disk.
    Returns the registry dict (metadata + objects).
    """
    proj_dir = _project_dir(project_id)
    gz_path = proj_dir / "current.xml.gz"
    registry_path = proj_dir / "registry.json"

    if not gz_path.exists():
        raise FileNotFoundError(f"No saved snapshot for project {project_id}")

    raw_xml = gzip.decompress(gz_path.read_bytes())

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp.write(raw_xml)
        tmp_path = tmp.name

    try:
        ctx.loadXML(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if registry_path.exists():
        return json.loads(registry_path.read_text(encoding="utf-8"))
    return {"metadata": {}, "objects": {}}


def restore_version(project_id: str, version_id: int, ctx, db) -> dict:
    """
    Decompress an archived version from SQLite and load it into ctx.
    Returns the registry dict.
    """
    from app.db.models import ProjectVersion

    row = db.query(ProjectVersion).filter(
        ProjectVersion.id == version_id,
        ProjectVersion.project_id == project_id,
    ).first()

    if not row:
        raise ValueError(f"Version {version_id} not found for project {project_id}")

    raw_xml = lzma.decompress(row.scene_xml)

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp.write(raw_xml)
        tmp_path = tmp.name

    try:
        ctx.loadXML(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return json.loads(row.registry_json)


# ── List ──────────────────────────────────────────────────────────────────────

def list_versions(project_id: str, db) -> list:
    """Return all version rows for a project (without the blob)."""
    from app.db.models import ProjectVersion

    rows = (
        db.query(
            ProjectVersion.id,
            ProjectVersion.version_num,
            ProjectVersion.label,
            ProjectVersion.created_at,
            ProjectVersion.bytes_original,
            ProjectVersion.bytes_compressed,
        )
        .filter(ProjectVersion.project_id == project_id)
        .order_by(ProjectVersion.version_num.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "version_num": r.version_num,
            "label": r.label,
            "created_at": r.created_at,
            "bytes_original": r.bytes_original,
            "bytes_compressed": r.bytes_compressed,
        }
        for r in rows
    ]
