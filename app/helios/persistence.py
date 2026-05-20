"""
Context persistence — scenario-level save, load, and migration.

Storage layout (per scenario):
  data/projects/{project_id}/scenarios/{scenario_id}/
      context_file/
          context.xml             ← PyHelios state for this scenario
          archives/
              autosave_<ts>.xml.gz  ← rotated history, capped at MAX_AUTOSAVE_ARCHIVES
      weather/
          *.csv                   ← uploaded weather CSVs persist here
      metadata/                   ← reserved for future use
      export_files/               ← reserved for future use

  SQLite project_versions table:
      scene_xml BLOB              ← lzma-compressed XML (archived versions)
      registry_json TEXT

Compression tiers:
  gzip  (stdlib) — autosave archives.  Fast, ~70% reduction.
  lzma  (stdlib) — versioned snapshots in SQLite. Slower, ~85-90% reduction.

Phase 1 transitional rule:
    The project's in-memory PyHelios scene (ProjectContext) is NOT persisted
    to disk. Only scenario contexts (ScenarioContext) are persisted — they
    capture weather state today, and will absorb the scene state once
    Phase 2 collapses ProjectContext into ScenarioContext.
"""
import gzip
import json
import logging
import lzma
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from app.core.config import settings


logger = logging.getLogger(__name__)
MAX_AUTOSAVE_ARCHIVES = 10


# ── Path helpers ─────────────────────────────────────────────────────────────


def _ensure_scenario_structure(project_id: str, scenario_id: str) -> Path:
    """Create the canonical per-scenario folder shape. Idempotent.

    After this call the following subfolders are guaranteed to exist:
        context_file/
        context_file/archives/
        weather/
        metadata/
        export_files/

    Returns the scenario's root folder.
    """
    base = settings.scenario_dir(project_id, scenario_id)
    base.mkdir(parents=True, exist_ok=True)
    (base / "context_file").mkdir(exist_ok=True)
    (base / "context_file" / "archives").mkdir(exist_ok=True)
    (base / "weather").mkdir(exist_ok=True)
    (base / "metadata").mkdir(exist_ok=True)
    (base / "export_files").mkdir(exist_ok=True)
    return base


def _scenario_context_xml(project_id: str, scenario_id: str) -> Path:
    return settings.scenario_context_file_dir(project_id, scenario_id) / "context.xml"


def _scenario_archives_dir(project_id: str, scenario_id: str) -> Path:
    return settings.scenario_context_file_dir(project_id, scenario_id) / "archives"


# ── Autosave ──────────────────────────────────────────────────────────────────


def _rotate_scenario_current(project_id: str, scenario_id: str) -> None:
    """
    Compress existing context.xml → archives/autosave_<TIMESTAMP>.xml.gz
    Delete oldest archive if over MAX_AUTOSAVE_ARCHIVES.
    """
    current_xml = _scenario_context_xml(project_id, scenario_id)
    if not current_xml.exists():
        return

    archives_dir = _scenario_archives_dir(project_id, scenario_id)
    archives_dir.mkdir(parents=True, exist_ok=True)

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


def trigger_scenario_autosave(sctx) -> None:
    """
    Persist a scenario's PyHelios context to disk.

    Path:
        data/projects/<pid>/scenarios/<sid>/context_file/context.xml

    Rotates the previous context.xml into archives/ as a gzipped backup.
    No-op when PyHelios isn't available or the context lacks writeXML.
    """
    if not sctx.context or not hasattr(sctx.context, "writeXML"):
        return

    _ensure_scenario_structure(sctx.project_id, sctx.scenario_id)

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        sctx.context.writeXML(str(tmp_path))
        raw_xml = tmp_path.read_bytes()
    except Exception:
        logger.exception("[scenario-autosave] writeXML failed for scenario %s", sctx.scenario_id)
        return
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        _rotate_scenario_current(sctx.project_id, sctx.scenario_id)
        _scenario_context_xml(sctx.project_id, sctx.scenario_id).write_bytes(raw_xml)
        logger.debug(
            "[scenario-autosave] saved scenario %s (%d bytes)",
            sctx.scenario_id, len(raw_xml),
        )
    except Exception:
        logger.exception(
            "[scenario-autosave] rotation/write failed for scenario %s",
            sctx.scenario_id,
        )


