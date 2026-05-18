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
import logging
import lzma
import tempfile
from datetime import datetime
from pathlib import Path

from app.core.config import settings


logger = logging.getLogger(__name__)
MAX_AUTOSAVE_ARCHIVES = 10


def _project_dir(project_id: str) -> Path:
    d = settings.resolved_projects_dir / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _autosave_archives_dir(project_id: str) -> Path:
    d = _project_dir(project_id) / "autosave_archives"
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

        # Save as raw XML per ticket requirements
        (proj_dir / "current.xml").write_bytes(raw_xml)

        # Registry sidecar
        (proj_dir / "registry.json").write_text(
            json.dumps({"metadata": metadata, "objects": registry}, indent=2),
            encoding="utf-8",
        )

    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── Autosave ──────────────────────────────────────────────────────────────────

def _rotate_current(project_id: str) -> None:
    """
    Compress existing current.xml → autosave_archives/autosave_TIMESTAMP.xml.gz
    Delete oldest archive if over MAX_AUTOSAVE_ARCHIVES.
    """
    current_xml = settings.resolved_projects_dir / project_id / "current.xml"
    if not current_xml.exists():
        return

    archives_dir = _autosave_archives_dir(project_id)

    # Enforce cap (sort oldest → newest by mtime)
    existing = sorted(archives_dir.glob("autosave_*.xml.gz"), key=lambda p: p.stat().st_mtime)
    while len(existing) >= MAX_AUTOSAVE_ARCHIVES:
        existing[0].unlink(missing_ok=True)
        existing = existing[1:]

    # Compress current.xml into the timestamped archive
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archive_path = archives_dir / f"autosave_{ts}.xml.gz"
    
    raw_xml = current_xml.read_bytes()
    archive_path.write_bytes(gzip.compress(raw_xml, compresslevel=6))
    current_xml.unlink(missing_ok=True)


def trigger_autosave(ctx, project_id: str) -> None:
    """
    Fire-and-forget autosave. Called after every context mutation.
    Overwrites current.xml.gz and rotates the previous one into archives.
    """
    # Stub guard: no-op if writeXML is not available
    if not hasattr(ctx, "writeXML"):
        return

    proj_dir = _project_dir(project_id)

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        ctx.writeXML(str(tmp_path))
        raw_xml = tmp_path.read_bytes()
    except Exception:
        logger.exception("[autosave] writeXML failed for project %s", project_id)
        return
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        _rotate_current(project_id)
        # Write new current file as raw XML
        (proj_dir / "current.xml").write_bytes(raw_xml)
        logger.debug("[autosave] saved project %s (%d bytes)", project_id, len(raw_xml))
    except Exception:
        logger.exception("[autosave] rotation/write failed for project %s", project_id)


def trigger_scenario_autosave(sctx) -> None:
    """
    Persist scenario-specific weather context to disk.
    Path: data/scenarios/{scenario_id}/weather_context.xml.gz
    """
    if not sctx.context or not hasattr(sctx.context, "writeXML"):
        return

    scenario_dir = settings.resolved_scenarios_dir / sctx.scenario_id
    scenario_dir.mkdir(parents=True, exist_ok=True)
    xml_path = scenario_dir / "weather_context.xml"

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        sctx.context.writeXML(str(tmp_path))
        raw_xml = tmp_path.read_bytes()
        # Save as raw XML
        xml_path.write_bytes(raw_xml)
        logger.debug("[scenario-autosave] saved scenario %s (%d bytes)",
                     sctx.scenario_id, len(raw_xml))
    except Exception:
        logger.exception("[scenario-autosave] failed for scenario %s", sctx.scenario_id)
    finally:
        tmp_path.unlink(missing_ok=True)


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
    Restore context from current.xml on disk.
    Returns the registry dict (metadata + objects).
    """
    proj_dir = _project_dir(project_id)
    xml_path = proj_dir / "current.xml"
    registry_path = proj_dir / "registry.json"

    # Fallback to old compressed file if it exists during migration
    legacy_gz_path = proj_dir / "current.xml.gz"

    if not xml_path.exists():
        if legacy_gz_path.exists():
            raw_xml = gzip.decompress(legacy_gz_path.read_bytes())
            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
                tmp.write(raw_xml)
                tmp_path = tmp.name
            try:
                ctx.loadXML(tmp_path)
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        else:
            raise FileNotFoundError(f"No saved snapshot for project {project_id}")
    else:
        # Load raw XML directly (fast path)
        ctx.loadXML(str(xml_path))

    if registry_path.exists():
        return json.loads(registry_path.read_text(encoding="utf-8"))
    return {"metadata": {}, "objects": {}}


def load_scenario_snapshot(sctx) -> None:
    """
    Restore scenario weather context from weather_context.xml on disk.
    """
    xml_path = settings.resolved_scenarios_dir / sctx.scenario_id / "weather_context.xml"
    legacy_gz_path = settings.resolved_scenarios_dir / sctx.scenario_id / "weather_context.xml.gz"

    try:
        if xml_path.exists():
            # Load raw XML directly (fast path)
            sctx.context.loadXML(str(xml_path))
            logger.info("[scenario-load] restored weather data for scenario %s", sctx.scenario_id)
        elif legacy_gz_path.exists():
            # Fallback for old compressed files
            raw_xml = gzip.decompress(legacy_gz_path.read_bytes())
            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
                tmp.write(raw_xml)
                tmp_path = tmp.name
            try:
                sctx.context.loadXML(tmp_path)
                logger.info("[scenario-load] restored weather data (legacy gz) for scenario %s", sctx.scenario_id)
            finally:
                Path(tmp_path).unlink(missing_ok=True)
    except Exception:
        logger.exception("[scenario-load] failed for scenario %s", sctx.scenario_id)


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
