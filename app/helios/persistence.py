"""
Context persistence — scenario-level save + load.

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

  SQLite project_versions table (defined in migrations/001_initial.sql):
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
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from app.core.config import settings


logger = logging.getLogger(__name__)

# How many rotated context.xml snapshots to keep per scenario. ONE: the
# previous save is a rollback point, older ones were never read by anything and
# a scene of this size makes them expensive — a 613 MB context.xml gzips to
# tens of MB, ten deep, per scenario, across 1,300+ scenarios.
#
# The rotation prunes to below this number BEFORE writing the new archive, so a
# value of N leaves exactly N on disk.
MAX_AUTOSAVE_ARCHIVES = 1


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
    _started = time.monotonic()

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
        logger.info("[save]    written   scenario=%s %.1f MB in %.1fs",
                    sctx.scenario_id[:8], len(raw_xml) / 1048576,
                    time.monotonic() - _started)
    except Exception:
        logger.exception(
            "[scenario-autosave] rotation/write failed for scenario %s",
            sctx.scenario_id,
        )


# ── Deferred save ─────────────────────────────────────────────────────────────
#
# ONE worker: saves for a scenario stay ordered, so a slower earlier write can
# never land after — and overwrite — a newer one. Geometry and weather share the
# same context.xml, so they must share this queue too.

_SAVE_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autosave")


def queue_scenario_autosave(sctx) -> None:
    """QUEUE a context.xml save — the caller does NOT wait for the write.

    No response is built from context.xml (geometry serializes from the DB +
    session state, weather from the live context), so making a mutation wait on
    writeXML was pure latency — and on a 1000x1000 ground that is a million
    primitives serialized while the user watches a spinner.

    The lock is taken INSIDE the queued work, not around the submit: held at
    submit time it would be released before the write ran, letting writeXML
    serialize a context mid-mutation.

    Best-effort — `trigger_scenario_autosave` no-ops when headless and
    swallows/logs any writeXML failure, so a queued save never surfaces.
    """
    # Imported here, not at module scope: session_store is a higher layer and
    # importing it eagerly would make persistence depend on the registry.
    from app.core.session_store import registry

    def _run() -> None:
        with registry._scenario_lock.read():
            trigger_scenario_autosave(sctx)

    logger.debug("[save]    queued    scenario=%s", sctx.scenario_id[:8])
    _SAVE_POOL.submit(_run)


def wait_for_scenario_saves() -> None:
    """Block until every already-queued save has been written.

    The pool has ONE worker, so a task submitted now cannot run until the saves
    ahead of it are done — waiting on it waits for them.
    """
    _SAVE_POOL.submit(lambda: None).result()


# ── Load ──────────────────────────────────────────────────────────────────────


def load_scenario_snapshot(sctx) -> bool:
    """
    Restore a scenario's PyHelios context from disk.

    Reads:
        data/projects/<pid>/scenarios/<sid>/context_file/context.xml

    Returns True when the context is trustworthy — loaded, or nothing to load.
    Returns False when loadXML RAISED, because it does not unwind: a failed load
    leaves everything it read so far in the context (3,000,000 primitives
    measured on a 613 MB file). Hydration then rebuilds every DB row on top of
    those orphans, so the scene is held twice and the doubled context is saved
    back — making the next open worse again. Callers must discard the context
    on False rather than build on it.
    """
    new_xml = _scenario_context_xml(sctx.project_id, sctx.scenario_id)
    if not new_xml.exists():
        logger.info("[context] no snapshot scenario=%s — building from the DB",
                    sctx.scenario_id[:8])
        return True

    size_mb = new_xml.stat().st_size / 1048576
    started = time.monotonic()
    try:
        sctx.context.loadXML(str(new_xml))
        logger.info("[context] loaded    scenario=%s %.1f MB in %.1fs",
                    sctx.scenario_id[:8], size_mb, time.monotonic() - started)
        return True
    except Exception as exc:
        # One line, not a traceback. This failure is EXPECTED and handled: a
        # tiled ground fails to reload because texture_repeat is not written to
        # context.xml, so the repeat returns as 1 and the engine's
        # subdiv < repeat x texture_pixels check rejects it. It fires on every
        # open of such a scenario, and a 12-line stack for a known, recovered
        # condition trains the reader to skip the log — which is how a real
        # error gets missed. Anything OTHER than that keeps its traceback.
        detail = str(exc)
        if "resolution of the texture image" in detail:
            logger.warning(
                "[context] load-failed scenario=%s after %.1fs — texture repeat "
                "not persisted (known engine limit); rebuilding from the DB",
                sctx.scenario_id[:8], time.monotonic() - started)
        else:
            logger.exception("[context] load-failed scenario=%s — rebuilding "
                             "from the DB", sctx.scenario_id[:8])
        return False


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