# ── Load ──────────────────────────────────────────────────────────────────────


def load_scenario_snapshot(sctx) -> None:
    """
    Restore a scenario's PyHelios context from disk.

    Read order (first match wins):
      1. New nested path:
            data/projects/<pid>/scenarios/<sid>/context_file/context.xml
      2. Legacy flat path:
            data/scenarios/<sid>/weather_context.xml
            data/scenarios/<sid>/weather_context.xml.gz

    Legacy paths exist only for files that haven't been migrated yet by
    `migrate_disk_layout()`. They'll be cleaned up automatically the next
    time the migration runs (next server startup).
    """
    new_xml = _scenario_context_xml(sctx.project_id, sctx.scenario_id)

    legacy_root = settings.data_dir / "scenarios" / sctx.scenario_id
    legacy_xml = legacy_root / "weather_context.xml"
    legacy_gz = legacy_root / "weather_context.xml.gz"

    try:
        if new_xml.exists():
            sctx.context.loadXML(str(new_xml))
            logger.info("[scenario-load] restored scenario %s", sctx.scenario_id)
        elif legacy_xml.exists():
            sctx.context.loadXML(str(legacy_xml))
            logger.info("[scenario-load] restored (legacy xml) scenario %s", sctx.scenario_id)
        elif legacy_gz.exists():
            raw_xml = gzip.decompress(legacy_gz.read_bytes())
            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
                tmp.write(raw_xml)
                tmp_path = tmp.name
            try:
                sctx.context.loadXML(tmp_path)
                logger.info("[scenario-load] restored (legacy gz) scenario %s", sctx.scenario_id)
            finally:
                Path(tmp_path).unlink(missing_ok=True)
    except Exception:
        logger.exception("[scenario-load] failed for scenario %s", sctx.scenario_id)


# ── Versioning (SQLite-based, unchanged) ─────────────────────────────────────


def save_version(project_id: str, label: str, ctx, registry: dict,
                 metadata: dict, db) -> int:
    """
    Compress current XML with lzma and insert a new project_versions row.
    Returns the new version id.
    """
    from app.db.models import ProjectVersion, Project
    from sqlalchemy import func

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        ctx.writeXML(tmp_path)
        raw_xml = Path(tmp_path).read_bytes()
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    compressed = lzma.compress(raw_xml, preset=6)

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

    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        from datetime import datetime as _dt, timezone as _tz
        project.updated_at = _dt.now(_tz.utc).isoformat()
        project.current_version_id = row.id

    db.commit()
    db.refresh(row)
    return row.id


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


# ── One-time migration to nested layout ──────────────────────────────────────


def migrate_disk_layout() -> None:
    """
    One-time migration from legacy parallel-tree layout into nested.

    Old paths (any combination may exist):
      data/projects/<pid>/current.xml(.gz)         → discarded (project autosave dropped)
      data/projects/<pid>/autosave_archives/*      → discarded
      data/scenarios/<sid>/weather_context.xml(.gz) → moved
      data/<pid>/<sid>/weather.csv                  → moved

    New paths:
      data/projects/<pid>/scenarios/<sid>/context_file/context.xml
      data/projects/<pid>/scenarios/<sid>/weather/weather.csv

    Idempotent — skips files already at the new path. Safe to call on
    every startup. Logs the number of items moved.

    Wrapped in a top-level guard: any unexpected error logs but does NOT
    crash startup. Migration retries next time the server boots.
    """
    try:
        _migrate_disk_layout_impl()
    except Exception:
        logger.exception("[disk-migration] unexpected error; will retry on next startup")


def _migrate_disk_layout_impl() -> None:
    """Inner migration body — see migrate_disk_layout() for the contract."""
    from sqlalchemy.orm import Session
    from app.db.database import SessionLocal
    from app.db.models import Scenario, Project

    db: Session = SessionLocal()
    try:
        scenarios = list(db.query(Scenario).all())
        projects = list(db.query(Project).all())
    finally:
        db.close()

    moved = 0

    # ── (1) Per-scenario weather XML → scenarios/<sid>/context_file/context.xml
    legacy_scenarios_root = settings.data_dir / "scenarios"
    if legacy_scenarios_root.exists():
        for s in scenarios:
            new_ctx_dir = settings.scenario_context_file_dir(s.project_id, s.id)
            legacy_dir = legacy_scenarios_root / s.id

            for fname, is_gz in (
                ("weather_context.xml", False),
                ("weather_context.xml.gz", True),
            ):
                src = legacy_dir / fname
                if not src.exists():
                    continue

                new_ctx_dir.mkdir(parents=True, exist_ok=True)
                (new_ctx_dir / "archives").mkdir(exist_ok=True)
                dst = new_ctx_dir / "context.xml"

                if dst.exists():
                    # Already migrated — discard the legacy copy
                    try:
                        src.unlink(missing_ok=True)
                    except OSError:
                        pass
                    continue

                try:
                    if is_gz:
                        raw = gzip.decompress(src.read_bytes())
                        dst.write_bytes(raw)
                        try:
                            src.unlink(missing_ok=True)
                        except OSError:
                            pass
                    else:
                        src.replace(dst)
                    moved += 1
                except Exception:
                    logger.debug("[disk-migration] could not move %s (will retry next startup)", src)

            # Remove now-empty legacy scenario dir
            try:
                legacy_dir.rmdir()
            except OSError:
                pass

        # Remove now-empty legacy_scenarios_root
        try:
            legacy_scenarios_root.rmdir()
        except OSError:
            pass

    # ── (2) Per-scenario weather CSV → scenarios/<sid>/weather/weather.csv
    for s in scenarios:
        legacy_csv = settings.data_dir / s.project_id / s.id / "weather.csv"
        if not legacy_csv.exists():
            continue

        try:
            new_weather_dir = settings.scenario_dir(s.project_id, s.id) / "weather"
            new_weather_dir.mkdir(parents=True, exist_ok=True)
            dst = new_weather_dir / "weather.csv"

            if dst.exists():
                try:
                    legacy_csv.unlink(missing_ok=True)
                except OSError:
                    pass
            else:
                legacy_csv.replace(dst)
                moved += 1
        except Exception:
            logger.debug("[disk-migration] could not move CSV %s (will retry next startup)", legacy_csv)

        # Try to remove the now-empty legacy parent folders
        for legacy_parent in (legacy_csv.parent, legacy_csv.parent.parent):
            try:
                legacy_parent.rmdir()
            except OSError:
                pass

    # ── (3) Discard legacy project-level current.xml + autosave_archives
    # Phase 1 drops project-level autosave entirely — these files are no
    # longer useful and would just clutter the projects folder.
    #
    # All cleanup is best-effort: skip anything that errors (e.g. a Windows
    # file lock on a file currently held open by another process). Migration
    # is idempotent so the next startup will try again.
    for project in projects:
        proj_dir = settings.resolved_projects_dir / project.id
        for legacy_name in ("current.xml", "current.xml.gz", "registry.json"):
            legacy = proj_dir / legacy_name
            if legacy.exists():
                try:
                    legacy.unlink(missing_ok=True)
                    moved += 1
                except OSError:
                    logger.debug("[disk-migration] could not remove %s (will retry next startup)", legacy)
        legacy_archives = proj_dir / "autosave_archives"
        if legacy_archives.exists() and legacy_archives.is_dir():
            try:
                shutil.rmtree(legacy_archives)
                moved += 1
            except OSError:
                logger.debug("[disk-migration] could not remove %s (will retry next startup)", legacy_archives)

    if moved:
        logger.info("[disk-migration] moved/cleaned %d items into nested layout", moved)
